# SPDX-License-Identifier: MIT
"""The communication audio contract from both ends (quality/contracts/communication-audio.json), built
and run in the system test container (no PulseAudio, no Android, no KWin needed):

- consumer: the phone mode session's client (agent/assistant/session/audio.cpp, driven muted by
  tools/system/communication_client.cpp) against a stand-in of the service on
  $XDG_RUNTIME_DIR/rungic-communication.sock: it opens muted, follows the positions of its epoch only,
  learns the new epoch from a flush, and reports the service's error.
- provider: the real service (shared/media/communication-audio.cpp) with a fake pactl (its PulseAudio
  pipe devices are FIFOs, as module-pipe-sink/-source make them) and a stand-in of Android's capture
  socket at /mnt/android-wayland/capture.sock (the audio contract's communication ops): a client opens
  a session, gets ready and positions, its PCM reaches Android framed with the epoch, a flush moves the
  epoch, mute and close are answered, a second client is refused, and the devices are unloaded. With
  PulseAudio's pipe kept full, Android gets the call's PCM at the rate it plays, a little ahead and
  never falling behind (an underrun is heard as stutter, docs/101)."""
import json
import os
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

import contracts
import harness

SRC = Path('/src')
BUILD = Path('/tmp/communication')
CONTRACT = contracts.load('communication-audio')
REQUESTS, REPLIES = CONTRACT['messages']['requests'], CONTRACT['messages']['replies']
CAPTURE = Path('/mnt/android-wayland/capture.sock')   # the provider's fixed path (the app's socket)
PACTL = '''import os, sys
args = sys.argv[1:]
with open(os.environ['FAKE_PACTL_LOG'], 'a') as log:
    log.write(' '.join(args) + '\\n')
if args[:1] == ['load-module']:
    path = next(a[5:] for a in args if a.startswith('file='))
    if not os.path.exists(path):
        os.mkfifo(path, 0o600)
    print(30 if args[1] == 'module-pipe-source' else 31)
elif args[:1] == ['get-sink-volume']:
    print('Volume: front-left: 26214 /  40% / -23.88 dB,   front-right: 26214 /  40% / -23.88 dB')
'''


def compile_(output, *sources, include=None, extra=()):
    flags = subprocess.run(['pkg-config', '--cflags', '--libs', 'Qt6Core', 'Qt6Network', 'gstreamer-1.0',
                            'gstreamer-app-1.0', *extra], capture_output=True, text=True, check=True).stdout.split()
    done = subprocess.run(['g++', '-std=gnu++20', '-O1', '-o', str(output), *([f'-I{include}'] if include else []),
                           *map(str, sources), *flags], capture_output=True, text=True)
    if done.returncode:
        raise harness.Failed(f'building {output.name}: {done.stderr[-600:]}')
    return output


def build():
    BUILD.mkdir(exist_ok=True)
    (BUILD / 'bin').mkdir(exist_ok=True)
    (BUILD / 'bin/pactl').write_text(f'#!/usr/bin/python3\n{PACTL}')
    (BUILD / 'bin/pactl').chmod(0o755)
    service = compile_(BUILD / 'rungic-communication-audio', SRC / 'shared/media/communication-audio.cpp')
    client = compile_(BUILD / 'communication_client', SRC / 'tools/system/communication_client.cpp',
                      SRC / 'agent/assistant/session/audio.cpp', include=SRC / 'agent/assistant/session')
    session = SRC / 'agent/assistant/session'
    player = compile_(BUILD / 'call_playback', SRC / 'tools/system/call_playback.cpp', session / 'session.cpp',
                      session / 'audio.cpp', session / 'core.cpp', include=session, extra=['Qt6WebSockets'])
    return service, client, player


def runtime(name):
    path = Path(f'/tmp/rt-{name}')
    path.mkdir(mode=0o700, exist_ok=True)
    return path


class Lines:
    """JSON lines from a socket, with a timeout."""

    def __init__(self, sock):
        self.sock, self.buffer = sock, b''

    def send(self, message):
        self.sock.sendall((json.dumps(message) + '\n').encode())

    def next(self, want=lambda m: True, timeout=5):
        deadline = time.monotonic() + timeout
        while True:
            while b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                message = json.loads(line)
                if want(message):
                    return message
            left = deadline - time.monotonic()
            if left <= 0:
                raise harness.Failed('no answer from the communication audio socket')
            self.sock.settimeout(left)
            part = self.sock.recv(65536)
            if not part:
                return None
            self.buffer += part


