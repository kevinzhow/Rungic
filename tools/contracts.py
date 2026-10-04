# SPDX-License-Identifier: MIT
"""The contracts of the Android interfaces (quality/contracts/*.json, quality/README.md 分层).

A contract lists an interface's queries with an example reply each: every key of the example is
required in a real reply, with the example value's type (null: anything; a list whose first item is
an object: every item keeps that object). A query may give several possible replies ("replies"),
of which a reply keeps one. The same contract checks both ends: Android's provider on the phone
(validate() on its real replies, tools/rungic_acceptance.py interface_contract, which sends only the
queries marked "read_only") and the Linux consumers, run against StandIn, a socket that answers as
the contract says.

  with StandIn('platform-bridge') as bridge:          # $RUNGIC_PLATFORM_SOCKET for the consumer
      os.environ['RUNGIC_PLATFORM_SOCKET'] = bridge.path
      ...                                              # bridge.requests: what the consumer asked

A query's fields:
  request    the request; a query answers a request equal to it, or with "match", one whose listed
             fields ("op", or "args.0" for a list's first item) equal it (the request's other fields
             are typed examples the consumer must send)
  reply      the example reply; or "replies", several possible ones
  read_only  true: the provider check on the phone sends it (queries only: nothing changes there)
  socket     which of the contract's "sockets" it goes to (default: the first; a contract without
             "sockets" is on the platform bridge's)
  payload    the request field giving the length of the raw bytes that follow the request line
  private    reply fields the provider check on the phone never reads back (the clipboard's text)
  timeout    seconds the provider check waits for the reply (default 5)
  command    instead of a socket: a read-only shell command the provider check runs ("as": root,
             container or user; "format": json, properties, exit or text with "expect")
  examples   more replies keeping the contract, for consumer tests (an install in progress, a failure)

A socket: {"path": ..., "env": the variable that moves it} or {"abstract": name}, with "peer_uid"
when consumers check who serves it (SO_PEERCRED). Consumers with a fixed address reach a stand-in
through route() in-process, or `contracts.py run ROUTES SCRIPT ARGS...` in a process of their own.
A binary interface ("framing": "binary", the codec) has its own stand-in (CodecStandIn).
"""
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import weakref
from pathlib import Path

CONTRACTS = Path(__file__).resolve().parents[1] / 'quality/contracts'
PLATFORM = {'path': '/mnt/android-wayland/platform.sock', 'env': 'RUNGIC_PLATFORM_SOCKET'}


def load(name):
    return json.loads((CONTRACTS / f'{name}.json').read_text())


def names():
    return sorted(p.stem for p in CONTRACTS.glob('*.json'))


def kind(value):
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, (int, float)):
        return 'number'
    return {str: 'string', list: 'array', dict: 'object'}.get(type(value), type(value).__name__)


def validate(example, reply, where='reply'):
    """The ways `reply` breaks the contract that `example` stands for ([] when it keeps it)."""
    problems = []
    if not isinstance(reply, dict):
        return [f'{where}: not an object but {kind(reply)}']
    for key, value in example.items():
        if key not in reply:
            problems.append(f'{where}.{key}: missing')
        elif value is not None and kind(reply[key]) != kind(value):
            problems.append(f'{where}.{key}: {kind(reply[key])}, not {kind(value)}')
        elif isinstance(value, dict):
            problems += validate(value, reply[key], f'{where}.{key}')
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            for i, item in enumerate(reply[key]):
                problems += validate(value[0], item, f'{where}.{key}[{i}]')
    return problems


def replies(q):
    return q.get('replies') or [q['reply']]


def check_reply(q, reply):
    """[] when `reply` keeps one of the query's replies, else the problems of the closest one."""
    best = None
    for example in replies(q):
        found = validate(example, reply)
        if not found:
            return []
        if best is None or len(found) < len(best):
            best = found
    return best


def check_request(q, request):
    """The ways a consumer's request differs from the query's (typed) request."""
    return validate(q['request'], request, 'request') if 'match' in q else []


_MISSING = object()


def field(value, path):
    """A request's field by its "match" name: "op", or "args.0" for the first of a list."""
    for part in path.split('.'):
        if isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return _MISSING
    return value


def matches(q, request):
    if 'match' in q:
        return all(field(request, k) is not _MISSING and field(request, k) == field(q['request'], k)
                   for k in q['match'])
    return q['request'] == request


def query(contract, request):
    """The contract's query answering a request, or None."""
    for q in contract['queries']:
        if 'request' in q and matches(q, request):
            return q
    return None


def sockets(contract):
    return contract.get('sockets') or {'platform': PLATFORM}


