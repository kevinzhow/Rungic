#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""tools/build_on_device.py and the phone's transfer command without the Mac mini or the phone (docs/71):
the default build host, the Mac's system proxy in every container command, the incremental source
sync, and what the phone's restricted key may do on the build host."""
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import build_on_device

ROOT = Path(__file__).resolve().parents[1]

SCUTIL = """<dictionary> {
  ExceptionsList : <array> {
    0 : 127.0.0.1
  }
  HTTPEnable : 1
  HTTPPort : 6152
  HTTPProxy : 127.0.0.1
  HTTPSEnable : 1
  HTTPSPort : 6152
  HTTPSProxy : 127.0.0.1
  SOCKSEnable : 1
  SOCKSPort : 6153
  SOCKSProxy : 127.0.0.1
}
"""


class FakeMac(build_on_device.MacMini):
    """The Mac mini's ssh as a list of commands; scutil answers with the given system proxy."""

    def __init__(self, scutil=SCUTIL, container=None):
        self.commands, self.scutil, self.container = [], scutil, container

    def ssh(self, command, timeout, check=True, data=None, stdout=subprocess.PIPE):
        self.commands.append(command)
        out = b''
        if command == 'scutil --proxy':
            out = self.scutil.encode()
        elif command.startswith(f'{self.DOCKER} inspect'):
            out = (self.container or '').encode()
        return subprocess.CompletedProcess(command, 0, out, b'')


class ProxyTests(unittest.TestCase):
    # covers: delivery.build-hosts/E6
    def test_every_container_command_carries_the_macs_system_proxy(self):
        mac = FakeMac(container=f'{build_on_device.MacMini.image()} true')
        want = {'http_proxy': 'http://host.docker.internal:6152', 'https_proxy': 'http://host.docker.internal:6152',
                'no_proxy': 'localhost,127.0.0.1'}
        self.assertEqual(mac.proxy(), want)
        mac.run('apt-get update')
        mac.out('true')
        command = mac.commands[-1]
        self.assertTrue(command.startswith(f'{mac.DOCKER} exec -i '), command)
        for key, value in want.items():
            self.assertIn(f'-e {key}={value} ', command)
        # scutil is asked once per session, not per command.
        self.assertEqual(mac.commands.count('scutil --proxy'), 1)

    # covers: delivery.build-hosts/E6
    def test_the_build_image_gets_the_proxy_too(self):
        mac = FakeMac(container='')
        mac.ensure()
        build = next(c for c in mac.commands if ' build -q ' in c)
        self.assertIn('--build-arg http_proxy=http://host.docker.internal:6152 ', build)
        self.assertIn('--build-arg https_proxy=http://host.docker.internal:6152 ', build)

    # covers: delivery.build-hosts/E6
    def test_no_system_proxy_no_flags(self):
        mac = FakeMac(scutil='<dictionary> {\n  HTTPEnable : 0\n  HTTPSEnable : 0\n}\n',
                      container=f'{build_on_device.MacMini.image()} true')
        self.assertEqual(mac.proxy(), {})
        self.assertEqual(mac.exec('true'), f'{mac.DOCKER} exec {mac.CONTAINER} true')


class DefaultHostTests(unittest.TestCase):
    def host_for(self, env):
        seen = []
        with patch.dict(os.environ, env, clear=False), patch.object(sys, 'argv', ['build_on_device.py', 'kwin', 'status']), \
                patch.object(build_on_device, 'status', lambda component: seen.append(build_on_device.host.name) or ''):
            if 'RUNGIC_BUILD_HOST' not in env:
                os.environ.pop('RUNGIC_BUILD_HOST', None)
            build_on_device.main()
        return seen[0]

    # covers: delivery.build-hosts/E1
    def test_the_mac_mini_unless_told_otherwise(self):
        saved = build_on_device.host
        self.addCleanup(setattr, build_on_device, 'host', saved)
        self.assertEqual(self.host_for({}), 'macmini')
        self.assertEqual(self.host_for({'RUNGIC_BUILD_HOST': 'phone'}), 'phone')

    # covers: delivery.build-hosts/E1
    def test_a_mac_mini_build_leaves_the_phone_alone(self):
        # install and divert change the phone's system: refused on the build host (tools/conftest.py
        # fails any test that reaches the phone).
        saved = build_on_device.host
        self.addCleanup(setattr, build_on_device, 'host', saved)
        build_on_device.host = FakeMac()
        for action in (lambda: build_on_device.install('kwin'), lambda: build_on_device.divert('kwin', ['a=/b'])):
            with self.assertRaisesRegex(SystemExit, 'changes the phone'):
                action()