def keeps(name, message, steps, what):
    problems = contracts.validate(REPLIES[name], message or {})
    if problems:
        raise harness.Failed(f'{what}: {message} breaks the contract: {problems}')
    steps.append(what)


# ---- consumer ------------------------------------------------------------------------------------

class Service:
    """The service as the contract describes it, for the session's client: ready on open, positions of
    the current epoch every 50 ms, a flush answered with the next epoch (and one stale position of the
    old one right after it), mute answered and then the session failed."""

    def __init__(self, path):
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        self.server.listen(1)
        self.requests, self.problems = [], []
        self.epoch, self.played, self.lock = 1, 0, threading.Lock()
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        conn, _ = self.server.accept()
        lines = Lines(conn)
        opened = threading.Event()

        def positions():
            while opened.is_set():
                with self.lock:
                    self.played += 240
                    lines.send({**REPLIES['position'], 'epoch': self.epoch, 'playedFrames': self.played,
                                'writtenFrames': self.played + 4800})
                time.sleep(0.05)
        try:
            while True:
                request = lines.next(timeout=30)
                if request is None:
                    return
                self.requests.append(request)
                example = REQUESTS.get(request.get('op'))
                found = contracts.validate(example, request, 'request') if example else ['unknown op']
                self.problems += [f'{request.get("op")}: {p}' for p in found]
                if request['op'] == 'open':
                    lines.send({**REPLIES['ready'], 'sessionId': request['sessionId'], 'microphone': not request['muted']})
                    opened.set()
                    threading.Thread(target=positions, daemon=True).start()
                elif request['op'] == 'flush':
                    with self.lock:
                        lines.send({**REPLIES['flushed'], 'id': request['id'], 'epoch': self.epoch, 'playedFrames': self.played,
                                    'writtenFrames': self.played + 4800, 'newEpoch': self.epoch + 1})
                        lines.send({**REPLIES['position'], 'epoch': self.epoch, 'playedFrames': 999999, 'writtenFrames': 999999})
                        self.epoch, self.played = self.epoch + 1, 0
                elif request['op'] == 'mute':
                    lines.send({**REPLIES['muted'], 'id': request['id'], 'muted': request['muted']})
                    opened.clear()
                    lines.send(REPLIES['error'])
        except (OSError, harness.Failed):
            return


# covers[consumer]: iface:communication-audio
def consumer(client, steps):
    run = runtime('consumer')
    service = Service(run / 'rungic-communication.sock')
    done = subprocess.run([str(client), 'consumer-test'], capture_output=True, text=True, timeout=30,
                          env={**os.environ, 'XDG_RUNTIME_DIR': str(run)})
    events = {}
    for event in map(json.loads, done.stdout.splitlines()):
        events.setdefault(event['event'], event)   # the first of each (closing the client reports a disconnect)
    if 'timeout' in events or 'failed' not in events:
        raise harness.Failed(f'the client did not finish: {done.stdout[-500:]} {done.stderr[-300:]}')
    if service.problems:
        raise harness.Failed(f'the client broke the contract: {service.problems}')
    ops = [r['op'] for r in service.requests]
    if ops != ['open', 'flush', 'mute'] or service.requests[0]['muted'] is not True:
        raise harness.Failed(f'requests {service.requests}')
    steps.append('the session opens muted with its id, then flushes and mutes, as the contract asks')
    if not events['ready']['opened']:
        raise harness.Failed('ready did not open the client')
    playing = events['playing']
    if not (playing['epoch'] == 1 and playing['played'] > 0 and playing['written'] == playing['played'] + 4800):
        raise harness.Failed(f'positions not followed: {playing}')
    steps.append("the client follows the service's positions")
    if events['flushed']['epoch'] != 2 or events['after-flush']['epoch'] != 2:
        raise harness.Failed(f'flush: {events["flushed"]} {events["after-flush"]}')
    if not 0 < events['after-flush']['played'] < 999999:
        raise harness.Failed(f'a position of the old epoch was taken: {events["after-flush"]}')
    steps.append('a flush moves the client to the new epoch; positions of the old one are ignored')
    if events['failed']['error'] != REPLIES['error']['error']:
        raise harness.Failed(f'error: {events["failed"]}')
    steps.append("the service's error reaches the session")


# ---- provider ------------------------------------------------------------------------------------

