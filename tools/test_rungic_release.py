#!/usr/bin/env python3
"""rungic_release's Android-side file handling without a device: sync_android() records what it
changes and restore_android() puts it back (the snapshot rollback path, docs/70)."""
import hashlib
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

import rungic_release


class FakeDevice:
    """Files on the phone as a dict; understands the few commands rungic_release sends."""

    def __init__(self, files):
        self.files = dict(files)

    def run(self, command, level='root', timeout=None, check=True):
        result = type('Result', (), {'stdout': '', 'stderr': '', 'returncode': 0})()
        for part in command.split('&&'):
            argv = shlex.split(part.split('|')[0].split(';')[0])
            if argv[0] == 'sha256sum':
                data = self.files.get(argv[1])
                result.stdout = hashlib.sha256(data).hexdigest() + '\n' if data is not None else ''
            elif argv[0] == 'install':
                self.files[argv[3]] = self.files[argv[2]]
            elif argv[0] == 'mv':
                self.files[argv[2]] = self.files.pop(argv[1])
            elif argv[0] == 'rm':
                for path in argv[2:]:
                    self.files.pop(path, None)
        return result

    def push(self, local, name, timeout=None):
        remote = f'/data/local/tmp/{name}'
        self.files[remote] = Path(local).read_bytes()
        return remote

    def pull(self, path, local, timeout=None):
        Path(local).write_bytes(self.files[path])


class AndroidFilesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'system').mkdir()
        self.record = self.root / 'record'
        self.record.mkdir()

    def release(self, sources):
        files = {}
        for path, (source, data) in sources.items():
            (self.root / source).write_bytes(data)
            files[path] = {'source': source, 'sha256': hashlib.sha256(data).hexdigest(), 'mode': '644'}
        return {'version': 'test', 'android': files}

    def patched(self, device):
        return [patch.object(rungic_release, name, getattr(device, name)) for name in ('run', 'push', 'pull')] + \
               [patch.object(rungic_release, 'WORKSPACE', self.root)]

    # covers: delivery.release-deploy/E3
    def test_rollback_restores_replaced_and_removes_added_files(self):
        device = FakeDevice({'/data/adb/x/config': b'old config', '/data/adb/x/same': b'same'})
        info = self.release({'/data/adb/x/config': ('system/config', b'new config'),
                             '/data/adb/x/hook': ('system/hook', b'new hook'),
                             '/data/adb/x/same': ('system/same', b'same')})
        patches = self.patched(device)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        changed = rungic_release.sync_android(info, self.record)
        self.assertEqual(sorted(changed), ['/data/adb/x/config', '/data/adb/x/hook'])
        self.assertEqual(device.files['/data/adb/x/config'], b'new config')
        self.assertEqual(device.files['/data/adb/x/hook'], b'new hook')

        restored = rungic_release.restore_android(self.record)
        self.assertEqual(sorted(restored), ['/data/adb/x/config', '/data/adb/x/hook'])
        self.assertEqual(device.files['/data/adb/x/config'], b'old config')
        self.assertNotIn('/data/adb/x/hook', device.files)
        self.assertEqual(device.files['/data/adb/x/same'], b'same')
        self.assertFalse([p for p in device.files if p.startswith('/data/local/tmp/')])

    # covers: delivery.release-deploy/E3
    def test_nothing_to_restore_without_changes(self):
        device = FakeDevice({'/data/adb/x/same': b'same'})
        info = self.release({'/data/adb/x/same': ('system/same', b'same')})
        patches = self.patched(device)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        self.assertEqual(rungic_release.sync_android(info, self.record), [])
        self.assertEqual(rungic_release.restore_android(self.record), [])


class DeployFailureTests(unittest.TestCase):
    """A deploy that fails after the snapshot must return to it (2026-09-26: an Android-side
    source check stopped a deploy halfway, after the install, and left the rootfs there)."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'system').mkdir()
        (self.root / 'system/config').write_bytes(b'lxc config')
        self.info = {'version': 'test', 'packages': {}, 'android': {
            '/data/adb/x/config': {'source': 'system/config', 'sha256': hashlib.sha256(b'lxc config').hexdigest(),
                                   'mode': '644'}}}
        self.calls = []
        stubs = dict(
            WORKSPACE=self.root, DEPLOY=self.root / 'deploy', HISTORY=self.root / 'history.json',
            RELEASE_HISTORY=self.root / 'release-history.json',
            releases=lambda: [self.info], preflight=lambda: ([], []), rootfs_state=lambda: ('image', 'none'),
            with_container_stopped=self.stopped, device_release=lambda: ('previous', None),
            android_layouts=lambda info: ('rungic', 'rungic'),
            installed_versions=lambda: {}, integrity_summary=lambda: {}, ensure_apt_source=lambda: None,
            sync_repo=lambda: {}, apt_install=lambda info, record: (True, ''), run=lambda *a, **k: None)
        for name, value in stubs.items():
            p = patch.object(rungic_release, name, value)
            p.start()
            self.addCleanup(p.stop)
        # Nothing may reach the phone: every device command goes through rungic_device._run (a test
        # once cleared the real phone's development overlay through an unstubbed path, docs/97).
        import rungic_device

        def no_device(*args, **kwargs):
            raise AssertionError(f'an offline test reached the device: {args[:1]}')
        p = patch.object(rungic_device, '_run', no_device)
        p.start()
        self.addCleanup(p.stop)
        import rungic_acceptance
        p = patch.object(rungic_acceptance, 'session_ready', lambda ctx: {'passed': True})
        p.start()
        self.addCleanup(p.stop)
        import sys, types
        agent = types.SimpleNamespace(snapshot=lambda label, since: {'folder': 'evidence'})
        p = patch.dict(sys.modules, {'rungic_agent': agent})
        p.start()
        self.addCleanup(p.stop)

    def stopped(self, action, before_start=None):
        self.calls.append(action)
        if before_start:
            before_start()
        return True, action

    # covers: delivery.release-deploy/E4
    def test_changed_android_source_aborts_before_the_snapshot(self):
        (self.root / 'system/config').write_bytes(b'edited since the build')
        log = rungic_release.deploy('test')
        self.assertEqual(log['result'], 'aborted')
        self.assertEqual(self.calls, [])

    # covers: delivery.release-deploy/E3 delivery.rootfs-snapshot/E1
    def test_an_error_after_the_install_rolls_back(self):
        def broken(info, record):
            raise SystemExit('boom')
        with patch.object(rungic_release, 'sync_android', broken):
            log = rungic_release.deploy('test', acceptance='none')
        self.assertEqual(self.calls, ['snapshot', 'rollback'])
        self.assertEqual(log['result'], 'error, rolled back to the snapshot')
        self.assertIn('boom', [s for s in log['steps'] if s['step'] == 'error'][0]['reason'])


if __name__ == '__main__':
    unittest.main()
