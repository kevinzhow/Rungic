"""Independent payload corruption and wrong-device checks; no ADB operations."""
import argparse
import json
import shlex
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest

import standalone

from standalone import FILES, digest, preflight, verify, validate_cast_host, cast_payload


class PayloadTests(unittest.TestCase):
    # covers: install.standalone-install/E8
    def test_cached_host_requires_matching_cast_sources_and_jar(self):
        for old in ({}, {'cast_build': {}}, {'cast_build': {'schema': 1, 'inputs': {}}}):
            with self.subTest(old=old), self.assertRaisesRegex(ValueError, 'stale'):
                validate_cast_host(old)
        host = {'cast_build': {'schema': 1, 'inputs': cast_payload.build_inputs(), 'jar_sha256': 'abc'},
                'cast_jar_sha256': 'abc'}
        validate_cast_host(host)
        host['cast_jar_sha256'] = 'different'
        with self.assertRaisesRegex(ValueError, 'provenance'):
            validate_cast_host(host)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in FILES:
            (self.root / name).write_text('fixture')
        self.manifest = {'schema': 1, 'kind': 'rungic-standalone', 'release': 'test.1',
                         'files': {n: {'bytes': 7, 'sha256': digest(self.root / n)} for n in FILES}}
        self.save()

    def save(self):
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest))

    # covers: install.standalone-install/E2
    def test_valid_payload(self):
        self.assertEqual(verify(self.root, digest(self.root / 'manifest.json'))['release'], 'test.1')

    # covers: install.standalone-install/E2
    def test_wrong_trusted_manifest_rejected(self):
        with self.assertRaisesRegex(ValueError, 'trusted'):
            verify(self.root, '0' * 64)

    # covers: install.standalone-install/E2
    def test_corrupt_or_truncated_component_rejected(self):
        for contents in ('fixturE', ''):
            (self.root / 'host-seed.tar.gz').write_text(contents)
            with self.assertRaisesRegex(ValueError, 'host-seed'):
                verify(self.root)

    # covers: install.standalone-install/E2
    def test_file_symlink_rejected(self):
        p = self.root / 'rungic.apk'
        p.unlink(); p.symlink_to(self.root / 'termux.apk')
        with self.assertRaisesRegex(ValueError, 'rungic.apk'):
            verify(self.root)

    # covers: install.standalone-install/E2
    def test_traversal_inventory_rejected(self):
        self.manifest['files']['../outside'] = self.manifest['files'].pop('rungic.apk')
        self.save()
        with self.assertRaisesRegex(ValueError, 'inventory'):
            verify(self.root)

    # covers: install.standalone-install/E2
    def test_shell_release_rejected(self):
        self.manifest['release'] = 'a;touch /tmp/bad'
        self.save()
        with self.assertRaisesRegex(ValueError, 'release'):
            verify(self.root)

    # covers: install.standalone-install/E1
    def test_wrong_device_and_kernel_rejected_before_boot_read(self):
        manifest = dict(fingerprint='expected', product='vantage', kernel_release='6.12',
                        minimum_battery_percent=30, boot_bytes=4096, boot_sha256='good')
        class Fake:
            def __init__(self, fields): self.fields, self.calls = fields, 0
            def shell(self, *args, **kwargs):
                self.calls += 1
                return '\n'.join(self.fields) if self.calls == 1 else 'good  -'
        fields = ['0', 'expected', 'vantage', '6.12', 'Enforcing', '_a', '80']
        self.assertEqual(preflight(Fake(fields), manifest)['slot'], '_a')
        for i, bad in [(0, '2000'), (1, 'wrong'), (2, 'portov'), (3, '6.6'), (4, 'Permissive'), (5, '../bad'), (6, '10')]:
            wrong = fields.copy(); wrong[i] = bad; device = Fake(wrong)
            with self.subTest(field=i), self.assertRaises(ValueError):
                preflight(device, manifest)
            self.assertEqual(device.calls, 1)
        manifest['boot_sha256'] = 'different'
        with self.assertRaisesRegex(ValueError, 'boot image'):
            preflight(Fake(fields), manifest)


