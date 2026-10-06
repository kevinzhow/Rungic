"""Execute real shell scripts in a local filesystem. Do not access ADB or a handset."""
import argparse
from contextlib import nullcontext
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import standalone


class RemovalShell(unittest.TestCase):
    shell = ['bash']
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.adb = self.base / 'data/adb'
        self.home = self.adb / 'rungic-lxc/runtime/var/lib/lxc/plasma/state/home'
        self.home.mkdir(parents=True)
        (self.home / '.private').write_bytes(b'example\x00content')
        (self.home / '.private').chmod(0o640)
        os.setxattr(self.home / '.private', 'user.test', b'original')
        (self.home / 'empty').mkdir()
        (self.home / 'link').symlink_to('/outside/user-file')
        os.link(self.home / '.private', self.home / 'hardlink')
        controller = self.adb / 'rungic-plasma/rungic-plasma'
        controller.parent.mkdir(parents=True)
        controller.write_text('#!/bin/sh\n[ "$1" = stop ] || { echo "Unknown controller action" >&2; exit 1; }\nexit 0\n')
        controller.chmod(0o755)
        for name in ('proc', 'proc/1', 'sys/block', 'product/etc/rungic', 'data/local/tmp'):
            (self.base / name).mkdir(parents=True)
        (self.base / 'proc/mounts').write_text('')
        (self.base / 'proc/self').mkdir()
        (self.base / 'proc/self/mountinfo').write_text('1 0 0:1 / / rw - rootfs rootfs rw\n')
        (self.base / 'proc/1/mountinfo').write_text('1 0 0:1 / / rw - rootfs rootfs rw\n')
        (self.base / 'proc/1/cmdline').write_bytes(b'init\x00')
        (self.base / 'product/etc/rungic/firstboot.sh').write_text('old seed')
        self.unrelated = self.adb / 'rungic-history-backup'
        self.unrelated.write_text('keep')

    def run_shell(self, purge=False, extra='', preview=False, package_kind=None):
        script = standalone.uninstall_root_script(purge=purge, operation_id='test', preview=preview, package_kind=package_kind)
        for prefix in ('/data/adb', '/data/data', '/data/local/tmp', '/proc', '/sys/block', '/product', '/vendor'):
            script = re.sub(r'(?<![\w/])' + re.escape(prefix) + r'\b', str(self.base) + prefix, script)
        script = script.replace(str(self.adb / 'magisk/busybox'), getattr(self, 'busybox', '/usr/bin/busybox'))
        env = dict(os.environ)
        # Android context changes are represented separately from filesystem checks.
        return subprocess.run([*self.shell, '-c', 'set -eu\nchcon() { :; }\n' + extra + script],
                              capture_output=True, text=True, env=env, timeout=30)

    def wfd_fixture(self, source='/adb/rungic-wfd/wfdconfig.xml', mode='propagate'):
        target = self.base / 'vendor/etc/wfdconfig.xml'
        target.parent.mkdir(parents=True)
        target.write_text('vendor original config')
        tables = [self.base / 'proc/1/mountinfo', self.base / 'proc/self/mountinfo']
        for table in tables:
            table.write_text(table.read_text() + f'9 1 0:1 {source} {target} rw shared:51 - f2fs /dev/block/dm-54 rw\n')
        wrapper = self.base / 'busybox-fixture'
        record = self.base / 'umount.log'
        # nsenter/umount are explicit OS boundaries; the generated worker remains real.
        wrapper.write_text('#!/bin/sh\nset -eu\nif [ "$1" = nsenter ]; then\n'
            '  printf "%s\\n" "$*" >> ' + str(record) + '\n'
            '  [ "$2" = -t ] && [ "$3" = 1 ] && [ "$4" = -m ] && [ "$5" = -- ]\n'
            '  [ "$7" = umount ] && [ "$8" = ' + str(target) + ' ]\n'
            '  [ ! -d ' + str(self.base / 'proc/4426') + ' ]\n' +
            ('  exit 1\n' if mode == 'failure' else
             ''.join('  sed -i \'/shared:51/d\' ' + str(t) + '\n' for t in (tables if mode == 'propagate' else tables[:1]))) +
            '  exit 0\nfi\nexec /usr/bin/busybox "$@"\n')
        wrapper.chmod(0o755)
        self.busybox = str(wrapper)
        return target, record

    def test_removal_releases_only_owned_wfd_bind_after_watcher_exit(self):
        # covers: install.standalone-uninstall/E4
        target, record = self.wfd_fixture()
        watcher = self.base / 'proc/4426'
        watcher.mkdir()
        (watcher / 'cmdline').write_bytes(b'app_process\x00/system/bin\x00com.rungic.cast.Main\x00watch\x00')
        (watcher / 'mountinfo').write_text((self.base / 'proc/1/mountinfo').read_text())
        result = self.run_shell(purge=True, extra=f'kill() {{ [ "$1" = -TERM ] && [ "$2" = 4426 ]; rm -rf "{watcher}"; }}\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(record.read_text().splitlines()), 1)
        self.assertEqual(target.read_text(), 'vendor original config')
        self.assertFalse(self.home.exists())
        self.assertNotIn('rungic-wfd', (self.base / 'proc/1/mountinfo').read_text())

    def test_wfd_preview_is_read_only_and_explains_planned_unmount(self):
        # covers: install.standalone-uninstall/E1
        target, record = self.wfd_fixture()
        before = (self.base / 'proc/1/mountinfo').read_text()
        result = self.run_shell(preview=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('check\twfd_config\tPASS\t', result.stdout)
        self.assertIn('执行时会撤销投屏配置的挂载', result.stdout)
        self.assertFalse(record.exists())
        self.assertEqual((self.base / 'proc/1/mountinfo').read_text(), before)
        self.assertTrue(self.home.exists())

    def test_foreign_stacked_unreadable_or_failed_wfd_mount_is_never_deleted_through(self):
        # covers: install.standalone-uninstall/E4
        for mode in ('foreign', 'stacked', 'unreadable', 'empty', 'failure', 'residual'):
            with self.subTest(mode=mode):
                fixture = RemovalShell(); fixture.shell = self.shell; fixture.setUp()
                try:
                    target, record = fixture.wfd_fixture(source='/vendor/original.xml' if mode == 'foreign' else '/adb/rungic-wfd/wfdconfig.xml', mode=mode)
                    table = fixture.base / 'proc/1/mountinfo'
                    if mode == 'stacked': table.write_text(table.read_text() + f'10 1 0:1 /vendor/other.xml {target} rw - f2fs /dev/block/dm-54 rw\n')
                    if mode == 'unreadable': table.unlink()
                    if mode == 'empty': table.write_text('')
                    result = fixture.run_shell(purge=True)
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(fixture.home.exists())
                    self.assertNotIn('deleted\t', result.stdout)
                    self.assertEqual(record.exists(), mode in ('failure', 'residual'))
                    self.assertEqual(target.read_text(), 'vendor original config')
                finally: fixture.doCleanups()

    def test_preview_reports_every_readonly_gate_without_device_mutation(self):
        # covers: install.standalone-uninstall/E1
        proc = self.base / 'proc/123'
        proc.mkdir()
        (proc / 'cmdline').write_bytes(b'com.rungic.plasma.DeviceDaemon 10000 --token fixture-secret\x00')
        (proc / 'mountinfo').write_text('1 0 0:1 / / rw - rootfs rootfs rw\n')
        (self.base / 'proc/mounts').write_text('none ' + str(self.home / 'Shared') + ' none rw 0 0\n')
        (self.base / 'proc/self/mountinfo').write_text('invalid-id 0 0:1 / / rw - rootfs rootfs rw\n')
        mapper = self.base / 'sys/block/dm-0/dm'
        mapper.mkdir(parents=True)
        (mapper / 'name').write_text('rungic-root')
        controller = self.adb / 'rungic-plasma/rungic-plasma'
        controller.write_text('#!/bin/sh\nexit 99\n')
        before = {str(path.relative_to(self.base)): path.lstat().st_ino for path in self.base.rglob('*')}
        result = self.run_shell(preview=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [line.split('\t', 3) for line in result.stdout.splitlines()]
        self.assertEqual([row[1] for row in rows], ['paths', 'processes', 'wfd_config', 'mounts', 'images', 'home'])
        self.assertEqual([row[2] for row in rows], ['PASS', 'BLOCKED', 'PASS', 'BLOCKED', 'BLOCKED', 'UNKNOWN'])
        after = {str(path.relative_to(self.base)): path.lstat().st_ino for path in self.base.rglob('*')}
        self.assertEqual(before, after)
        self.assertIn('com.rungic.plasma.DeviceDaemon 10000', result.stdout)
        self.assertIn(str(self.home / 'Shared'), result.stdout)
        self.assertNotIn('fixture-secret', result.stdout)
        self.assertEqual((self.home / '.private').read_bytes(), b'example\x00content')
        self.assertFalse((self.adb / 'rungic-uninstalling').exists())
        self.assertFalse((self.adb / 'modules').exists())

    def test_preview_rejects_ambiguous_stacked_mount_without_writing(self):
        # covers: install.standalone-uninstall/E1
        table = self.base / 'proc/self/mountinfo'
        table.write_text(table.read_text() + '2 0 0:1 / / rw - rootfs rootfs rw\n')
        result = self.run_shell(preview=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('check\thome\tUNKNOWN\tCannot resolve the home mount.', result.stdout)
        self.assertTrue(self.home.exists())
        self.assertFalse((self.adb / 'rungic-uninstalling').exists())
        self.assertFalse((self.adb / 'rungic-preserved').exists())

    def test_process_disappearance_and_persistent_read_errors(self):
        # covers: install.standalone-uninstall/E4
        # James Bach's proc_race.py cases, against the actual generated worker.
        for kind in ('mountinfo', 'cmdline'):
            for mode in ('healthy', 'exited', 'alive_unreadable'):
                with self.subTest(kind=kind, mode=mode):
                    fixture = RemovalShell()
                    fixture.shell = self.shell
                    fixture.setUp()
                    try:
                        proc = fixture.base / 'proc/123'
                        proc.mkdir()
                        (proc / 'cmdline').write_bytes(b'innocent-background-worker\x00')
                        (proc / 'mountinfo').write_text('1 0 0:1 / / rw - rootfs rootfs rw\n')
                        injected = fixture.base / 'fault-triggered'
                        fault = ''
                        if mode != 'healthy':
                            victim = proc if mode == 'exited' else proc / kind
                            if kind == 'mountinfo':
                                fault = f"grep() {{ case \"${{3-}}\" in '{proc}/mountinfo') touch '{injected}'; command rm -rf -- '{victim}';; esac; command grep \"$@\"; }}\n"
                            else:
                                fault = f"[() {{ if command [ \"${{1-}}\" = -r ] && command [ \"${{2-}}\" = '{proc}/cmdline' ]; then command [ \"$@\"; code=$?; touch '{injected}'; command rm -rf -- '{victim}'; return \"$code\"; fi; command [ \"$@\"; }}\n"
                        result = fixture.run_shell(purge=True, extra=fault)
                        self.assertEqual(injected.exists(), mode != 'healthy', result.stderr)
                        self.assertEqual(proc.exists(), mode != 'exited')
                        if mode == 'alive_unreadable':
                            self.assertNotEqual(result.returncode, 0)
                            self.assertTrue(fixture.home.exists())
                            self.assertNotIn('deleted\t', result.stdout)
                        else:
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertFalse(fixture.home.exists())
                        self.assertEqual(fixture.unrelated.read_text(), 'keep')
                    finally:
                        fixture.doCleanups()

    def test_mount_parser_rejects_invalid_ids_and_failure_after_intent(self):
        # covers: install.standalone-uninstall/E2
        # James Bach's mount_boundary.py failure and malformed-ID cases.
        for mode in ('malformed_mount_id', 'empty_after_intent'):
            with self.subTest(mode=mode):
                fixture = RemovalShell()
                fixture.shell = self.shell
                fixture.setUp()
                try:
                    table = fixture.base / 'proc/self/mountinfo'
                    intent = fixture.adb / 'rungic-preserved/home-test/intent'
                    original_inode = fixture.home.stat().st_ino
                    extra = ''
                    if mode == 'malformed_mount_id':
                        table.write_text('not-a-mount-id 0 0:1 / / rw - rootfs rootfs rw\n')
                    else:
                        extra = f"sync() {{ if [ -f '{intent}' ]; then : > '{table}'; fi; command sync; }}\n"
                    result = fixture.run_shell(extra=extra)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertTrue(fixture.home.exists())
                    self.assertEqual(fixture.home.stat().st_ino, original_inode)
                    self.assertTrue((fixture.adb / 'rungic-lxc').exists())
                    self.assertFalse((intent.parent / 'home').exists())
                    self.assertNotIn('deleted\t', result.stdout)
                    self.assertEqual((fixture.home / '.private').read_bytes(), b'example\x00content')
                    self.assertEqual(os.getxattr(fixture.home / '.private', 'user.test'), b'original')
                finally:
                    fixture.doCleanups()

    def test_default_preserves_nested_home_and_allows_fresh_install_guard(self):
        # covers: install.standalone-uninstall/E2
        original_inode = self.home.stat().st_ino
        result = self.run_shell()
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = self.adb / 'rungic-preserved/home-test/home'
        self.assertEqual(saved.stat().st_ino, original_inode)
        self.assertEqual((saved / '.private').read_bytes(), b'example\x00content')
        self.assertEqual((saved / '.private').stat().st_mode & 0o777, 0o640)
        self.assertEqual((saved / 'hardlink').stat().st_ino, (saved / '.private').stat().st_ino)
        self.assertEqual(os.readlink(saved / 'link'), '/outside/user-file')
        self.assertTrue((saved / 'empty').is_dir())
        self.assertEqual((saved / '.private').stat().st_uid, os.getuid())
        self.assertEqual((saved / '.private').stat().st_gid, os.getgid())
        self.assertEqual(os.getxattr(saved / '.private', 'user.test'), b'original')
        self.assertFalse((self.adb / 'rungic-lxc').exists())
        self.assertTrue(self.unrelated.exists())
        from test_standalone_guards import ExistingRuntime
        guard = ExistingRuntime('test_an_existing_runtime_is_refused')
        guard.setUp()
        try:
            guard.adb = self.adb
            (self.adb / 'magisk').mkdir()
            (self.adb / 'magisk/busybox').symlink_to('/usr/bin/busybox')
            self.assertFalse(guard.install(), 'An unfinished removal must block installation')
            (self.adb / 'rungic-uninstalling').unlink()
            self.assertTrue(guard.install(), 'The supported install entry must reach staging after final readback')
        finally:
            guard.doCleanups()
        self.assertEqual(self.run_shell().returncode, 0)

    def test_purge_preserves_historical_copies(self):
        # covers: install.standalone-uninstall/E3
        old = self.adb / 'rungic-preserved/home-history/home'
        old.mkdir(parents=True)
        (old / 'file').write_text('history')
        result = self.run_shell(purge=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.home.exists())
        self.assertEqual((old / 'file').read_text(), 'history')

    def test_legacy_controller_needs_only_stop(self):
        # covers: install.standalone-uninstall/E3 install.standalone-uninstall/E4
        for purge in (False, True):
            with self.subTest(purge=purge):
                fixture=RemovalShell()
                fixture.shell=self.shell
                fixture.setUp()
                try:
                    calls=fixture.base/'controller-calls'
                    controller=fixture.adb/'rungic-plasma/rungic-plasma'
                    controller.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "'+str(calls)+'"\n'
                                          '[ "$1" = stop ] || { echo "plasma {open|start|stop|status}" >&2; exit 1; }\n'
                                          'echo "Plasma Mobile stopped"\n')
                    result=fixture.run_shell(purge=purge)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual(calls.read_text().splitlines(),['stop'])
                    self.assertFalse(fixture.home.exists())
                    self.assertFalse(controller.exists())
                    self.assertEqual(fixture.unrelated.read_text(),'keep')
                    if not purge:
                        self.assertEqual((fixture.adb/'rungic-preserved/home-test/home/.private').read_bytes(),b'example\x00content')
                finally:
                    fixture.tmp.cleanup()

    def test_stop_failure_prevents_copy_and_deletion(self):
        # covers: install.standalone-uninstall/E4
        controller = self.adb / 'rungic-plasma/rungic-plasma'
        controller.write_text('#!/bin/sh\nexit 7\n')
        result = self.run_shell()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.home.exists())
        self.assertFalse((self.adb / 'rungic-preserved').exists())
        self.assertFalse((self.adb / 'rungic-uninstalling').exists())

    def test_mount_and_loop_prevent_deletion(self):
        # covers: install.standalone-uninstall/E4
        (self.base / 'proc/mounts').write_text(f'none {self.home}/Shared none rw 0 0\n')
        result = self.run_shell(purge=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.home.exists())
        (self.base / 'proc/mounts').write_text('')
        loop = self.base / 'sys/block/loop0/loop'
        loop.mkdir(parents=True)
        (loop / 'backing_file').write_text(str(self.adb / 'rungic-lxc/images/rootfs.img'))
        self.assertNotEqual(self.run_shell(purge=True).returncode, 0)
        self.assertTrue(self.home.exists())

    def test_moved_record_resumes_after_runtime_deletion_is_interrupted(self):
        # covers: install.standalone-uninstall/E5
        result = self.run_shell(extra='rm() { return 1; }\n')
        self.assertNotEqual(result.returncode, 0)
        saved = self.adb / 'rungic-preserved/home-test/home'
        self.assertTrue(saved.exists(), result.stderr)
        self.assertTrue((self.adb / 'rungic-uninstalling').exists())
        self.assertFalse(self.home.exists())
        self.assertEqual(self.run_shell().returncode, 0)
        self.assertTrue((saved / '.private').exists())

    def test_replaced_preserved_home_inode_prevents_runtime_deletion(self):
        # covers: install.standalone-uninstall/E5
        self.run_shell(extra='rm() { return 1; }\n')
        saved = self.adb / 'rungic-preserved/home-test/home'
        saved.rename(saved.with_name('original-home'))
        saved.mkdir()
        self.assertNotEqual(self.run_shell().returncode, 0)
        self.assertTrue((self.adb / 'rungic-lxc').exists())


    def test_changed_home_inode_after_rename_stops_runtime_deletion(self):
        # covers: install.standalone-uninstall/E2
        result = self.run_shell(extra='mv() { case "$1" in -T) command mv "$@"; command mv "$target" "$target.original"; mkdir "$target";; *) command mv "$@";; esac; }\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('inode', result.stderr)
        self.assertTrue((self.adb / 'rungic-lxc').exists())

    def test_intent_before_rename_resumes_without_overwriting_data(self):
        # covers: install.standalone-uninstall/E5
        result = self.run_shell(extra='mv() { case "$1" in -T) return 18;; *) command mv "$@";; esac; }\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.home.exists())
        self.assertFalse((self.adb / 'rungic-uninstalling').exists())
        self.assertEqual(self.run_shell().returncode, 0)

    def test_rename_done_but_no_moved_record_resumes(self):
        # covers: install.standalone-uninstall/E5
        result = self.run_shell(extra='mv() { case "$1" in */intent.tmp) return 7;; *) command mv "$@";; esac; }\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.home.exists())
        self.assertEqual(self.run_shell().returncode, 0)

    def test_cross_mount_refusal_precedes_any_deletion(self):
        # covers: install.standalone-uninstall/E2
        preserved = self.adb / 'rungic-preserved'
        preserved.mkdir()
        (self.base / 'proc/self/mountinfo').write_text('1 0 0:1 / / rw - rootfs rootfs rw\n'
            + '2 1 0:1 / ' + str(preserved) + ' rw - tmpfs tmpfs rw\n')
        result = self.run_shell()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('永久删除', result.stderr)
        self.assertTrue(self.home.exists())
        self.assertFalse((preserved / 'home-test').exists())
        self.assertFalse((self.adb / 'rungic-uninstalling').exists())

    def test_mapper_process_and_namespace_mount_fail_closed(self):
        # covers: install.standalone-uninstall/E4
        mapper = self.base / 'sys/block/dm-0/dm'
        mapper.mkdir(parents=True)
        (mapper / 'name').write_text('rungic-before')
        self.assertNotEqual(self.run_shell(purge=True).returncode, 0)
        (mapper / 'name').unlink()
        proc = self.base / 'proc/123'
        proc.mkdir()
        (proc / 'mountinfo').write_text(str(self.home) + '/Shared')
        self.assertNotEqual(self.run_shell(purge=True).returncode, 0)
        (proc / 'mountinfo').unlink()
        (proc / 'cmdline').write_bytes(b'/data/adb/rungic-plasma/android-device\x00watch\x00')
        # Translate the fixture as the worker paths are translated.
        (proc / 'cmdline').write_bytes(str(self.adb / 'rungic-plasma/android-device').encode() + b'\x00watch\x00')
        result = self.run_shell(purge=True, extra='sleep() { :; }\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.home.exists())

    def test_home_symlink_and_record_collision_never_follow_external_data(self):
        # covers: install.standalone-uninstall/E2
        outside = self.base / 'external'
        outside.mkdir()
        (outside / 'file').write_text('private')
        import shutil
        shutil.rmtree(self.home)
        self.home.symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(self.run_shell(purge=True).returncode, 0)
        self.assertEqual((outside / 'file').read_text(), 'private')

    def test_unexpected_home_type_stops_before_any_data_deletion(self):
        # covers: install.standalone-uninstall/E2
        import shutil
        shutil.rmtree(self.home)
        self.home.write_text('user data in an unexpected layout')
        result = self.run_shell()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.home.read_text(), 'user data in an unexpected layout')
        self.assertFalse((self.adb / 'rungic-uninstalling').exists())

    def test_empty_guard_is_executable_and_never_replays_old_seed(self):
        # covers: install.standalone-uninstall/E6
        result = self.run_shell(purge=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        guard = self.adb / 'modules/rungic-install-compat/system/product/etc/rungic/firstboot.sh'
        self.assertEqual(subprocess.run(['sh', str(guard)], capture_output=True).returncode, 0)
        self.assertTrue((self.adb / 'rungic-uninstalled').exists())
        self.assertTrue((self.base / 'product/etc/rungic/firstboot.sh').exists())


    def test_package_type_survives_deletion_and_legacy_resume_is_upgraded(self):
        # covers: install.standalone-uninstall/E5, install.standalone-uninstall/E7
        pending = self.adb / 'rungic-uninstalling'
        pending.write_text('test:0:-\n')
        result = self.run_shell(package_kind='ordinary')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(pending.read_text().strip(), 'test:0:-:ordinary')
        self.assertEqual((self.adb / 'rungic-uninstalled').read_text().strip(), 'test:ordinary')
        conflict = self.run_shell(package_kind='system')
        self.assertNotEqual(conflict.returncode, 0)
        self.assertEqual(pending.read_text().strip(), 'test:0:-:ordinary')
        self.assertEqual(self.run_shell(package_kind='ordinary').returncode, 0)

    def test_damaged_record_and_missing_preserved_target_fail_closed(self):
        # covers: install.standalone-uninstall/E5
        self.run_shell(extra='rm() { return 1; }\n')
        record = self.adb / 'rungic-preserved/home-test'
        (record / 'home').rename(record / 'saved-outside-target')
        self.assertNotEqual(self.run_shell().returncode, 0)
        self.assertTrue((self.adb / 'rungic-lxc').exists())
        (record / 'intent').write_text('damaged\n')
        self.assertNotEqual(self.run_shell().returncode, 0)
        self.assertTrue((record / 'saved-outside-target/.private').exists())

    def test_shared_inventory_handles_added_installer_path(self):
        # covers: install.standalone-uninstall/E7
        extra = self.adb / 'rungic-added'
        extra.mkdir()
        (extra / 'file').write_text('installed')
        with mock.patch.dict(standalone.PATHS, RUNGIC_ADDED='/data/adb/rungic-added'):
            result = self.run_shell(purge=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(extra.exists())



class RemovalShellAsh(RemovalShell):
    shell = ['/usr/bin/busybox', 'ash']


class PendingRemoval(unittest.TestCase):
    def test_preflight_refusal_allows_the_existing_controller_to_start_again(self):
        # covers: install.standalone-uninstall/E4
        from test_container_control import Controller
        controller = Controller('test_healthy_storage_keeps_the_running_container')
        controller.setUp()
        try:
            root = controller.root
            installed = root / 'data/adb/rungic-plasma/rungic-plasma'
            installed.write_text(controller.script.read_text())
            installed.chmod(0o755)
            mounts = root / 'proc/mounts'
            mounts.parent.mkdir(exist_ok=True)
            mounts.write_text('none ' + str(root / 'data/adb/rungic-lxc/runtime') + ' none rw 0 0\n')
            (root / 'proc/1').mkdir(exist_ok=True)
            (root / 'proc/1/mountinfo').write_text('1 0 0:1 / / rw - rootfs rootfs rw\n')
            (root / 'proc/1/cmdline').write_bytes(b'init\x00')
            script = standalone.uninstall_root_script(operation_id='refusal')
            for prefix in ('/data/adb', '/data/data', '/data/local/tmp', '/proc', '/sys/block', '/product', '/vendor'):
                script = re.sub(r'(?<![\w/])' + re.escape(prefix) + r'\b', str(root) + prefix, script)
            result = subprocess.run(['/usr/bin/busybox', 'ash', '-c', script],
                                    env=controller.env, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('没有删除任何内容', result.stderr)
            self.assertFalse((root / 'data/adb/rungic-uninstalling').exists())
            code, output, error = controller.run_action('start')
            self.assertEqual(code, 0, error)
            self.assertTrue((controller.state / 'running').exists())
        finally:
            controller.doCleanups()

    def test_product_entry_points_refuse_pending_removal(self):
        # covers: install.standalone-uninstall/E4
        repository = Path(__file__).resolve().parents[2]
        sources = {
            'system/rungic-plasma': ('start', 'boot-start', 'restart-session', 'account-prepare', 'account-setup'),
            'system/rungic-runtime': ('start', 'boot', 'watch'),
            'tools/ci/rungic-firstboot.sh': ('',),
            'tools/ci/rungic-install-service.sh': ('',),
            'tools/ci/rungic-install-boot-dispatch.sh': ('',),
        }
        for shell in (['bash'], ['/usr/bin/busybox', 'ash']):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                adb = root / 'adb'
                adb.mkdir()
                pending = adb / 'rungic-uninstalling'
                pending.touch()
                for name, actions in sources.items():
                    script = (repository / name).read_text().replace('/data/adb', str(adb))
                    for action in actions:
                        result = subprocess.run([*shell, '-c', 'id() { echo 0; }\n' + script, 'test', action],
                                                capture_output=True, text=True, timeout=5)
                        self.assertNotEqual(result.returncode, 0, (shell, name, action))
                        self.assertIn('卸载未完成，请重新运行卸载', result.stderr)
                self.assertFalse((adb / 'rungic-lxc').exists(), 'A refused start must not create runtime state')
                # Model an already queued controller call: the marker appears when
                # its existing control lock is acquired, after the first guard.
                for action in ('start', 'boot-start', 'restart-session', 'account-prepare', 'account-setup'):
                    pending.unlink()
                    (adb / 'rungic-plasma').mkdir(exist_ok=True)
                    script = (repository / 'system/rungic-plasma').read_text().replace('/data/adb', str(adb))
                    prelude = 'id() { echo 0; }\nflock() { touch ' + str(pending) + '; }\n'
                    result = subprocess.run([*shell, '-c', prelude + script, 'test', action],
                                            capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 1, (shell, action, result.stderr))
                    self.assertFalse((adb / 'rungic-lxc').exists())


class Preview(unittest.TestCase):
    def test_default_and_purge_preview_never_mutate_device(self):
        # covers: install.standalone-uninstall/E1
        for purge in (False, True):
            device = mock.Mock()
            device.shell.side_effect = ['USB', 'UserInfo{0:Owner:13}', '0', '\n'.join('check\t' + name + '\tPASS\t' for name in ('paths', 'processes', 'wfd_config', 'mounts', 'images', 'home'))]
            with mock.patch.object(standalone, 'Device', return_value=device), \
                 mock.patch.object(standalone, 'uninstall_state', return_value={'paths': {}, 'termux_path': 'termux'}), mock.patch('builtins.print'):
                args = argparse.Namespace(serial='USB', adb_port=5037, adb='adb', purge=purge,
                                          yes_delete=False, report=None)
                result = standalone.uninstall(args)
                self.assertTrue(result['plan_only'])
                self.assertFalse(result['complete'])
                device.push.assert_not_called()
                self.assertEqual(device.shell.call_count, 4)
                device.maintenance.assert_not_called()

    def test_preview_reports_blocked_and_unknown_checks_without_execution(self):
        # covers: install.standalone-uninstall/E1
        response = '\n'.join('check\t' + name + '\t' + state + '\t' + reason for name, state, reason in
                [('paths', 'PASS', ''), ('processes', 'BLOCKED', 'worker active'),
                 ('wfd_config', 'PASS', ''), ('mounts', 'PASS', ''), ('images', 'PASS', ''), ('home', 'UNKNOWN', 'mount read failed')])
        device = mock.Mock()
        device.shell.side_effect = ['USB', 'UserInfo{0:Owner:13}', '0', response]
        with tempfile.TemporaryDirectory() as root:
            args = argparse.Namespace(serial='USB', adb_port=5037, adb='adb', purge=False,
                                      yes_delete=False, report=Path(root) / 'report')
            with mock.patch.object(standalone, 'Device', return_value=device), \
                 mock.patch.object(standalone, 'uninstall_state', return_value={'paths': {}, 'termux_path': 'termux'}), \
                 mock.patch('builtins.print'):
                result = standalone.uninstall(args)
            self.assertEqual(result['would_stop_at'], 'processes')
            self.assertFalse(result['preflight_allowed'])
            self.assertFalse(result['complete'])
            self.assertEqual(result['preflight'][-1]['state'], 'UNKNOWN')
            self.assertEqual(json.loads((args.report / 'report.json').read_text())['would_stop_at'], 'processes')
            self.assertIn('mount read failed', (args.report / 'report.md').read_text())
        device.maintenance.assert_not_called()
        device.push.assert_not_called()
        self.assertEqual(device.shell.call_count, 4)

    def test_wrong_device_reports_failure_without_mutation(self):
        # covers: install.standalone-uninstall/E1
        with tempfile.TemporaryDirectory() as root:
            device = mock.Mock()
            device.shell.return_value = 'OTHER'
            args = argparse.Namespace(serial='USB', adb_port=5037, adb='adb', purge=False,
                                      yes_delete=True, report=Path(root) / 'report')
            with mock.patch.object(standalone, 'Device', return_value=device), self.assertRaises(ValueError):
                standalone.uninstall(args)
            report = json.loads((args.report / 'report.json').read_text())
            self.assertFalse(report['complete'])
            self.assertIn('serial differs', report['error'])
            device.maintenance.assert_not_called()

    def test_failed_readback_keeps_report_and_pending_removal(self):
        # covers: install.standalone-uninstall/E7
        with tempfile.TemporaryDirectory() as root:
            device = mock.Mock()
            device.maintenance.return_value = nullcontext()
            device.shell.side_effect = ['USB', 'UserInfo{0:Owner:13}', '0',
                                        'deleted\t' + standalone.PATHS['RUNGIC_LXC']]
            before = {'paths': {}, 'termux_path': 'termux', 'user_packages': '', 'uninstalled': 'old:system'}
            after = {'paths': {standalone.PATHS['RUNGIC_CONTROLLER']: True},
                     'termux_path': 'termux', 'user_packages': ''}
            args = argparse.Namespace(serial='USB', adb_port=5037, adb='adb', purge=True,
                                      yes_delete=True, report=Path(root) / 'report')
            with mock.patch.object(standalone, 'Device', return_value=device), \
                 mock.patch.object(standalone, 'uninstall_state', side_effect=[before, after, after]), \
                 self.assertRaisesRegex(ValueError, 'incomplete'):
                standalone.uninstall(args)
            report = json.loads((args.report / 'report.json').read_text())
            self.assertFalse(report['complete'])
            self.assertTrue(report['failed'])
            self.assertIn(standalone.PATHS['RUNGIC_LXC'], report['deleted'])
            self.assertIn(standalone.PATHS['RUNGIC_CONTROLLER'], report['not_touched'])
            self.assertNotIn('finish', [step['name'] for step in report['steps']])

    def test_independent_path_readback_failure_never_claims_verified_deletion(self):
        # covers: install.standalone-uninstall/E7
        with tempfile.TemporaryDirectory() as root:
            device = mock.Mock()
            device.maintenance.return_value = nullcontext()
            removed = standalone.PATHS['RUNGIC_LXC']
            device.shell.side_effect = ['USB', 'UserInfo{0:Owner:13}', '0', 'deleted\t' + removed]
            before = {'paths': {}, 'termux_path': 'termux', 'user_packages': '', 'uninstalled': 'old:system'}
            failure = subprocess.CalledProcessError(1, 'path-readback', output='Injected final readback failure')
            args = argparse.Namespace(serial='USB', adb_port=5037, adb='adb', purge=True,
                                      yes_delete=True, report=Path(root) / 'report')
            with mock.patch.object(standalone, 'Device', return_value=device), \
                 mock.patch.object(standalone, 'uninstall_state', side_effect=[before, failure, failure]), \
                 self.assertRaises(subprocess.CalledProcessError):
                standalone.uninstall(args)
            report = json.loads((args.report / 'report.json').read_text())
            entry = next(entry for entry in report['path_results'] if entry['path'] == removed)
            self.assertEqual(entry['operation'], 'deleted')
            self.assertEqual(entry['readback'], 'unknown')
            self.assertFalse(report['complete'])
            self.assertIn('readback_error', report)
            self.assertNotIn('finish', [step['name'] for step in report['steps']])
            markdown = (args.report / 'report.md').read_text()
            self.assertIn('删除脚本报告已删除 | 读回未完成，未确认', markdown)
            self.assertNotIn('已删除并读回', markdown)

    def test_confirmation_without_report_never_contacts_device(self):
        # covers: install.standalone-uninstall/E1
        args = argparse.Namespace(serial='USB', adb_port=5037, adb='adb', purge=True,
                                  yes_delete=True, report=None)
        with mock.patch.object(standalone, 'Device') as device, self.assertRaises(ValueError):
            standalone.uninstall(args)
        device.assert_not_called()

    def test_multiple_android_users_stop_before_device_mutation(self):
        # covers: install.standalone-uninstall/E1
        device = mock.Mock()
        device.shell.side_effect = ['USB', 'UserInfo{0:Owner:13}\nUserInfo{10:Other:10}']
        args = argparse.Namespace(serial='USB', adb_port=5037, adb='adb', purge=True,
                                  yes_delete=True, report=None)
        with tempfile.TemporaryDirectory() as directory:
            args.report = Path(directory) / 'report'
            with mock.patch.object(standalone, 'Device', return_value=device), self.assertRaisesRegex(ValueError, 'user 0'):
                standalone.uninstall(args)
            self.assertFalse(json.loads((args.report / 'report.json').read_text())['complete'])
            self.assertTrue((args.report / 'report.md').exists())
        self.assertEqual(device.shell.call_count, 2)
        device.maintenance.assert_not_called()

    def test_package_update_removal_targets_only_rungic_and_keeps_failure(self):
        # covers: install.standalone-uninstall/E7
        device = mock.Mock()
        device.maintenance.return_value = nullcontext()
        before = {'paths': {}, 'termux_path': 'termux', 'user_packages': 'package:' + standalone.APP}
        commands = []
        updated = True

        def shell(command, **kwargs):
            nonlocal updated
            commands.append((command, kwargs.get('root', False)))
            if command == 'getprop ro.serialno': return 'USB'
            if command == 'pm list users': return 'UserInfo{0:Owner:13}'
            if command == 'id -u': return '0'
            if 'RUNGIC_APP_DATA_READBACK' in command: return 'CE\tEMPTY\nDE\tEMPTY'
            if command.startswith('pm path '): return 'package:/product/app/Rungic/Rungic.apk'
            if command.startswith('pm list packages --user 0'): return 'package:' + standalone.APP
            if command.startswith('dumpsys'): return f'Packages:\n  Package [{standalone.APP}] (abc):\n    versionCode=54\n    flags=[ SYSTEM ' + ('UPDATED_SYSTEM_APP' if updated else '') + ' ]\n'
            if command.startswith('pm uninstall-system-updates'):
                updated = False
                return 'Success'
            if command.startswith('pm clear'): return 'Success'
            if command.startswith('pm uninstall --user'):
                raise subprocess.CalledProcessError(1, 'adb', output=b'Failure [package busy]')
            return ''

        device.shell.side_effect = shell
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(serial='USB', adb_port=5037, adb='adb', purge=True,
                                      yes_delete=True, report=Path(directory) / 'report')
            with mock.patch.object(standalone, 'Device', return_value=device), \
                 mock.patch.object(standalone, 'uninstall_state', return_value=before), \
                 self.assertRaisesRegex(ValueError, '卸载用户 0'):
                standalone.uninstall(args)
            report = json.loads((args.report / 'report.json').read_text())
            self.assertFalse(report['complete'])
            self.assertIn('package busy', report['steps'][-1]['output'])
            self.assertNotIn('finish', [step['name'] for step in report['steps']])
            self.assertTrue((args.report / 'report.md').exists())
        self.assertIn(('pm uninstall-system-updates ' + standalone.APP, False), commands)
        self.assertNotIn(('pm uninstall-system-updates', False), commands)
        self.assertTrue(all(not root for command, root in commands if command.startswith('pm ')))

    def test_invalid_stage_descriptor_is_not_a_deletion_glob(self):
        # covers: install.standalone-uninstall/E1
        with self.assertRaises(ValueError):
            standalone.uninstall_root_script(stages=['/data/local/tmp/rungic-../private'])


class MaintenanceLock(unittest.TestCase):
    def test_lock_contention_releases_on_eof_and_binds_adb_target(self):
        # covers: install.standalone-uninstall/E4
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            adb = root / 'adb'
            # No handset: execute the exact lease command against local lock files.
            adb.write_text("#!/usr/bin/python3\nimport os,sys\n"
                           "first=sys.stdin.buffer.readline()\ncommand=sys.stdin.buffer.readline().decode()\n"
                           "command=command.replace('/data/adb/magisk/busybox flock','/usr/bin/flock').replace('/system/bin/sh','/bin/sh').replace('/data/adb/',sys.argv[1]+'/')\n"
                           "os.execl('/bin/sh','sh','-c',command)\n")
            adb.chmod(0o755)
            args = argparse.Namespace(adb=str(adb), adb_port=5037, serial='USB')
            device = standalone.Device(args)
            self.assertEqual(device.adb[-4:], ['-P', '5037', '-s', 'USB'])
            device.adb = [str(adb), str(root)]
            with device.maintenance(removal=True):
                with self.assertRaisesRegex(ValueError, 'active'):
                    with device.maintenance():
                        self.fail('Concurrent maintenance acquired the same lock')
            with device.maintenance():
                pass


class InstallationInventory(unittest.TestCase):
    def test_installer_and_fixed_firstboot_paths_have_inventory_owners(self):
        # covers: install.standalone-uninstall/E7
        import inspect
        source = inspect.getsource(standalone.install) + inspect.getsource(standalone._install)
        firstboot = (Path(__file__).resolve().parent / 'rungic-firstboot.sh').read_text()
        literal_paths = set(re.findall(r'/data/adb/[A-Za-z0-9._/-]+', source + firstboot))
        for path in literal_paths:
            if path == '/data/adb/':
                continue  # The fixed host-component loop is checked against its archive below.
            owned = path in standalone.RETAINED or any(path == root or path.startswith(root + '/')
                    for root in [*standalone.PATHS.values(), standalone.COMPAT, standalone.PRESERVED])
            self.assertTrue(owned, 'Installer path has no removal or retention owner: ' + path)
        for variable in standalone.PATHS:
            self.assertNotIn('${' + variable + ':', firstboot, 'First boot must use fixed production paths')

    def test_host_seed_and_casting_services_have_explicit_owners(self):
        # covers: install.standalone-uninstall/E7
        import build_host_seed
        owned = set(standalone.PATHS.values())
        for source, destination, mode in build_host_seed.ANDROID_FILES:
            installed = '/data/adb/' + destination
            self.assertTrue(any(installed == path or installed.startswith(path + '/') for path in owned), installed)
        for source, destination, mode in standalone.cast_payload.FILES:
            if destination.startswith('service.d/'):
                self.assertIn('/data/adb/' + destination, owned)
                self.assertIn('/data/adb/' + destination + '.new', owned)


class HostFlow(unittest.TestCase):
    def test_actual_host_entry_reports_operation_and_observation_separately(self):
        # covers: install.standalone-uninstall/E1, install.standalone-uninstall/E7
        # James Bach's host_flow.py conditions exercise the real worker and host entry.
        import sys
        source = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as root:
            result = subprocess.run([sys.executable, str(source / 'uninstall_host_flow_cases.py'),
                                     str(source.parents[1]), root], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            cases = json.loads((Path(root) / 'results.json').read_text())
            self.assertEqual(len(cases), 8)
            for case in cases:
                self.assertTrue(all(case['checks'].values()), case)
            failed = Path(root) / 'readback_failure'
            report = json.loads((failed / 'report/report.json').read_text())
            removed = standalone.PATHS['RUNGIC_LXC']
            entry = next(item for item in report['path_results'] if item['path'] == removed)
            self.assertEqual(entry, {'path': removed, 'operation': 'deleted', 'readback': 'unknown'})
            self.assertFalse((failed / 'root/data/adb/rungic-lxc').exists())
            self.assertTrue((failed / 'root/data/adb/rungic-uninstalling').is_file())
            self.assertIn('删除脚本报告已删除 | 读回未完成，未确认', (failed / 'report/report.md').read_text())
            marker = Path(root) / 'finish_readback_marker'
            self.assertFalse(json.loads((marker / 'report/report.json').read_text())['complete'])
            self.assertTrue((marker / 'root/data/adb/rungic-uninstalling').is_file())
            normal = json.loads((Path(root) / 'normal/report/report.json').read_text())
            entry = next(item for item in normal['path_results'] if item['path'] == removed)
            self.assertEqual(entry['operation'], 'deleted')
            self.assertEqual(entry['readback'], 'absent')


class ApkVersionSources(unittest.TestCase):
    def dump(self, active_flags='SYSTEM UPDATED_SYSTEM_APP', extra=''):
        return f"""Packages:
  Package [{standalone.APP}] (active):
    codePath=/data/app/current
    versionCode=239 minSdk=31
    versionName=2.39
    flags=[ {active_flags} ]
Hidden system packages:
  Package [{standalone.APP}] (system):
    codePath=/product/app/Rungic
    versionCode=26
    versionName=2.6
    flags=[ SYSTEM ]
{extra}"""

    def test_active_update_and_hidden_base_do_not_mix(self):
        # covers: install.standalone-uninstall/E1
        versions = standalone.removal_apk_versions(self.dump())
        self.assertEqual(versions['active']['version_name'], '2.39')
        self.assertEqual(versions['system']['version_name'], '2.6')
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / 'report.json'
            standalone.write_removal_report(report, {'plan_only': True, 'before': {
                'apk_sources': versions, 'user_packages': 'package:' + standalone.APP}})
            text = report.with_suffix('.md').read_text()
            self.assertIn('用户 0 更新包：2.39（代码 239）', text)
            self.assertIn('系统分区 APK：2.6（代码 26）', text)

    def test_ordinary_apk_does_not_invent_a_system_base(self):
        # covers: install.standalone-uninstall/E1
        versions = standalone.removal_apk_versions(self.dump('HAS_CODE').split('Hidden system packages:')[0])
        self.assertEqual(versions['active']['version_name'], '2.39')
        self.assertIsNone(versions['system'])

    def test_duplicate_package_or_failed_readback_is_unknown(self):
        # covers: install.standalone-uninstall/E1
        active = self.dump().split('Hidden system packages:')[0]
        self.assertIsNone(standalone.removal_apk_versions(active + active)['active'])
        self.assertEqual(standalone.removal_apk_versions('Failure [Binder]'), {'active': None, 'system': None})

    def test_system_apk_without_update_uses_its_active_system_version(self):
        # covers: install.standalone-uninstall/E1
        versions = standalone.removal_apk_versions(self.dump('SYSTEM').split('Hidden system packages:')[0])
        self.assertEqual(versions['system'], versions['active'])
