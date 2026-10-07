#!/usr/bin/env python3
"""The standalone installer's two refusals before anything is written (docs/75, docs/91):

- standalone.py install refuses a phone that already has a Rungic runtime; only the same release
  with the same manifest may resume its unfinished install. install() runs with a stand-in device:
  its guard script (the first root script after the preflight) runs in a temporary /data/adb, and
  any later command means the install went on to staging.
- the first-boot script stops before writing the image when /data has less than the whole image
  plus 512 MiB free: the real rungic-firstboot.sh runs in a path sandbox (as test_install_republish)
  with df answering a chosen free space; the sparse writer records whether it was reached.
"""
import argparse
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock

import standalone

HERE = Path(__file__).resolve().parent
FIRSTBOOT = HERE / 'rungic-firstboot.sh'
APP = 'com.rungic.plasma'


def executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


class Staging(Exception):
    """The install got past the guard to its first staging command."""


class ExistingRuntime(unittest.TestCase):
    RELEASE = 'test.1'

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.payload = self.root / 'payload'
        self.payload.mkdir()
        for name in standalone.FILES:
            (self.payload / name).write_text('fixture')
        manifest = {'schema': 1, 'kind': 'rungic-standalone', 'release': self.RELEASE, 'rootfs_bytes': 1024 ** 3,
                    'files': {n: {'bytes': 7, 'sha256': standalone.digest(self.payload / n)} for n in standalone.FILES}}
        (self.payload / 'manifest.json').write_text(json.dumps(manifest))
        self.trusted = standalone.digest(self.payload / 'manifest.json')
        self.adb = self.root / 'data/adb'
        executable(self.adb / 'magisk/busybox', '#!/bin/sh\n')
        executable(self.root / 'debug_ramdisk/magisk', '#!/bin/sh\n')     # Magisk's runtime is up
        bin_dir = self.root / 'bin'
        # df -k /data as Android prints it, with plenty of space: the space check is not under test here.
        executable(bin_dir / 'df', '#!/bin/sh\necho "Filesystem 1K-blocks Used Available Use% Mounted"\n'
                                   'echo "/dev/block/dm-50 200000000 1000 190000000 1% /data"\n')
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")

    def install(self):
        """standalone.install() against the sandbox; True when it went on to staging."""
        test = self
        scripts = []

        class Device:
            def __init__(self, args):
                pass

            def shell(self, script, root=False, timeout=120):
                scripts.append(script)
                if len(scripts) > 1:
                    raise Staging(script)
                # As Device.shell sends it (set -eu first), with /data/adb in the sandbox.
                text = 'set -eu\n' + re.sub(r'(?<![\w/])/data/adb\b', str(test.adb), script)
                text = re.sub(r'(?<![\w/])/debug_ramdisk\b', str(test.root / 'debug_ramdisk'), text)
                result = subprocess.run(['sh', '-c', text], env=test.env, capture_output=True, text=True, timeout=30)
                if result.returncode:
                    raise subprocess.CalledProcessError(result.returncode, 'sh', result.stdout, result.stderr)
                return result.stdout.strip()

            def maintenance(self):
                return nullcontext()

            def push(self, local, remote):
                raise Staging(remote)

        args = argparse.Namespace(payload=self.payload, manifest_sha256=self.trusted)
        with mock.patch.object(standalone, 'Device', Device), mock.patch.object(standalone, 'preflight', lambda d, m: {}):
            try:
                standalone.install(args)
            except Staging:
                return True
            except subprocess.CalledProcessError:
                return False
        raise AssertionError('install() neither staged nor refused')

    def active(self, release, manifest):
        target = self.adb / 'rungic-install'
        target.mkdir(parents=True, exist_ok=True)
        (target / 'active.env').write_text(f'RELEASE_ID={release}\n')
        (target / 'manifest.sha256').write_text(manifest + '\n')

    # covers: install.standalone-install/E4
    def test_a_phone_without_rungic_is_installed(self):
        self.assertTrue(self.install())

    # covers: install.standalone-install/E4
    def test_an_existing_runtime_is_refused(self):
        for runtime in ('rungic-lxc', 'rungic-plasma'):
            with self.subTest(runtime=runtime):
                (self.adb / runtime).mkdir()
                self.assertFalse(self.install())
                (self.adb / runtime).rmdir()

    # covers: install.standalone-install/E4
    def test_only_the_same_release_and_manifest_resume(self):
        # An unfinished install of this release: its runtime directories may already exist.
        (self.adb / 'rungic-lxc').mkdir()
        self.active(self.RELEASE, self.trusted)
        self.assertTrue(self.install())
        self.active('test.2', self.trusted)
        self.assertFalse(self.install(), 'a new release must not replace an installed one')
        self.active(self.RELEASE, '0' * 64)
        self.assertFalse(self.install(), 'the same release name with another manifest is refused')


