# SPDX-License-Identifier: MIT
"""Stand-ins of Android's media interfaces for system tests (quality/interfaces.yaml: camera, audio,
codec), speaking the protocols of the Rungic app's providers so the Linux consumers run unchanged:

  Capture   CaptureBridge.java's capture socket ($RUNGIC_CAPTURE_SOCKET): "microphone" (48 kHz mono
            s16le after a JSON header), "phone-output" (the phone's own speaker), "camera" (frames
            of android-yuv420 with the provider's binary header). The microphone plays a sine; a
            camera sends a picture with a marker in its top-left corner and a counter that moves.
  Codec     CodecBridge.java's broker ($RUNGIC_CODEC_SOCKET): MAGIC, OPEN -> a channel socket and a
            32 MiB shared memory passed with SCM_RIGHTS, then the session protocol (configure, FRAME,
            DRAIN, FLUSH, CLOSE; each output record acknowledged with 0xac). Its "decoder" does not
            decode: it answers each access unit with a grey I420 picture of the configured size, in
            presentation order once enough later frames came (as a decoder of B-frames does) and
            in bursts, so one exchange carries several records. It can refuse to open, as the
            provider does when no hardware component is there.

Each records what the consumer did (requests, open connections, protocol problems)."""
import json
import math
import mmap
import os
import socket
import struct
import tempfile
import threading
import time


class _Server:
    def __init__(self, name):
        self.dir = tempfile.TemporaryDirectory(prefix=f'rungic-{name}-')
        self.path = os.path.join(self.dir.name, f'{name}.sock')
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(16)
        self.lock = threading.Lock()
        self.problems = []
        self.closed = False

    def __enter__(self):
        self.accepting = threading.Thread(target=self._accept, daemon=True)
        self.accepting.start()
        return self

    def __exit__(self, *exc):
        self.closed = True
        try:
            self.server.shutdown(socket.SHUT_RDWR)     # wakes the accepting thread, which then ends
        except OSError:
            pass
        self.accepting.join(2)
        self.server.close()
        self.dir.cleanup()

    def _accept(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve_safely, args=(conn,), daemon=True).start()

    def _serve_safely(self, conn):
        try:
            with conn:
                self.serve(conn)
        except (OSError, ValueError, struct.error) as error:
            if not self.closed and not isinstance(error, (BrokenPipeError, ConnectionResetError)):
                self.problem(f'{type(error).__name__}: {error}')

    def problem(self, text):
        with self.lock:
            self.problems.append(text)


def _line(conn):
    data = bytearray()
    while not data.endswith(b'\n'):
        c = conn.recv(1)
        if not c:
            raise EOFError('closed before a request')
        data += c
    return json.loads(data)