def socket_of(contract, q):
    """(name, spec) of the socket a query goes to."""
    found = sockets(contract)
    name = q.get('socket') or next(iter(found))
    return name, found[name]


def address(spec):
    """The address a consumer connects to: a path, or '\\0name' for an abstract socket."""
    return '\0' + spec['abstract'] if 'abstract' in spec else spec['path']


# ---- consumers with a fixed address -------------------------------------------------------------
_PEERS = weakref.WeakKeyDictionary()   # sockets connected to a stand-in -> the uid they report


def _routed(routes):
    """socket.socket's connect and getsockopt with the connections to an address of `routes`
    ({address: (path, peer uid or None)}) going to the path, SO_PEERCRED on them reporting that uid."""
    connect, getsockopt = socket.socket.connect, socket.socket.getsockopt

    def routed_connect(self, where):
        key = where.decode(errors='surrogateescape') if isinstance(where, bytes) else where
        if isinstance(key, str) and key in routes:
            path, uid = routes[key]
            if uid is not None:
                _PEERS[self] = uid
            return connect(self, path)
        return connect(self, where)

    def routed_getsockopt(self, level, option, *args):
        if level == socket.SOL_SOCKET and option == socket.SO_PEERCRED and self in _PEERS:
            return struct.pack('3i', os.getpid(), _PEERS[self], _PEERS[self])
        return getsockopt(self, level, option, *args)
    return routed_connect, routed_getsockopt


def reroute(routes):
    """For the rest of this process: the addresses of `routes` lead to their paths (see _routed)."""
    socket.socket.connect, socket.socket.getsockopt = _routed(routes)


def route(monkeypatch, *standins):
    """In this process, the stand-ins' contract addresses lead to them (pytest's monkeypatch undoes it)."""
    connect, getsockopt = _routed({s.address: (s.path, s.spec.get('peer_uid')) for s in standins})
    monkeypatch.setattr(socket.socket, 'connect', connect)
    monkeypatch.setattr(socket.socket, 'getsockopt', getsockopt)


def consumer(script, *args, standins=()):
    """argv running a consumer script in a process of its own, its fixed addresses routed to stand-ins."""
    routes = {s.address: [s.path, s.spec.get('peer_uid')] for s in standins}
    return [sys.executable, __file__, 'run', json.dumps(routes), str(script), *map(str, args)]


class StandIn:
    """The interface as its contracts describe it, on a Unix socket of its own: each known query is
    answered with its example (or `replies[name]`, or what `handler(name, request)` returns), which must
    keep the contract; anything else with an error, as the provider does. Records every request
    (`requests`), the ways the consumer's requests or the answers break the contract (`problems`) and
    the raw payloads (`payloads`). After a query named in `streams`, `streams[name](conn, stream,
    request)` carries the rest of the connection (audio, frames). Several contracts on one socket
    (the platform bridge's): StandIn(['network', 'telephony'])."""

    def __init__(self, name, replies=None, handler=None, streams=None, socket_name=None, keep_contract=True):
        self.names = [name] if isinstance(name, str) else list(name)
        contracts = [load(n) for n in self.names]
        self.socket_name = socket_name or next(iter(sockets(contracts[0])))
        self.spec = sockets(contracts[0])[self.socket_name]
        self.address = address(self.spec)
        self.queries = [q for c in contracts for q in c['queries']
                        if 'request' in q and socket_of(c, q)[0] == self.socket_name
                        and address(socket_of(c, q)[1]) == self.address]
        self.contract = {'queries': self.queries}
        self.replies = replies or {}
        self.handler = handler
        self.streams = streams or {}
        self.keep_contract = keep_contract
        for qname, reply in self.replies.items():
            q = next(q for q in self.queries if q['name'] == qname)
            problems = check_reply(q, reply)
            if problems and keep_contract:
                raise ValueError(f'a stand-in reply breaks the contract: {problems}')
        self.requests, self.problems, self.payloads = [], [], []
        self.lock = threading.Lock()
        self.dir = tempfile.TemporaryDirectory(prefix='rungic-standin-')
        self.path = os.path.join(self.dir.name, f'{self.socket_name}.sock')
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(16)
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.close()
        self.dir.cleanup()

    def answer(self, request):
        """(the contract's query, the reply). A subclass may return the reply alone."""
        q = query(self.contract, request)
        if not q:
            return None, {'error': f'unknown request {request.get("op")!r}'}
        reply = self.handler(q['name'], request) if self.handler else None
        if reply is None:
            reply = self.replies.get(q['name'], replies(q)[0])
        problems = check_reply(q, reply) if not (isinstance(reply, dict) and 'error' in reply) else []
        if problems and self.keep_contract:
            with self.lock:
                self.problems.append(f'{q["name"]}: the stand-in answered {problems}')
        return q, reply

    def serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.connection, args=(conn,), daemon=True).start()

    def connection(self, conn):
        with conn, conn.makefile('rb') as stream:
            line = stream.readline()
            try:
                request = json.loads(line)
            except ValueError:
                request = {'invalid': line.decode(errors='replace')}
            q = query(self.contract, request) if isinstance(request, dict) else None
            with self.lock:
                self.requests.append(request)
                if q:
                    self.problems += [f'{q["name"]}: {p}' for p in check_request(q, request)]
            if q and q.get('payload'):
                data = stream.read(int(request.get(q['payload']) or 0))
                with self.lock:
                    self.payloads.append(data)
            answered = self.answer(request) if isinstance(request, dict) else (None, {'error': 'not an object'})
            # A subclass may answer some requests itself with the reply alone (the platform bridge's
            # writing ops in the desktop's tests); the query is then the contract's, if it has one.
            q, reply = answered if isinstance(answered, tuple) else (query(self.contract, request), answered)
            try:
                conn.sendall((json.dumps(reply) + '\n').encode())
                if q and q['name'] in self.streams:
                    self.streams[q['name']](conn, stream, request)
            except OSError:
                pass


