#!/usr/bin/env python3
"""Use cases (docs/96): the desktop comes back after the APK's data is cleared or the APK is
force-stopped and opened again.

Runs the real controller block and session tail with stubbed platform commands. The device
scenarios (pm clear, force stop with a relaunch 30 s later) are recorded in docs/96.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "system/rungic-plasma"
SESSION = ROOT / "desktop/session"


def stub(directory, name, body):
    path = directory / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "log"
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", TEST_LOG=str(self.log))

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []


class StaleWaylandDirectory(Sandbox):
    """Cleared APK data recreates files/tmp; the running container still binds the old one."""

    def run_block(self, apk_inode, bound_inode):
        text = CONTROLLER.read_text()
        block = re.search(r"\n(    startup_phase=wayland-directory\n.*?\n    fi\n)", text, re.S)
        self.assertIsNotNone(block, "wayland-directory block not found in system/rungic-plasma")
        # stat of the APK directory (host side) and, through attach, of the container's bind.
        stub(self.bin, "stat", f'[ -n "{apk_inode}" ] || exit 1; echo {apk_inode}')
        script = (
            'running() { return 0; }\n'
            f'attach() {{ [ -n "{bound_inode}" ] || return 1; echo {bound_inode}; }}\n'
            f'ENTER="{self.bin}/enter"\n' + block.group(1))
        stub(self.bin, "enter", 'echo "enter $*" >> "$TEST_LOG"')
        return subprocess.run(["sh", "-c", "set -eu\n" + script], env=self.env,
                              capture_output=True, text=True, timeout=10)

    # covers: install.app-restart-recovery/E1
    def test_recreated_directory_restarts_the_container(self):
        result = self.run_block("2002", "1001")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), ["enter /usr/bin/lxc-stop -n plasma -t 15"])
        self.assertIn("recreated", result.stderr)

    # covers: install.app-restart-recovery/E2
    def test_same_directory_keeps_the_container(self):
        result = self.run_block("1001", "1001")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])

    # covers: install.app-restart-recovery/E2
    def test_unreadable_side_keeps_the_container(self):
        for apk, bound in (("", "1001"), ("1001", "")):
            result = self.run_block(apk, bound)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.calls(), [], (apk, bound))


class FailedShellOnRetry(Sandbox):
    """plasmashell already failed; the APK's retry sends start."""

    def run_block(self, action, shell_failed):
        text = CONTROLLER.read_text()
        block = re.search(r"\n(    # A plasmashell that failed.*?\n    fi\n)", text, re.S)
        self.assertIsNotNone(block, "failed-plasmashell block not found in system/rungic-plasma")
        env_file = self.root / 'session.env'
        env_file.touch()
        stub(self.bin, 'systemctl', """case "$*" in
          *ActiveState*plasma-plasmashell.service*) echo """ + ('failed' if shell_failed else 'active') + """ ;;
          *ActiveState*) echo active ;;
          *ActiveEnterTimestampMonotonic*) echo 0 ;;
          *is-active*user@1000.service*) exit 0 ;;
          *) exit 2 ;;
        esac""")
        stub(self.bin, 'user-exec', 'exec "$@"')
        query = block.group(1).replace('/run/user/1000/rungic-session.env', str(env_file)).replace(
            '/usr/bin/rungic-plasma-user-exec', str(self.bin / 'user-exec'))
        script = (
            f'action={action}\nrunning() {{ return 0; }}\n'
            'attach() { echo "attach $*" >> "$TEST_LOG"; "$@"; }\n'
            + query + 'echo "action=$action"\n')
        return subprocess.run(["sh", "-c", "set -eu\n" + script], env=self.env,
                              capture_output=True, text=True, timeout=10)

    # covers: install.app-restart-recovery/E3
    def test_failed_shell_turns_start_into_a_session_restart(self):
        result = self.run_block("start", shell_failed=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("action=restart-session", result.stdout)
        self.assertIn("ActiveState plasma-plasmashell.service", "\n".join(self.calls()))

    # covers: install.app-restart-recovery/E3
    def test_running_shell_keeps_start(self):
        result = self.run_block("start", shell_failed=False)
        self.assertIn("action=start", result.stdout)

    # covers: install.app-restart-recovery/E3
    def test_restart_session_is_not_queried(self):
        result = self.run_block("restart-session", shell_failed=True)
        self.assertIn("action=restart-session", result.stdout)
        self.assertEqual(self.calls(), [])


class PreviousSessionStopping(Sandbox):
    """The APK is opened again while the previous session's plasmashell is still stopping."""

    def run_tail(self, stop_polls):
        text = SESSION.read_text()
        tail = text[text.index("# Stopping the previous session"):]
        tail = tail.replace("exec /usr/bin/startplasmamobile", 'echo startplasmamobile >> "$TEST_LOG"')
        counter = self.root / "polls"
        counter.write_text("0")
        # systemctl --user list-jobs: a stop job for the first `stop_polls` calls, then none.
        stub(self.bin, "systemctl", f'''n=$(cat {counter}); echo $((n+1)) > {counter}
echo "systemctl $*" >> "$TEST_LOG"
[ "$n" -lt {stop_polls} ] && echo "812 plasma-plasmashell.service stop running"
[ "$n" -lt {stop_polls} ] && echo "813 app-kclockd@autostart.service start waiting"
exit 0''')
        stub(self.bin, "sleep", ":")
        return subprocess.run(["sh", "-c", "set -eu\n" + tail], env=self.env,
                              capture_output=True, text=True, timeout=10)

    # covers: install.app-restart-recovery/E4
    def test_session_starts_after_the_previous_stop_jobs(self):
        result = self.run_tail(stop_polls=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(calls[-1], "startplasmamobile")
        self.assertEqual(sum(c.startswith("systemctl --user list-jobs") for c in calls), 4)
        self.assertIn("Waited 3x0.1 s", result.stdout)

    # covers: install.app-restart-recovery/E4
    def test_start_jobs_alone_do_not_delay(self):
        result = self.run_tail(stop_polls=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), ["systemctl --user list-jobs --no-legend", "startplasmamobile"])
        self.assertNotIn("Waited", result.stdout)

    # covers: install.app-restart-recovery/E4
    def test_wait_is_bounded(self):
        result = self.run_tail(stop_polls=10**6)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1], "startplasmamobile")
        self.assertEqual(len(self.calls()), 201)


if __name__ == "__main__":
    unittest.main()