class InstallerTransferTests(unittest.TestCase):
    # covers: install.standalone-install/E2
    def test_transferred_inventory_is_checked_before_apk_install(self):
        # Exercise the transfer/check boundary on disk. Android identity, free
        # space and package operations are stand-ins; checksum commands are real.
        class PackageInstallReached(Exception):
            pass

        for schema, corrupt in ((1, False), (2, False), (2, True)):
            with self.subTest(schema=schema, corrupt=corrupt), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                payload = root / 'payload'
                payload.mkdir()
                names = FILES | ({'build-manifest.json'} if schema == 2 else set())
                for name in names:
                    (payload / name).write_text('fixture: ' + name)
                manifest = {'schema': schema, 'release': 'test.1', 'rootfs_bytes': 1024,
                            'files': {name: {'sha256': digest(payload / name)} for name in names}}
                (payload / 'manifest.json').write_text(json.dumps(manifest))
                stage = standalone.staging_path(manifest['release'])
                local_stage = root / 'stage'

                class Device:
                    def __init__(self):
                        self.pushed = set()
                        self.checked = set()
                        self.packages = []

                    def push(self, local, remote):
                        self.pushed.add(remote)
                        target = local_stage / Path(remote).name
                        shutil.copyfile(local, target)
                        if corrupt and target.name == 'build-manifest.json':
                            target.write_text('corrupt transfer')

                    def shell(self, script, **kwargs):
                        if script.startswith('mkdir -p '):
                            local_stage.mkdir()
                        elif '| sha256sum -c -' in script:
                            self.checked = {shlex.split(line)[1].split('  ', 1)[1]
                                            for line in script.splitlines()}
                            result = subprocess.run(['sh', '-c', 'set -eu\n' +
                                script.replace(stage + '/', str(local_stage) + '/')],
                                capture_output=True, text=True, timeout=30)
                            if result.returncode:
                                raise subprocess.CalledProcessError(result.returncode, 'sh',
                                                                    result.stdout, result.stderr)
                        elif script.startswith('pm path '):
                            return 'package:/product/app/Termux/Termux.apk'
                        elif script.startswith('pm install '):
                            self.packages.append(script)
                            raise PackageInstallReached()
                        return ''

                device = Device()
                args = argparse.Namespace(manifest_sha256=digest(payload / 'manifest.json'))
                expected_error = subprocess.CalledProcessError if corrupt else PackageInstallReached
                with self.assertRaises(expected_error) as caught:
                    standalone._install(args, device, payload, manifest, {})
                expected = {stage + '/' + name for name in names}
                self.assertEqual(device.checked, expected)
                self.assertEqual(device.pushed, expected | {stage + '/manifest.json'})
                self.assertEqual({path.name for path in local_stage.iterdir()}, names | {'manifest.json'})
                if corrupt:
                    self.assertIn('build-manifest.json', caught.exception.stdout)
                    self.assertEqual(device.packages, [])
                else:
                    self.assertEqual(device.packages, [f'pm install -r {stage}/rungic.apk'])


class BootCompatibilityTests(unittest.TestCase):
    # covers: install.standalone-install/E5
    def test_old_product_caller_selects_managed_release_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            managed = root / 'rungic-install'
            payload = managed / 'payload'
            payload.mkdir(parents=True)
            legacy = root / 'rungic-install-legacy'
            legacy.mkdir()
            (legacy / 'firstboot.sh').write_text('echo legacy\n')
            (payload / 'firstboot.sh').write_text('echo managed\n')
            script = Path(__file__).with_name('rungic-install-boot-dispatch.sh').read_text()
            script = script.replace('/system/bin/sh', '/bin/sh').replace('/data/adb', str(root))
            def invoke():
                return subprocess.run(['/bin/sh'], input=script, text=True, capture_output=True)
            self.assertEqual(invoke().stdout.strip(), 'legacy')
            (managed / 'active.env').write_text('RELEASE_ID=test.1\n')
            self.assertEqual(invoke().stdout.strip(), 'managed')
            (payload / 'firstboot.sh').unlink()
            failed = invoke()
            self.assertNotEqual(failed.returncode, 0)
            self.assertNotIn('legacy', failed.stdout)



if __name__ == '__main__':
    unittest.main()