class LocalHost:
    """A build host whose container is this machine: scripts run here with sh, BASE under a temp dir."""
    name, jobs = 'local', 2

    def __init__(self, bin_dir):
        self.env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}'}
        self.steps = None

    def run(self, script, timeout=120, check=True):
        result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, env=self.env)
        if check and result.returncode:
            raise AssertionError(result.stderr)
        return result

    def out(self, script, timeout=120):
        return self.run(script, timeout).stdout

    def put_tar(self, archive, directory):
        Path(directory).mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as tar:
            tar.extractall(directory, filter='tar')

    def background(self, component, steps):
        self.steps = steps


class IncrementalSyncTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'chown').write_text('#!/bin/sh\nexit 0\n')     # the container builds as root; here we are not
        (bin_dir / 'chown').chmod(0o755)
        self.base = self.root / 'rungic-build'
        self.staged = self.root / 'staged'
        for p in (patch.object(build_on_device, 'BASE', str(self.base)),
                  patch.object(build_on_device, 'host', LocalHost(bin_dir)),
                  patch.object(build_on_device, 'stage', self.stage)):
            p.start()
            self.addCleanup(p.stop)

    def stage(self, component):
        """As pq.py source extracts it: every file with a fresh mtime (quilt)."""
        now = time.time()
        for path in self.staged.rglob('*'):
            os.utime(path, (now, now))
        archive = self.root / 'stage.tar'
        with tarfile.open(archive, 'w') as tar:
            tar.add(self.staged / 'src', arcname='src')
            tar.add(self.staged / 'cmake-shims', arcname='cmake-shims')
        return archive

    def write(self, name, text):
        path = self.staged / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    # covers: delivery.build-hosts/E2
    def test_unchanged_files_keep_their_time_and_the_obj_tree_stays(self):
        self.write('src/kept.cpp', 'same\n')
        self.write('src/changed.cpp', 'before\n')
        self.write('src/removed.cpp', 'gone soon\n')
        self.write('src/debian/rules', 'rules\n')
        self.write('cmake-shims/x.cmake', '\n')
        build_on_device.sync('demo')
        src = self.base / 'demo/src'
        obj = src / 'obj-aarch64-linux-gnu/kept.o'
        obj.parent.mkdir()
        obj.write_text('object')
        (src / 'debian/.debhelper').mkdir()
        old = time.time() - 3600
        for path in (src / 'kept.cpp', src / 'changed.cpp'):
            os.utime(path, (old, old))

        time.sleep(0.01)
        self.write('src/changed.cpp', 'after\n')
        (self.staged / 'src/removed.cpp').unlink()
        build_on_device.sync('demo')

        self.assertEqual((src / 'kept.cpp').stat().st_mtime, old, 'an unchanged file keeps its time: make skips it')
        self.assertEqual((src / 'changed.cpp').read_text(), 'after\n')
        self.assertFalse((src / 'removed.cpp').exists())
        self.assertEqual(obj.read_text(), 'object', 'the last build output stays')
        self.assertTrue((src / 'debian/.debhelper').is_dir())
        self.assertFalse((self.base / 'demo/incoming').exists())

        build_on_device.start('demo', 'incremental', 2)
        steps = build_on_device.host.steps
        self.assertIn(f'test -d {src}/obj-aarch64-linux-gnu', steps)
        self.assertIn('debian/rules binary', steps)
        self.assertNotIn('dpkg-buildpackage', steps)

    # covers: delivery.build-hosts/E2
    def test_incremental_build_compiles_make_and_ninja_trees_before_packaging(self):
        for generator in ('make', 'ninja'):
            with self.subTest(generator=generator):
                src = self.base / generator / 'src'
                obj = src / 'obj-aarch64-linux-gnu'
                obj.mkdir(parents=True)
                (src / 'input').write_text('updated source\n')
                (src / 'debian').mkdir()
                rules = src / 'debian/rules'
                rules.write_text('#!/bin/sh\nset -eu\n'
                                 'test "$1" = binary\n'
                                 'cmp input obj-aarch64-linux-gnu/output\n'
                                 'cp obj-aarch64-linux-gnu/output packaged\n')
                rules.chmod(0o755)
                if generator == 'make':
                    (obj / 'Makefile').write_text('output: ../input\n\tcp ../input output\n')
                else:
                    (obj / 'build.ninja').write_text('rule copy\n  command = cp $in $out\n'
                                                   'build output: copy ../input\n')
                build_on_device.start(generator, 'incremental', 2)
                build_on_device.host.run(build_on_device.host.steps)
                self.assertEqual((src / 'packaged').read_text(), 'updated source\n')
                # Reusing the same tree must compile a subsequent source change too.
                time.sleep(0.01)
                (src / 'input').write_text('second revision\n')
                build_on_device.host.run(build_on_device.host.steps)
                self.assertEqual((src / 'packaged').read_text(), 'second revision\n')


