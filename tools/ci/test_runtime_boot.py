#!/usr/bin/env python3
"""Execute the independent boot supervisor against fake Android/LXC boundaries.

The production shell runs whole. No phone, process killing, or host systemd is used.
"""
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\n' + text + '\n')
    path.chmod(0o755)


class Runtime(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.children = []
        self.channels = []
        self.addCleanup(self.stop_children)
        self.base = self.root / 'data/adb/rungic-plasma'
        self.state = self.root / 'data/adb/rungic-lxc/runtime/var/lib/lxc/plasma/state/host/runtime'
        self.state.mkdir(parents=True)
        self.base.mkdir(parents=True)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.enabled = self.base / 'runtime.enabled'
        self.enabled.touch()
        self.configured = self.state.parent / 'account.json'
        self.configured.write_text('{"configured":true,"username":"tester"}')
        (self.root / 'storage/emulated/0/Android').mkdir(parents=True)
        for path, data in [('sys/kernel/random/boot_id', 'boot-test'), ('self/cgroup', '0::/'), ('uptime', '0.0 0.0')]:
            p = self.root / 'proc' / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data)
        self.calls = self.root / 'calls'
        executable(self.bin / 'id', 'echo 0')
        executable(self.bin / 'getprop', 'echo "${TEST_BOOT:-1}"')
        executable(self.bin / 'sleep', '''echo "sleep $*" >> "$TEST_ROOT/calls"
n=$(cat "$TEST_ROOT/ticks" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$TEST_ROOT/ticks"
echo "$((n * 10)).0 0.0" > "$TEST_ROOT/proc/uptime"
if [ "${TEST_MODE:-}" = crash ] && [ -f "$TEST_ROOT/running" ] && [ "$1" = 10 ]; then rm "$TEST_ROOT/running"; fi
if [ "${TEST_TICKS:-0}" -gt 0 ] && [ "$n" -ge "$TEST_TICKS" ]; then rm -f "$TEST_ROOT/data/adb/rungic-plasma/runtime.enabled"; fi''')
        executable(self.base / 'rungic-plasma', '''case "$1" in
 runtime-status) [ "${TEST_MODE:-}" != unknown ] || exit 19; if [ -f "$TEST_ROOT/running" ]; then echo RUNNING; else echo STOPPED; fi;;
 boot-start) echo start >> "$TEST_ROOT/calls"; [ "${TEST_MODE:-}" != fail ] || exit 17; touch "$TEST_ROOT/running";;
 *) echo "unexpected $*" >> "$TEST_ROOT/calls"; exit 99;;
esac''')
        executable(self.root / 'data/adb/magisk/busybox', '''case "$1" in
 timeout) shift 2; exec "$@";;
 setsid) echo "$$" >> "$TEST_ROOT/child-pids"
 echo detached >> "$TEST_ROOT/calls"
 echo ready > "$TEST_ROOT/ready"
 read release < "$TEST_ROOT/release";;
 *) exit 90;;
esac''')
        # The installed root provider (system/root-provider) as on a Magisk phone, with the sandbox's BusyBox.
        (self.base / 'root-provider').write_text(f'RUNGIC_ROOT=magisk\nRUNGIC_BUSYBOX={self.root}/data/adb/magisk/busybox\n')
        script = ROOT / 'system/rungic-runtime'
        text = script.read_text().replace('/system/bin/sh', '/bin/sh')
        text = re.sub(r'(?<![\w/])(/data/adb|/storage/emulated/0|/dev/memcg|/dev/cpuctl|/dev/stune|/sys/fs/cgroup|/proc)\b',
                      str(self.root) + r'\1', text)
        self.script = self.base / 'rungic-runtime'
        self.script.write_text(text)
        self.script.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}', TEST_ROOT=str(self.root))

    def run_action(self, action='watch', **env):
        if action == 'start':
            channels = []
            for name in ['ready', 'release']:
                path = self.root / name
                os.mkfifo(path)
                channel = os.open(path, os.O_RDWR | os.O_NONBLOCK)
                self.channels.append(channel)
                channels.append(channel)
            result = subprocess.run(['sh', str(self.script), action],
                                    env={**self.env, **env}, capture_output=True, text=True, timeout=10)
            self.assertTrue(select.select([channels[0]], [], [], 10)[0], 'The fake child did not report readiness.')
            self.assertEqual(os.read(channels[0], 32), b'ready\n')
            pid = int((self.root / 'child-pids').read_text().splitlines()[-1])
            self.children.append(os.pidfd_open(pid))
            return result
        return subprocess.run(['sh', str(self.script), action], env={**self.env, **env},
                              capture_output=True, text=True, timeout=10)

    def stop_children(self):
        # A pidfd identifies the child even if the numeric PID changes owners.
        try:
            for child in self.children:
                try:
                    signal.pidfd_send_signal(child, signal.SIGTERM)
                    self.assertTrue(select.select([child], [], [], 10)[0], 'The fake child did not exit.')
                finally:
                    os.close(child)
        finally:
            for channel in self.channels:
                os.close(channel)

    def lines(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    # covers: install.independent-runtime/E1
    def test_starts_without_activity_and_keeps_healthy_container(self):
        result = self.run_action(TEST_TICKS='3')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.lines().count('start'), 1)
        self.assertFalse(any('unexpected' in line for line in self.lines()))
        self.assertIn('container-running', (self.state / 'host.log').read_text())
        self.assertTrue((self.root / 'running').exists(), 'stopping supervision must not destroy the desktop')

    # covers: install.independent-runtime/E7 desktop.network/E9
    def test_each_boot_converges_android_settings_before_linux_starts(self):
        # The power-save allowlist (Linux's own network without the app) and the other Android
        # settings come from rungic-converge, once per boot, before the container (docs/122).
        executable(self.base / 'rungic-converge', 'echo "converge $*" >> "$TEST_ROOT/calls"')
        result = self.run_action(TEST_TICKS='3')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line for line in self.lines() if line.startswith('converge')], ['converge apply'],
                         'once per boot, not per tick')
        self.assertLess(self.lines().index('converge apply'), self.lines().index('start'))

    # covers: install.independent-runtime/E7
    def test_a_failed_convergence_still_starts_linux(self):
        executable(self.base / 'rungic-converge', 'exit 1')
        result = self.run_action(TEST_TICKS='3')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.lines().count('start'), 1)
        self.assertIn('converge-failed', (self.state / 'host.log').read_text())

    # covers: install.independent-runtime/E2
    def test_failures_are_bounded_and_diagnostics_survive(self):
        result = self.run_action(TEST_MODE='fail')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.lines().count('start'), 5)
        self.assertEqual([v for v in self.lines() if v.startswith('sleep')],
                         ['sleep 3', 'sleep 12', 'sleep 27', 'sleep 30', 'sleep 30'])
        self.assertEqual((self.state / 'status').read_text().strip(), 'restart-limit')
        log = (self.state / 'host.log').read_text()
        self.assertIn('boot=boot-test start-failed exit=17', log)
        self.assertIn('restart-limit reached', log)

    # covers: install.independent-runtime/E2
    def test_short_running_crashes_do_not_reset_retry_budget(self):
        result = self.run_action(TEST_MODE='crash')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.lines().count('start'), 5)
        self.assertEqual((self.state / 'host.log').read_text().count('container-exit'), 5)

    # covers: install.independent-runtime/E2
    def test_unknown_container_state_never_attempts_a_restart(self):
        (self.root / 'running').touch()
        result = self.run_action(TEST_MODE='unknown', TEST_TICKS='3')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('start', self.lines())
        self.assertEqual((self.state / 'host.log').read_text().count('container-state-unavailable'), 1)

    # covers: install.independent-runtime/E3
    def test_stop_remains_stopped_across_boot(self):
        self.assertEqual(self.run_action('stop').returncode, 0)
        self.assertFalse(self.enabled.exists())
        self.assertEqual(self.run_action('boot').returncode, 0)
        self.assertEqual(self.lines(), [])
        self.assertTrue((self.base / 'runtime.disabled').exists())
        self.assertEqual(self.run_action('start').returncode, 0)
        self.assertFalse((self.base / 'runtime.disabled').exists())

    def test_start_handshake_with_high_file_descriptors(self):
        files = [open(os.devnull) for _ in range(20)]
        try:
            self.assertGreaterEqual(files[-1].fileno(), 20)
            self.test_stop_remains_stopped_across_boot()
        finally:
            for stream in files:
                stream.close()

    # covers: install.independent-runtime/E1
    def test_install_account_and_unlock_are_not_bypassed(self):
        self.configured.write_text('{"configured":false}')
        self.assertEqual(self.run_action().returncode, 0)
        self.assertNotIn('start', self.lines())
        self.assertEqual((self.state / 'status').read_text().strip(), 'account-setup-required')
        self.configured.write_text('{"configured":true}')
        (self.root / 'storage/emulated/0/Android').rmdir()
        self.assertEqual(self.run_action(TEST_TICKS='2').returncode, 0)
        self.assertNotIn('start', self.lines())
        self.assertEqual(self.lines().count('sleep 30'), 2)

    # covers: install.independent-runtime/E2
    def test_app_freezer_cgroup_is_rejected_before_start(self):
        (self.root / 'proc/self/cgroup').write_text('0::/uid_10123/pid_123')
        self.assertEqual(self.run_action().returncode, 1)
        self.assertEqual(self.lines(), [])
        self.assertIn('inherited-app-cgroup', (self.state / 'host.log').read_text())

    # covers: install.independent-runtime/E4
    def test_log_retention_is_bounded(self):
        (self.state / 'host.log').write_text('x' * 65536)
        (self.root / 'running').touch()
        self.assertEqual(self.run_action(TEST_TICKS='1').returncode, 0)
        self.assertEqual((self.state / 'host.previous.log').stat().st_size, 65536)
        self.assertLess((self.state / 'host.log').stat().st_size, 1024)


