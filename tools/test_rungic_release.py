#!/usr/bin/env python3
"""rungic_release's Android-side file handling without a device: sync_android() records what it
changes and restore_android() puts it back (the snapshot rollback path, docs/70)."""
import hashlib
import json
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
            sync_repo=lambda **kw: {}, apt_install=lambda info, record: (True, ''), run=lambda *a, **k: None)
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



class BuildHostDirectTests(unittest.TestCase):
    """The phone takes the packages from the build host's release pool (AGENTS.md); they are sent
    from here only when the build host or the phone's key to it fails."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pool = Path(self.tmp.name)
        for name, data in (('big_1_arm64.deb', b'big'), ('big-dbgsym_1_arm64.deb', b'sym'),
                           ('changed_1_arm64.deb', b'here'), ('local_1_arm64.deb', b'local')):
            (self.pool / name).write_bytes(data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_build_host_links_what_it_built_and_gets_the_rest_once(self):
        names = sorted(p.name for p in self.pool.iterdir())
        put = []
        # The build host has the two kwin files (one as .ddeb); the others are copied to it.
        with patch('build_on_device.MacMini.out', return_value='big_1_arm64.deb\nbig-dbgsym_1_arm64.deb\n') as out, \
                patch('build_on_device.MacMini.put', side_effect=lambda src, dest, mode: put.append(Path(dest).name)), \
                patch.dict('os.environ', {'RUNGIC_BUILD_HOST': 'macmini'}):
            staged = rungic_release.stage_on_build_host(names, self.pool)
        self.assertEqual(sorted(staged), names)
        self.assertEqual(sorted(put), ['changed_1_arm64.deb', 'local_1_arm64.deb'])
        self.assertEqual(staged['big_1_arm64.deb']['path'], 'release-pool/big_1_arm64.deb')
        self.assertEqual(staged['big_1_arm64.deb']['sha256'], hashlib.sha256(b'big').hexdigest())
        script = out.call_args[0][0]
        self.assertIn(f'big_1_arm64.deb {hashlib.sha256(b"big").hexdigest()}', script)
        self.assertIn('${n%.deb}.ddeb', script)

    def test_an_unreachable_build_host_sends_everything_from_here(self):
        with patch('build_on_device.MacMini.out', side_effect=OSError('no route')), \
                patch.dict('os.environ', {'RUNGIC_BUILD_HOST': 'macmini'}):
            self.assertEqual(rungic_release.stage_on_build_host(['big_1_arm64.deb'], self.pool), {})

    def test_a_build_on_the_phone_sends_everything_from_here(self):
        with patch.dict('os.environ', {'RUNGIC_BUILD_HOST': 'phone'}):
            self.assertEqual(rungic_release.stage_on_build_host(['big_1_arm64.deb'], self.pool), {})

    def test_sync_sends_only_what_the_phone_did_not_take(self):
        sent, taken = [], []

        def extract(archive, dest):
            import tarfile
            with tarfile.open(archive) as tar:
                sent.extend(tar.getnames())

        for fails in (False, True):
            sent.clear(), taken.clear()
            fetch = (lambda kept, repo: (_ for _ in ()).throw(rungic_release.DeviceError('no key'))) if fails \
                else (lambda kept, repo: taken.extend(kept))
            for index in ('Packages', 'Packages.gz', 'Packages.xz', 'Release'):
                (self.pool / index).write_bytes(b'')
            with patch.object(rungic_release, 'run', return_value=type('R', (), {'stdout': ''})()), \
                    patch.object(rungic_release, 'stage_on_build_host',
                                 return_value={'big_1_arm64.deb': {'path': 'kwin/big_1_arm64.deb', 'size': 3, 'sha256': ''}}), \
                    patch.object(rungic_release, 'fetch_kept', side_effect=fetch), \
                    patch.object(rungic_release, 'DEPLOY', self.pool / 'deploy'), \
                    patch('rungic_device.extract_in_container', side_effect=extract):
                rungic_release.sync_repo(pool=self.pool, device_repo='/var/lib/rungic-apt')
            if fails:
                self.assertIn('big_1_arm64.deb', sent)
            else:
                self.assertEqual(taken, ['big_1_arm64.deb'])
                self.assertNotIn('big_1_arm64.deb', sent)
            self.assertIn('local_1_arm64.deb', sent)

    def test_a_deploy_brings_only_its_release_debs(self):
        """20261008.1: mirroring the whole pool started copying 829 old builds for 89 packages."""
        sent, staged = [], []

        def extract(archive, dest):
            import tarfile
            with tarfile.open(archive) as tar:
                sent.extend(tar.getnames())

        for index in ('Packages', 'Packages.gz', 'Packages.xz', 'Release'):
            (self.pool / index).write_bytes(b'')
        (self.pool / ('old_0_arm64.deb' + rungic_release.REMOTE)).write_text(
            json.dumps({'path': 'old/old_0_arm64.deb', 'size': 1, 'sha256': ''}))
        with patch.object(rungic_release, 'run', return_value=type('R', (), {'stdout': ''})()), \
                patch.object(rungic_release, 'stage_on_build_host',
                             side_effect=lambda names, pool: staged.extend(names) or {}), \
                patch.object(rungic_release, 'fetch_kept', side_effect=AssertionError('an old build fetched')), \
                patch.object(rungic_release, 'DEPLOY', self.pool / 'deploy'), \
                patch('rungic_device.extract_in_container', side_effect=extract):
            rungic_release.sync_repo(pool=self.pool, device_repo='/var/lib/rungic-apt', only={'big_1_arm64.deb'})
        self.assertEqual(staged, ['big_1_arm64.deb'])
        self.assertEqual(sorted(n for n in sent if n.endswith('.deb')), ['big_1_arm64.deb'])
        self.assertIn('Packages', sent)

    def test_release_debs_are_the_pinned_versions_without_the_epoch(self):
        info = {'packages': {'big': '1', 'kwrite': '4:25.1', 'coupled': '9'}}
        for name in ('big_0_arm64.deb', 'kwrite_25.1_arm64.deb', 'rungic-release_20261008.1_all.deb'):
            (self.pool / name).write_bytes(b'')
        with patch.object(rungic_release, 'POOL', self.pool):
            self.assertEqual(rungic_release.release_debs(info, '20261008.1'),
                             {'big_1_arm64.deb', 'kwrite_25.1_arm64.deb', 'rungic-release_20261008.1_all.deb'})

if __name__ == '__main__':
    unittest.main()