class Android:
    """Android's capture socket as the audio contract gives its communication ops."""

    def __init__(self):
        self.epoch, self.packets, self.lock = 1, [], threading.Lock()
        self.arrived = []          # (time, bytes so far): when the PCM came

    def output(self, conn, stream, request):
        while True:
            head = stream.read(12)
            if len(head) < 12:
                return
            epoch, count = struct.unpack('>QI', head)
            pcm = stream.read(count)
            with self.lock:
                self.packets.append((epoch, pcm))
                self.arrived.append((time.monotonic(), (self.arrived[-1][1] if self.arrived else 0) + len(pcm)))

    def control(self, conn, stream, request):
        for line in stream:
            ask = json.loads(line)
            with self.lock:
                written = sum(len(p) for e, p in self.packets if e == self.epoch) // 2
                reply = {'ok': True, 'epoch': self.epoch, 'playedFrames': written // 2, 'writtenFrames': written, 'rate': 48000}
                if ask['op'] == 'flush':
                    if ask['epoch'] <= self.epoch:
                        reply = {'error': 'Stale playback generation'}
                    else:
                        reply['newEpoch'], self.epoch = ask['epoch'], ask['epoch']
            conn.sendall((json.dumps({**reply, 'id': ask.get('id', 0)}) + '\n').encode())

    def pcm(self, epoch):
        with self.lock:
            return b''.join(p for e, p in self.packets if e == epoch)

    def microphone(self, conn, stream, request):
        """The phone's microphone in a quiet room: silence, 20 ms at a time, as long as it is open."""
        try:
            while True:
                conn.sendall(bytes(1920))
                time.sleep(0.02)
        except OSError:
            return


RATE = 48000 * 2                 # bytes a second: 48 kHz mono s16le


# covers[system]: agent.phone-mode/E16
def paced(fifo, android, steps, seconds=3.0):
    """PulseAudio's pipe sink writes whenever the pipe has room: keep it full for `seconds` and see how
    far ahead of playback Android is, every 100 ms. Ahead by up to the service's lead (80 ms; this
    stand-in, a Python thread, sees the PCM a little late), never behind and not drifting: the old
    pacing (read 20 ms, wait 20 ms) lost ground all the time, -400 ms after 3 s here."""
    with android.lock:
        before = android.arrived[-1][1] if android.arrived else 0
    block, written, stop = bytes(1920), [0], threading.Event()

    def pulseaudio():
        while not stop.is_set():
            try:
                written[0] += os.write(fifo, block)
            except BlockingIOError:
                time.sleep(0.005)
    feeder = threading.Thread(target=pulseaudio, daemon=True)
    start = time.monotonic()
    feeder.start()
    time.sleep(seconds)
    stop.set()
    feeder.join()
    with android.lock:
        arrived = [(t - start, n - before) for t, n in android.arrived if t >= start]
    ahead = []
    for at in [0.3 + i / 10 for i in range(int((seconds - 0.4) * 10))]:
        got = max((n for t, n in arrived if t <= at), default=0)
        ahead.append(1000 * (got - at * RATE) / RATE)
    drift = sum(ahead[-5:]) / 5 - sum(ahead[:5]) / 5
    if min(ahead) < 10 or max(ahead) > 160 or drift < -20:
        raise harness.Failed('Android ahead of playback by ' + ', '.join(f'{a:.0f}' for a in ahead) + ' ms')
    steps.append(f'kept full, the pipe reaches Android at the rate it plays, {min(ahead):.0f}-{max(ahead):.0f} ms ahead')


def wait_for(condition, what, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise harness.Failed(f'timed out waiting for {what}')
        time.sleep(0.02)


# covers[provider]: iface:communication-audio
def provider(service, steps):
    run = runtime('provider')
    log = run / 'pactl.log'
    android = Android()
    with contracts.StandIn('audio', socket_name='capture', streams={
            'communication-output': android.output, 'communication-control': android.control}) as capture:
        CAPTURE.unlink(missing_ok=True)
        CAPTURE.symlink_to(capture.path)
        process = subprocess.Popen([str(service)], stderr=open(run / 'service.err', 'w'), env={
            **os.environ, 'XDG_RUNTIME_DIR': str(run), 'PATH': f'{BUILD / "bin"}:{os.environ["PATH"]}',
            'FAKE_PACTL_LOG': str(log)})
        try:
            path = run / 'rungic-communication.sock'
            wait_for(path.exists, 'the service socket')
            owner = Lines(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
            owner.sock.connect(str(path))
            owner.send({**REQUESTS['open'], 'sessionId': 'provider-test', 'muted': True, 'id': 1})
            ready = owner.next(lambda m: m.get('type') != 'position')
            keeps('ready', ready, steps, 'open: ready, muted (no microphone), with the PulseAudio devices')
            if ready['microphone'] is not False or ready['sessionId'] != 'provider-test':
                raise harness.Failed(f'ready: {ready}')
            asked = [r['op'] for r in capture.requests]
            if asked != ['communication-output', 'communication-control'] or capture.problems:
                raise harness.Failed(f'Android was asked {capture.requests} {capture.problems}')
            steps.append("Android's communication output and control are opened for the session, not the microphone")
            keeps('position', owner.next(lambda m: m.get('type') == 'position'), steps, 'positions every 100 ms')
            fifo = os.open(run / 'rungic-communication-output.pcm', os.O_WRONLY | os.O_NONBLOCK)
            first = bytes(range(256)) * 15                 # what PulseAudio's android_communication sink writes
            os.write(fifo, first)
            wait_for(lambda: android.pcm(1) == first, 'the PCM at Android, epoch 1')
            steps.append("the call's PCM reaches Android in packets of the session's epoch")
            owner.send({**REQUESTS['flush'], 'sessionId': 'provider-test', 'id': 2})
            flushed = owner.next(lambda m: m.get('id') == 2 and m.get('type') != 'position')
            keeps('flushed', flushed, steps, 'flush: answered with the next epoch')
            if flushed['newEpoch'] != 2 or android.epoch != 2:
                raise harness.Failed(f'flush: {flushed}, Android at {android.epoch}')
            second = bytes(reversed(first))
            os.write(fifo, second)
            wait_for(lambda: android.pcm(2) == second, 'the PCM at Android, epoch 2')
            steps.append('after a flush the PCM carries the new epoch')
            paced(fifo, android, steps)
            owner.send({**REQUESTS['mute'], 'sessionId': 'provider-test', 'id': 3})
            keeps('muted', owner.next(lambda m: m.get('id') == 3 and m.get('type') != 'position'), steps, 'mute answered')
            intruder = Lines(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
            intruder.sock.connect(str(path))
            intruder.send({**REQUESTS['open'], 'sessionId': 'intruder'})
            if intruder.next().get('error') != 'Communication audio is in use':
                raise harness.Failed('a second client was not refused')
            steps.append('a second client is refused while the session is open')
            owner.send({**REQUESTS['close'], 'sessionId': 'provider-test', 'id': 4})
            keeps('closed', owner.next(lambda m: m.get('id') == 4 and m.get('type') != 'position'), steps, 'close answered')
            os.close(fifo)
            wait_for(lambda: log.read_text().count('unload-module') == 2, 'the PulseAudio devices unloaded')
            calls = log.read_text().splitlines()
            if not (any(c.startswith('load-module module-pipe-sink sink_name=android_communication ') for c in calls)
                    and any(c.startswith('load-module module-pipe-source source_name=android_communication_microphone ') for c in calls)
                    and 'set-sink-volume android_communication 40%' in calls):
                raise harness.Failed(f'pactl: {calls}')
            steps.append("the devices follow the phone output's volume and are unloaded on close")
        finally:
            process.terminate()
            process.wait(5)
            CAPTURE.unlink(missing_ok=True)


def tone(seconds, hz=440):
    """A 440 Hz tone, 48 kHz mono s16le: speech stands in for nothing here, every sample is checked."""
    import math
    n = int(48000 * seconds)
    return b''.join(struct.pack('<h', int(8000 * math.sin(2 * math.pi * hz * i / 48000))) for i in range(n))


# covers[system]: agent.phone-mode/E16
def unmuted(service, steps):
    """A call with the microphone on: the call's sound goes through the echo canceller's reference
    (webrtcechoprobe) before Android. What PulseAudio writes must reach Android sample for sample, at
    the rate it plays (2026-10-04: with only the first sound heard, the rest of a reply was lost)."""
    run = runtime('unmuted')
    android = Android()
    with contracts.StandIn('audio', socket_name='capture', streams={
            'communication-output': android.output, 'communication-control': android.control,
            'communication-microphone': android.microphone}) as capture:
        CAPTURE.unlink(missing_ok=True)
        CAPTURE.symlink_to(capture.path)
        process = subprocess.Popen([str(service)], stderr=open(run / 'service.err', 'w'), env={
            **os.environ, 'XDG_RUNTIME_DIR': str(run), 'PATH': f'{BUILD / "bin"}:{os.environ["PATH"]}',
            'FAKE_PACTL_LOG': str(run / 'pactl.log'), 'GST_DEBUG': os.environ.get('GST_DEBUG', '1')})
        try:
            path = run / 'rungic-communication.sock'
            wait_for(path.exists, 'the service socket')
            owner = Lines(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
            owner.sock.connect(str(path))
            owner.send({**REQUESTS['open'], 'sessionId': 'unmuted-test', 'muted': False, 'id': 1})
            ready = owner.next(lambda m: m.get('type') not in ('position',))
            if not ready or ready.get('type') != 'ready' or ready.get('microphone') is not True:
                raise harness.Failed(f'unmuted open: {ready}; {(run / "service.err").read_text()[-800:]}')
            steps.append('unmuted: ready with the microphone, through the echo canceller')
            # PulseAudio's android_communication_microphone pipe source reads what the service writes.
            heard = os.open(run / 'rungic-communication-input.pcm', os.O_RDONLY | os.O_NONBLOCK)
            listening = threading.Event()
            listening.set()

            def pulseaudio_source():
                while listening.is_set():
                    try:
                        os.read(heard, 65536)
                    except BlockingIOError:
                        pass
                    time.sleep(0.01)
            threading.Thread(target=pulseaudio_source, daemon=True).start()
            fifo = os.open(run / 'rungic-communication-output.pcm', os.O_WRONLY | os.O_NONBLOCK)
            sound = tone(2.0)
            with android.lock:
                before = len(b''.join(p for e, p in android.packets if e == 1))
            start, at = time.monotonic(), 0
            while at < len(sound) and time.monotonic() - start < 10:
                try:
                    at += os.write(fifo, sound[at:at + 1920])
                except BlockingIOError:
                    time.sleep(0.005)
                except BrokenPipeError:
                    said = owner.next(lambda m: m.get('type') == 'error', timeout=1)
                    raise harness.Failed(f'the service closed the call after {at} of {len(sound)} bytes ({at / 96000:.2f} s): {said}; '
                                         f'Android got {len(android.pcm(1)) - before} bytes')
            time.sleep(0.5)
            got = android.pcm(1)[before:]
            errors = []
            if sound not in got:
                # Where it parts: the first window of 100 ms that differs, and its loudness.
                import array
                def loud(b):
                    a = array.array('h', b[:len(b) - len(b) % 2])
                    return int((sum(x * x for x in a) / max(1, len(a))) ** 0.5)
                windows = [loud(got[i:i + 9600]) for i in range(0, min(len(got), 2 * 96000 + 9600), 9600)]
                errors.append(f'{len(got)} bytes reached Android for {len(sound)} written; loudness per 100 ms: {windows}')
            if errors:
                raise harness.Failed('; '.join(errors) + f'; service: {(run / "service.err").read_text()[-600:]}')
            steps.append('unmuted: 2 s of tone reach Android sample for sample')
            os.close(fifo)
            listening.clear()
        finally:
            process.terminate()
            process.wait(5)
            CAPTURE.unlink(missing_ok=True)


class PlayingAndroid(Android):
    """Android's communication track as it behaves: 100 ms of buffer that plays at 48 kHz in real time;
    a full buffer is not read (the socket backs up); an empty one plays nothing (underrun); the
    playback head it reports moves in 100 ms steps."""
    BUFFER = 4800

    def __init__(self):
        super().__init__()
        self.written = self.played = self.underrun = 0
        self.playing = False
        threading.Thread(target=self.play, daemon=True).start()

    def play(self):
        last = time.monotonic()
        while True:
            time.sleep(0.005)
            now = time.monotonic()
            due, last = int((now - last) * 48000), now
            with self.lock:
                if not self.playing:
                    continue
                step = min(due, self.written - self.played)
                self.played += step
                self.underrun += due - step

    def output(self, conn, stream, request):
        while True:
            while True:
                with self.lock:
                    if self.written - self.played <= self.BUFFER:
                        break
                time.sleep(0.002)
            head = stream.read(12)
            if len(head) < 12:
                return
            epoch, count = struct.unpack('>QI', head)
            pcm = stream.read(count)
            with self.lock:
                self.packets.append((epoch, pcm))
                self.written += len(pcm) // 2
                self.playing = True

    def control(self, conn, stream, request):
        for line in stream:
            ask = json.loads(line)
            with self.lock:
                reply = {'ok': True, 'epoch': self.epoch, 'playedFrames': self.played - self.played % 4800,
                         'writtenFrames': self.written, 'rate': 48000}
            conn.sendall((json.dumps({**reply, 'id': ask.get('id', 0)}) + '\n').encode())


def loudness(pcm, window=9600):
    """RMS of each 100 ms of 48 kHz mono s16le."""
    import array
    out = []
    for i in range(0, len(pcm) - window + 1, window):
        a = array.array('h', pcm[i:i + window])
        out.append(int((sum(x * x for x in a) / len(a)) ** 0.5))
    return out


# covers[system]: agent.phone-mode/E16
def call_playback(service, player, steps):
    """A reply played as a call plays it: the real Session deciding when to play (tick), its pulsesink
    into a real PulseAudio's android_communication, the service, and an Android that plays in real time
    with a 100 ms buffer. All of a 3 s tone must be heard, Android running dry for at most 100 ms in all
    (start and end included): a stutter is Android's buffer running dry. (2026-10-04: the service kept
    Android 80 ms ahead with PulseAudio's silence, the session waited for that buffer to empty, and
    after the first sound only silence was played.)"""
    run = runtime('call')
    for f in run.iterdir():
        if f.is_file() or f.is_fifo() or f.is_socket():
            f.unlink()
    config = run / 'pulse.pa'
    config.write_text('.fail\nload-module module-native-protocol-unix auth-anonymous=1\n'
                      'load-module module-null-sink sink_name=android_phone rate=48000 channels=2\n'
                      'load-module module-suspend-on-idle timeout=3\n')
    env = {**os.environ, 'XDG_RUNTIME_DIR': str(run), 'HOME': str(run), 'PULSE_STATE_PATH': str(run / 'state'),
           'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/nonexistent'}
    pa = subprocess.Popen(['pulseaudio', '-n', '-F', str(config), '--daemonize=no', '--exit-idle-time=-1',
                           '--use-pid-file=no', '--realtime=no', '--high-priority=no', f'--log-target=file:{run / "pa.log"}'],
                          env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    android = PlayingAndroid()
    process = None
    try:
        wait_for(lambda: subprocess.run(['pactl', 'info'], env=env, capture_output=True).returncode == 0, 'PulseAudio', 10)
        with contracts.StandIn('audio', socket_name='capture', streams={
                'communication-output': android.output, 'communication-control': android.control,
                'communication-microphone': android.microphone}) as capture:
            CAPTURE.unlink(missing_ok=True)
            CAPTURE.symlink_to(capture.path)
            process = subprocess.Popen([str(service)], stderr=open(run / 'service.err', 'w'), env=env)
            wait_for((run / 'rungic-communication.sock').exists, 'the service socket')
            done = subprocess.run([str(player), '3'], env={**env, 'QT_QPA_PLATFORM': 'offscreen'},
                                  capture_output=True, text=True, timeout=30)
            said = [json.loads(line) for line in done.stdout.splitlines() if line.startswith('{')]
            end = said[-1] if said else {}
            levels = loudness(android.pcm(1))
            heard = [i for i, level in enumerate(levels) if level > 2000]
            span = heard[-1] - heard[0] + 1 if heard else 0
            gaps = [levels[i] for i in range(heard[0], heard[-1] + 1) if levels[i] <= 2000] if heard else []
            with android.lock:
                underrun = android.underrun
            if 'failed' in end or end.get('pendingBytes') != 0 or len(heard) < 28 or gaps or underrun > 4800:
                raise harness.Failed(f'Android ran dry for {underrun / 48:.0f} ms; session said {said} {done.stderr[-300:]}; loudness per 100 ms at Android: {levels}; '
                                     f'service: {(run / "service.err").read_text()[-300:]}')
            steps.append(f"a call's 3 s reply is heard for {span / 10:.1f} s without a gap "
                         f'(Android ran dry for {underrun / 48:.0f} ms in all, start and end included)')
    finally:
        if process:
            process.terminate()
            process.wait(5)
        pa.terminate()
        pa.wait(5)
        CAPTURE.unlink(missing_ok=True)


def test():
    steps = []
    service, client, player = build()
    consumer(client, steps)
    call_playback(service, player, steps)
    provider(service, steps)
    unmuted(service, steps)
    return steps


if __name__ == '__main__':
    harness.run('communication_audio', test)
