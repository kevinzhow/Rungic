#!/usr/bin/env python3
"""Check fail-closed CI2 installation at shell, receipt and filesystem boundaries."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import prepare_rootfs
from build_rootfs_image import check_install_completion, installation_provenance

HERE = Path(__file__).resolve().parent
SOURCE = 'b' * 40

class RootfsInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release = self.root / 'release.json'
        self.release.write_text('{"version":"test.1","packages":{}}\n')
        self.state = self.root / 'var/lib/rungic-apt'
        self.state.mkdir(parents=True)
        self.status = self.root / 'var/lib/dpkg/status'
        self.status.parent.mkdir(parents=True)
        self.status.write_text('installed fixture\n')
        self.receipt = {'schema': 1, 'source_commit': SOURCE, 'release': 'test.1',
                        'release_sha256': hashlib.sha256(self.release.read_bytes()).hexdigest(),
                        'dpkg_status_sha256': hashlib.sha256(self.status.read_bytes()).hexdigest(),
                        'all_installation_steps_completed': True}

    def write_receipt(self):
        (self.state / 'root-install.complete').write_text(json.dumps(self.receipt))

    # covers: install.rungicos-image/E3
    def test_stdin_reader_reproduces_silent_skip_and_new_runner_fails(self):
        reader = self.root / 'maintainer.sh'
        reader.write_text('read -r answer || exit 42\n')
        marker = self.root / 'completed'
        commands = f'bash {reader}\nprintf done > {marker}\n'
        old = subprocess.run(['bash', '-se'], input=commands, text=True, capture_output=True)
        self.assertEqual(old.returncode, 0)
        self.assertFalse(marker.exists(), 'old reader consumes the following command')
        script = self.root / 'install.sh'
        script.write_text('set -eu\n' + commands)
        caller = ('from prepare_rootfs import run; '
                  f'run("bash", {str(script)!r})')
        new = subprocess.run([sys.executable, '-c', caller], input='old inherited input\n',
                             text=True, capture_output=True, env=dict(os.environ, PYTHONPATH=str(HERE)))
        self.assertNotEqual(new.returncode, 0, new.stdout + new.stderr)
        self.assertIn('42', new.stderr)
        self.assertFalse(marker.exists())

    # covers: install.rungicos-image/E3
    def test_missing_receipt_rejects_image(self):
        with self.assertRaisesRegex(ValueError, 'completion'):
            check_install_completion(self.root, self.release, SOURCE)

    # covers: install.rungicos-image/E3
    def test_matching_receipt_accepted(self):
        self.write_receipt()
        check_install_completion(self.root, self.release, SOURCE)

    # covers: install.rungicos-image/E3
    def test_mismatched_or_incomplete_receipt_rejects_image(self):
        for key, value in [('source_commit', 'c' * 40), ('release', 'test.2'),
                           ('release_sha256', '0' * 64), ('dpkg_status_sha256', '0' * 64),
                           ('all_installation_steps_completed', False), ('schema', 0)]:
            with self.subTest(field=key):
                original = self.receipt[key]
                self.receipt[key] = value
                self.write_receipt()
                with self.assertRaisesRegex(ValueError, 'completion'):
                    check_install_completion(self.root, self.release, SOURCE)
                self.receipt[key] = original

    # covers: install.rungicos-image/E3
    def test_later_root_modification_invalidates_receipt(self):
        self.write_receipt()
        self.status.write_text('post-failure ad hoc repair\n')
        with self.assertRaisesRegex(ValueError, 'completion'):
            check_install_completion(self.root, self.release, SOURCE)

    # covers: install.rungicos-image/E3
    def test_failed_attempt_cannot_resume_or_reach_image_builder(self):
        repo = self.root / 'repo'
        repo.mkdir()
        (repo / 'release.json').write_text('{"version":"test.1","packages":{"rungic-plasma-config":"1","gir1.2-gst-plugins-base-1.0":"1+rungic1"}}')
        calls = []
        def fail_guest(*command):
            calls.append(command)
            if command[0] == 'mmdebstrap':
                tree = Path(command[7])
                (tree / 'var/lib').mkdir(parents=True)
            else:
                raise subprocess.CalledProcessError(42, command)
        args = SimpleNamespace(output=self.root/'attempt-1', packages=repo, source_commit=SOURCE,
                               firefox_version='1', suite='test', mirror='http://mirror.invalid',
                               qemu=None, size_gib=16, prepare_only=False)
        with patch('prepare_rootfs.platform.machine', return_value='aarch64'), patch('prepare_rootfs.run', side_effect=fail_guest):
            with self.assertRaises(subprocess.CalledProcessError):
                prepare_rootfs.prepare(args)
            self.assertFalse((args.output/'root-install.complete.json').exists())
            self.assertFalse((args.output/'image').exists())
            count = len(calls)
            with self.assertRaises(FileExistsError):
                prepare_rootfs.prepare(args)
            self.assertEqual(len(calls), count)
            args.output = self.root/'attempt-2'
            with self.assertRaises(subprocess.CalledProcessError):
                prepare_rootfs.prepare(args)
        self.assertTrue((self.root/'attempt-1/prepared-root').is_dir())
        self.assertTrue((self.root/'attempt-2/prepared-root').is_dir())
        self.assertFalse(any('build_rootfs_image.py' in str(command) for command in calls))

    # covers: install.rungicos-image/E3
    @unittest.skipUnless(__import__('shutil').which('apt-get'), 'APT parser unavailable')
    def test_bootstrap_source_format_is_accepted_by_real_apt(self):
        repo = self.root / 'repo'
        repo.mkdir()
        (repo / 'release.json').write_text('{"version":"test.1","packages":{"rungic-plasma-config":"1","gir1.2-gst-plugins-base-1.0":"1+rungic1"}}')
        def stop_at_guest(*command):
            if command[0] == 'mmdebstrap':
                (Path(command[7]) / 'var/lib').mkdir(parents=True)
            else:
                raise subprocess.CalledProcessError(42, command)
        args = SimpleNamespace(output=self.root/'attempt', packages=repo, source_commit=SOURCE,
                               firefox_version='1', suite='test', mirror='http://mirror.invalid',
                               qemu=None, size_gib=16, prepare_only=False)
        with patch('prepare_rootfs.platform.machine', return_value='aarch64'), patch('prepare_rootfs.run', side_effect=stop_at_guest):
            with self.assertRaises(subprocess.CalledProcessError):
                prepare_rootfs.prepare(args)
        state = args.output/'prepared-root/var/lib/rungic-apt'
        self.assertNotIn('gir1.2-gst-plugins-base-1.0', (state/'runtime-packages.txt').read_text().splitlines(),
                         'a later unversioned argument must not override an exact release pin')
        self.assertIn('gir1.2-gst-plugins-base-1.0=1+rungic1', (state/'exact-packages.txt').read_text().splitlines())
        sources = list(state.glob('bootstrap.*'))
        self.assertEqual(len(sources), 1)
        result = subprocess.run(['apt-get', '-o', f'Dir::Etc::sourcelist={sources[0]}',
                                 '-o', 'Dir::Etc::sourceparts=-', 'indextargets'],
                                capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stderr)

    # covers: install.rungicos-image/E3
    def test_configuration_is_package_owned(self):
        script = (HERE/'install_rootfs.sh').read_text()
        orchestration = (HERE/'prepare_rootfs.py').read_text()
        self.assertNotIn('/etc/apt', script + orchestration)
        config_install = script.index('--no-install-recommends "$config"')
        remaining_install = script.index('--no-install-recommends "${release_packages[@]}"')
        self.assertLess(config_install, remaining_install)

    # covers: install.rungicos-image/E3
    def test_missing_receipt_stops_real_image_cli_before_writing_output(self):
        image = self.root/'image/rootfs.img'
        result = subprocess.run([sys.executable, str(HERE/'build_rootfs_image.py'), '--inside',
               '--root', str(self.root), '--release', str(self.release), '--output', str(image),
               '--firefox-version', '1', '--install-source', SOURCE], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('completion', result.stderr)
        self.assertFalse(image.parent.exists())

    # covers: install.rungicos-image/E3
    def test_image_cli_requires_exactly_one_explicit_provenance_mode(self):
        for flags in ([], ['--install-source', SOURCE, '--unverified-root']):
            image = self.root / 'image/rootfs.img'
            result = subprocess.run([sys.executable, str(HERE/'build_rootfs_image.py'), '--inside',
                '--root', str(self.root), '--release', str(self.release), '--output', str(image),
                '--firefox-version', '1', *flags], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(image.parent.exists())

    # covers: install.rungicos-image/E3
    def test_report_provenance_matches_receipt_and_unverified_rejects_stale_receipt(self):
        self.assertIsNone(installation_provenance(self.root, self.release, None))
        self.write_receipt()
        self.assertEqual(installation_provenance(self.root, self.release, SOURCE),
            {'source_commit': SOURCE, 'sha256': hashlib.sha256(
                (self.state/'root-install.complete').read_bytes()).hexdigest()})
        with self.assertRaisesRegex(ValueError, 'unverified root'):
            installation_provenance(self.root, self.release, None)

    # covers: install.rungicos-image/E3
    def test_binary_baseline_update_discards_only_copied_receipt_and_selects_unverified(self):
        import build_fingerprinted_rootfs as binary
        import shutil
        self.write_receipt()
        repo = self.root/'updates'; repo.mkdir()
        (repo/'release.json').write_bytes(self.release.read_bytes())
        output = self.root/'new'; calls = []
        def run(*command):
            calls.append(command)
            if command[0] == 'cp':
                shutil.copytree(self.root/'baseline', output/'prepared-root', dirs_exist_ok=True)
            if command[0] == 'unshare':
                self.assertFalse((output/'prepared-root/var/lib/rungic-apt/root-install.complete').exists())
        baseline = self.root/'baseline'; baseline.mkdir()
        shutil.copytree(self.state, baseline/'var/lib/rungic-apt')
        argv = ['binary', '--base-root', str(baseline), '--packages', str(repo),
                '--qemu', '/unused-qemu', '--output', str(output), '--firefox-version', '1']
        with patch.object(sys, 'argv', argv), patch.object(binary, 'run', side_effect=run), \
             patch.object(binary, 'inventory', return_value={'packages': {}}):
            binary.main()
        self.assertTrue((baseline/'var/lib/rungic-apt/root-install.complete').exists())
        self.assertIn('--unverified-root', calls[-1])
        with patch.object(binary.subprocess, 'run') as invoke:
            binary.run('true')
            self.assertEqual(invoke.call_args.kwargs['stdin'], subprocess.DEVNULL)

if __name__ == '__main__':
    unittest.main()
