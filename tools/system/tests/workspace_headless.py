# SPDX-License-Identifier: MIT
"""An agent workspace beside the user's session, with no Android at all (docs/research/91, 97).

The user's session is a headless KWin of the phone's size with an app of the user's in it (active).
The real workspace, `rungic-workspace 1` as its unit starts it, comes up headless (KWin --virtual,
its own D-Bus, Xwayland, accessibility bus, sound sink, keeper) with no Android host: no
/mnt/android-wayland, the platform bridge a stand-in of its contract. Then:

- an app started in the workspace (rungic-workspace-env) opens there and never in the user's session;
  the agent's clicks and typing (rungic-workspace-input: KWin's fake input) reach it as
  ordinary events, and the user's active window and pointer do not move (captures are not here: KWin
  without a GPU cancels every ScreenShot2 capture);
- its accessibility bus is its own (rungic-workspace-a11y): the user's apps register on the user's,
  the workspace's on its own, and after the workspace restarted the user's bus is the same and the
  user's new apps still register on it;
- its sound goes into its own null sink: not to the user's output while the assistant's screen does not
  show it, connected to the default output while shown (the keeper, from the bridge's state), off again
  as a thumbnail of the TV's director view; the unit's ExecStopPost leaves no sink or loopback;
- the user's KWin going away (Plasma's restart or crash) leaves the workspace and its app running.
"""
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import contracts
import harness

from gi.repository import Gio, GLib

SLOT = '1'
APP = '''
import sys, gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
name, title, log = sys.argv[1], sys.argv[2], sys.argv[3]
GLib.set_prgname(name)
GLib.set_application_name(name)
w = Gtk.Window(title=title)
w.set_default_size(600, 400)
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, homogeneous=True)
button = Gtk.Button(label="press")
entry = Gtk.Entry()
def note(text):
    with open(log, "a") as f:
        f.write(text + "\\n")
button.connect("clicked", lambda *a: note("clicked"))
entry.connect("changed", lambda e: note("text " + e.get_text()))
box.pack_start(button, True, True, 0)
box.pack_start(entry, True, True, 0)
w.add(box)
w.connect("destroy", Gtk.main_quit)
w.show_all()
Gtk.main()
'''
# In the workspace, as the agent's tools are (rungic_cua): its KWin's windows and the pointer.
QUERY = '''
import json
from rungic_cua.kwin import KWin
k = KWin()
print(json.dumps({**k.windows(), "cursor": k.cursor()}))
'''
INPUT = '''
import sys, time
from rungic_cua.kwin import KWin
from rungic_cua.fakeinput import WorkspaceInput
k = KWin()
i = WorkspaceInput(k.cursor)
x, y, w, h = map(float, sys.argv[1:5])
i.click(x + w / 2, y + h * 0.3)
time.sleep(0.5)
i.click(x + w / 2, y + h * 0.85)
time.sleep(0.5)
i.type_text("Rungic09AbC123!@中文")
time.sleep(0.5)
i.close()
'''
A11Y_ADDRESS = '''
from gi.repository import Gio
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
print(bus.call_sync("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress", None, None, 0, 10000).unpack()[0])
'''


def ws(*argv, timeout=30):
    """A command in the workspace, as rungic-workspace-env runs it."""
    done = subprocess.run(['rungic-workspace-env', SLOT, *argv], capture_output=True, text=True, timeout=timeout)
    if done.returncode:
        raise harness.Failed(f'{argv[:2]} in the workspace: {done.stderr.strip()[-400:]}')
    return done.stdout


def ws_state():
    return json.loads(ws('python3', '-c', QUERY))


def a11y_apps(address):
    """The names of the applications registered on an accessibility bus."""
    conn = Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    try:
        children = conn.call_sync('org.a11y.atspi.Registry', '/org/a11y/atspi/accessible/root', 'org.a11y.atspi.Accessible',
                                  'GetChildren', None, None, 0, 5000).unpack()[0]
        names = []
        for bus, path in children:
            try:
                names.append(conn.call_sync(bus, path, 'org.freedesktop.DBus.Properties', 'Get',
                                            GLib.Variant('(ss)', ('org.a11y.atspi.Accessible', 'Name')),
                                            None, 0, 3000).unpack()[0])
            except GLib.Error:
                pass
        return names
    finally:
        conn.close_sync(None)


def pactl(*args):
    return subprocess.run(['pactl', *args], capture_output=True, text=True, timeout=10).stdout


def modules(kind):
    rows = [line.split('\t') for line in pactl('list', 'short', 'modules').splitlines()]
    if kind == 'sink':
        return [r for r in rows if r[1] == 'module-null-sink' and f'sink_name=rungic_ws{SLOT}' in r[2]]
    return [r for r in rows if r[1] == 'module-loopback' and f'source=rungic_ws{SLOT}.monitor' in r[2]]


def sink_of_streams():
    sinks = {r.split('\t')[0]: r.split('\t')[1] for r in pactl('list', 'short', 'sinks').splitlines()}
    return [sinks.get(r.split('\t')[1]) for r in pactl('list', 'short', 'sink-inputs').splitlines()]