class CodecStandIn:
    """The codec broker of quality/contracts/codec.json (binary, descriptors over SCM_RIGHTS) on a socket
    of its own: each OPEN gets a channel and shared memory; a configuration is accepted unless
    `reject(config)` returns a message; each FRAME is answered with the records `output(config, frame)`
    returns ({'type': 'DECODED', 'planes': [(stride, step, bytes)] x3, 'width', 'height', 'crop'} or
    {'type': 'ENCODED' or 'CONFIG', 'data': bytes}, both with 'id', 'flags' and 'pts'), each written
    to the output half and acknowledged; DRAIN with EOS. Records `configs`, `frames` (with the input
    bytes), `commands` and `problems` (the consumer broke the protocol).

    Channel version 2 (`configure2`, `output2` records) unless `version=1` (an app before it: MAGIC2
    answered with ERROR). A DECODED record with 'buffer': (slot, bytes) and 'offsets' is a picture in
    one of the decoder's buffers: the slot's DMA-BUF (a memfd here) goes with its first record, its
    bytes refreshed for every record; 'depth' 10 for P010. `descriptors` counts those sent."""

    def __init__(self, output=None, reject=None, version=2):
        contract = load('codec')
        self.k = {name: int(value, 0) for name, value in contract['constants'].items()}
        self.spec = contract['sockets']['broker']
        self.address = address(self.spec)
        self.messages = contract['messages']
        self.output, self.reject = output or (lambda config, frame: []), reject or (lambda config: None)
        self.configs, self.frames, self.commands, self.problems = [], [], [], []
        self.version, self.descriptors = version, 0
        self.dir = tempfile.TemporaryDirectory(prefix='rungic-standin-')
        self.path = os.path.join(self.dir.name, 'codec.sock')
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(8)

    def __enter__(self):
        threading.Thread(target=self.serve, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.close()
        self.dir.cleanup()

    @staticmethod
    def words(conn, count):
        data = bytearray()
        while len(data) < 4 * count:
            part = conn.recv(4 * count - len(data))
            if not part:
                raise EOFError
            data.extend(part)
        return list(struct.unpack(f'>{count}I', data))

    @staticmethod
    def send(conn, *values):
        conn.sendall(struct.pack(f'>{len(values)}I', *(v & 0xffffffff for v in values)))

    def serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.broker, args=(conn,), daemon=True).start()

    def broker(self, conn):
        import mmap
        with conn:
            try:
                if self.words(conn, 1)[0] != self.k['MAGIC']:
                    self.problems.append('broker: no MAGIC first')
                    return
                while True:
                    if self.words(conn, 1)[0] != self.k['OPEN']:
                        self.problems.append('broker: a word other than OPEN')
                        return
                    local, remote = socket.socketpair()
                    memory = os.memfd_create('rungic-codec-frames')
                    os.ftruncate(memory, 2 * self.k['HALF'])
                    shared = mmap.mmap(memory, 2 * self.k['HALF'])
                    socket.send_fds(conn, [struct.pack('>I', self.k['CHANNEL'])], [remote.fileno(), memory])
                    remote.close()
                    os.close(memory)
                    threading.Thread(target=self.session, args=(local, shared), daemon=True).start()
            except (EOFError, OSError):
                return

    def session(self, conn, shared):
        k = self.k
        slots = {}            # slot -> the memfd standing in for that decoder buffer's DMA-BUF
        with conn:
            try:
                magic = self.words(conn, 1)[0]
                if magic == k['MAGIC2'] and self.version == 2:
                    names = self.messages['configure2']
                elif magic == k['MAGIC2']:
                    message = b'java.io.IOException: Channel version'
                    self.send(conn, k['ERROR'], len(message))
                    conn.sendall(message)
                    return
                elif magic == k['MAGIC']:
                    names = self.messages['configure']
                else:
                    self.problems.append('channel: no MAGIC first')
                    return
                config = dict(zip(names, [magic] + self.words(conn, len(names) - 1)))
                config['version'] = 2 if magic == k['MAGIC2'] else 1
                self.configs.append(config)
                refused = self.reject(config)
                if refused:
                    message = refused.encode()
                    self.send(conn, k['ERROR'], len(message))
                    conn.sendall(message)
                    return
                name = 'c2.qti.%s.%s' % (('avc', 'hevc', 'vp9')[config['kind']], 'encoder' if config['encoder'] else 'decoder')
                self.send(conn, k['DONE'], len(name))
                conn.sendall(name.encode())
                while True:
                    command = self.words(conn, 1)[0]
                    self.commands.append(command)
                    if command == k['CLOSE']:
                        return
                    if command == k['FLUSH']:
                        self.send(conn, k['DONE'])
                        continue
                    if command == k['DRAIN']:
                        self.send(conn, k['EOS'], k['DONE'])
                        continue
                    if command != k['FRAME']:
                        self.problems.append(f'channel: unknown command {command}')
                        return
                    fid, high, low, flags, length = self.words(conn, 5)
                    if not 0 < length <= k['HALF']:
                        self.problems.append(f'channel: frame length {length}')
                        return
                    frame = {'id': fid, 'pts': (high << 32) | low, 'flags': flags, 'data': bytes(shared[:length])}
                    self.frames.append(frame)
                    for record in self.output(config, frame):
                        self.write(conn, shared, record, config['version'], slots)
                    self.send(conn, k['DONE'])
            except (EOFError, OSError):
                return
            finally:
                shared.close()
                for fd in slots.values():
                    os.close(fd)

    def write(self, conn, shared, record, version=1, slots=None):
        k, half = self.k, self.k['HALF']
        attach, buffer, size = [], -1, 0
        if record['type'] == 'DECODED' and 'buffer' in record:
            buffer, contents = record['buffer']
            if buffer not in slots:
                slots[buffer] = os.memfd_create('rungic-decoder-buffer')
                os.ftruncate(slots[buffer], len(contents))
                attach = [slots[buffer]]
                self.descriptors += 1
            os.pwrite(slots[buffer], contents, 0)
            planes = [v for stride, step, length in record['planes'] for v in (stride, step, length)]
            meta = [record['width'], record['height'], *record.get('crop', (0, 0)), *planes]
            offsets, size = record['offsets'], len(contents)
        elif record['type'] == 'DECODED':
            payload = b''.join(data for _, _, data in record['planes'])
            planes = [v for stride, step, data in record['planes'] for v in (stride, step, len(data))]
            meta = [record['width'], record['height'], *record.get('crop', (0, 0)), *planes]
            lengths = [len(data) for _, _, data in record['planes']]
            offsets = [0, lengths[0], lengths[0] + lengths[1]]
        else:
            payload = record['data']
            meta, offsets = [0] * 13, [0, 0, 0]
        if buffer < 0:
            shared[half:half + len(payload)] = payload
            size = len(payload)
        pts = record.get('pts', 0)
        values = [k[record['type']], record.get('id', 0), record.get('flags', 0), size, pts >> 32, pts & 0xffffffff, *meta]
        if version == 2:
            values += [*offsets, buffer, record.get('depth', 8)]
        elif buffer >= 0:
            self.problems.append('stand-in: a buffer picture on a version 1 channel')
        data = struct.pack(f'>{len(values)}I', *(v & 0xffffffff for v in values))
        if attach:
            socket.send_fds(conn, [data], attach)
        else:
            conn.sendall(data)
        if self.words(conn, 1)[0] != k['ACK']:
            self.problems.append('channel: an output record was not acknowledged')


def main(argv):
    """`contracts.py run ROUTES SCRIPT ARGS...`: SCRIPT (- for stdin) as __main__, its addresses routed."""
    if len(argv) < 3 or argv[0] != 'run':
        print(__doc__, file=sys.stderr)
        return 2
    import runpy
    reroute({k: tuple(v) for k, v in json.loads(argv[1]).items()})
    sys.argv = argv[2:]
    if argv[2] == '-':                     # the script on stdin, as `python3 -`
        exec(compile(sys.stdin.read(), '<stdin>', 'exec'), {'__name__': '__main__'})
    else:
        runpy.run_path(argv[2], run_name='__main__')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
