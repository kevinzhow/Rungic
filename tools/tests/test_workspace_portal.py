"""Actual Wayland protocol and D-Bus activation; only the KDE backend is a test process."""
import os
from pathlib import Path
import select
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
SERVER = r'''
#include <wayland-server.h>
#include <stdio.h>
#include <unistd.h>
int main(void) {
    struct wl_display *display = wl_display_create();
    if (!display || wl_display_add_socket(display, "wayland-test") < 0) return 1;
    puts("listening"); fflush(stdout);
    char command;
    if (read(0, &command, 1) != 1) return 2;
    wl_display_run(display);
    wl_display_destroy(display);
}
'''
BACKEND = r'''
#include <dbus/dbus.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
int main(int argc, char **argv) {
    FILE *record = fopen(argv[1], "w");
    if (!record) return 2;
    fprintf(record, "%d\n%s\n", getpid(), argc > 2 ? argv[2] : ""); fclose(record);
    if (argc > 2) return 17;
    DBusError error; dbus_error_init(&error);
    DBusConnection *bus = dbus_bus_get(DBUS_BUS_SESSION, &error);
    if (!bus || dbus_bus_request_name(bus, "org.freedesktop.impl.portal.desktop.kde", 0, &error) != DBUS_REQUEST_NAME_REPLY_PRIMARY_OWNER) return 3;
    while (dbus_connection_read_write_dispatch(bus, -1)) {}
}
'''


