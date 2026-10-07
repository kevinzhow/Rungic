#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Locate the development phone and run commands on Android, in LXC or as the desktop user.

Machine-specific values stay outside the repository:

  RUNGIC_ADB        adb executable (default: PATH, then common SDK locations)
  RUNGIC_SERIAL     hardware serial, ro.serialno (default ZY32MVJS25)
  RUNGIC_ADB_PORT   optional explicit local adb server port (core acceptance uses 5037)
  RUNGIC_TRANSPORT  adb transport id to use as-is, e.g. 10.77.0.16:44995

The same keys may be written as KEY=VALUE lines in .work/device.env; the
environment wins. The names from before the Rungic rename (MOTO_ADB, ...) still work.

prog() and first_path() name the container's programs and files as a shell word that takes the
Rungic name and falls back to the name from before the rename (docs/70), so these tools keep
working on a release that is rolled back to. PLASMA and LXC_DIR do the same for the Android side
(/data/adb/rungic-* after the phase C cutover, /data/adb/moto-* before), and apk() for the APK.
Without RUNGIC_TRANSPORT the phone is found by ro.serialno, so a changing wireless-debugging port
needs no edits.

Scripts are sent on stdin instead of being nested inside `adb shell su -c`
quoting. The exit status of the script is returned.

devices() lists every connected adb device and selected() points these tools at one of them for a
block (rungic_release.py deploy --all and status --all go through the phones one after another).
"""
import contextlib
import functools
import os
import shlex
import shutil
import subprocess
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
DEFAULT_SERIAL = 'ZY32MVJS25'
# Sets $p to the Android-side launcher (system/rungic-plasma), under its name before the cutover if
# that is what the phone has.
LAUNCHER_SH = 'p=/data/adb/rungic-plasma/rungic-plasma; [ -x $p ] || p=/data/adb/moto-plasma/moto-plasma'
# Shell words for root scripts: the launcher, and the Plasma LXC directory (config, state/).
PLASMA = f'"$({LAUNCHER_SH}; echo $p)"'
LXC_DIR = ('"$(d=/data/adb/rungic-lxc; [ -d $d ] || d=/data/adb/moto-lxc; '
           'echo $d/runtime/var/lib/lxc/plasma)"')


APK = 'com.rungic.plasma'
FORMER_APK = 'dev.moto.plasma'


@functools.cache
def apk():
    """Package name of the desktop APK on the phone: com.rungic.plasma, or dev.moto.plasma before the
    phase C cutover (docs/70)."""
    enabled = out('pm list packages -e', 'shell')
    return APK if f'package:{APK}\n' in enabled + '\n' else FORMER_APK


def plasma_command(*args):
    """A root command line that runs the launcher with args (for su -c)."""
    return f'{LAUNCHER_SH}; exec $p {shlex.join(args)}'


class DeviceError(RuntimeError):
    pass


@functools.cache
def config():
    values = {}
    env_file = WORKSPACE / '.work/device.env'
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"\'')
    for key in ('RUNGIC_ADB', 'RUNGIC_ADB_PORT', 'RUNGIC_SERIAL', 'RUNGIC_TRANSPORT'):
        former = 'MOTO_' + key[len('RUNGIC_'):]
        if key not in values and former in values:
            values[key] = values.pop(former)
        for name in (key, former):
            if os.environ.get(name):
                values[key] = os.environ[name]
                break
    return values


def prog(name):
    """Shell word for the container program rungic-NAME, or moto-NAME on a release from before the
    Rungic rename (docs/70)."""
    return f'"$(command -v rungic-{name} || command -v moto-{name} || echo rungic-{name})"'


def first_path(new, old):
    """Shell word for a container path under its Rungic name, else its name before the rename."""
    return f'"$( [ -e {new} ] || [ ! -e {old} ] && echo {new} || echo {old})"'


@functools.cache
def adb_path():
    candidates = [config().get('RUNGIC_ADB'), shutil.which('adb'),
                  str(Path.home() / 'Android/Sdk/platform-tools/adb'),
                  str(Path.home() / 'android-sdk/platform-tools/adb')]
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    raise DeviceError('adb not found; set RUNGIC_ADB')


def adb_server():
    """A selected server is explicit; ordinary callers retain their existing adb configuration."""
    port = config().get('RUNGIC_ADB_PORT')
    if port is not None and (not port.isdigit() or not 1 <= int(port) <= 65535):
        raise DeviceError('RUNGIC_ADB_PORT must be 1..65535')
    return [adb_path(), *(['-P', port] if port is not None else [])]


def serial():
    return config().get('RUNGIC_SERIAL', DEFAULT_SERIAL)


@functools.cache
def transport():
    """Return the adb transport id of the phone whose ro.serialno matches."""
    if config().get('RUNGIC_TRANSPORT'):
        return config()['RUNGIC_TRANSPORT']
    # stdin=DEVNULL: adb shell otherwise consumes the caller's stdin (e.g. an MCP stdio stream).
    out = subprocess.run([*adb_server(), 'devices'], capture_output=True, text=True, timeout=15,
                         stdin=subprocess.DEVNULL).stdout
    devices = [line.split()[0] for line in out.splitlines()[1:]
               if line.strip() and line.split()[-1] == 'device']
    if serial() in devices:
        return serial()
    for device in devices:
        try:
            found = subprocess.run([*adb_server(), '-s', device, 'shell', 'getprop', 'ro.serialno'],
                                   capture_output=True, text=True, timeout=10,
                                   stdin=subprocess.DEVNULL).stdout.strip()
        except subprocess.TimeoutExpired:
            continue
        if found == serial():
            return device
    raise DeviceError(f'Phone {serial()} not among adb devices {devices}; '
                      'connect it or set RUNGIC_TRANSPORT')


def devices():
    """The connected adb devices: [(transport, state, model)] from `adb devices -l` (state 'device' when
    usable; 'offline', 'unauthorized' otherwise)."""
    out = subprocess.run([*adb_server(), 'devices', '-l'], capture_output=True, text=True, timeout=15,
                         stdin=subprocess.DEVNULL).stdout
    result = []
    for line in out.splitlines()[1:]:
        words = line.split()
        if len(words) >= 2:
            model = next((w.split(':', 1)[1] for w in words[2:] if w.startswith('model:')), None)
            result.append((words[0], words[1], model))
    return result


@contextlib.contextmanager
def selected(serial=None, transport=None, port=None):
    """Commands go to this phone until the block ends: RUNGIC_SERIAL and RUNGIC_TRANSPORT for this
    process and the tools it starts (rungic_plasma.py restarts the session), with the cached lookups of
    the previous phone (its transport, APK name, transfer directory) cleared before and after."""
    keys = ('RUNGIC_SERIAL', 'RUNGIC_TRANSPORT', 'MOTO_SERIAL', 'MOTO_TRANSPORT')
    if port is not None:
        keys += ('RUNGIC_ADB_PORT', 'MOTO_ADB_PORT')
    saved = {key: os.environ.get(key) for key in keys}

    def clear():
        for cached in (config, globals()['transport'], apk, container_transfer):   # the argument hides transport()
            cached.cache_clear()
    for key in saved:
        os.environ.pop(key, None)
    if serial:
        os.environ['RUNGIC_SERIAL'] = serial
    if transport:
        os.environ['RUNGIC_TRANSPORT'] = transport
    if port is not None:
        os.environ['RUNGIC_ADB_PORT'] = str(port)
    clear()
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        clear()


def adb(*args):
    """adb argv prefix bound to the phone."""
    return [*adb_server(), '-s', transport(), *args]


def _run(argv, script, timeout, check):
    try:
        result = subprocess.run(argv, input=script, capture_output=True, text=True,
                                timeout=timeout, errors='replace')
    except subprocess.TimeoutExpired as error:
        raise DeviceError(f'Timed out after {timeout}s: {argv[-1]}') from error
    if check and result.returncode:
        raise DeviceError(f'Exit {result.returncode}: {result.stderr.strip() or result.stdout.strip()[-2000:]}')
    return result


# Each level runs a POSIX sh script read from stdin.
LEVELS = {
    'shell': 'sh',                                  # Android shell user
    'root': 'su -c sh',                             # Android root (Magisk)
    'container': 'su -c ' + shlex.quote(plasma_command('exec', 'sh')),        # LXC root
    'user': 'su -c ' + shlex.quote(plasma_command('user-exec', 'sh')),        # desktop user, session environment
}


def run(script, level='root', timeout=60, check=True):
    """Run a shell script at one of LEVELS; returns CompletedProcess."""
    return _run(adb('shell', LEVELS[level]), script, timeout, check)


def out(script, level='root', timeout=60):
    return run(script, level, timeout).stdout


def push(src, name=None, timeout=300):
    """adb push a local file to /data/local/tmp; returns the remote path for a following root script."""
    remote = '/data/local/tmp/' + (name or Path(src).name)
    subprocess.run(adb('push', str(src), remote), check=True, capture_output=True, timeout=timeout,
                   stdin=subprocess.DEVNULL)
    return remote


# Files between this computer and the container (docs/61 §7). The container's rootfs is an image
# mounted only in the container's namespace, so nothing is read or written under it from Android:
# files pass through state/host/transfer, bind-mounted at /var/lib/rungic-host (/var/lib/moto-host
# before phase C, and alongside it until phase D).
HOST_TRANSFER = f'{LXC_DIR}/state/host/transfer'


@functools.cache
def container_transfer():
    """Container path of the transfer directory."""
    rungic = run('mountpoint -q /var/lib/rungic-host', 'container', check=False).returncode == 0
    return f"/var/lib/{'rungic' if rungic else 'moto'}-host/transfer"


def _transfer_name(hint):
    import secrets
    return f'{secrets.token_hex(6)}-{Path(hint).name}'


def to_container(src, dest=None, mode=None, timeout=600):
    """Copy a local file into the container: to `dest` (installed root-owned, `mode` if given), or,
    without dest, only into the transfer directory; returns the container path."""
    name = _transfer_name(src)
    remote = push(src, name, timeout)
    host = HOST_TRANSFER
    run(f'mkdir -p {host} && cp {remote} {host}/{name} && chmod 644 {host}/{name}; rm -f {remote}', 'root', timeout)
    staged = f'{container_transfer()}/{name}'
    if dest is None:
        return staged
    install = f'install -o 0 -g 0 -m {mode} ' if mode else 'install -o 0 -g 0 '
    run(f'mkdir -p "$(dirname {dest})" && {install}{staged} {dest}; rm -f {staged}', 'container', timeout)
    return dest


def from_container(path, target, timeout=1800):
    """Copy a file from the container to a local path."""
    name = _transfer_name(path)
    transfer = container_transfer()
    run(f'mkdir -p {transfer} && cp {path} {transfer}/{name} && chmod 644 {transfer}/{name}', 'container', timeout)
    stage = f'/data/local/tmp/{name}'
    try:
        run(f'cp {HOST_TRANSFER}/{name} {stage} && chmod 644 {stage}', 'root', timeout)
        subprocess.run(adb('pull', stage, str(target)), check=True, capture_output=True, timeout=timeout,
                       stdin=subprocess.DEVNULL)
    finally:
        run(f'rm -f {stage} {HOST_TRANSFER}/{name}', 'root', check=False)


def extract_in_container(archive, directory, timeout=1800):
    """Unpack a local tar into a container directory (root-owned files)."""
    staged = to_container(archive, timeout=timeout)
    run(f'mkdir -p {directory} && tar -xf {staged} -C {directory} --no-same-owner; rc=$?; rm -f {staged}; exit $rc',
        'container', timeout)


if __name__ == '__main__':
    print(f'adb={adb_path()} serial={serial()} transport={transport()}')
