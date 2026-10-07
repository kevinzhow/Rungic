#!/usr/bin/env python3
"""Codex from OpenAI's standalone installation (docs/99): where it is found, the stable-only update
check, and the install command, which is the one `codex update` runs for a standalone install."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'agent/assistant'))
import codex_install  # noqa: E402


class VersionTests(unittest.TestCase):
    # covers: agent.codex-install/E2
    def test_parse(self):
        self.assertEqual(codex_install.parse_version('codex-cli 0.159.2\n'), (0, 159, 2))
        self.assertEqual(codex_install.parse_version('rust-v0.160.0'), (0, 160, 0))
        self.assertIsNone(codex_install.parse_version('rust-v0.160.0-alpha.3'))   # never a pre-release
        self.assertIsNone(codex_install.parse_version(''))
        self.assertEqual(codex_install.version_text((0, 159, 2)), '0.159.2')

    # covers: agent.codex-install/E2
    def test_numeric_order(self):
        self.assertGreater(codex_install.parse_version('0.160.0'), codex_install.parse_version('0.159.12'))


def reply(data):
    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return lambda request, timeout=15: Reply(json.dumps(data).encode())


class LatestTests(unittest.TestCase):
    # covers: agent.codex-install/E2
    def test_stable_tag(self):
        self.assertEqual(codex_install.latest_release(reply({'tag_name': 'rust-v0.159.2'})), (0, 159, 2))

    # covers: agent.codex-install/E2
    def test_prerelease_refused(self):
        with self.assertRaises(ValueError):
            codex_install.latest_release(reply({'tag_name': 'rust-v0.160.0', 'prerelease': True}))
        with self.assertRaises(ValueError):
            codex_install.latest_release(reply({'tag_name': 'rust-v0.160.0-beta.1'}))


class UpdateCheckTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.asked = 0
        self.latest = (0, 160, 0)

    def fetch(self):
        self.asked += 1
        if isinstance(self.latest, Exception):
            raise self.latest
        return self.latest

    def check(self, installed=(0, 159, 2)):
        return codex_install.UpdateCheck(lambda: installed, self.fetch, lambda: self.now)

    # covers: agent.codex-install/E2
    def test_available(self):
        r = self.check().check()
        self.assertEqual((r['installed'], r['latest'], r['available']), ('0.159.2', '0.160.0', True))

    # covers: agent.codex-install/E2
    def test_up_to_date(self):
        self.latest = (0, 159, 2)
        self.assertFalse(self.check().check()['available'])

    # covers: agent.codex-install/E2
    def test_cached_until_forced_or_stale(self):
        c = self.check()
        c.check(); c.check()
        self.assertEqual(self.asked, 1)
        c.check(force=True)
        self.assertEqual(self.asked, 2)
        self.now += codex_install.CHECK_EVERY_S
        c.check()
        self.assertEqual(self.asked, 3)

    # covers: agent.codex-install/E2
    def test_offline_keeps_the_last_answer(self):
        c = self.check()
        c.check()
        self.latest = OSError('network is unreachable')
        r = c.check(force=True)
        self.assertEqual((r['latest'], r['available'], r['error']), ('0.160.0', True, 'network is unreachable'))

    # covers: agent.codex-install/E2
    def test_not_installed_has_nothing_to_update(self):
        r = self.check(installed=None).check()
        self.assertEqual((r['installed'], r['available']), ('', False))


class InstallationTests(unittest.TestCase):
    # covers: agent.codex-install/E4
    def test_standalone_and_command(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {'CODEX_HOME': home}):
                self.assertIsNone(codex_install.standalone())
                self.assertIsNone(codex_install.command())
                binary = Path(home) / 'packages/standalone/current/bin/codex'
                binary.parent.mkdir(parents=True)
                binary.write_text('#!/bin/sh\n')
                binary.chmod(0o755)
                self.assertEqual(codex_install.standalone(), binary)
                with patch.object(codex_install, 'LAUNCHER', str(Path(home) / 'no-launcher')):
                    self.assertEqual(codex_install.command(), str(binary))

    # covers: agent.codex-install/E1
    def test_install_command_is_codex_updates_own(self):
        script = codex_install.install_command()[-1]
        self.assertIn('curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh', script)
        self.assertIn('/etc/profile.d/proxy.sh', script)

    # covers: agent.codex-install/E4
    def test_launcher_runs_the_standalone(self):
        wrapper = (Path(__file__).resolve().parents[2] / 'agent/codex/codex-wrapper').read_text()
        self.assertIn('packages/standalone/current/bin/codex', wrapper)
        self.assertNotIn('/usr/lib/codex', wrapper)


if __name__ == '__main__':
    unittest.main()


# covers: agent.codex-install/E1
def test_installation_check_uses_binary_and_keeps_unreadable_state_unknown(tmp_path):
    with patch.dict(os.environ, {'CODEX_HOME': str(tmp_path)}):
        assert codex_install.installed() is False
        binary = tmp_path / 'packages/standalone/current/bin/codex'
        binary.parent.mkdir(parents=True)
        binary.write_text('not executable yet')
        assert codex_install.installed() is True
        assert codex_install.standalone() is None
        with patch.object(Path, 'stat', side_effect=PermissionError('unreadable')):
            assert codex_install.installed() is None
