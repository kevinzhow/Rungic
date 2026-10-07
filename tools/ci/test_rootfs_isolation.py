#!/usr/bin/env python3
"""Prevent host Python cache paths from reserving logins in release images."""
import tempfile
import unittest
from pathlib import Path
from arm64_chroot import guest_environment
from build_rootfs_image import check_home_layout, check_fresh_account, check_preinstalled_apps

class RootfsIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'home/rungic').mkdir(parents=True)

    def seed_accounts(self):
        (self.root / 'etc').mkdir()
        (self.root / 'etc/passwd').write_text('root:x:0:0::/root:/bin/sh\nrungic:x:1000:1000::/home/rungic:/bin/sh\n')
        (self.root / 'etc/shadow').write_text('root:*:0::::::\nrungic:!:0::::::\n')

    def seed_app_policy(self):
        config = Path('etc/dpkg/dpkg.cfg.d/zz-rungic-apps')
        path = self.root / config
        path.parent.mkdir(parents=True)
        source = Path(__file__).resolve().parents[2] / 'system/config' / config
        path.write_text(source.read_text())
        binary = self.root / 'usr/bin/kwrite'
        binary.parent.mkdir(parents=True)
        binary.write_text('#!/bin/sh\n')
        binary.chmod(0o755)
        entry = self.root / 'usr/share/applications/org.kde.kwrite.desktop'
        entry.parent.mkdir(parents=True)
        entry.write_text('[Desktop Entry]\nName=KWrite\nExec=kwrite\n')

    # covers: install.rungicos-image/E2
    def test_preinstalled_apps_keeps_desktop_and_emoji_fonts(self):
        self.seed_app_policy()
        check_preinstalled_apps(self.root, {'plasma-desktop': (), 'fonts-noto-color-emoji': (), 'kwrite:arm64': ()})

    # covers: install.rungicos-image/E2
    def test_image_without_editor_rejected(self):
        self.seed_app_policy()
        with self.assertRaisesRegex(ValueError, 'editor kwrite missing'):
            check_preinstalled_apps(self.root, {})

    # covers: install.rungicos-image/E2
    def test_editor_without_executable_or_desktop_entry_rejected(self):
        self.seed_app_policy()
        for name in ('usr/bin/kwrite', 'usr/share/applications/org.kde.kwrite.desktop'):
            path = self.root / name
            original = path.read_bytes()
            path.unlink()
            with self.subTest(path=name), self.assertRaisesRegex(ValueError, 'binary or desktop entry missing'):
                check_preinstalled_apps(self.root, {'kwrite': ()})
            path.write_bytes(original)
            path.chmod(0o755)

    # covers: install.rungicos-image/E2
    def test_editor_not_executable_rejected(self):
        self.seed_app_policy()
        (self.root / 'usr/bin/kwrite').chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'binary or desktop entry missing'):
            check_preinstalled_apps(self.root, {'kwrite': ()})

    # covers: install.rungicos-image/E2
    def test_leftover_kate_launcher_rejected(self):
        self.seed_app_policy()
        (self.root / 'usr/share/applications/org.kde.kate.desktop').write_text('[Desktop Entry]\nName=Kate\n')
        with self.assertRaisesRegex(ValueError, 'excluded app files remain'):
            check_preinstalled_apps(self.root, {'kwrite': (), 'kate': ()})

    # covers: install.rungicos-image/E2
    def test_reused_root_with_removed_apps_rejected(self):
        for name in ('angelfish', 'haruna', 'kjournaldbrowser', 'klevernotes', 'marknote'):
            with self.subTest(package=name), self.assertRaisesRegex(ValueError, name):
                check_preinstalled_apps(self.root, {name + ':arm64': ()})

    # covers: install.rungicos-image/E2
    def test_old_config_without_app_policy_rejected(self):
        with self.assertRaisesRegex(ValueError, 'exclusion rules missing'):
            check_preinstalled_apps(self.root, {})

    # covers: install.rungicos-image/E2
    def test_leftover_emoji_shortcut_rejected_even_if_broken(self):
        self.seed_app_policy()
        shortcut = self.root / 'usr/share/kglobalaccel/org.kde.plasma.emojier.desktop'
        shortcut.parent.mkdir(parents=True)
        shortcut.symlink_to('/nonexistent-emoji-entry')
        with self.assertRaisesRegex(ValueError, 'excluded app files remain'):
            check_preinstalled_apps(self.root, {})

    # covers: install.rungicos-image/E1
    def test_clean_identity(self):
        self.seed_accounts()
        check_fresh_account(self.root)

    # covers: install.rungicos-image/E1
    def test_unlocked_account_rejected_without_hash_in_error(self):
        self.seed_accounts()
        (self.root / 'etc/shadow').write_text('root:*:0::::::\nrungic:secret-hash:0::::::\n')
        with self.assertRaisesRegex(ValueError, 'passwords must be locked') as error:
            check_fresh_account(self.root)
        self.assertNotIn('secret-hash', str(error.exception))

    # covers: install.rungicos-image/E1
    def test_old_account_marker_rejected(self):
        self.seed_accounts()
        marker = self.root / 'var/lib/rungic-host/account.json'
        marker.parent.mkdir(parents=True)
        marker.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'completion marker'):
            check_fresh_account(self.root)

    # covers: install.rungicos-image/E1
    def test_auth_credentials_rejected(self):
        self.seed_accounts()
        token = self.root / 'home/rungic/.codex/auth.json'
        token.parent.mkdir()
        token.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'credentials'):
            check_fresh_account(self.root)

    # covers: install.rungicos-image/E1
    def test_extra_personal_account_rejected(self):
        self.seed_accounts()
        with (self.root / 'etc/passwd').open('a') as f:
            f.write('builder:x:1001:1001::/home/builder:/bin/sh\n')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            check_fresh_account(self.root)

    # covers: install.rungicos-image/E1
    def test_clean_template(self):
        check_home_layout(self.root, '/home/rungic')

    # covers: install.rungicos-image/E1
    def test_host_cache_home_rejected(self):
        cache = self.root / 'home/builder/project/.work/cache/python'
        cache.mkdir(parents=True)
        (cache / 'module.pyc').write_bytes(b'cache')
        with self.assertRaisesRegex(ValueError, 'builder'):
            check_home_layout(self.root, '/home/rungic')

    # covers: install.rungicos-image/E1
    def test_legacy_alias(self):
        alias = self.root / 'home/linux'
        for target in ('rungic', '/home/rungic'):
            alias.symlink_to(target)
            check_home_layout(self.root, '/home/rungic')
            alias.unlink()

    # covers: install.rungicos-image/E1
    def test_unrelated_alias_rejected(self):
        (self.root / 'home/linux').symlink_to('/outside')
        with self.assertRaisesRegex(ValueError, 'linux'):
            check_home_layout(self.root, '/home/rungic')

    # covers: install.rungicos-image/E1
    def test_template_cannot_escape_tree(self):
        home = self.root / 'home/rungic'
        home.rmdir()
        home.symlink_to('/tmp')
        with self.assertRaisesRegex(ValueError, 'real directory'):
            check_home_layout(self.root, '/home/rungic')

    # covers: install.rungicos-image/E5
    def test_host_python_settings_removed_without_losing_proxy(self):
        host = {'PYTHONPYCACHEPREFIX': '/home/builder/.cache',
                'PYTHONPATH': '/host/modules', 'PYTHONHOME': '/host/python',
                'HOME': '/home/builder', 'https_proxy': 'http://proxy.invalid:6152'}
        env = guest_environment(host)
        self.assertFalse(any(key.startswith('PYTHON') for key in env))
        self.assertEqual(env['HOME'], '/root')
        self.assertEqual(env['https_proxy'], host['https_proxy'])
        self.assertIn('PYTHONPYCACHEPREFIX', host)

if __name__ == '__main__':
    unittest.main()
