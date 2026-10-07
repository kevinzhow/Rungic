#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The overlay holding Home brings up (agent/assistant/app/qml/AssistantOverlay.qml, docs/67), its
real QML run offline (assistant_qml.py): what the navigation panel's Hold and the voice service do to
it, and its compact call bar. The C++ Overlay (overlay.cpp: the layer-shell window, KWin's effects)
and AgentClient are stand-ins that record what the QML asks of them. Also the overlay's systemd unit
waiting for plasmashell (a private D-Bus daemon). Requires PySide6.
"""
from pathlib import Path
import configparser
import os
import shutil
import subprocess
import sys
import time
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import assistant_qml as q  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / 'agent/assistant/rungic-voice-overlay.service'
# The app in a process of its own (as on the phone), switching its theme in the settings file the
# overlay reads too.
APP_SWITCHES_THEME = '''
import sys
sys.path.insert(0, {tests!r})
import assistant_qml as q
stage = q.Stage(config={config!r})
engine = stage.engine()
root = stage.load(engine, "Main")
q.spin(0.2)
q.js(engine, root, "settings.theme = {theme!r}")
q.spin(1.5)
'''


class OverlayTest(unittest.TestCase):
    def setUp(self):
        self.stage = q.Stage()
        self.engine = self.stage.engine()
        self.win = self.stage.load(self.engine, 'AssistantOverlay')
        self.content = q.content(self.win)
        q.spin(0.2)
        q.send(self.engine, 'assistantOpened', {'conversation': 'main', 'title': 'Main conversation', 'main': True, 'history': []})

    def tearDown(self):
        self.engine.deleteLater()
        q.spin(0.05)
        self.stage.close()

    def hold(self, pressed, screen=''):
        q.js(self.engine, q.singleton(self.engine, 'Overlay'), f'holdRequested({str(pressed).lower()}, "{screen}")')
        q.spin(0.1)

    def overlay_calls(self, name=None):
        return [c for c in q.calls(self.engine, 'Overlay') if name is None or c[0] == name]

    def theme_dark(self):
        return self.engine.singletonInstance('com.rungic.design', 'Theme').property('dark')

    # covers: agent.home-hold/E1
    def test_holding_home_brings_it_up_on_that_screen_and_talks_until_release(self):
        self.assertFalse(self.win.property('shown'))
        self.hold(True, 'WL-1')
        self.assertEqual(self.overlay_calls('present'), [['present', 'WL-1']], 'mapped on the screen Home was held on')
        self.assertTrue(self.win.property('shown'))
        self.assertIn(['assistantTalk', 'WL-1'], q.calls(self.engine), 'listening at once, the reply for that screen')
        self.assertEqual(self.win.property('view'), 'listen')
        self.assertIn('Speak now', q.texts(self.content))
        self.hold(False)
        self.assertEqual(q.calls(self.engine)[-1], ['releaseTalking'], 'lifting sends')
        # Another screen next time: mapped there.
        self.win.setProperty('shown', False)
        self.hold(True, 'CAST-1')
        self.assertEqual(self.overlay_calls('present')[-1], ['present', 'CAST-1'])

    # covers: agent.home-hold/E6
    def test_hold_home_off_in_the_settings_does_nothing(self):
        q.send(self.engine, 'replied', 'Setup', {'preferences': {'homeHold': False}})
        q.clear(self.engine)
        self.hold(True, 'WL-1')
        self.hold(False)
        self.assertEqual(self.overlay_calls('present'), [])
        self.assertFalse(self.win.property('shown'))
        self.assertEqual([c for c in q.calls(self.engine) if c[0] in ('assistantTalk', 'releaseTalking')], [])
        # Turned on again (the settings page's SetPreferences tells everyone).
        q.send(self.engine, 'event', {'type': 'preferences', 'homeHold': True})
        self.hold(True, 'WL-1')
        self.assertTrue(self.win.property('shown'))

    # covers: agent.home-hold/E6 agent.chat-app/E7
    def test_it_takes_the_apps_theme_also_when_changed_while_it_runs(self):
        def app_sets(theme):
            subprocess.run([sys.executable, '-c', APP_SWITCHES_THEME.format(
                tests=str(Path(__file__).resolve().parent), config=str(self.stage.config), theme=theme)],
                check=True, timeout=60, env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
        self.assertFalse(self.theme_dark(), 'the system is light here')
        app_sets('dark')
        self.hold(True, 'WL-1')
        self.assertTrue(self.theme_dark(), 'the app chose dark: the overlay is dark when it comes up')
        self.hold(False)
        self.win.setProperty('shown', False)
        app_sets('light')
        self.hold(True, 'WL-1')
        self.assertFalse(self.theme_dark(), 'and light again after the app switched back')

    # covers: agent.home-hold/E5
    def test_a_hold_past_a_minute_is_taken_as_a_lost_release(self):
        from PySide6.QtCore import QObject
        # The overlay's one-minute timer, driven: shortened so the test need not wait the minute.
        limit = [t for t in self.win.findChildren(QObject) if t.metaObject().className() == 'QQmlTimer'
                 and t.property('interval') == 60000]
        self.assertEqual(len(limit), 1, 'the overlay has its one-minute limit on a hold')
        limit[0].setProperty('interval', 300)
        self.hold(True, 'WL-1')
        self.assertTrue(self.win.property('holding'))
        # The service says it listens (as it does once talking started).
        q.send(self.engine, 'event', {'type': 'state', 'conversation': 'main', 'phase': 'listening'})
        q.clear(self.engine)
        q.spin(0.6)
        self.assertFalse(self.win.property('holding'))
        self.assertFalse(self.win.property('shown'), 'the overlay closes')
        calls = q.calls(self.engine)
        self.assertIn(['cancelTalking'], calls, 'the recording is cancelled: nothing of it is sent')
        self.assertNotIn(['releaseTalking'], calls)
        self.assertNotIn(['stopTalking'], calls)
        q.spin(0.4)
        self.assertEqual(self.overlay_calls()[-1], ['conceal'])
        # A lift that comes after that changes nothing.
        q.clear(self.engine)
        self.hold(False)
        self.assertEqual(q.calls(self.engine), [])

    # covers: agent.call-card/E5
    def test_away_from_the_assistant_a_small_call_bar_stays(self):
        q.clear(self.engine, 'Overlay')
        q.send(self.engine, 'event', {'type': 'call-started', 'callId': 'k1', 'conversation': 'other', 'contact': '张三',
                                      'goal': '订位', 'time': time.time()})
        q.send(self.engine, 'event', {'type': 'call-state', 'callId': 'k1', 'state': 'connected', 'connectedAt': time.time() - 65})
        q.spin(1.2)
        self.assertTrue(self.win.property('compactCall'), 'the call goes on while the assistant is not shown')
        self.assertTrue(self.overlay_calls('present'), 'its bar is on the screen')
        texts = q.texts(self.content)
        self.assertIn('张三', texts)
        self.assertIn('Assistant on the call', texts)
        self.assertTrue([t for t in texts if t in ('1:04', '1:05', '1:06', '1:07')], 'counting from when it connected')
        self.assertIn('Hang up', texts)
        self.assertNotIn('Speak now', texts, 'the sheet and its scrim are not up')
        # Only the bar takes touches: the rest of the screen stays the other apps'.
        bar = [i for i in q.items(self.content) if i.property('radius') == 18 and q.shown(i)][0]
        touch = self.overlay_calls('setTouchableRect')[-1]
        self.assertEqual(touch[1:], [bar.x(), bar.y(), bar.width(), bar.height()])
        self.assertLess(touch[3] * touch[4], self.win.property('width') * self.win.property('height') / 10)
        hang_up = [b for b in q.items(self.content) if b.property('text') == 'Hang up' and b.inherits('QQuickAbstractButton')][0]
        q.click(hang_up)
        self.assertIn('"op":"hang-up"', q.calls(self.engine)[-1][1])
        self.assertIn('"callId":"k1"', q.calls(self.engine)[-1][1])
        q.clear(self.engine, 'Overlay')
        q.send(self.engine, 'event', {'type': 'call-ended', 'callId': 'k1'})
        self.assertFalse(self.win.property('compactCall'))
        self.assertIn(['conceal'], self.overlay_calls(), 'the bar goes with the call')


class UnitTest(unittest.TestCase):
    # covers: agent.home-hold/E7
    @unittest.skipUnless(shutil.which('dbus-daemon') and shutil.which('busctl'), 'needs dbus-daemon and busctl')
    def test_the_overlay_starts_once_plasmashell_is_on_the_bus(self):
        unit = configparser.ConfigParser(interpolation=None, strict=False)
        unit.read(UNIT)
        self.assertIn('plasma-plasmashell.service', unit['Unit']['After'])
        self.assertEqual(unit['Service']['Type'], 'dbus')
        self.assertEqual(unit['Service']['Restart'], 'on-failure')
        wait = unit['Service']['ExecStartPre']
        self.assertEqual(wait, '/usr/libexec/rungic-wait-dbus org.kde.plasmashell')
        gate = ROOT / 'shared/platform/wait-dbus.sh'
        daemon = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--print-address'], stdout=subprocess.PIPE, text=True)
        try:
            address = daemon.stdout.readline().strip()
            env = {**os.environ, 'DBUS_SESSION_BUS_ADDRESS': address}
            started = time.monotonic()
            waiting = subprocess.Popen(['/bin/sh', str(gate), 'org.kde.plasmashell'], env=env)
            time.sleep(2.5)
            self.assertIsNone(waiting.poll(), 'no plasmashell yet: still waiting, not started and not failed')
            shell = subprocess.Popen([sys.executable, '-c', 'from gi.repository import Gio, GLib\n'
                                      'Gio.bus_own_name(Gio.BusType.SESSION, "org.kde.plasmashell", 0, None, None, None)\n'
                                      'GLib.MainLoop().run()'], env=env)
            try:
                self.assertEqual(waiting.wait(timeout=10), 0, 'plasmashell came: the overlay may start')
                self.assertGreater(time.monotonic() - started, 2.5)
            finally:
                shell.kill()
        finally:
            daemon.kill()


if __name__ == '__main__':
    unittest.main()