class PortalReadiness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = ('g++', 'cc', 'pkg-config', 'dbus-daemon', 'dbus-send')
        if not all(shutil.which(tool) for tool in required):
            raise unittest.SkipTest('needs native compiler, pkg-config and D-Bus tools')
        if subprocess.run(['pkg-config', '--exists', 'wayland-client', 'wayland-server', 'dbus-1']).returncode:
            raise unittest.SkipTest('needs Wayland client/server and D-Bus development libraries')
        cls.build = tempfile.TemporaryDirectory(prefix='portal-build-')
        root = Path(cls.build.name)
        for name, code, library, compiler in (
                ('server', SERVER, 'wayland-server', 'cc'), ('backend', BACKEND, 'dbus-1', 'cc')):
            source = root / (name + '.c'); source.write_text(code)
            flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', library], text=True))
            subprocess.run([compiler, str(source), *flags, '-o', str(root / name)], check=True)
        flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'wayland-client'], text=True))
        subprocess.run(['g++', '-std=c++20', '-Wall', '-Wextra', '-Werror',
                        str(ROOT / 'agent/workspace/portal.cpp'), *flags, '-o', str(root / 'portal')], check=True)
        cls.portal, cls.server, cls.backend = (str(root / name) for name in ('portal', 'server', 'backend'))

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='portal-rt-'); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.record = self.root / 'backend-started'
        self.env = {**os.environ, 'XDG_RUNTIME_DIR': str(self.root), 'WAYLAND_DISPLAY': 'wayland-test'}
        self.env.pop('WAYLAND_SOCKET', None)

    def launch(self, argv, **kwargs):
        proc = subprocess.Popen(argv, **kwargs)
        def finish():
            if proc.poll() is None: proc.kill()
            proc.communicate(timeout=3)
        self.addCleanup(finish)
        return proc

    def listening(self):
        proc = self.launch([self.server], env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.assertTrue(select.select([proc.stdout], [], [], 3)[0], 'test server did not create socket')
        self.assertEqual(proc.stdout.readline(), b'listening\n')
        return proc

    def gate(self, timeout=1000, backend=None):
        return self.launch([self.portal, '--timeout-ms', str(timeout), '--', backend or self.backend,
                            str(self.record), 'original argument'], env=self.env, stderr=subprocess.PIPE)

    def test_socket_created_and_connected_is_not_yet_ready_then_exec_preserves_pid_and_exit(self):
        # covers: agent.workspaces/E9
        server = self.listening(); proc = self.gate()
        time.sleep(0.15)
        self.assertIsNone(proc.poll())
        self.assertFalse(self.record.exists(), 'backend launched while compositor had not dispatched')
        server.stdin.write(b's'); server.stdin.flush()
        self.assertEqual(proc.wait(3), 17)
        self.assertEqual(self.record.read_text().splitlines(), [str(proc.pid), 'original argument'])

    def test_unresponsive_listener_times_out_without_launching_backend(self):
        # covers: agent.workspaces/E9
        self.listening(); started = time.monotonic(); proc = self.gate(timeout=200)
        _, error = proc.communicate(timeout=2)
        self.assertEqual(proc.returncode, 1); self.assertFalse(self.record.exists())
        self.assertIn(b'Wayland did not respond within 200 ms', error)
        self.assertLess(time.monotonic() - started, 1)

    def test_missing_socket_can_arrive_before_deadline(self):
        # covers: agent.workspaces/E9
        proc = self.gate(); time.sleep(0.1); self.assertFalse(self.record.exists())
        server = self.listening(); server.stdin.write(b's'); server.stdin.flush()
        self.assertEqual(proc.wait(3), 17)

    def test_dead_compositor_and_missing_backend_fail_without_retry(self):
        # covers: agent.workspaces/E9
        server = self.listening(); proc = self.gate(); time.sleep(0.1)
        server.kill(); server.wait(3)
        self.assertEqual(proc.wait(2), 1); self.assertFalse(self.record.exists())
        server = self.listening(); server.stdin.write(b's'); server.stdin.flush()
        proc = self.gate(backend=str(self.root / 'missing-backend'))
        _, error = proc.communicate(timeout=2)
        self.assertEqual(proc.returncode, 1); self.assertIn(b'cannot execute backend', error)

    def test_absent_workspace_address_never_defaults_to_user_display(self):
        # covers: agent.workspaces/E9
        self.env.pop('WAYLAND_DISPLAY')
        proc = self.gate(); _, error = proc.communicate(timeout=2)
        self.assertEqual(proc.returncode, 1); self.assertIn(b'address is missing', error)
        self.assertFalse(self.record.exists())

    def test_private_bus_activation_uses_gate_before_owning_backend_name(self):
        # covers: agent.workspaces/E9
        server = self.listening()
        services = self.root / 'services'; services.mkdir()
        source = ROOT / 'agent/workspace/dbus/org.freedesktop.impl.portal.desktop.kde.service.in'
        content = source.read_text().replace('@CMAKE_INSTALL_FULL_LIBEXECDIR@/rungic-workspace-portal',
            f'{self.portal} --timeout-ms 1500').replace('@KDE_PORTAL_EXEC@', f'{self.backend} {self.record}')
        (services / source.name.removesuffix('.in')).write_text(content)
        config = self.root / 'bus.conf'
        config.write_text(f'''<busconfig><type>session</type><listen>unix:tmpdir={self.root}</listen>
<auth>EXTERNAL</auth><servicedir>{services}</servicedir><policy context="default">
<allow own="*"/><allow send_destination="*"/><allow receive_sender="*"/>
</policy></busconfig>''')
        bus = self.launch(['dbus-daemon', '--nofork', '--print-address=1', '--config-file=' + str(config)],
                          env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertTrue(select.select([bus.stdout], [], [], 3)[0])
        address = bus.stdout.readline().decode().strip(); self.assertTrue(address.startswith('unix:'))
        call = self.launch(['dbus-send', '--bus=' + address, '--print-reply', '--reply-timeout=2500',
                           '--dest=org.freedesktop.DBus', '/org/freedesktop/DBus',
                           'org.freedesktop.DBus.StartServiceByName',
                           'string:org.freedesktop.impl.portal.desktop.kde', 'uint32:0'],
                          env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(0.2)
        self.assertIsNone(call.poll()); self.assertFalse(self.record.exists())
        server.stdin.write(b's'); server.stdin.flush()
        out, error = call.communicate(timeout=3)
        self.assertEqual(call.returncode, 0, error.decode()); self.assertIn(b'uint32 1', out)
        self.assertTrue(self.record.exists())

    def test_cross_build_refuses_host_portal_configuration(self):
        # covers: agent.workspaces/E9
        if not shutil.which('cmake'):
            self.skipTest('needs CMake')
        result = subprocess.run(['cmake', '-S', str(ROOT / 'agent/workspace'),
                                 '-B', str(self.root / 'cross-build'), '-DCMAKE_SYSTEM_NAME=Linux'],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('cross-compiling cannot verify', result.stdout + result.stderr)
