#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Phase C1 of the Rungic rename (docs/70): the Android side of the phone from moto to Rungic names.

  rungic_cutover.py status            the layout, APKs, directories and grants as they are now
  rungic_cutover.py up [--apk APK]    APK dev.moto.plasma -> com.rungic.plasma, /data/adb/moto-* ->
                                      /data/adb/rungic-* (the rootfs image to /data/adb/rungic-lxc/images),
                                      new launcher, enter programs, cast tool and boot scripts
  rungic_cutover.py down              back to the moto layout and APK (files kept by `up`)

The container release is not touched: the new launcher also mounts the state under the old names
(/var/lib/moto-*) and the APK links moto-gpu-alloc to its socket, so releases from before the rename
run on the new layout until phase D. Directory renames stay on one f2fs (O(1)). Every step is
recorded in .work/cutover/<time>-<action>/cutover.json; files that `up` replaces or retires are kept on
the phone in /data/adb/rungic-cutover/ (as `down` needs them) until phase D.

Needs: tools/build_enter.sh plasma and lxc, shared/android/rungic-cast/build.sh, android/build-apk.sh.
Magisk's su grant for the new APK is written with `magisk --sqlite "INSERT OR REPLACE ..."`, which returns
no rows (docs/39: never a query that can return SQL NULL on Magisk 31.0).
"""
import argparse
import datetime
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

import rungic_device
from rungic_device import WORKSPACE, adb, out, push, run

NEW_APK, OLD_APK = rungic_device.APK, rungic_device.FORMER_APK
KEEP = '/data/adb/rungic-cutover'           # files `up` retired or replaced, for `down`
TERMUX_LXC = '/data/data/com.termux/files/usr/bin/lxc'
LXC = '/runtime/var/lib/lxc'
# The directory renames, and inside them the image directory (toybox losetup: paths under 64 bytes,
# system/rootfs-image) and the state directories the container binds.
MOVES = [
    ('/data/adb/moto-plasma', '/data/adb/rungic-plasma'),
    ('/data/adb/moto-lxc', '/data/adb/rungic-lxc'),
    ('/data/adb/moto-wfd', '/data/adb/rungic-wfd'),
    (f'/data/adb/rungic-lxc{LXC}/plasma/images', '/data/adb/rungic-lxc/images'),
    (f'/data/adb/rungic-lxc{LXC}/plasma/state/moto-cores', f'/data/adb/rungic-lxc{LXC}/plasma/state/rungic-cores'),
    (f'/data/adb/rungic-lxc{LXC}/plasma/state/moto-apt', f'/data/adb/rungic-lxc{LXC}/plasma/state/rungic-apt'),
]
# Files under their old names (paths after MOVES); `up` keeps them in KEEP, `down` puts them back.
RETIRED = [
    '/data/adb/rungic-plasma/moto-plasma', '/data/adb/rungic-plasma/moto-plasma-enter',
    '/data/adb/rungic-lxc/moto-lxc', '/data/adb/rungic-lxc/moto-lxc-enter',
    '/data/adb/rungic-wfd/moto-cast', '/data/adb/rungic-wfd/moto-cast-watch', '/data/adb/rungic-wfd/moto-cast.jar',
    '/data/adb/service.d/moto-cast-watch.sh', '/data/adb/service.d/moto-wfd-sepolicy.sh',
]
BUILD = WORKSPACE / '.work/build'
# (source, path on the phone, mode); a file that exists is copied to KEEP first.
INSTALL = [
    ('system/rungic-plasma', '/data/adb/rungic-plasma/rungic-plasma', '755'),
    (BUILD / 'android/rungic-plasma-enter', '/data/adb/rungic-plasma/rungic-plasma-enter', '755'),
    ('system/android-audio', '/data/adb/rungic-plasma/android-audio', '755'),
    ('system/android-audio.pa', '/data/adb/rungic-plasma/android-audio.pa', '644'),
    ('system/rootfs-image', '/data/adb/rungic-plasma/rootfs-image', '755'),
    ('system/rootfs.sepolicy.rule', '/data/adb/rungic-plasma/rootfs.sepolicy.rule', '644'),
    ('system/rootfs-mount-hook', '/data/adb/rungic-plasma/rootfs-mount-hook', '755'),
    ('system/plasma.config', f'/data/adb/rungic-lxc{LXC}/plasma/config', '644'),
    ('lxc/rungic-lxc', '/data/adb/rungic-lxc/rungic-lxc', '755'),
    (BUILD / 'android/rungic-lxc-enter', '/data/adb/rungic-lxc/rungic-lxc-enter', '700'),
    ('lxc/alpine.config', f'/data/adb/rungic-lxc{LXC}/alpine/config', '664'),
    ('shared/android/rungic-cast/rungic-cast', '/data/adb/rungic-wfd/rungic-cast', '755'),
    ('shared/android/rungic-cast/rungic-cast-watch', '/data/adb/rungic-wfd/rungic-cast-watch', '755'),
    (BUILD / 'rungic-cast/rungic-cast.jar', '/data/adb/rungic-wfd/rungic-cast.jar', '644'),
    ('shared/android/wfd.sepolicy.rule', '/data/adb/rungic-wfd/wfd.sepolicy.rule', '644'),
    ('shared/android/rungic-cast-watch.sh', '/data/adb/service.d/rungic-cast-watch.sh', '755'),
    ('shared/android/rungic-wfd-sepolicy.sh', '/data/adb/service.d/rungic-wfd-sepolicy.sh', '755'),
]
DEFAULT_APK = WORKSPACE / '.work/refs/plasma-mobile-20260923/Rungic-2.0.apk'


def root(script, timeout=120, check=True):
    return run(script, 'root', timeout, check)


def layout():
    return out('[ -d /data/adb/rungic-plasma ] && echo rungic || echo moto').strip()


def packages():
    text = out('pm list packages -U', 'shell')
    found = {}
    for line in text.splitlines():
        parts = dict(p.split(':', 1) for p in line.split() if ':' in p)
        if parts.get('package') in (NEW_APK, OLD_APK):
            found[parts['package']] = int(parts.get('uid', 0))
    enabled = out('pm list packages -e', 'shell').split()
    return {name: {'uid': uid, 'enabled': f'package:{name}' in enabled} for name, uid in found.items()}


def status():
    state = {'layout': layout(), 'apks': packages()}
    state['adb'] = out('ls -la /data/adb /data/adb/service.d').splitlines()
    state['rootfs'] = root(f'{rungic_device.PLASMA} rootfs status', check=False).stdout.strip()
    state['container'] = root(f'{rungic_device.PLASMA} status | grep -m1 State', check=False).stdout.strip()
    return state


class Record:
    def __init__(self, action):
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        self.dir = WORKSPACE / f'.work/cutover/{stamp}-{action}'
        self.dir.mkdir(parents=True)
        self.log = {'action': action, 'steps': []}

    def step(self, name, **data):
        self.log['steps'].append({'step': name, 'time': datetime.datetime.now().isoformat(timespec='seconds'), **data})
        (self.dir / 'cutover.json').write_text(json.dumps(self.log, indent=1, ensure_ascii=False) + '\n')
        print(f'[{name}] ' + ', '.join(f'{k}={v}' for k, v in data.items() if not isinstance(v, (dict, list))),
              flush=True)

    def save(self, name, text):
        (self.dir / name).write_text(text)


def stop_all(launcher, lxc, cast_watch, apk, dm):
    """The desktop APK, the Plasma container with its image and audio, alpine and the cast watcher."""
    root(f'am force-stop {apk}', check=False)
    result = root(f'{launcher} stop', timeout=240, check=False)
    text = result.stdout + result.stderr
    if result.returncode:
        raise SystemExit(f'the container did not stop: {text}')
    root(f'[ "$({lxc} status | grep -m1 State | tr -s " " | cut -d" " -f2)" != RUNNING ] || {lxc} stop', timeout=60,
         check=False)
    # The watchers: the cast reconnect loop and the audio monitor (it ends once its flag is gone).
    # The cast watcher waits on a logcat pipeline and ends only once its children are gone.
    root(f'for p in $(pgrep -f {cast_watch}); do pkill -P $p; kill $p; done; pkill -f "android-audio watch"; true',
         check=False)
    left = root(f'dmctl getpath {dm} 2>/dev/null; pidof lxc-start; losetup -a | grep rootfs.img; true',
                check=False).stdout.strip()
    if left:
        raise SystemExit(f'still in use after stopping: {left}')
    return text.strip()


def keep(paths, recorder):
    """Copy (existing) paths to KEEP, keeping modes, owners and labels."""
    script = [f'mkdir -p {KEEP}']
    for path in paths:
        target = KEEP + path
        script.append(f'if [ -e {path} ] && [ ! -e {target} ]; then mkdir -p {shlex.quote(str(Path(target).parent))} '
                      f'&& cp -a {path} {target}; fi')
    root('\n'.join(script))
    recorder.step('keep', paths=paths)


def install_files(recorder):
    missing = [str(src) for src, _, _ in INSTALL if not (WORKSPACE / src).exists()]
    if missing:
        raise SystemExit(f'build these first: {missing}')
    keep([path for _, path, _ in INSTALL], recorder)
    for src, path, mode in INSTALL:
        remote = push(WORKSPACE / src, 'rungic-cutover-file')
        root(f'install -m {mode} {remote} {path}.new && mv {path}.new {path} && rm -f {remote}')
    # The Termux shortcut `lxc`: its own owner and label stay (the file is rewritten in place).
    remote = push(WORKSPACE / 'lxc/termux-lxc', 'rungic-cutover-file')
    root(f'[ ! -f {TERMUX_LXC} ] || cat {remote} > {TERMUX_LXC}; rm -f {remote}')
    recorder.step('install', files=[path for _, path, _ in INSTALL] + [TERMUX_LXC])


def grant(recorder, prefs_from):
    """Settings, permissions and the su grant of the old APK for the new one."""
    uid = packages()[NEW_APK]['uid']
    data = f'/data/data/{NEW_APK}'
    # The new app's files carry its data directory's label (its own MCS categories).
    root(f'''set -e
label=$(ls -dZ {data} | cut -d" " -f1)
mkdir -p {data}/shared_prefs
cp {prefs_from} {data}/shared_prefs/MainActivity.xml
chown {uid}:{uid} {data}/shared_prefs {data}/shared_prefs/MainActivity.xml
chmod 771 {data}/shared_prefs; chmod 660 {data}/shared_prefs/MainActivity.xml
chcon "$label" {data}/shared_prefs {data}/shared_prefs/MainActivity.xml''')
    for permission in ('android.permission.CAMERA', 'android.permission.RECORD_AUDIO',
                       'android.permission.BLUETOOTH_SCAN', 'android.permission.BLUETOOTH_CONNECT'):
        root(f'pm grant {NEW_APK} {permission}')
    root(f'appops set {NEW_APK} SYSTEM_ALERT_WINDOW allow')
    # Magisk keeps grants in its SQLite policies; KernelSU has no such store (the user grants the app
    # in its manager), system/root-provider tells which is active.
    granted = root(f'. /data/adb/rungic-plasma/root-provider; [ "$RUNGIC_ROOT" = magisk ] || {{ echo kernelsu; exit 0; }}; '
                   f'"$RUNGIC_MAGISK" --sqlite "INSERT OR REPLACE INTO policies (uid,policy,until,logging,notification) '
                   f'VALUES({uid},2,0,1,1)"')
    if 'kernelsu' in (getattr(granted, 'stdout', '') or ''):
        print(f'KernelSU: grant {NEW_APK} root in the KernelSU manager', flush=True)
    recorder.step('grant', uid=uid, prefs=bool(prefs_from))


def wait_ready(launcher, apk, recorder, timeout=180):
    root(f'am start --user 0 -n {apk}/.MainActivity')
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready = root(f'{launcher} exec sh -c "test -f /run/user/1000/rungic-session.env && pidof kwin_wayland '
                     '>/dev/null && pidof plasmashell >/dev/null && echo ready"', check=False).stdout
        if 'ready' in ready:
            recorder.step('ready', seconds=round(timeout - (deadline - time.time())))
            return True
        time.sleep(3)
    recorder.step('not-ready', timeout=timeout)
    return False


def up(apk):
    if layout() != 'moto':
        raise SystemExit('the Android side already has the Rungic layout')
    apk = Path(apk)
    if not apk.exists():
        raise SystemExit(f'no APK at {apk}: android/build-apk.sh')
    recorder = Record('up')
    before = status()
    recorder.save('before.json', json.dumps(before, indent=1, ensure_ascii=False) + '\n')
    recorder.save('appops-before.txt', out(f'appops get {OLD_APK}', 'root'))
    recorder.save('package-before.txt', out(f'dumpsys package {OLD_APK}', 'root'))
    if 'state=none' not in before['rootfs']:
        raise SystemExit(f'the rootfs image has a snapshot or merge in progress: {before["rootfs"]}')
    recorder.step('record', rootfs=before['rootfs'], apks=before['apks'])
    # The new APK first (not started): installing it needs nothing of the old layout.
    subprocess.run(adb('install', '-r', str(apk)), check=True, capture_output=True, timeout=300)
    recorder.step('apk-installed', apk=apk.name)
    text = stop_all('/data/adb/moto-plasma/moto-plasma', '/data/adb/moto-lxc/moto-lxc',
                    '/data/adb/moto-wfd/moto-cast-watch', OLD_APK, 'moto-plasma-root')
    recorder.step('stopped', output=text[-300:])
    moves = '\n'.join(f'[ -e {new} ] && {{ echo "{new} exists" >&2; exit 1; }}; mv {old} {new}' for old, new in MOVES)
    root(f'set -e\n{moves}')
    recorder.step('moved', moves=MOVES)
    keep(RETIRED, recorder)
    root('rm -f ' + ' '.join(RETIRED))
    recorder.step('retired', paths=RETIRED)
    install_files(recorder)
    grant(recorder, f'/data/data/{OLD_APK}/shared_prefs/MainActivity.xml')
    root(f'pm disable-user --user 0 {OLD_APK}')
    recorder.step('old-apk-disabled')
    # What the boot scripts do, now: the cast policy is loaded already; the watcher starts.
    root('nohup /data/adb/rungic-wfd/rungic-cast-watch </dev/null >/dev/null 2>&1 &')
    ok = wait_ready('/data/adb/rungic-plasma/rungic-plasma', NEW_APK, recorder)
    recorder.save('after.json', json.dumps(status(), indent=1, ensure_ascii=False) + '\n')
    recorder.step('done', ok=ok, record=str(recorder.dir))
    return ok


def down():
    if layout() != 'rungic':
        raise SystemExit('the Android side already has the moto layout')
    recorder = Record('down')
    recorder.save('before.json', json.dumps(status(), indent=1, ensure_ascii=False) + '\n')
    text = stop_all('/data/adb/rungic-plasma/rungic-plasma', '/data/adb/rungic-lxc/rungic-lxc',
                    '/data/adb/rungic-wfd/rungic-cast-watch', NEW_APK, 'rungic-root')
    recorder.step('stopped', output=text[-300:])
    # Files `up` installed: the kept originals go back, the others go.
    for _, path, _ in INSTALL:
        root(f'if [ -e {KEEP}{path} ]; then cp -a {KEEP}{path} {path}.old && mv {path}.old {path}; else rm -f {path}; fi')
    root(f'[ ! -f {KEEP}{TERMUX_LXC} ] || cat {KEEP}{TERMUX_LXC} > {TERMUX_LXC}')
    for path in RETIRED:
        root(f'[ ! -e {KEEP}{path} ] || cp -a {KEEP}{path} {path}')
    recorder.step('restored', files=[path for _, path, _ in INSTALL] + RETIRED)
    root('set -e\n' + '\n'.join(f'mv {new} {old}' for old, new in reversed(MOVES)))
    recorder.step('moved', moves=[(new, old) for old, new in reversed(MOVES)])
    root(f'pm enable --user 0 {OLD_APK}; pm disable-user --user 0 {NEW_APK}')
    root(f'rm -rf {KEEP}')
    recorder.step('apks', enabled=OLD_APK, disabled=NEW_APK)
    root('nohup /data/adb/moto-wfd/moto-cast-watch </dev/null >/dev/null 2>&1 &')
    ok = wait_ready('/data/adb/moto-plasma/moto-plasma', OLD_APK, recorder)
    recorder.step('done', ok=ok, record=str(recorder.dir))
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('status')
    p = sub.add_parser('up')
    p.add_argument('--apk', default=str(DEFAULT_APK))
    sub.add_parser('down')
    args = parser.parse_args()
    if args.action == 'status':
        print(json.dumps(status(), indent=1, ensure_ascii=False))
        return 0
    actions = {'up': lambda: up(args.apk), 'down': down}
    return 0 if actions[args.action]() else 1


if __name__ == '__main__':
    sys.exit(main())
