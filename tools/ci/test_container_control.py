#!/usr/bin/env python3
"""The Android-side controller (system/rungic-plasma) starting and restarting the container, run
whole in a path sandbox: Android's paths go to a temporary directory and the LXC tools behind
rungic-plasma-enter are a stand-in that keeps the container's state in files (running, what it
bound at start, how many times the session started). Also the LXC configuration it starts with.

Not shown here: the Enforcing SELinux domain and the real cgroup device filter, which only the
phone has (standalone.py's preflight refuses a phone that is not Enforcing, test_standalone.py).
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / 'system/rungic-plasma'
CONFIG = ROOT / 'system/plasma.config'
APP = 'com.rungic.plasma'

# The stand-in for lxc-info/-start/-stop/-attach as rungic-plasma-enter runs them.
ENTER = r'''#!/bin/sh
T=__STATE__
tool=$1
case "$tool" in
  /usr/bin/lxc-info) if [ -f $T/running ]; then echo RUNNING; else echo STOPPED; fi ;;
  /usr/bin/lxc-stop) echo stop >> $T/log; rm -f $T/running ;;
  /usr/bin/lxc-start)
    [ ! -f $T/busy ] || echo OVERLAP >> $T/log
    touch $T/busy
    shift
    echo "start $*" >> $T/log
    sleep 0.5
    # What the container binds when it starts: the APK's socket directory and the shared folder.
    stat -c %i __FILES__/tmp > $T/bound
    rm -f $T/shared-stale
    echo $(( $(cat $T/generation) + 1 )) > $T/generation
    touch $T/running
    rm -f $T/busy ;;
  /usr/bin/lxc-attach)
    shift 4    # -n plasma --
    case "$*" in
      *'test -r /mnt/android-shared/.'*) [ ! -f $T/shared-stale ] ;;
      'stat -c %i /mnt/android-wayland') cat $T/bound ;;
      # Container/storage tests provide an already healthy desktop session; detailed
      # systemd recovery decisions execute the real shell in test_setup/test_session_recovery.
      *'ActiveState rungic-plasma-session.service'*) echo wait ;;
      *is-failed*) exit 1 ;;
      *'systemctl restart rungic-plasma-session.service'*)
        echo session-restart >> $T/log
        echo $(( $(cat $T/generation) + 1 )) > $T/generation ;;
      # The session's compositor and shell: the main pids of its two user units, new with every
      # start or session restart (a workspace's KWin and plasmashell are not among them).
      *'MainPID plasma-kwin_wayland.service'*) g=$(cat $T/generation); echo "${g}1 ${g}2" ;;
      *) echo "attach $*" >> $T/log ;;
    esac ;;
  *) echo "enter $*" >> $T/log ;;
esac
'''


def executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


class Controller(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / 'container'
        self.state.mkdir()
        (self.state / 'generation').write_text('1\n')
        self.files = self.root / f'data/user/0/{APP}/files'
        (self.files / 'tmp').mkdir(parents=True)
        (self.root / 'storage/emulated/0/Android').mkdir(parents=True)
        base = self.root / 'data/adb/rungic-plasma'
        executable(base / 'rungic-plasma-enter',
                   ENTER.replace('__STATE__', str(self.state)).replace('__FILES__', str(self.files)))
        for helper in ('android-audio', 'android-device', 'android-media', 'android-clipboard', 'android-calls', 'rootfs-image'):
            executable(base / helper, f'#!/bin/sh\necho "{helper} $*" >> {self.state}/helpers\n')
        stubs = self.root / 'bin'
        executable(stubs / 'id', '#!/bin/sh\necho 0\n')
        # The APK data directory's SELinux label, as Android's ls -dZ prints it.
        executable(stubs / 'ls', '#!/bin/sh\nif [ "$1" = -dZ ]; then echo "u:object_r:app_data_file:s0:c512,c768 $2"; '
                                 'else exec /bin/ls "$@"; fi\n')
        self.env = dict(os.environ, PATH=f"{stubs}:{os.environ['PATH']}")
        self.devices('/dev/null', '/dev/zero')

    def devices(self, gpu, heap, video=None, encoder=None):
        """The controller with Android's paths in the sandbox; the GPU, DMA heap and video decoder and
        encoder nodes are `gpu`, `heap`, `video` and `encoder` (default: none there)."""
        text = re.sub(r'(?<![\w/])(/data/adb|/data/user/0|/storage/emulated/0|/product/etc/rungic|/dev/memcg|/dev/cpuctl)\b',
                      f'{self.root}\\1', CONTROLLER.read_text())
        text = text.replace('/dev/kgsl-3d0', gpu).replace('/dev/dma_heap/system', heap)
        text = text.replace('/dev/video32', video or str(self.root / 'no-video32'))
        text = text.replace('/dev/video33', encoder or str(self.root / 'no-video33'))
        self.script = self.root / 'rungic-plasma'
        self.script.write_text(text.replace('#!/system/bin/sh', '#!/bin/sh'))

    def control(self, action):
        return subprocess.Popen(['sh', str(self.script), action], env=self.env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def run_action(self, action):
        process = self.control(action)
        out, err = process.communicate(timeout=60)
        return process.returncode, out, err

    def log(self):
        path = self.state / 'log'
        return path.read_text().splitlines() if path.exists() else []

    def running(self):
        (self.state / 'running').touch()
        (self.state / 'bound').write_text(f"{(self.files / 'tmp').stat().st_ino}\n")

    # covers: install.container-base/E3
    def test_overlapping_start_and_restart_start_the_container_once(self):
        # Surface recreation sends start while an explicit restart is under way.
        processes = [self.control('start'), self.control('restart-session')]
        results = [(p.wait(timeout=60), p.stderr.read()) for p in processes]
        for code, err in results:
            self.assertEqual(code, 0, err)
        log = self.log()
        self.assertNotIn('OVERLAP', log)
        self.assertEqual(sum(line.startswith('start ') for line in log), 1, log)

    # covers: install.container-base/E1
    def test_devices_are_granted_by_their_current_numbers(self):
        # After an Android reboot the GPU's character device may have another major:minor.
        for gpu, heap, rules in (('/dev/null', '/dev/zero', ('c 1:3 rw', 'c 1:5 r')),
                                 ('/dev/full', '/dev/random', ('c 1:7 rw', 'c 1:8 r'))):
            self.devices(gpu, heap)
            (self.state / 'running').unlink(missing_ok=True)
            code, _, err = self.run_action('start')
            self.assertEqual(code, 0, err)
            start = [line for line in self.log() if line.startswith('start ')][-1]
            for rule in rules:
                self.assertIn(f'-s lxc.cgroup2.devices.allow={rule}', start)
        # The video decoder and encoder are granted when the phone has them, their absence stops nothing.
        self.devices('/dev/null', '/dev/zero', '/dev/urandom', '/dev/random')
        (self.state / 'running').unlink(missing_ok=True)
        code, _, err = self.run_action('start')
        self.assertEqual(code, 0, err)
        start = [line for line in self.log() if line.startswith('start ')][-1]
        self.assertIn('-s lxc.cgroup2.devices.allow=c 1:9 rw', start)
        self.assertIn('-s lxc.cgroup2.devices.allow=c 1:8 rw', start)
        self.devices('/dev/null', '/dev/zero')
        (self.state / 'running').unlink()
        code, _, err = self.run_action('start')
        self.assertEqual(code, 0, err)
        start = [line for line in self.log() if line.startswith('start ')][-1]
        self.assertEqual(start.count('lxc.cgroup2.devices.allow='), 2, start)
        # A node that is missing or not a character device stops the start: nothing is granted blindly.
        self.devices(str(self.root / 'missing-kgsl'), '/dev/zero')
        (self.state / 'running').unlink()
        code, _, err = self.run_action('start')
        self.assertNotEqual(code, 0)
        self.assertIn('Missing character device', err)
        self.assertEqual(sum(line.startswith('start ') for line in self.log()), 4)

    # covers: install.app-restart-recovery/E5
    def test_remounted_storage_restarts_the_container_and_the_shared_folder_returns(self):
        self.running()
        (self.state / 'shared-stale').touch()      # vold remounted storage: the container's bind is stale
        code, out, err = self.run_action('start')
        self.assertEqual(code, 0, err)
        self.assertIn('Android storage was remounted', err)
        self.assertEqual([line.split()[0] for line in self.log()], ['stop', 'start'])
        self.assertFalse((self.state / 'shared-stale').exists(), 'the new container binds the storage again')
        self.assertTrue((self.root / 'storage/emulated/0/Plasma').is_dir())
        self.assertIn('Plasma Mobile ready', out)

    # covers: install.app-restart-recovery/E5
    def test_healthy_storage_keeps_the_running_container(self):
        self.running()
        code, out, err = self.run_action('start')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.log(), [])
        self.assertIn('Plasma Mobile ready', out)

    # covers: install.app-restart-recovery/E5
    def test_storage_not_back_yet_is_not_bound_empty(self):
        # Before the user unlocks, Android's storage is not there: no folder under an empty mount.
        self.running()
        (self.state / 'shared-stale').touch()
        (self.root / 'storage/emulated/0/Android').rmdir()
        code, _, err = self.run_action('start')
        self.assertNotEqual(code, 0)
        self.assertIn('共享存储尚未就绪', err)
        self.assertEqual(self.log(), ['stop'])
        self.assertFalse((self.root / 'storage/emulated/0/Plasma').exists())

    # covers: install.independent-runtime/E1
    def test_boot_start_without_display_is_idempotent_and_hardware_failure_is_local(self):
        base = self.root / 'data/adb/rungic-plasma'
        (base / 'runtime.enabled').touch()
        executable(self.root / 'bin/chcon', ':')
        executable(base / 'android-device', 'exit 19')
        for _ in range(2):
            code, out, err = self.run_action('boot-start')
            self.assertEqual(code, 0, err)
            self.assertIn('Linux service manager ready', out)
            self.assertIn('Hardware backend failed: android-device', err)
        self.assertEqual(sum(line.startswith('start ') for line in self.log()), 1)
        self.assertFalse(any('session-restart' in line or line == 'stop' for line in self.log()))
        self.assertFalse((self.files / 'tmp/wayland-0').exists())

    # covers: install.independent-runtime/E3
    def test_boot_start_does_not_race_explicit_stop(self):
        base = self.root / 'data/adb/rungic-plasma'
        (base / 'runtime.enabled').touch()
        (base / 'runtime.disabled').touch()
        self.assertEqual(self.run_action('boot-start')[0], 0)
        self.assertEqual(self.log(), [])


class LxcConfig(unittest.TestCase):
    def entries(self, key):
        return [line.split('=', 1)[1].strip() for line in CONFIG.read_text().splitlines()
                if line.split('=', 1)[0].strip() == key]

    # covers: install.container-base/E1
    def test_android_network_without_network_admin_and_only_listed_devices(self):
        self.assertEqual(self.entries('lxc.net.0.type'), ['none'], "the container shares Android's network")
        dropped = set(' '.join(self.entries('lxc.cap.drop')).split())
        self.assertLessEqual({'net_admin', 'net_raw', 'sys_module', 'sys_rawio', 'sys_time', 'mac_admin',
                              'mac_override', 'sys_boot'}, dropped)
        self.assertEqual(self.entries('lxc.cgroup2.devices.deny'), ['a'], 'every device is denied first')
        # Only these, besides the GPU and DMA heap granted at start by their current numbers:
        # null zero full random urandom, tty console ptmx, ptys, fuse, tun.
        self.assertEqual(sorted(self.entries('lxc.cgroup2.devices.allow')), sorted([
            'c 1:3 rwm', 'c 1:5 rwm', 'c 1:7 rwm', 'c 1:8 rwm', 'c 1:9 rwm', 'c 5:0 rwm', 'c 5:1 rwm', 'c 5:2 rwm',
            'c 136:* rwm', 'c 10:229 rwm', 'c 10:200 rwm']))
        binds = [e.split()[0] for e in self.entries('lxc.mount.entry') if e.startswith('/dev/')]
        self.assertEqual(sorted(binds), ['/dev/dma_heap/system', '/dev/kgsl-3d0', '/dev/video32', '/dev/video33'])



class StateProbeWithoutClients(unittest.TestCase):
    # covers: install.independent-runtime/E1
    def test_native_state_query_works_when_client_bind_sources_are_absent(self):
        import shutil
        compiler = shutil.which('cc')
        if not compiler:
            self.skipTest('C compiler required for the native enter boundary')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mocks = root / 'boundary.c'
            mocks.write_text(r'''#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
uid_t __wrap_geteuid(void) { return 0; }
int __wrap_unshare(int flags) { return 0; }
int __wrap_mount(const char *source, const char *target, const char *type,
                 unsigned long flags, const void *data) {
    if (source && (strstr(source, "/storage/") || strstr(source, "/data/user/") || strstr(source, "com.termux"))) {
        errno = ENOENT; return -1;
    }
    return 0;
}
int __wrap_mkdir(const char *path, mode_t mode) { return 0; }
int __wrap_chdir(const char *path) { return 0; }
long __wrap_syscall(long number, ...) { return 0; }
int __wrap_umount2(const char *path, int flags) { return 0; }
int __wrap_execv(const char *path, char *const argv[]) { puts(path); return 0; }
''')
            # The real helper treats a returning execv as failure; replace only
            # the mock's successful exec boundary with process exit.
            mocks.write_text(mocks.read_text().replace('#include <errno.h>', '#include <errno.h>\n#include <stdlib.h>')
                             .replace('puts(path); return 0;', 'puts(path); exit(0);'))
            binary = root / 'enter'
            flags = [f'-Wl,--wrap={name}' for name in
                     ['geteuid', 'unshare', 'mount', 'mkdir', 'chdir', 'syscall', 'umount2', 'execv']]
            subprocess.run([compiler, str(ROOT / 'tools/rungic_plasma_enter.c'), str(mocks), *flags, '-o', str(binary)], check=True)
            status = subprocess.run([str(binary), '/usr/bin/lxc-info', '-n', 'plasma', '-sH'], capture_output=True, text=True)
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(status.stdout.strip(), '/usr/bin/lxc-info')
            for command in ['/usr/bin/lxc-start', '/usr/bin/lxc-attach']:
                result = subprocess.run([str(binary), command], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('bind shared Linux files', result.stderr)

if __name__ == '__main__':
    unittest.main()