class ExitRecords(unittest.TestCase):
    # covers: install.independent-runtime/E4
    def test_callbacks_record_only_exit_metadata_in_separate_private_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, HOME=directory, XDG_STATE_HOME=directory,
                       SERVICE_RESULT='signal', EXIT_CODE='killed', EXIT_STATUS='9', SECRET_TEST='must-not-be-logged')
            for unit in ['plasma-kwin_wayland', 'plasma-plasmashell', 'rungic-voice-agent']:
                done = subprocess.run(['sh', str(ROOT / 'system/service-exit'), unit], env=env,
                                      capture_output=True, text=True, timeout=5)
                self.assertEqual(done.returncode, 0, done.stderr)
                path = Path(directory) / 'rungic' / f'{unit}-exits.log'
                self.assertIn('result=signal code=killed status=9', path.read_text())
                self.assertNotIn('must-not-be-logged', path.read_text())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

class CriticalProtection(unittest.TestCase):
    # covers: install.independent-runtime/E6
    def test_only_current_account_main_pids_get_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / 'proc'
            for pid, uid, adj in [(111, 1000, 200), (222, 10001, 200), (333, 1000, -1000), (444, 1000, 200), (555, 1000, 200)]:
                p = proc / str(pid)
                p.mkdir(parents=True)
                (p / 'status').write_text(f'Uid:\t{uid}\t{uid}\t{uid}\t{uid}\nPPid:\t111\n')
                (p / 'oom_score_adj').write_text(str(adj))
            children = proc / '111/task/111/children'
            children.parent.mkdir(parents=True)
            children.write_text('444 555')
            (proc / '444/exe').symlink_to('/usr/bin/kwin_wayland')
            (proc / '555/exe').symlink_to('/usr/bin/ordinary-client')
            p = proc / 'sys/kernel/random/boot_id'
            p.parent.mkdir(parents=True)
            p.write_text('boot-test')
            helper = root / 'user-exec'
            executable(helper, '''case "$*" in
 *plasma-kwin_wayland.service*) pid=111;;
 *plasma-plasmashell.service*) pid=222;;
 *rungic-voice-agent.service*) pid=333;;
 *) exit 1;;
esac
[ "${TEST_ZERO:-0}" != 1 ] || pid=0
case "$*" in *'-P MainPID'*) echo "${TEST_REUSED_PID:-$pid}";;
 *) printf 'MainPID=%s\\nActiveState=active\\nResult=success\\nExecMainCode=0\\nExecMainStatus=0\\nNRestarts=0\\n' "$pid";; esac''')
            text = (ROOT / 'system/runtime-health').read_text().replace('/proc/', str(proc) + '/')
            text = text.replace('/var/lib/rungic-host/runtime', str(root / 'state')).replace('/usr/bin/rungic-plasma-user-exec', str(helper))
            def run(**env):
                return subprocess.run(['sh', '-c', text], env={**os.environ, **env}, capture_output=True, text=True, timeout=10)
            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((proc / '111/oom_score_adj').read_text().strip(), '-250')
            self.assertEqual((proc / '222/oom_score_adj').read_text(), '200')
            self.assertEqual((proc / '333/oom_score_adj').read_text(), '-1000')
            self.assertEqual((proc / '444/oom_score_adj').read_text().strip(), '-250')
            self.assertEqual((proc / '555/oom_score_adj').read_text(), '200')
            initial = (root / 'state/linux.log').read_text()
            self.assertIn('CompositorPIDs=111 444', initial)
            self.assertEqual(run().returncode, 0)
            self.assertEqual((root / 'state/linux.log').read_text(), initial, 'no log flood for unchanged services')
            (proc / '111/oom_score_adj').write_text('200')
            self.assertEqual(run(TEST_REUSED_PID='444').returncode, 0)
            self.assertNotEqual((proc / '111/oom_score_adj').read_text().strip(), '-250', 'stale PID must not receive a write')
            self.assertEqual(run(TEST_ZERO='1').returncode, 0)
            self.assertIn('MainPID=0', (root / 'state/linux.log').read_text(), 'stopped services must still be recorded')


if __name__ == '__main__':
    unittest.main()
