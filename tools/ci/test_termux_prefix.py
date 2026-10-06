"""Offline archive boundaries; no repository access or Android execution."""
import hashlib
import io
import contextlib
import functools
import os
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest import mock

import build_termux_prefix as builder


class BootstrapSeed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def apk(self, extra=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            z.comment = b'official-style-comment'
            for name, data in {'bin/bash': b'binary', 'lib/apt/methods/https': b'binary',
                               'lib/libc++.so': b'library', 'etc/termux/bootstrap/termux-bootstrap-second-stage.sh': b'script',
                               'SYMLINKS.txt': 'bash←./bin/sh\n'}.items(): z.writestr(name, data)
            if extra: z.writestr(*extra)
        apk = self.root / 'termux.apk'
        with zipfile.ZipFile(apk, 'w') as z:
            z.writestr('lib/arm64-v8a/libtermux-bootstrap.so', b'ELF-prefix' + stream.getvalue() + b'ELF-tail')
        return apk, hashlib.sha256(apk.read_bytes()).hexdigest()

    def test_official_installer_permissions_and_symlinks_are_preserved(self):
        apk, checksum = self.apk(); prefix = self.root / 'usr'
        result = builder.extract_bootstrap(apk, prefix, checksum)
        self.assertTrue((prefix / 'bin/bash').stat().st_mode & 0o100)
        self.assertTrue((prefix / 'lib/apt/methods/https').stat().st_mode & 0o100)
        self.assertTrue((prefix / 'etc/termux/bootstrap/termux-bootstrap-second-stage.sh').stat().st_mode & 0o100)
        self.assertFalse((prefix / 'lib/libc++.so').stat().st_mode & 0o100)
        self.assertEqual((prefix / 'bin/sh').readlink(), Path('bash'))
        self.assertEqual((prefix / 'bin/sh').read_bytes(), b'binary')
        self.assertEqual(result['symlinks'], 1)

    # covers: install.standalone-install
    # Host seed composition only; Android initialization remains untested here.
    def test_caller_umask_does_not_change_composed_archive(self):
        apk, checksum = self.apk(('var/lib/dpkg/status', b''))
        package = self.root / 'package'
        control = package / 'DEBIAN'; control.mkdir(parents=True)
        (control / 'control').write_text('Package: pulseaudio\nVersion: 1.0\nArchitecture: aarch64\nMaintainer: Test <test@example.invalid>\nDescription: Offline seed fixture\n')
        binary = package / builder.ANDROID_PREFIX.lstrip('/') / 'bin/pulseaudio'
        binary.parent.mkdir(parents=True); binary.write_bytes(b'fixture executable'); binary.chmod(0o755)
        deb = self.root / 'pulseaudio.deb'
        subprocess.run(['dpkg-deb', '--build', '--root-owner-group', str(package), str(deb)],
                       check=True, stdout=subprocess.DEVNULL)
        original_run = builder.run
        original_extract = builder.extract_bootstrap
        def offline_download(command, log):
            if command[0] == 'apt-get':
                if command[-1] == 'pulseaudio':
                    archives = Path(command[2]).parent / 'var/cache/apt/archives'
                    shutil.copyfile(deb, archives / deb.name)
            else:
                original_run(command, log)
        checksums = []
        for mask in (0o022, 0o002, 0o077):
            out = self.root / ('seed-' + oct(mask))
            previous = os.umask(mask)
            try:
                with mock.patch('sys.argv', ['builder', '--apk', str(apk), '--output', str(out)]), \
                     mock.patch.object(builder, 'run', side_effect=offline_download), \
                     mock.patch.object(builder, 'extract_bootstrap', functools.partial(original_extract, expected=checksum)), \
                     contextlib.redirect_stdout(io.StringIO()):
                    builder.main()
            finally:
                os.umask(previous)
            checksums.append(builder.digest(out / 'termux-prefix.tar.gz'))
            self.assertIn('Status: install ok unpacked', (out / 'prefix/usr/var/lib/dpkg/status').read_text())
        self.assertEqual(len(set(checksums)), 1, 'caller umask changed prefix archive bytes')

    def test_wrong_apk_hash_fails_before_extraction(self):
        apk, _ = self.apk(); prefix = self.root / 'usr'
        with self.assertRaisesRegex(ValueError, 'SHA-256'): builder.extract_bootstrap(apk, prefix, '0' * 64)
        self.assertFalse(prefix.exists())

    def test_archive_cannot_write_outside_prefix(self):
        apk, checksum = self.apk(('../escaped', b'bad'))
        with self.assertRaisesRegex(ValueError, 'unsafe archive path'):
            builder.extract_bootstrap(apk, self.root / 'usr', checksum)
        self.assertFalse((self.root / 'escaped').exists())

    def test_later_packages_cannot_write_through_a_prefix_symlink(self):
        prefix = self.root / 'usr'; prefix.mkdir()
        outside = self.root / 'outside'; outside.mkdir()
        (prefix / 'lib').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'directory symlink'):
            builder.destination(prefix, 'lib/injected.so')
        self.assertFalse((outside / 'injected.so').exists())


if __name__ == '__main__': unittest.main()
