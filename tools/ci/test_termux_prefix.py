"""Offline archive boundaries; no repository access or Android execution."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

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