class TransferTests(unittest.TestCase):
    """tools/pq/rungic-transfer: the phone's key runs only this on the build host."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = self.root / 'rungic-build'
        self.base.mkdir()
        (self.root / 'secret').write_text('not for the phone\n')
        # The real script, its fixed base moved under the temp directory.
        text = (ROOT / 'tools/pq/rungic-transfer').read_text()
        self.assertIn('\nBASE=/root/rungic-build\n', text)
        self.script = self.root / 'rungic-transfer'
        self.script.write_text(text.replace('\nBASE=/root/rungic-build\n', f'\nBASE={self.base}\n'))

    # covers: delivery.build-hosts/E4
    def test_pruning_preserves_exact_kept_names_with_shell_metacharacters(self):
        pool = self.base / build_on_device.MacMini.DEV_POOL
        pool.mkdir()
        keep = {'libegl-mesa0_26.3~devel_arm64.deb', 'with space.deb', 'literal$(touch CANARY).deb'}
        for name in {*keep, 'obsolete.deb', 'libegl-mesa0_26.2~devel_arm64.deb'}:
            (pool / name).write_text('package')
        mac = FakeMac()
        def run(script, timeout):
            return subprocess.run(['sh', '-eu', '-c', script], check=True,
                                  capture_output=True, text=True, cwd=pool)
        with patch.object(build_on_device, 'BASE', str(self.base)), patch.object(mac, 'run', run):
            mac.prune_kept(keep)
            self.assertEqual({p.name for p in pool.iterdir()}, keep)
            mac.prune_kept(set())
            self.assertEqual(list(pool.iterdir()), [])

    def transfer(self, command, data=b''):
        env = {'PATH': os.environ['PATH'], 'SSH_ORIGINAL_COMMAND': command}
        return subprocess.run(['sh', str(self.script)], input=data, capture_output=True, env=env, cwd=self.root)

    # covers: delivery.build-hosts/E4
    def test_put_and_get_relative_paths_under_the_build_directory(self):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w') as tar:
            info = tarfile.TarInfo('pkg_1_arm64.deb')
            info.size = 4
            tar.addfile(info, io.BytesIO(b'data'))
        put = self.transfer('put dev-pool/in', archive.getvalue())
        self.assertEqual(put.returncode, 0, put.stderr)
        self.assertEqual((self.base / 'dev-pool/in/pkg_1_arm64.deb').read_bytes(), b'data')
        got = self.transfer('get dev-pool/in/pkg_1_arm64.deb')
        self.assertEqual((got.returncode, got.stdout), (0, b'data'))

    # covers: delivery.build-hosts/E4
    def test_everything_else_is_refused(self):
        (self.base / 'f').write_text('x')
        for command in ('get /etc/passwd', f'get {self.root}/secret', 'get ../secret', 'get a/../../secret',
                        'get ..', 'put ../escape', 'put /tmp/x', 'rm -rf f', 'sh -c id', 'get', 'get f extra',
                        '', 'scp -t f', 'get *'):
            with self.subTest(command=command):
                result = self.transfer(command)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(b'not for the phone', result.stdout)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['rungic-build', 'rungic-transfer', 'secret'])
        self.assertEqual((self.base / 'f').read_text(), 'x')


if __name__ == '__main__':
    unittest.main()
