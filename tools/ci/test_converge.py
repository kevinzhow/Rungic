#!/usr/bin/env python3
"""Run system/rungic-converge whole against fake Android services (docs/122).

dumpsys, appops, Magisk's SQLite, owners and SELinux labels are files in a sandbox; no phone.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
APP = 'com.rungic.plasma'


def executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\n' + text + '\n')
    path.chmod(0o755)


class Converge(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.base = self.root / 'data/adb/rungic-plasma'
        self.state = self.root / 'data/adb/rungic-lxc/runtime/var/lib/lxc/plasma/state/host/runtime'
        self.base.mkdir(parents=True)
        (self.root / 'proc/sys/kernel/random').mkdir(parents=True)
        (self.root / 'proc/sys/kernel/random/boot_id').write_text('boot-test')
        (self.root / 'storage/emulated/0/Android').mkdir(parents=True)
        self.fake = self.root / 'fake'
        self.fake.mkdir()
        # Android as a fresh install left it, and then drifted: nothing below is right yet.
        self.app_dir('data/user/0/com.rungic.plasma', '10220:10220', 'u:object_r:app_data_file:s0:c220')
        self.app_dir('data/user/0/com.termux', '10225:10225', 'u:object_r:app_data_file:s0:c225')
        self.write('allowlist', '')
        self.write('overlay', 'SYSTEM_ALERT_WINDOW: default; time=+1d')
        self.write('policies', '')
        self.write('permissions', '\n'.join(f'      android.permission.{p}: granted=true' for p in
                                            ('RECORD_AUDIO', 'CAMERA', 'POST_NOTIFICATIONS', 'BLUETOOTH_CONNECT',
                                             'BLUETOOTH_SCAN', 'READ_PHONE_STATE')))
        executable(self.bin / 'id', 'echo 0')
        log = 'echo "$(basename "$0") $*" >> "$TEST_ROOT/calls"'
        # Owners and labels live in fake/meta<path>/{owner,label}.
        executable(self.bin / 'stat', '''[ "$1" = -c ] || exit 90
[ -e "$3" ] || exit 1
o=$(cat "$TEST_ROOT/fake/meta$3/owner" 2>/dev/null || echo 0:0)
case "$2" in %u) echo "${o%%:*}";; %u:%g) echo "$o";; *) exit 91;; esac''')
        executable(self.bin / 'ls', '''[ "$1" = -dZ ] || exit 90
[ -e "$2" ] || exit 1
echo "$(cat "$TEST_ROOT/fake/meta$2/label" 2>/dev/null || echo u:object_r:unlabeled:s0) $2"''')
        executable(self.bin / 'chown', log + '''
o=$1; shift; for p; do mkdir -p "$TEST_ROOT/fake/meta$p"; echo "$o" > "$TEST_ROOT/fake/meta$p/owner"; done''')
        executable(self.bin / 'chcon', log + '''
l=$1; shift; for p; do mkdir -p "$TEST_ROOT/fake/meta$p"; echo "$l" > "$TEST_ROOT/fake/meta$p/label"; done''')
        executable(self.bin / 'dumpsys', '''f=$TEST_ROOT/fake
case "$1 $2 ${3:-}" in
 "deviceidle whitelist ") cat "$f/allowlist";;
 "deviceidle whitelist +com.rungic.plasma") echo "dumpsys $*" >> "$TEST_ROOT/calls"
    [ -z "${REFUSE_ALLOWLIST:-}" ] || exit 1; echo "user,com.rungic.plasma,10220" >> "$f/allowlist";;
 "package com.rungic.plasma ") cat "$f/permissions";;
 *) exit 92;;
esac''')
        executable(self.bin / 'appops', '''case "$1" in
 get) cat "$TEST_ROOT/fake/overlay";;
 set) echo "appops $*" >> "$TEST_ROOT/calls"; [ -z "${REFUSE_APPOPS:-}" ] || exit 1
      echo "SYSTEM_ALERT_WINDOW: allow" > "$TEST_ROOT/fake/overlay";;
 *) exit 93;;
esac''')
        executable(self.bin / 'magisk', '''[ "$1" = --sqlite ] || exit 94
case "$2" in
 SELECT*) cat "$TEST_ROOT/fake/policies";;
 INSERT*) echo "magisk INSERT" >> "$TEST_ROOT/calls"; echo policy=2 > "$TEST_ROOT/fake/policies";;
 *) exit 95;;
esac''')
        executable(self.bin / 'busybox', 'case "$1" in timeout) shift 2; exec "$@";; *) exit 96;; esac')
        self.provider('magisk')
        text = (ROOT / 'system/rungic-converge').read_text().replace('/system/bin/sh', '/bin/sh')
        text = re.sub(r'(?<![\w/])(/data/adb|/data/user/0|/storage/emulated/0|/proc)\b', str(self.root) + r'\1', text)
        self.script = self.base / 'rungic-converge'
        self.script.write_text(text)
        self.script.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}', TEST_ROOT=str(self.root))

    def app_dir(self, path, owner, label):
        (self.root / path).mkdir(parents=True, exist_ok=True)
        meta = self.fake / ('meta' + str(self.root / path))
        meta.mkdir(parents=True, exist_ok=True)
        (meta / 'owner').write_text(owner + '\n')
        (meta / 'label').write_text(label + '\n')

    def write(self, name, text):
        (self.fake / name).write_text(text + '\n' if text else '')

    def provider(self, root):
        magisk = str(self.bin / 'magisk') if root == 'magisk' else ''
        (self.base / 'root-provider').write_text(
            f'RUNGIC_ROOT={root}\nRUNGIC_BUSYBOX={self.bin}/busybox\nRUNGIC_MAGISK={magisk}\n')

    def run_mode(self, mode, **env):
        (self.root / 'calls').unlink(missing_ok=True)
        result = subprocess.run(['sh', str(self.script), mode], env={**self.env, **env},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.state / 'converge.json').read_text())
        self.assertEqual(report['mode'], mode)
        return {i['id']: (i['state'], i['detail']) for i in report['items']}

    def calls(self):
        path = self.root / 'calls'
        return path.read_text().splitlines() if path.exists() else []

    # covers: install.independent-runtime/E7
    def test_apply_sets_what_is_wrong_and_then_leaves_it_alone(self):
        first = self.run_mode('apply')
        for item in ('background-network', 'overlay', 'root-grant', 'app-files', 'shared-folder', 'termux-home'):
            self.assertEqual(first[item][0], 'changed', item)
        self.assertEqual(first['permissions'], ('ok', ''))
        self.assertIn(f'dumpsys deviceidle whitelist +{APP}', self.calls())
        self.assertIn('magisk INSERT', self.calls())
        self.assertTrue((self.root / 'storage/emulated/0/Plasma').is_dir())
        self.assertTrue((self.root / f'data/user/0/{APP}/files/tmp').is_dir())
        log = (self.state / 'host.log').read_text()
        self.assertIn('converge background-network changed', log)
        second = self.run_mode('apply')
        self.assertEqual({k: v[0] for k, v in second.items()}, {k: 'ok' for k in second})
        self.assertEqual(self.calls(), [], 'nothing is written when everything is right')

    # covers: install.independent-runtime/E7
    def test_check_only_looks(self):
        result = self.run_mode('check')
        self.assertEqual(result['background-network'][0], 'differs')
        self.assertEqual(result['app-files'][0], 'differs')
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.root / 'storage/emulated/0/Plasma').exists())

    # covers: install.independent-runtime/E7
    def test_a_refusal_is_recorded_and_the_other_items_still_run(self):
        result = self.run_mode('apply', REFUSE_ALLOWLIST='1', REFUSE_APPOPS='1')
        self.assertEqual(result['background-network'][0], 'refused')
        self.assertEqual(result['overlay'][0], 'refused')
        self.assertEqual(result['root-grant'][0], 'changed')
        self.assertEqual(result['app-files'][0], 'changed')

    # covers: install.independent-runtime/E7
    def test_user_choices_are_reported_not_overridden(self):
        # Runtime permissions are the user's (docs/122); KernelSU's grants are not readable.
        self.write('permissions', '      android.permission.RECORD_AUDIO: granted=true\n'
                                  '      android.permission.FOREGROUND_SERVICE_CAMERA: granted=true')
        self.provider('kernelsu')
        result = self.run_mode('apply')
        self.assertEqual(result['permissions'][0], 'report')
        self.assertIn('CAMERA', result['permissions'][1])
        self.assertNotIn('RECORD_AUDIO', result['permissions'][1])
        self.assertEqual(result['root-grant'], ('report', 'kernelsu-manager'))
        self.assertFalse(any(c.startswith(('pm', 'magisk')) for c in self.calls()))

    # covers: install.independent-runtime/E7
    def test_locked_storage_and_a_missing_app_do_not_stop_the_run(self):
        (self.root / 'storage/emulated/0/Android').rmdir()
        (self.root / f'data/user/0/{APP}').rmdir()
        result = self.run_mode('apply')
        self.assertEqual(result['shared-folder'], ('report', 'storage-locked'))
        self.assertEqual(result['app-files'], ('refused', 'app-missing'))
        self.assertEqual(result['root-grant'], ('refused', 'app-missing'))
        self.assertEqual(result['termux-home'][0], 'changed')


class OneDeclaration(unittest.TestCase):
    # covers: install.independent-runtime/E7
    def test_only_rungic_converge_sets_these_on_the_phone(self):
        # First boot set the overlay and Magisk's grant once and a release never reached them
        # (docs/122): a second copy on the phone would drift from this one again.
        settings = re.compile(r'SYSTEM_ALERT_WINDOW allow|INTO policies|deviceidle whitelist \+')
        scripts = [p for p in (ROOT / 'system').iterdir() if p.is_file()]
        scripts += list((ROOT / 'tools/ci').glob('*.sh')) + list((ROOT / 'shared/android').rglob('*.sh'))
        found = sorted(str(p.relative_to(ROOT)) for p in scripts
                       if p.name != 'rungic-converge' and settings.search(p.read_text(errors='replace')))
        self.assertEqual(found, [])


if __name__ == '__main__':
    unittest.main()
