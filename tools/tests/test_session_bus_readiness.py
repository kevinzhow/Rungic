"""Startup gates on a private, real session bus; no phone or user's bus."""
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / 'shared/platform/wait-dbus.sh'
NAME = 'com.rungic.ReadinessTest'


class SessionBusReadiness(unittest.TestCase):
    def setUp(self):
        if not shutil.which('dbus-daemon'):
            self.skipTest('requires D-Bus daemon')
        if subprocess.run(['/usr/bin/python3', '-c', 'from gi.repository import Gio'],
                          capture_output=True).returncode:
            self.skipTest('requires system Python Gio')
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.env = {**os.environ, 'XDG_RUNTIME_DIR': self.temp.name}
        self.bus = self.launch(['dbus-daemon', '--session', '--nofork', '--print-address=1'])
        self.assertTrue(select.select([self.bus.stdout], [], [], 3)[0])
        self.env['DBUS_SESSION_BUS_ADDRESS'] = self.bus.stdout.readline().decode().strip()

    def launch(self, args):
        process = subprocess.Popen(args, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def cleanup():
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=3)
        self.addCleanup(cleanup)
        return process

    def own_name(self):
        owner = self.launch(['/usr/bin/python3', '-c', '''
from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
              'RequestName', GLib.Variant('(su)', ('com.rungic.ReadinessTest', 0)), None, 0, 1000, None)
print('owned', flush=True)
GLib.MainLoop().run()
'''])
        self.assertTrue(select.select([owner.stdout], [], [], 3)[0])
        self.assertEqual(owner.stdout.readline(), b'owned\n')
        return owner

    # covers: desktop-mode.audio-follow/E1
    def test_delayed_name_blocks_start_then_same_gate_succeeds(self):
        gate = self.launch(['/bin/sh', str(GATE), NAME, '2'])
        time.sleep(.2)
        self.assertIsNone(gate.poll(), 'service proceeded before its dependency owned the name')
        self.own_name()
        out, err = gate.communicate(timeout=3)
        self.assertEqual(gate.returncode, 0, err.decode())

    # covers: desktop-mode.audio-follow/E1
    def test_missing_name_times_out_and_does_not_start_service(self):
        started = time.monotonic()
        gate = self.launch(['/bin/sh', str(GATE), NAME, '1'])
        _, err = gate.communicate(timeout=3)
        self.assertEqual(gate.returncode, 1)
        self.assertIn(NAME.encode(), err)
        self.assertLess(time.monotonic() - started, 3)

    # covers: desktop-mode.audio-follow/E1
    def test_existing_owner_does_not_require_a_later_signal(self):
        self.own_name()
        gate = self.launch(['/bin/sh', str(GATE), NAME, '2'])
        _, err = gate.communicate(timeout=1)
        self.assertEqual(gate.returncode, 0, err.decode())

    # covers: desktop-mode.audio-follow/E1
    def test_invalid_timeout_cannot_select_an_unbounded_wait(self):
        for timeout in ('0', '00', '-1', 'nan', '121'):
            with self.subTest(timeout=timeout):
                gate = self.launch(['/bin/sh', str(GATE), NAME, timeout])
                _, err = gate.communicate(timeout=1)
                self.assertEqual(gate.returncode, 2, err.decode())

    # covers: agent.home-hold/E1
    def test_overlay_keeps_its_failure_message_and_exit_code(self):
        gate = self.launch(['/bin/sh', str(GATE), 'org.kde.plasmashell', '1'])
        _, err = gate.communicate(timeout=3)
        self.assertEqual(gate.returncode, 1)
        self.assertEqual(err.decode(), 'plasmashell not on the session bus after 1 s\n')

    # covers: agent.home-hold/E1
    def test_both_units_use_the_same_installed_gate(self):
        for unit, name in [('desktop/rungic-plasma-audio-follow.service', 'org.kde.KWin'),
                           ('agent/assistant/rungic-voice-overlay.service', 'org.kde.plasmashell')]:
            self.assertIn('ExecStartPre=/usr/libexec/rungic-wait-dbus ' + name,
                          (ROOT / unit).read_text())
        self.assertIn('shared/platform/wait-dbus.sh',
                      (ROOT / 'packaging/rungic-plasma-bridges/build.sh').read_text())