def stop_post():
    """The unit's ExecStopPost commands, as systemd runs them after the workspace stopped."""
    for line in Path('/usr/lib/systemd/user/rungic-workspace@.service').read_text().splitlines():
        if line.startswith('ExecStopPost='):
            command = line.split('=', 1)[1].lstrip('-').replace('%i', SLOT).replace('$$', '$')
            subprocess.run(['sh', '-c', command], capture_output=True, timeout=30)


# covers[system]: agent.workspaces/E1 agent.workspaces/E2 agent.workspaces/E4 agent.workspaces/E6 agent.workspaces/E7
# covers[consumer]: iface:platform-bridge
def test():
    tmp = Path('/tmp/workspace-headless')
    tmp.mkdir(exist_ok=True)
    (tmp / 'app.py').write_text(APP)
    with contracts.StandIn('platform-bridge') as bridge, harness.Session(1080, 2400) as s:
        runtime, home = s.runtime, Path.home()
        hidden = dict(next(q for q in bridge.contract['queries'] if q['name'] == 'agent-screen')['reply'])
        # The user's session bus starts what it activates (the accessibility bus) in the session's runtime
        # directory, as the phone's does.
        Gio.bus_get_sync(Gio.BusType.SESSION).call_sync(
            'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'UpdateActivationEnvironment',
            GLib.Variant('(a{ss})', ({k: os.environ[k] for k in ('XDG_RUNTIME_DIR', 'WAYLAND_DISPLAY')},)), None, 0, 5000)
        # The phone's sound server: the default output is the phone's.
        s.start(['pulseaudio', '-n', '--daemonize=no', '--exit-idle-time=-1', '--disallow-exit', '--disable-shm=yes',
                 '-L', 'module-native-protocol-unix', '-L', 'module-null-sink sink_name=phone_output'])
        s.wait_for(lambda: subprocess.run(['pactl', 'info'], capture_output=True).returncode == 0, 20, 'the sound server')
        pactl('set-default-sink', 'phone_output')

        s.start(['python3', str(tmp / 'app.py'), 'rungic-user-app', 'user app', str(tmp / 'user.log')])
        s.wait_for(lambda: s.find(caption='user app', active=True), 20, "the user's app, active")
        user_cursor = s.kwin_api.cursor()

        def start_workspace():
            # KWin here does not find the fake-input grant of rungic-workspace-input's desktop file (as for
            # the harness's KWin): its switch for test setups; the grant is the phone's to check.
            env = {**os.environ, 'RUNGIC_PLATFORM_SOCKET': bridge.path, 'KWIN_WAYLAND_NO_PERMISSION_CHECKS': '1'}
            proc = s.start(['rungic-workspace', SLOT], env=env)
            state = home / f'.local/state/rungic-workspaces/{SLOT}'
            s.wait_for(lambda: (state / 'bus').exists() and (runtime / f'wayland-ws-{SLOT}').exists() or proc.poll() is not None,
                       40, 'the workspace up')
            failed = proc.poll() is not None
            s.check(not failed, 'the workspace runs' + (f': {Path(f"/tmp/{SLOT}.err").read_text()[-400:]}' if failed else ''))
            return proc

        workspace = start_workspace()
        # No Android host compositor (its wayland-0); the directory itself is in the image, for stand-ins.
        host = [str(p) for p in Path('/mnt/android-wayland').glob('wayland-*')]
        failed_mark = (runtime / f'rungic-workspace-{SLOT}.failed').exists()
        s.check(not host and not failed_mark,
                f'the workspace is up with no Android host at all (headless) [host sockets {host}, failed mark {failed_mark}]')

        # ---- an app of the agent's, its input (E1, E2) ----------------------------------------
        s.start(['rungic-workspace-env', SLOT, 'python3', str(tmp / 'app.py'), 'rungic-ws-app', 'agent app',
                 str(tmp / 'ws.log')])
        seen_by_user = []

        def opened():
            seen_by_user.extend(w['caption'] for w in s.stack() if w['caption'] == 'agent app')
            return next((w for w in ws_state()['windows'] if w['caption'] == 'agent app'), None)
        window = s.wait_for(opened, 30, "the agent's app in the workspace")
        s.check(not seen_by_user and not s.find(caption='agent app'),
                "the agent's app opens in the workspace and never in the user's session")
        ws('python3', '-c', INPUT, *map(str, window['client']))
        s.wait_for(lambda: 'text Rungic09AbC123!@中文' in (tmp / 'ws.log').read_text() if (tmp / 'ws.log').exists() else False,
                   10, "the agent's input in its app")
        s.check('clicked' in (tmp / 'ws.log').read_text(), "the agent's click and typing reach its app as ordinary events")
        s.check(s.find(caption='user app', active=True) is not None and not (tmp / 'user.log').exists(),
                "the user's app stays active and gets none of the agent's input")
        s.check(s.kwin_api.cursor() == user_cursor, "the user's pointer does not move")

        # ---- accessibility buses (E4) ------------------------------------------------------------
        user_a11y = subprocess.run(['python3', '-c', A11Y_ADDRESS], capture_output=True, text=True, timeout=20).stdout.strip()
        ws_a11y = ws('python3', '-c', A11Y_ADDRESS).strip()
        s.check(user_a11y and ws_a11y and user_a11y.split(',')[0] != ws_a11y.split(',')[0]
                and f'rungic-workspace-{SLOT}-a11y' in ws_a11y,
                f'the workspace has an accessibility bus of its own ({ws_a11y.split(",")[0]}; the user\'s {user_a11y.split(",")[0]})')
        s.wait_for(lambda: 'rungic-ws-app' in a11y_apps(ws_a11y), 15, "the agent's app on the workspace's a11y bus")
        s.check('rungic-user-app' in a11y_apps(user_a11y) and 'rungic-ws-app' not in a11y_apps(user_a11y),
                "the user's app on the user's accessibility bus, the agent's not")

        # ---- sound (E6) ----------------------------------------------------------------------------
        s.check(len(modules('sink')) == 1, 'the workspace has its own null sink')
        player = s.start(['rungic-workspace-env', SLOT, 'pacat', '--playback'], stdin=open('/dev/zero', 'rb'))
        s.wait_for(lambda: f'rungic_ws{SLOT}' in sink_of_streams(), 15, "the workspace's stream")
        s.check(sink_of_streams() == [f'rungic_ws{SLOT}'], "the workspace's sound plays into its own sink, not the phone's output")
        time.sleep(7)    # the keeper's checks (3 s apart) with the screen hidden
        s.check(not modules('loopback'), 'not shown: its sink is not connected to the output (nobody hears it)')
        bridge.replies['agent-screen'] = {**hidden, 'tvShown': [int(SLOT)], 'tvHeard': int(SLOT)}
        s.wait_for(lambda: modules('loopback'), 25, 'the sound on while shown')
        s.wait_for(lambda: 'phone_output' in sink_of_streams(), 10, "the workspace's sound on the phone's output")
        s.check(len(modules('loopback')) == 1, "shown: its sink's monitor goes to the default output, once")
        bridge.replies['agent-screen'] = {**hidden, 'tvShown': [int(SLOT), 2], 'tvHeard': 2}
        s.wait_for(lambda: not modules('loopback'), 25, 'the sound off as a thumbnail')
        s.check(True, "a thumbnail of the TV's director view is not heard")
        bridge.replies['agent-screen'] = {**hidden, 'tvShown': [int(SLOT)], 'tvHeard': int(SLOT)}
        s.wait_for(lambda: modules('loopback'), 25, 'the sound on again')
        player.kill()

        # ---- stopped as its unit stops it, then started again (E4, E6) -------------------------
        workspace.send_signal(signal.SIGTERM)
        status = workspace.wait(30)
        s.check(status == 0, f'the workspace stops cleanly ({status})')
        stop_post()
        s.check(not modules('sink') and not modules('loopback'), 'after it stopped no sink or loopback of it is left')
        bridge.replies['agent-screen'] = hidden
        workspace = start_workspace()
        user_a11y_after = subprocess.run(['python3', '-c', A11Y_ADDRESS], capture_output=True, text=True,
                                         timeout=20).stdout.strip()
        ws_a11y_after = ws('python3', '-c', A11Y_ADDRESS).strip()
        s.check(user_a11y_after == user_a11y and ws_a11y_after.split(',')[0] != user_a11y.split(',')[0],
                "after the workspace restarted, the user's accessibility bus is the same and the workspace's its own")
        s.start(['python3', str(tmp / 'app.py'), 'rungic-user-app2', 'user app 2', str(tmp / 'user2.log')])
        s.wait_for(lambda: 'rungic-user-app2' in a11y_apps(user_a11y), 20, "the user's new app on the user's a11y bus")
        s.check(True, "the user's new apps still register for accessibility after the workspace restarted")

        # ---- the user's interface goes away (E7) -------------------------------------------------
        s.start(['rungic-workspace-env', SLOT, 'python3', str(tmp / 'app.py'), 'rungic-ws-app', 'agent app 2',
                 str(tmp / 'ws2.log')])
        s.wait_for(lambda: any(w['caption'] == 'agent app 2' for w in ws_state()['windows']), 30, "the agent's app again")
        s.kwin.kill()
        s.kwin.wait(10)
        time.sleep(3)
        s.check(workspace.poll() is None and any(w['caption'] == 'agent app 2' for w in ws_state()['windows']),
                "the user's KWin gone, the workspace and the agent's app in it go on")
        steps = list(s.steps)
        workspace.send_signal(signal.SIGTERM)
        workspace.wait(30)
        return steps


def traced():
    try:
        return test()
    except Exception:
        import traceback
        keeper = Path.home() / f'.local/state/rungic-workspaces/{SLOT}/keeper.log'
        raise harness.Failed(traceback.format_exc()[-1200:] + ' keeper: ' +
                             (keeper.read_text(errors='replace')[-1200:] if keeper.exists() else '-'))


if __name__ == '__main__':
    harness.run('workspace_headless', traced)