class Capture(_Server):
    """The app's capture socket. `visible` False: requests are refused as the provider does when the
    Linux desktop is not in front. `cameras` as capture-info lists them (id -> metadata)."""

    def __init__(self, cameras=()):
        super().__init__('capture')
        self.cameras = {str(c['id']): c for c in cameras}
        self.visible = True
        self.requests = []       # every request, in order
        self.live = {}           # op (camera: "camera <id>") -> open connections
        self.received = {}       # op -> bytes received (phone-output)

    def count(self, op):
        with self.lock:
            return self.live.get(op, 0)

    def ops(self):
        with self.lock:
            return [r.get('op') for r in self.requests]

    def serve(self, conn):
        try:
            request = _line(conn)
        except EOFError:
            return
        with self.lock:
            self.requests.append(request)
        op = request.get('op')
        key = f'camera {request.get("id")}' if op == 'camera' else op
        if not self.visible and op != 'phone-output':
            conn.sendall(json.dumps({'error': '请先返回 Plasma Mobile'}).encode() + b'\n')
            return
        with self.lock:
            self.live[key] = self.live.get(key, 0) + 1
        try:
            if op == 'microphone':
                self.microphone(conn)
            elif op == 'phone-output':
                self.phone_output(conn)
            elif op == 'camera' and str(request.get('id')) in self.cameras:
                self.camera(conn, self.cameras[str(request['id'])])
            else:
                conn.sendall(json.dumps({'error': 'Unsupported capture operation'}).encode() + b'\n')
        except (BrokenPipeError, ConnectionResetError):
            pass        # the consumer stopped
        finally:
            with self.lock:
                self.live[key] -= 1

    def microphone(self, conn, frequency=440, amplitude=8000):
        conn.sendall(b'{"ok":true,"rate":48000,"channels":1,"format":"s16le"}\n')
        n, start = 0, time.monotonic()
        while self.visible and not self.closed:
            block = struct.pack('<960h', *(int(amplitude * math.sin(2 * math.pi * frequency * (n + i) / 48000))
                                            for i in range(960)))
            conn.sendall(block)                 # 20 ms, at the pace of the real recording
            n += 960
            delay = start + n / 48000 - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    def phone_output(self, conn):
        conn.sendall(b'{"ok":true,"rate":48000,"channels":2,"format":"s16le"}\n')
        while True:
            data = conn.recv(65536)
            if not data:
                return
            with self.lock:
                self.received['phone-output'] = self.received.get('phone-output', 0) + len(data)

    def camera(self, conn, meta):
        width, height, rotation = meta['width'], meta['height'], meta['rotation']
        conn.sendall(json.dumps({**meta, 'ok': True, 'format': 'android-yuv420'}).encode() + b'\n')
        y = bytearray(width * height)
        # The marker: a white block at the sensor's top-left, so a consumer can see the rotation.
        for row in range(height // 8):
            y[row * width:row * width + width // 8] = b'\xff' * (width // 8)
        chroma = bytes([128]) * (width // 2 * height // 2)
        frame = 0
        while self.visible and not self.closed:
            # A counter that moves: a grey bar along the bottom row band, at a new place each frame.
            band = bytearray(y)
            x = (frame * 16) % (width - 16)
            for row in range(height - height // 8, height):
                band[row * width + x:row * width + x + 16] = b'\x80' * 16
            header = struct.pack('>14iq', 0x4d43414d, 1, width, height, rotation, width, width // 2, width // 2,
                                 1, 1, 1, len(band), len(chroma), len(chroma), time.monotonic_ns())
            conn.sendall(header + bytes(band) + chroma + chroma)
            frame += 1
            time.sleep(1 / 30)


HALF = 16 * 1024 * 1024
MAGIC, OPEN, CHANNEL, MAGIC2 = 0x4d434231, 0x4f50454e, 0x4d434631, 0x4d434232
FRAME, DRAIN, FLUSH, CLOSE, ACK = 1, 2, 3, 4, 0xac
DONE, ENCODED, DECODED, CONFIG, EOS, ERROR = 0, 1, 2, 3, 4, -1


class Codec(_Server):
    """The app's codec broker. `refuse` makes every configure fail as without a hardware component.
    Channel version 2 (pictures still in shared memory: the app may decline buffers) unless
    `version=1`, an app before it."""

    def __init__(self, refuse=False, depth=2, burst=2, version=2):
        super().__init__('codec')
        self.refuse, self.version = refuse, version
        # Pictures wait until `depth` later frames came (no later frame of the stream may then come
        # before them: as deep as the stream reorders), and leave `burst` at a time.
        self.depth, self.burst = depth, burst
        self.opened = []         # (encoder, kind, width, height) of each session configured
        self.sessions = 0        # sessions open now
        self.commands = []       # every command, in order: ('FRAME', id, pts) / ('FLUSH',) ...
        self.records = 0         # output records acknowledged

    def serve(self, conn):
        if self._int(conn) != MAGIC:
            raise ValueError('broker version')
        while True:
            try:
                op = self._int(conn)
            except EOFError:
                return
            if op != OPEN:
                raise ValueError('broker operation')
            local, remote = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
            memory = os.memfd_create('rungic-codec-frames')
            os.ftruncate(memory, HALF * 2)
            socket.send_fds(conn, [struct.pack('>I', CHANNEL)], [remote.fileno(), memory])
            remote.close()
            threading.Thread(target=self._session_safely, args=(local, memory), daemon=True).start()

    def _session_safely(self, conn, memory):
        shared = mmap.mmap(memory, HALF * 2)
        os.close(memory)
        with self.lock:
            self.sessions += 1
        try:
            with conn:
                self.session(conn, shared)
        except EOFError:
            pass
        except (OSError, ValueError, struct.error) as error:
            if not self.closed:
                self.problem(f'session: {type(error).__name__}: {error}')
        finally:
            with self.lock:
                self.sessions -= 1
            shared.close()

    @staticmethod
    def _int(conn, signed=True):
        data = b''
        while len(data) < 4:
            part = conn.recv(4 - len(data))
            if not part:
                raise EOFError
            data += part
        return struct.unpack('>i' if signed else '>I', data)[0]

    def _long(self, conn):
        return (self._int(conn, False) << 32) | self._int(conn, False)

    def _error(self, conn, text):
        message = f'java.io.IOException: {text}'.encode()
        conn.sendall(struct.pack('>ii', ERROR, len(message)) + message)

    def session(self, conn, shared):
        magic = self._int(conn, False)
        if magic == MAGIC2 and self.version < 2:
            self._error(conn, 'Channel version')
            return
        if magic not in (MAGIC, MAGIC2):
            raise ValueError('channel version')
        values = [magic] + [self._int(conn) for _ in range(12 if magic == MAGIC2 else 11)]
        version = 2 if magic == MAGIC2 else 1
        encoder, kind, width, height = values[1] == 1, values[2], values[3], values[4]
        name = f'c2.qti.{("avc", "hevc", "vp9")[kind]}.{"encoder" if encoder else "decoder"}'
        if self.refuse:
            self._error(conn, 'Hardware codec unavailable')
            return
        with self.lock:
            self.opened.append((encoder, kind, width, height))
        conn.sendall(struct.pack('>ii', DONE, len(name)) + name.encode())
        pending = []        # (pts, id) not yet output
        while True:
            cmd = self._int(conn)
            if cmd == CLOSE:
                return
            if cmd == FLUSH:
                with self.lock:
                    self.commands.append(('FLUSH',))
                pending.clear()
                conn.sendall(struct.pack('>i', DONE))
                continue
            if cmd == FRAME:
                fid, pts, flags, length = self._int(conn), self._long(conn), self._int(conn), self._int(conn)
                if length <= 0 or length > HALF:
                    raise ValueError('frame size')
                with self.lock:
                    self.commands.append(('FRAME', fid, pts))
                if encoder:
                    self._error(conn, 'the stand-in does not encode')
                    return
                pending.append((pts, fid))
                out = []
                if len(pending) >= self.depth + self.burst:
                    pending.sort()
                    out, pending = pending[:self.burst], pending[self.burst:]
            elif cmd == DRAIN:
                with self.lock:
                    self.commands.append(('DRAIN',))
                out, pending = sorted(pending), []
            else:
                raise ValueError(f'codec operation {cmd}')
            for pts, fid in out:
                self._picture(conn, shared, fid, pts, width, height, version)
            if cmd == DRAIN:
                conn.sendall(struct.pack('>i', EOS))
            conn.sendall(struct.pack('>i', DONE))

    def _picture(self, conn, shared, fid, pts, width, height, version):
        luma, chroma = width * height, (width // 2) * (height // 2)
        shared[HALF:HALF + luma] = bytes([fid % 200 + 16]) * luma
        shared[HALF + luma:HALF + luma + 2 * chroma] = b'\x80' * (2 * chroma)
        meta = (width, height, 0, 0, width, 1, luma, width // 2, 1, chroma, width // 2, 1, chroma)
        record = struct.pack('>iiiiq13i', DECODED, fid, 0, luma + 2 * chroma, pts, *meta)
        if version == 2:          # plane offsets, shared memory (-1), depth
            record += struct.pack('>5i', 0, luma, luma + chroma, -1, 8)
        conn.sendall(record)
        if self._int(conn) != ACK:
            raise ValueError('frame acknowledgment')
        with self.lock:
            self.records += 1
