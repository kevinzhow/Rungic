#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Linux's own network (docs/116, Settings -> Services: "Own network").

Linux (the container's systemd and everything below it) has a network namespace of its own. Its
connections are made by pasta in Android's network namespace under the Rungic app's Android uid, so
Android and VPN apps (SwiftWire, Clash) treat Linux as that app: per-app VPN rules, private DNS and
metered networks apply to it as to any app. Shared with Android, Linux traffic carried Linux uids
(0, 1000), system identities to Android that VPNs treat differently (2026-10-06: TLS through a Clash
TUN failed for root while apps worked).

  rungic-own-network host --app-uid UID   run from the Android side (system/rungic-plasma) after the
                                          container started in an empty network namespace: in the
                                          container's mount and PID namespaces, Android's network
                                          namespace, as root. Starts and supervises pasta, and relays
                                          the abstract sockets Linux uses to reach Android services
                                          (abstract sockets belong to a network namespace).
  rungic-own-network status               in Linux: whether its own network is up (the switch's unit).
  rungic-own-network mode                 in Linux: writes the switch to the file the Android side
                                          reads at the next start (/var/lib/rungic-host/network-mode).

The switch is the unit rungic-own-network.service, enabled or not; it takes effect when Linux next
starts (a namespace is chosen before systemd runs).
"""
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

MODE_FILE = '/var/lib/rungic-host/network-mode'
# Written when the own network could not be set up: the Android side starts Linux on the shared
# network once (recovery first), then tries again.
FAILED_FILE = MODE_FILE + '.failed'
SWITCH_UNIT = 'rungic-own-network.service'
RUN = '/run/rungic-own-network'
LOG = '/var/log/plasma/own-network.log'
NETNS = RUN + '/netns'
STATUS = RUN + '/status.json'
# Linux's side of the link: pasta's addresses (as user-mode networking commonly uses).
ADDRESS, PREFIX, GATEWAY, INTERFACE = '10.0.2.15', '24', '10.0.2.2', 'eth0'
# Android services Linux reaches on abstract sockets (rungic_platform_transport, cellular_audio,
# clipboard): relayed from Linux's namespace to Android's, each with the uid its clients check the
# service has (DeviceDaemon and CallDaemon run as root, ClipboardDaemon as shell).
RELAYED = {'com.rungic.device.v1': 0, 'com.rungic.calls.v1': 0, 'com.rungic.clipboard.v1': 2000}
# The Android services accept these client uids (DeviceDaemon, CallDaemon, ClipboardDaemon: root,
# Android system, the app); the relay connects as root, so it passes on only these.
CLIENT_UIDS = (0, 1000)
CLONE_NEWNET = 0x40000000
RESTART_MAX_S = 30


def log(*parts):
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + ' '.join(str(p) for p in parts)
    print(line, flush=True)


def pasta_argv(app_uid, netns=NETNS):
    """pasta as the app's uid with only the capabilities it needs: to enter Linux's namespace and set
    up its interface (sys_admin, net_admin) and to forward SSH's port 22 (net_bind_service). Not
    pasta's --runas: it drops to the uid before entering the namespace, and then cannot."""
    caps = '+sys_admin,+net_admin,+net_bind_service'
    return ['setpriv', f'--reuid={app_uid}', f'--regid={app_uid}', '--clear-groups',
            f'--inh-caps={caps}', f'--ambient-caps={caps}',
            'pasta', '--foreground', '--quiet', '--netns', netns, '--config-net',
            '-I', INTERFACE, '-a', ADDRESS, '-n', PREFIX, '-g', GATEWAY,
            # In: SSH from the local network (as when Linux shared Android's network).
            '-t', '22', '-u', 'none',
            # Out: Linux's 127.0.0.1 ports that Android's side listens on (PulseAudio's TCP and
            # the like) reach Android's loopback.
            '-T', 'auto', '-U', 'auto']


def peer_uid(conn):
    _pid, uid, _gid = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    return uid


def pump(source, target):
    try:
        while True:
            data = source.recv(65536)
            if not data:
                break
            target.sendall(data)
    except OSError:
        pass
    finally:
        try:
            target.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def relay_connection(client, name, connect=None):
    """One connection: checked, then joined to the Android service of `name` both ways."""
    try:
        if peer_uid(client) not in CLIENT_UIDS:
            raise PermissionError(name)
        upstream = (connect or abstract_connect)(name)
    except OSError:
        client.close()
        return
    up = threading.Thread(target=pump, args=(client, upstream), daemon=True)
    up.start()
    pump(upstream, client)
    up.join(5)
    for s in (client, upstream):
        try:
            s.close()
        except OSError:
            pass


def abstract_connect(name):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect('\0' + name)
    s.settimeout(None)
    return s


def listen_in(netns_fd, android_fd, name, uid=0):
    """An abstract socket `name` in Linux's namespace (a socket keeps the namespace it was made in),
    listening as `uid`: the identity its clients see (SO_PEERCRED is taken at listen). Before any
    thread starts: the effective uid is the whole process's."""
    os.setns(netns_fd, CLONE_NEWNET)
    try:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind('\0' + name)
        os.seteuid(uid)
        try:
            server.listen(16)
        finally:
            os.seteuid(0)
    finally:
        os.setns(android_fd, CLONE_NEWNET)
    return server


def serve_relay(server, name):
    while True:
        client, _ = server.accept()
        threading.Thread(target=relay_connection, args=(client, name), daemon=True).start()


def bind_netns():
    """Linux's namespace (that of its PID 1) as a path pasta can open: pasta runs as the app's uid,
    which may not open another user's /proc/PID/ns, and closes the files it inherits. The directory
    is searchable whatever umask started us: Android's side starts Linux at boot from rungic-runtime
    with umask 077, and a 0700 directory left pasta unable to open the path (2026-10-08, docs/121)."""
    os.makedirs(RUN, exist_ok=True)
    os.chmod(RUN, 0o755)
    if not os.path.exists(NETNS):
        open(NETNS, 'w').close()
    if subprocess.run(['mountpoint', '-q', NETNS]).returncode != 0:
        subprocess.run(['mount', '--bind', '/proc/1/ns/net', NETNS], check=True)


def write_status(status):
    try:
        temporary = STATUS + '.tmp'
        with open(temporary, 'w') as stream:
            json.dump(status, stream)
        os.replace(temporary, STATUS)
    except OSError:
        pass


def mark_failed(reason):
    log('own network not set up:', reason)
    try:
        with open(FAILED_FILE, 'w') as stream:
            stream.write(str(reason) + '\n')
    except OSError:
        pass


def host(app_uid):
    """The Android side's daemon: relays, then pasta, restarted when it ends (recovery first)."""
    if os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net'):
        raise SystemExit('Linux shares this network namespace: nothing to do')
    try:
        bind_netns()
        argv = pasta_argv(app_uid)
        for program in (argv[0], 'pasta'):
            if not shutil.which(program):
                raise OSError(f'{program} is not installed')
    except (OSError, subprocess.SubprocessError) as error:
        mark_failed(error)
        raise SystemExit(1)
    status = {'appUid': app_uid, 'started': time.time(), 'pasta': None, 'restarts': 0, 'relays': []}
    android_fd = os.open('/proc/self/ns/net', os.O_RDONLY)
    netns_fd = os.open(NETNS, os.O_RDONLY)
    servers = {}
    for name, uid in RELAYED.items():
        try:
            servers[name] = listen_in(netns_fd, android_fd, name, uid)
        except OSError as error:
            log('relay', name, 'not set up:', error)
    for name, server in servers.items():
        threading.Thread(target=serve_relay, args=(server, name), daemon=True).start()
        status['relays'].append(name)
    # pasta opens /dev/net/tun in Linux's /dev, which its systemd may still be populating.
    for _ in range(50):
        if os.path.exists('/dev/net/tun'):
            break
        time.sleep(0.2)
    delay = 1
    while os.path.exists('/proc/1'):
        started = time.monotonic()
        process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        status['pasta'] = process.pid
        write_status(status)
        log('pasta started as uid', app_uid, 'pid', process.pid)
        _out, err = process.communicate()
        log('pasta ended', process.returncode, (err or '').strip()[-500:])
        status['restarts'] += 1
        status['pasta'] = None
        write_status(status)
        if time.monotonic() - started < 10 and status['restarts'] == 3 and not status.get('up'):
            mark_failed('pasta ends at start: ' + (err or '').strip()[-200:])
        status['up'] = status.get('up') or time.monotonic() - started >= 10
        delay = 1 if time.monotonic() - started > 60 else min(RESTART_MAX_S, delay * 2)
        time.sleep(delay)


def default_route_via_gateway(routes=None):
    if routes is None:
        try:
            with open('/proc/net/route') as stream:
                routes = stream.read()
        except OSError:
            return False
    for line in routes.splitlines()[1:]:
        fields = line.split()
        if len(fields) > 2 and fields[0] == INTERFACE and fields[1] == '00000000':
            gateway = socket.inet_ntoa(struct.pack('<I', int(fields[2], 16)))
            return gateway == GATEWAY
    return False


def status():
    """In Linux: up (0) when it has its own namespace with pasta's link and default route."""
    own = os.path.exists(f'/sys/class/net/{INTERFACE}') and default_route_via_gateway()
    print('Linux has its own network (as the Rungic app)' if own else
          'Linux shares Android\'s network now; the switch takes effect when Linux next starts')
    return 0


def mode(enabled=None):
    """Write the switch where the Android side reads it at the next start."""
    if enabled is None:
        enabled = subprocess.run(['systemctl', 'is-enabled', '--quiet', SWITCH_UNIT]).returncode == 0
    value = 'own' if enabled else 'shared'
    folder = os.path.dirname(MODE_FILE)
    os.makedirs(folder, exist_ok=True)
    temporary = MODE_FILE + '.tmp'
    with open(temporary, 'w') as stream:
        stream.write(value + '\n')
    os.replace(temporary, MODE_FILE)
    print(f'{MODE_FILE}: {value}')
    return 0


def main(argv):
    if len(argv) >= 2 and argv[1] == 'host':
        if '--app-uid' not in argv:
            raise SystemExit('host needs --app-uid UID')
        uid = int(argv[argv.index('--app-uid') + 1])
        if uid < 10000:
            raise SystemExit('the app uid must be an Android app uid')
        host(uid)
        return 0
    if len(argv) >= 2 and argv[1] == 'status':
        return status()
    if len(argv) >= 2 and argv[1] == 'mode':
        return mode()
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
