# SPDX-License-Identifier: MIT
"""The Android device page (desktop/device-panel.py, installed as rungic-platform) on the Linux side:
the real GTK/libadwaita app on GTK's headless Broadway backend and a private session bus, against a
stand-in of the platform bridge (tools/contracts.py's, answering the writing ops as PlatformBridge.java
does). A small driver inside the app's process reads its rows and activates them as a finger would,
on the app's own main loop; the app's code is unchanged. What Android then does (opening its settings
page, vibrating, turning the screen) is the phone's."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import contracts  # noqa: E402

PANEL = ROOT / 'desktop/device-panel.py'

try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    HAVE_GTK = shutil.which('gtk4-broadwayd') is not None
except (ImportError, ValueError):
    HAVE_GTK = False
pytestmark = pytest.mark.skipif(not HAVE_GTK, reason='needs GTK 4, libadwaita and gtk4-broadwayd')

# Commands on stdin, one JSON reply per command on stdout, run on the app's main loop.
DRIVER = textwrap.dedent('''
    import json, runpy, sys
    import gi
    gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
    from gi.repository import Adw, GLib, Gtk
    script = sys.argv[1]
    run = Adw.Application.run
    app = None

    def widgets(widget):
        child = widget.get_first_child()
        while child:
            yield child
            yield from widgets(child)
            child = child.get_next_sibling()

    def command(line):
        cmd = json.loads(line)
        what = cmd['do']
        if what == 'state':
            out = {'rows': {k: r.get_subtitle() for k, r in app.rows.items()},
                   'message': app.message.get_description() or '',
                   'orientation': app.orientation.get_selected(), 'follow': app.follow.get_active(),
                   'scale_sensitive': app.scale.get_sensitive(), 'scale': round(app.scale.get_value(), 2)}
        elif what == 'activate':
            rows = [w for w in widgets(app.window) if isinstance(w, Adw.ActionRow) and w.get_title() == cmd['title']]
            rows[0].emit('activated')
            out = {'ok': len(rows)}
        elif what == 'orientation':
            app.orientation.set_selected(cmd['value']); out = {'ok': 1}
        elif what == 'follow':
            app.follow.set_active(cmd['value']); out = {'ok': 1}
        elif what == 'scale':
            app.scale.set_value(cmd['value']); out = {'ok': 1}
        sys.stdout.write(json.dumps(out) + '\\n'); sys.stdout.flush()

    def readable(channel, condition):
        line = sys.stdin.readline()
        if not line:
            app.quit(); return False
        command(line)
        return True

    def driven(self, argv):
        global app
        app = self
        GLib.io_add_watch(GLib.IOChannel.unix_new(sys.stdin.fileno()), GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP, readable)
        return run(self, argv)
    Adw.Application.run = driven
    sys.argv = [script]
    runpy.run_path(script, run_name='__main__')
''')

STATUS = {'version': 1, 'model': 'XT2537-4', 'manufacturer': 'motorola', 'android': '16', 'sdk': 36,
          'timezone': 'Asia/Shanghai', 'orientation': 'portrait', 'foreground': True, 'keepAwake': False,
          'windowBrightness': -1.0,
          'network': {'connected': True, 'validated': True, 'transport': 'Wi-Fi', 'interface': 'wlan0',
                      'addresses': ['192.168.5.20/24'], 'dns': ['192.168.5.1']},
          'battery': {'percent': 81, 'temperature': 31.5, 'status': 2, 'plugged': True}}
NETWORK = {'networks': [{'interface': 'wlan0', 'ssid': 'Home Wi-Fi', 'rssi': -51, 'frequency': 5180, 'linkMbps': 866}]}
MEMORY = {'choice': '4096', 'limit_mib': 4096, 'usage_mib': 1500, 'peak_mib': 2100, 'total_mib': 11000}


class Platform(contracts.StandIn):
    def __init__(self):
        super().__init__('platform-bridge', {'status': dict(STATUS)})
        self.memory = MEMORY
        self.network = NETWORK

    def answer(self, request):
        op = request.get('op')
        if op == 'network-get':
            return self.network
        if op == 'container-memory':
            return self.memory if self.memory else {'error': 'Unsupported operation'}
        if op in ('settings', 'vibrate', 'orientation', 'brightness'):
            return {'ok': True}
        return super().answer(request)

    def writes(self):
        return [r for r in self.requests if r.get('op') not in ('status', 'network-get', 'container-memory')]


class Panel:
    def __init__(self, platform):
        self.tmp = tempfile.TemporaryDirectory(prefix='rungic-panel-')
        runtime = Path(self.tmp.name)
        runtime.chmod(0o700)
        self.bus = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--print-address=1',
                                     f'--address=unix:path={runtime}/bus'], stdout=subprocess.PIPE, text=True)
        address = self.bus.stdout.readline().strip()
        assert address.startswith(f'unix:path={runtime}/bus')
        display = 40 + os.getpid() % 200
        env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), DBUS_SESSION_BUS_ADDRESS=address,
                   RUNGIC_PLATFORM_SOCKET=platform.path, GDK_BACKEND='broadway', BROADWAY_DISPLAY=f':{display}',
                   LANG='C.UTF-8', LANGUAGE='', GSK_RENDERER='cairo', GIO_USE_VFS='local')
        env.pop('WAYLAND_DISPLAY', None)
        env.pop('DISPLAY', None)
        self.broadway = subprocess.Popen(['gtk4-broadwayd', '--address', '127.0.0.1', f':{display}'], env=env,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while not list(runtime.glob('broadway*.socket')):
            assert time.monotonic() < deadline, 'broadwayd did not start'
            time.sleep(0.05)
        self.app = subprocess.Popen([sys.executable, '-c', DRIVER, str(PANEL)], env=env, text=True,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(runtime / 'app.err', 'w'))

    def do(self, **cmd):
        self.app.stdin.write(json.dumps(cmd) + '\n')
        self.app.stdin.flush()
        line = self.app.stdout.readline()
        if not line:
            raise AssertionError('the app quit: ' + (Path(self.tmp.name) / 'app.err').read_text()[-2000:])
        return json.loads(line)

    def state(self):
        return self.do(do='state')

    def wait(self, condition, what, timeout=10):
        deadline = time.monotonic() + timeout
        while True:
            state = self.state()
            if condition(state):
                return state
            if time.monotonic() > deadline:
                raise AssertionError(f'timed out waiting for {what}: {state}')
            time.sleep(0.1)

    def close(self):
        for process in (self.app, self.broadway, self.bus):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()
        self.tmp.cleanup()


@pytest.fixture
def panel():
    opened = []

    def open_(platform):
        p = Panel(platform)
        opened.append(p)
        return p
    yield open_
    for p in opened:
        p.close()


def loaded(state):
    return state['rows']['model'] == 'motorola XT2537-4'


# covers: desktop.device-panel/E2
# covers[consumer]: iface:platform-bridge
def test_each_settings_button_asks_android_for_its_page(panel):
    with Platform() as platform:
        p = panel(platform)
        state = p.wait(loaded, 'the page loaded')
        assert 'Home Wi-Fi' in state['rows']['wifi'] and state['message'] == ''
        for title, target in (('Manage networks', 'network'), ('Android display settings', 'display'),
                              ('Sound and output devices', 'sound'), ('Bluetooth devices', 'bluetooth'),
                              ('Date and time zone', 'datetime'), ('Location settings', 'location')):
            assert p.do(do='activate', title=title) == {'ok': 1}, title
            p.wait(lambda s: {'op': 'settings', 'target': target} in platform.requests, f'{title} sent', 5)
        assert p.do(do='activate', title='Test vibration') == {'ok': 1}
        p.wait(lambda s: {'op': 'vibrate'} in platform.requests, 'vibration sent', 5)
        assert [r['target'] for r in platform.writes() if r['op'] == 'settings'] == \
            ['network', 'display', 'sound', 'bluetooth', 'datetime', 'location']


# covers: desktop.device-panel/E3
def test_the_orientation_choice_is_sent_at_once_and_shows_androids(panel):
    with Platform() as platform:
        p = panel(platform)
        state = p.wait(loaded, 'the page loaded')
        assert state['orientation'] == 1                         # Android says portrait
        assert not [r for r in platform.writes() if r['op'] == 'orientation'], 'showing it sends nothing'
        p.do(do='orientation', value=2)
        p.wait(lambda s: {'op': 'orientation', 'mode': 'landscape'} in platform.requests, 'landscape sent', 5)
        p.do(do='orientation', value=0)
        p.wait(lambda s: {'op': 'orientation', 'mode': 'system'} in platform.requests, 'follow Android sent', 5)
        # Changed on Android's side: the page shows it without sending it back.
        sent = len(platform.writes())
        platform.replies['status'] = dict(STATUS, orientation='landscape')
        p.wait(lambda s: s['orientation'] == 2, "Android's landscape shown", 6)
        assert len(platform.writes()) == sent


def test_brightness_follows_android_or_the_slider(panel):
    with Platform() as platform:
        p = panel(platform)
        state = p.wait(loaded, 'the page loaded')
        assert state['follow'] and not state['scale_sensitive']
        p.do(do='follow', value=False)
        p.wait(lambda s: {'op': 'brightness', 'value': 0.5} in platform.requests, 'the slider level sent', 5)
        p.do(do='scale', value=0.3)
        p.wait(lambda s: {'op': 'brightness', 'value': 0.3} in platform.requests, 'the new level sent', 5)
        p.do(do='follow', value=True)
        p.wait(lambda s: {'op': 'brightness', 'value': -1} in platform.requests, 'follow Android sent', 5)


# covers: desktop.device-panel/E1
# covers: install.memory-limit/E4
def test_the_memory_row_says_what_the_use_is_made_of(panel):
    with Platform() as platform:
        platform.memory = {**MEMORY, 'swap_limit_mib': 5120, 'programs_mib': 598, 'cache_mib': 1039,
                           'shmem_mib': 227, 'swap_mib': 938}
        p = panel(platform)
        state = p.wait(loaded, 'the page loaded')
        row = state['rows']['memory']
        assert '1500 MB' in row and '4096 MB' in row
        assert '598' in row and '1039' in row and '227' in row and '938' in row, row


# covers: install.memory-limit/E4
def test_a_launcher_without_the_breakdown_shows_the_use_alone(panel):
    with Platform() as platform:                                  # MEMORY: a launcher from before 2026-10-03
        p = panel(platform)
        row = p.wait(loaded, 'the page loaded')['rows']['memory']
        assert '1500 MB' in row and '\n' not in row, row


# covers: desktop.device-panel/E4
def test_an_old_or_unreachable_android_side_is_said_and_old_data_is_not_shown(panel):
    with Platform() as platform:
        platform.memory = None                                    # an app from before container-memory
        p = panel(platform)
        state = p.wait(loaded, 'the page loaded')
        assert 'Rungic APK 2.5' in state['rows']['memory']
        platform.__exit__(None, None, None)                       # the app goes away
        state = p.wait(lambda s: s['message'] != '', 'a message', 8)
        assert state['message'] == 'Cannot reach the Android side. Open Rungic on the phone.'
        assert not any('Home Wi-Fi' in v or 'XT2537' in v or '81%' in v for v in state['rows'].values()), \
            f'data from before is still shown: {state["rows"]}'


# covers: desktop.device-panel/E4
def test_a_response_too_large_is_an_error(panel):
    with Platform() as platform:
        platform.network = {'networks': [{'interface': 'wlan%d' % i, 'ssid': 'x' * 200} for i in range(400)]}
        p = panel(platform)
        state = p.wait(lambda s: s['message'] != '' and 'Connecting' not in s['message'], 'the error', 8)
        assert state['message'] == 'The Android host sent a response that is too large'
        assert set(state['rows'].values()) == {'Not available'}