class FirstBootSpace(unittest.TestCase):
    """The first-boot script on a phone whose runtime is in place, up to writing the rootfs image."""

    ROOTFS_BYTES = 16 * 1024 ** 3

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        stubs = self.root / 'bin'
        self.free = self.root / 'free-kib'
        self.writes = self.root / 'writes'
        for name in ('chcon', 'restorecon', 'pm'):
            executable(stubs / name, '#!/bin/sh\n:\n')
        executable(stubs / 'df', f'#!/bin/sh\necho "Filesystem 1K-blocks Used Available Use% Mounted"\n'
                                 f'echo "/dev/block/dm-50 300000000 1 $(cat {self.free}) 1% /data"\n')
        # busybox flock -n LOCK sh SCRIPT SEED: run the command without the lock.
        executable(self.root / 'data/adb/magisk/busybox', '#!/bin/sh\nshift 3\nexec "$@"\n')
        executable(self.root / 'debug_ramdisk/magisk', '#!/bin/sh\nexit 0\n')     # Magisk's runtime is up
        # Already installed parts the script skips: the Termux prefix and the LXC host.
        executable(self.root / 'data/user/0/com.termux/files/usr/bin/pulseaudio', '#!/bin/sh\n')
        executable(self.root / 'data/adb/rungic-lxc/rungic-lxc-enter', '#!/bin/sh\n')
        executable(self.root / 'data/adb/rungic-plasma/rungic-plasma-enter', '#!/bin/sh\n')
        executable(self.root / 'data/adb/rungic-wfd/install.sh', '#!/bin/sh\nexit 0\n')   # casting in place
        (self.root / f'data/user/0/{APP}/files').mkdir(parents=True)
        self.seed = self.root / 'seed'
        self.seed.mkdir()
        sums = {}
        for name, key in (('host-seed.tar.gz', 'HOST_SEED_SHA256'), ('rootfs.img.gz', 'ROOTFS_GZ_SHA256'),
                          ('termux.apk', 'TERMUX_APK_SHA256'), ('termux-prefix.tar.gz', 'TERMUX_PREFIX_SHA256'),
                          ('rungic.apk', 'RUNGIC_APK_SHA256')):
            (self.seed / name).write_bytes(name.encode())
            sums[key] = hashlib.sha256(name.encode()).hexdigest()
        executable(self.seed / 'rungic-sparse-write', f'#!/bin/sh\necho "$@" >> {self.writes}\ncat > /dev/null\nexit 1\n')
        sums['SPARSE_WRITE_SHA256'] = hashlib.sha256((self.seed / 'rungic-sparse-write').read_bytes()).hexdigest()
        sums.update(ROOTFS_SHA256='0' * 64, ROOTFS_BYTES=str(self.ROOTFS_BYTES), PHONE_HTTP_PROXY='', RELEASE_ID='test.1')
        (self.seed / 'seed.env').write_text(''.join(f"{k}='{v}'\n" for k, v in sums.items()))
        text = re.sub(r'(?<![\w/])(/data/adb|/data/user/0|/product/etc/rungic|/debug_ramdisk)\b', f'{self.root}\\1', FIRSTBOOT.read_text())
        text = text.replace('/system/bin/sh', 'bash').replace('export PATH=', 'export IGNORED_PATH=')
        self.script = self.root / 'firstboot.sh'
        self.script.write_text(text)
        self.env = dict(os.environ, PATH=f"{stubs}:{os.environ['PATH']}")

    def first_boot(self, free_kib):
        self.free.write_text(str(free_kib))
        subprocess.run(['bash', str(self.script), str(self.seed)], env=self.env, capture_output=True, text=True, timeout=60)
        status = self.root / f'data/user/0/{APP}/files/rungic-install.properties'
        return dict(line.split('=', 1) for line in status.read_text().splitlines())

    # covers: install.standalone-install/E9
    def test_too_little_space_stops_before_the_image_is_written(self):
        need = self.ROOTFS_BYTES // 1024 + 512 * 1024
        status = self.first_boot(need - 1)
        self.assertEqual((status['state'], status['phase'], status['error']), ('failed', 'rootfs', 'space'))
        self.assertFalse(self.writes.exists(), 'the image writer must not start')
        images = self.root / 'data/adb/rungic-lxc/images'
        self.assertEqual([p.name for p in images.iterdir()], [], 'no partial image is left on /data')
        log = (self.root / 'data/adb/rungic-firstboot.log').read_text()
        self.assertIn('insufficient image reserve', log)

    # covers: install.standalone-install/E9
    def test_the_whole_image_and_512_mib_are_enough(self):
        need = self.ROOTFS_BYTES // 1024 + 512 * 1024
        status = self.first_boot(need)
        self.assertNotEqual(status['error'], 'space')
        self.assertEqual(self.writes.read_text().split()[1], str(self.ROOTFS_BYTES), 'the writer is reached')


if __name__ == '__main__':
    unittest.main()
