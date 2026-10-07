#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""tools/system_test.py with the Mac mini stood in for (quality/README.md 分层): the working tree as it
is goes to a throwaway container built on the ARM64 build image, the tests run there and nowhere
else, and each test's result is kept under .work/system-tests/."""
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import system_test


class FakeMac:
    """The build host's ssh: records the commands, answers the container run with the given lines."""
    DOCKER = '/usr/local/bin/docker'

    def __init__(self, lines, have_image=True):
        self.lines, self.have_image = lines, have_image
        self.commands, self.uploads = [], {}

    def image(self):
        return 'rungic-arm64-host:0123456789ab'

    def ensure(self):
        self.commands.append('ensure')

    def proxy(self):
        return {'http_proxy': 'http://host.docker.internal:6152'}

    def ssh(self, command, timeout, check=True, data=None, stdout=subprocess.PIPE):
        self.commands.append(command)
        if data is not None:
            self.uploads[command] = data
        out = b''
        if 'image inspect' in command:
            out = b'yes\n' if self.have_image else b''
        elif ' run --rm ' in command:
            out = ''.join(json.dumps(l) + '\n' for l in self.lines).encode() + b'build noise\n'
        return subprocess.CompletedProcess(command, 0, out, b'')


class SystemTestRunTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'repo'
        for name, text in {'tools/system/run-in-container.sh': 'exit 0\n', 'tools/system/Dockerfile': 'FROM x\n',
                           'system/config/etc/dpkg/dpkg.cfg.d/zz-rungic-apps': 'path-exclude=/kate.desktop\n',
                           'tools/system/tests/one.py': '', 'tools/system/tests/two.py': '', 'agent/screen/a.cpp': '',
                           'benchmarks/big.json': '{}', '.gitignore': '/.work/\n*.o\n'}.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        subprocess.run(['git', 'add', '-A'], cwd=self.root, check=True)
        (self.root / 'agent/screen/new.cpp').write_text('not committed yet')
        (self.root / 'agent/screen/built.o').write_text('ignored')
        for name, value in (('ROOT', self.root), ('TESTS', self.root / 'tools/system/tests')):
            p = patch.object(system_test, name, value)
            p.start()
            self.addCleanup(p.stop)

    def run_with(self, mac):
        self.runs = getattr(self, 'runs', 0) + 1      # a record folder per run (they are named by the second)
        with patch.object(system_test.build_on_device, 'MacMini', lambda: mac), patch('builtins.print'), \
                patch.object(system_test, 'RESULTS', self.root / f'.work/system-tests/{self.runs}'):
            return system_test.main(['run'])

    # covers: delivery.system-tests/E1
    def test_the_working_tree_runs_in_a_throwaway_container_and_each_result_is_kept(self):
        mac = FakeMac([{'test': 'one', 'passed': True, 'seconds': 3}, {'test': 'two', 'passed': True, 'seconds': 4}])
        self.assertEqual(self.run_with(mac), 0)
        # The working tree as it is: uncommitted files too, ignored and skipped ones not.
        [archive] = [data for command, data in mac.uploads.items() if 'tar -xzf' in command]
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
            names = sorted(tar.getnames())
        self.assertIn('agent/screen/new.cpp', names)
        self.assertNotIn('agent/screen/built.o', names)
        self.assertNotIn('benchmarks/big.json', names)
        self.assertIn('tools/system/tests/one.py', names)
        # A container of its own, removed afterwards, on the system test image; the source read-only.
        [run] = [c for c in mac.commands if ' run --rm ' in c]
        self.assertRegex(run, r'^/usr/local/bin/docker run --rm .*-v /tmp/rungic-system/src-[0-9a-f]{12}:/src:ro '
                              r'rungic-system:[0-9a-f]{12} sh /src/tools/system/run-in-container.sh one two$')
        self.assertFalse([c for c in mac.commands if 'adb' in c])
        [record] = list((self.root / '.work/system-tests/1').iterdir())
        self.assertEqual(json.loads((record / 'results.json').read_text()),
                         [{'test': 'one', 'passed': True, 'seconds': 3}, {'test': 'two', 'passed': True, 'seconds': 4}])
        self.assertTrue((record / 'stderr.txt').exists())

    # covers: delivery.system-tests/E1
    def test_a_failed_or_missing_result_fails_the_run(self):
        self.assertEqual(self.run_with(FakeMac([{'test': 'one', 'passed': True}, {'test': 'two', 'passed': False,
                                                                                  'error': 'not so'}])), 1)
        self.assertEqual(self.run_with(FakeMac([{'test': 'one', 'passed': True}])), 1)     # two said nothing

    # covers: delivery.system-tests/E1
    def test_the_image_is_built_on_the_build_image_with_the_proxy(self):
        mac = FakeMac([{'test': 'one', 'passed': True}, {'test': 'two', 'passed': True}], have_image=False)
        self.run_with(mac)
        [build] = [c for c in mac.commands if ' build -q ' in c]
        self.assertIn('--build-arg BASE=rungic-arm64-host:0123456789ab ', build)
        self.assertIn('--build-arg http_proxy=http://host.docker.internal:6152 ', build)
        with tarfile.open(fileobj=io.BytesIO(mac.uploads[build])) as tar:
            self.assertEqual(tar.extractfile('rungic-apps').read(), b'path-exclude=/kate.desktop\n')
        # A changed product policy must not reuse an image carrying the previous launchers.
        first = system_test.image(FakeMac([], have_image=True))
        (self.root / 'system/config/etc/dpkg/dpkg.cfg.d/zz-rungic-apps').write_text('path-exclude=/another.desktop\n')
        self.assertNotEqual(first, system_test.image(FakeMac([], have_image=True)))


if __name__ == '__main__':
    unittest.main()
