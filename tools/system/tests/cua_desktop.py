# SPDX-License-Identifier: MIT
"""The agent's desktop tools on a real KWin (docs/60, docs/68): rungic-cua as Codex uses it in an agent
workspace, on a headless 1920x1080 KWin (--virtual), driving a GTK test app that logs what reaches it.

- desktop_launch opens an app by a name in another language (its .desktop file's Name[zh_CN]) and by
  its desktop id; the second time it activates the open window instead of starting another; in a
  workspace it starts the app through systemd-run in a scope of its own, bound to the workspace.
- Codex's desktop_screenshot/desktop_act and the API backup's shared actions reach the app as ordinary pointer and
  keyboard events through the workspace's fake input, a screen pixel for a pixel.
- What the screenshot shows is the task's window alone (its frame, not the screen); with a popup menu
  open it is the window with its menu, and the note says so. (KWin here has no GPU: its ScreenShot2
  cancels every capture, so the pixels themselves and their scaling are tools/tests/test_cua_tools.py's.)
- desktop_window closes, minimizes and maximizes through KWin; an app that asks to save stays
  open and the tool says still_open.

Stand-ins: kstart and systemd-run (no systemd user manager in the container) are scripts that log their
arguments and start the program; arc_cua (plan two's executor, an upstream package not shipped to the
container) is an empty module. KWin here is Ubuntu's, without Rungic's commitText patch: the actual keysym
helper is checked with mixed case, shifted symbols and Unicode; backend/TypeSafe
compatibility routes are checked against a real missing method. Upstream
TypeSafe observation types are stand-ins; the text adapter is source code."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import harness

SRC = Path('/src')
APP = r'''
import json, sys
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk
prgname, logfile, title, mode = sys.argv[1:5]
GLib.set_prgname(prgname)
log = open(logfile, "a", buffering=1)
def say(**kw):
    log.write(json.dumps(kw) + "\n")
say(event="started", mode=mode)
win = Gtk.Window(title=title)
win.set_default_size(640, 480)
if mode == "unsaved":
    def ask(*_):
        d = Gtk.MessageDialog(transient_for=win, modal=True, buttons=Gtk.ButtonsType.YES_NO, text="Save changes?")
        d.set_title("Save changes?")
        d.show()
        say(event="asked to save")
        return True
    win.connect("delete-event", ask)
    win.add(Gtk.Label(label="unsaved work"))
elif mode == "entry":
    entry = Gtk.Entry()
    entry.get_accessible().set_name("fallback input")
    entry.connect("changed", lambda widget: say(event="text", text=widget.get_text()))
    win.add(entry)
else:
    area = Gtk.DrawingArea()
    area.set_can_focus(True)
    area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK
                    | Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK | Gdk.EventMask.KEY_PRESS_MASK)
    def draw(widget, cr):
        cr.set_source_rgb(1, 1, 1); cr.paint()
        cr.set_source_rgb(1, 0, 0); cr.rectangle(100, 80, 20, 20); cr.fill()
    area.connect("draw", draw)
    menu = Gtk.Menu()
    for label in ("First item", "Second item", "Third item", "Fourth item"):
        menu.append(Gtk.MenuItem(label=label))
    menu.show_all()
    def press(widget, event):
        widget.grab_focus()
        kind = {Gdk.EventType.BUTTON_PRESS: "press", Gdk.EventType._2BUTTON_PRESS: "double"}.get(event.type, str(event.type))
        say(event=kind, button=event.button, x=event.x, y=event.y)
        if event.button == 3 and event.type == Gdk.EventType.BUTTON_PRESS:
            menu.popup_at_pointer(event)
        return True
    area.connect("button-press-event", press)
    area.connect("button-release-event", lambda w, e: say(event="release", button=e.button, x=e.x, y=e.y))
    area.connect("motion-notify-event", lambda w, e: say(event="motion", x=e.x, y=e.y,
                 held=bool(e.state & Gdk.ModifierType.BUTTON1_MASK)) if e.state & Gdk.ModifierType.BUTTON1_MASK else None)
    def scroll(widget, event):
        ok, dx, dy = event.get_scroll_deltas()
        say(event="scroll", direction=str(event.direction), dy=dy if ok else 0)
    area.connect("scroll-event", scroll)
    area.connect("key-press-event", lambda w, e: say(event="key", name=Gdk.keyval_name(e.keyval)))
    win.add(area)
win.connect("destroy", lambda *_: (say(event="closed"), Gtk.main_quit()))
win.show_all()
Gtk.main()
'''
KSTART = '''#!/usr/bin/env python3
# kstart --application ID | --desktopfile ID -- PROGRAM ARGS: start the desktop file's program (log the call).
import os, sys, gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio
open(os.environ["CALLS_LOG"], "a").write(" ".join(["kstart", *sys.argv[1:]]) + "\\n")
args = sys.argv[1:]
if "--" in args:
    os.execvp(args[args.index("--") + 1], args[args.index("--") + 1:])
Gio.DesktopAppInfo.new(args[args.index("--application") + 1] + ".desktop").launch([], None)
'''
SYSTEMD_RUN = '''#!/bin/sh
# systemd-run --user --scope ... -- COMMAND: no user manager here; log the call, run the command.
echo "systemd-run $*" >> "$CALLS_LOG"
while [ "$1" != "--" ]; do shift; done
shift
exec "$@"
'''


def setup(tmp):
    """The stand-ins and the test app's desktop file."""
    stubs = tmp / 'stubs'
    (stubs / 'arc_cua').mkdir(parents=True)
    (stubs / 'arc_cua/__init__.py').write_text('class DesktopExecutor: pass\nclass RuntimeConfig: pass\n'
                                               'def result_to_dict(r): return r\ndef subtask_from_dict(d): return d\n')
    (stubs / 'arc_cua/policies.py').write_text('class TypeSafeJevPolicy: pass\n')
    (stubs / 'arc_cua/errors.py').write_text('class StaleDesktopState(Exception): pass\n'
                                             'class UnsupportedDesktopAction(Exception): pass\n')
    (stubs / 'arc_cua/keyboard.py').write_text('def parse_hotkey(t): return [], t\n')
    (stubs / 'arc_cua/models.py').write_text('class ActionKind: pass\nclass DesktopElement: pass\n'
                                             'class DesktopSnapshot: pass\nclass ExecutableAction: pass\n')
    # Only upstream observation types are absent; linux.py's actual text
    # adapter below runs against the real KWin/input, without their use.
    typesafe = stubs / 'typesafe_computer_use'
    typesafe.mkdir()
    (typesafe / '__init__.py').write_text('__path__.append("/src/agent/computer-use/typesafe")\n')
    (typesafe / 'ax_walk.py').write_text('AX_PRESS = "press"\nclass AxAttrs: pass\nclass Frame: pass\n'
                                        'def walk_actionable(*args): raise RuntimeError("observation not installed")\n')
    (typesafe / 'models.py').write_text('TEXT_ROLES = set()\nclass Abort(Exception): pass\nclass AxNode: pass\n'
                                       'class Field: pass\nclass Missed(Exception): pass\n')
    sys.path.insert(0, str(stubs))
    bin_dir = tmp / 'bin'
    bin_dir.mkdir()
    for name, text in (('kstart', KSTART), ('systemd-run', SYSTEMD_RUN)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    os.environ['PATH'] = f'{bin_dir}:{os.environ["PATH"]}'
    os.environ['CALLS_LOG'] = str(tmp / 'calls.log')
    (tmp / 'app.py').write_text(APP)
    apps = Path.home() / '.local/share/applications'
    apps.mkdir(parents=True, exist_ok=True)
    (apps / 'rungic-test-canvas.desktop').write_text(
        '[Desktop Entry]\nType=Application\nName=Rungic Test Canvas\nName[zh_CN]=测试画板\n'
        f'Exec=python3 {tmp}/app.py rungic-test-canvas {tmp}/canvas.log Canvas canvas\n')


def events(log, kind=None):
    try:
        lines = [json.loads(l) for l in Path(log).read_text().splitlines()]
    except OSError:
        return []
    return [e for e in lines if kind is None or e['event'] == kind]


# covers[system]: agent.computer-use/E1 agent.computer-use/E2 agent.computer-use/E3 agent.computer-use/E5 agent.computer-use/E6 agent.computer-use/E8
def test():
    tmp = Path('/tmp/cua-desktop')
    tmp.mkdir(exist_ok=True)
    setup(tmp)
    with harness.Session(1920, 1080, 'cua') as s:
        os.environ['RUNGIC_WORKSPACE'] = '1'            # rungic-cua as in the agent's workspace 1
        from rungic_cua import activity, luna, mode, server
        mode.PLAN_FILE = tmp / 'plan'
        mode.save('codex')
        activity.DIR = s.runtime / 'rungic-agent-screen'
        cua = server.Cua()
        backend = SimpleNamespace(kwin=s.kwin_api, input=s.pointer(), _no_virtual_keyboard=lambda: None)
        cua._backend = backend
        canvas_log = tmp / 'canvas.log'

        # ---- desktop_launch (E6) ----
        launched = cua.call('desktop_launch', {'app': '测试画板'})
        window = launched.get('window') or {}
        s.check(launched['launched']['id'] == 'rungic-test-canvas' and window.get('id'),
                'desktop_launch finds the app by its Chinese name and returns its window')
        s.wait_for(lambda: s.find(cls='rungic-test-canvas', active=True), 10, 'the canvas, active')
        s.check(True, 'the launched window is the active one')
        calls = (tmp / 'calls.log').read_text()
        s.check('systemd-run --user --scope' in calls and 'BindsTo=rungic-workspace@1.service' in calls
                and 'kstart --application rungic-test-canvas' in calls,
                'in a workspace the app starts in a scope of its own, bound to the workspace')
        again = cua.call('desktop_launch', {'app': 'rungic-test-canvas'})
        s.check(again.get('already_open') and again['window']['id'] == window['id'],
                'launching it again by desktop id activates the open window')
        time.sleep(1)
        s.check(len(events(canvas_log, 'started')) == 1, 'and starts no second instance')

        # ---- what the screenshot shows (E2): the task's window alone, or with its popups ----
        output = cua.agent_output()
        computer = luna.ComputerUse(backend, output, window['id'])
        frame = s.find(cls='rungic-test-canvas')['frame']
        args, region, scope = computer.screen._region()
        s.check(args == ['window', window['id']] and list(region) == frame and scope == 'only the "Canvas" window',
                f"the screenshot is of the task's window alone ({region}), not the 1920x1080 screen")

        # ---- input (E1): ordinary pointer and key events, one screen pixel per pixel ----
        computer.screen.origin, computer.screen.scale = (0.0, 0.0), 1.0      # pixels of the whole screen
        client = next(w for w in s.kwin_api.windows()['windows'] if w['id'] == window['id'])['client']
        gx, gy = client[0] + client[2] // 2, client[1] + client[3] // 2

        # Default MCP dispatch drives the real compositor/input. Only image pixels are a
        # stand-in: Ubuntu's GPU-less KWin cannot capture (the same limitation as E2 above).
        from PIL import Image
        direct = cua.agent_screen()
        def capture():
            _args, region, scope = direct.screen._region()
            direct.screen.origin = region[:2]
            direct.screen.scale = 1.0
            direct.screen.scope = scope
            return 'data:image/jpeg;base64,c3RhbmQtaW4=', Image.new('RGB', tuple(map(int, region[2:]))), False
        direct.screen.capture = capture
        backend.bus = None
        try:
            cua.call('desktop_act', {'actions': [{'type': 'click', 'x': 1, 'y': 1}]})
        except ValueError as error:
            s.check('desktop_screenshot first' in str(error), 'Codex actions require a preceding screenshot')
        else:
            raise harness.Failed('Codex action accepted coordinates before a screenshot')
        image = cua.call('desktop_screenshot', {})
        origin = direct.screen.origin
        result = cua.call('desktop_act', {'actions': [{'type': 'click', 'x': gx - origin[0], 'y': gy - origin[1]}]})
        clicked = s.wait_for(lambda: events(canvas_log, 'press'), 5, 'the Codex MCP click')[-1]
        s.check(image['shows'] == 'only the "Canvas" window' and result['done'] and result.get('__image__')
                and abs(clicked['x'] - (gx - client[0])) <= 1 and abs(clicked['y'] - (gy - client[1])) <= 1,
                'default Codex dispatch clicks the real app in window-image coordinates and returns another image')
        try:
            cua.call('desktop_goal', {'goal': 'must not call an API'})
        except ValueError as error:
            s.check('no API goal executor' in str(error), 'default Codex dispatch rejects the independent API executor')
        else:
            raise harness.Failed('Codex mode accepted desktop_goal')
        canvas_log.write_text('')  # subsequent shared-input checks count only their own events
        computer.execute({'type': 'click', 'x': gx, 'y': gy})
        first = s.wait_for(lambda: events(canvas_log, 'press'), 5, 'the click')[-1]
        computer.execute({'type': 'click', 'x': gx + 37, 'y': gy + 23})
        second = s.wait_for(lambda: len(events(canvas_log, 'press')) > 1 and events(canvas_log, 'press'), 5,
                            'a second click')[-1]
        s.check(first['button'] == 1 and abs(second['x'] - first['x'] - 37) <= 1 and abs(second['y'] - first['y'] - 23) <= 1,
                'clicks arrive as left-button presses, 37, 23 pixels apart as sent (within a pixel)')
        computer.execute({'type': 'double_click', 'x': gx - 50, 'y': gy - 40})
        s.wait_for(lambda: events(canvas_log, 'double'), 5, 'the double click')
        s.check(True, 'a double click arrives as one')
        before, released = len(events(canvas_log, 'motion')), len(events(canvas_log, 'release'))
        computer.execute({'type': 'drag', 'path': [{'x': gx, 'y': gy}, {'x': gx + 150, 'y': gy + 60}]})
        release = s.wait_for(lambda: events(canvas_log, 'release')[released:], 5, 'the drag')[-1]
        moves = events(canvas_log, 'motion')[before:]
        s.check(moves and all(m['held'] for m in moves) and abs(release['x'] - first['x'] - 150) <= 1
                and abs(release['y'] - first['y'] - 60) <= 1, 'a drag moves with the button held and lets go at its end')
        computer.execute({'type': 'scroll', 'x': gx, 'y': gy, 'scroll_y': 300})
        scrolls = s.wait_for(lambda: events(canvas_log, 'scroll'), 5, 'the scroll')
        s.check(sum(e['dy'] for e in scrolls) > 0 or 'DOWN' in scrolls[-1]['direction'], 'a scroll down scrolls down')
        computer.execute({'type': 'keypress', 'keys': ['ENTER']})
        computer.execute({'type': 'keypress', 'keys': ['a']})
        backend.input.type_text('ok')
        keys = s.wait_for(lambda: len(events(canvas_log, 'key')) >= 4 and events(canvas_log, 'key'), 5, 'the keys')
        s.check([k['name'] for k in keys][:4] == ['Return', 'a', 'o', 'k'], 'keys and ASCII typing arrive as key events')

        # ---- a popup belongs to the window's screenshot (E2) ----
        computer.execute({'type': 'click', 'x': gx + 200, 'y': gy + 50, 'button': 'right'})
        related = s.wait_for(lambda: s.kwin_api.target(window['id'])['related'], 5, 'the popup menu')
        args, region, scope = computer.screen._region()
        menu = related[0]['frame']

        def inside(f):
            return (region[0] <= f[0] and region[1] <= f[1] and f[0] + f[2] <= region[0] + region[2] + 1
                    and f[1] + f[3] <= region[1] + region[3] + 1)
        s.check(args[0] == 'area' and inside(frame) and inside(menu)
                and scope == 'the "Canvas" window with its open menus and dialogs',
                f'with a menu open, the screenshot takes the window with its menu ({region}, menu {menu}) and says so')
        s.key('ESCAPE')

        # ---- desktop_window (E5) ----
        s.start(['python3', str(tmp / 'app.py'), 'rungic-test-unsaved', str(tmp / 'unsaved.log'), 'Unsaved', 'unsaved'])
        s.wait_for(lambda: s.find(cls='rungic-test-unsaved'), 10, 'the unsaved window')
        unsaved = next(w for w in cua.windows()['windows'] if w['app'] == 'rungic-test-unsaved')
        asked = cua.call('desktop_window', {'window_id': unsaved['id'], 'action': 'close'})
        s.check(asked['still_open'] is True and 'still open' in asked['note'] and events(tmp / 'unsaved.log', 'asked to save'),
                'closing an app that asks to save leaves it open and says still_open; nobody answers for the user')
        cua.call('desktop_window', {'window_id': window['id'], 'action': 'minimize'})
        s.check(next(w for w in cua.windows()['windows'] if w['id'] == window['id'])['minimized'],
                'minimize minimizes')
        cua.call('desktop_window', {'window_id': window['id'], 'action': 'maximize'})
        s.wait_for(lambda: s.find(cls='rungic-test-canvas', frame=[0, 0, 1920, 1080]), 5, 'the maximized canvas')
        s.check(True, 'maximize fills the screen (and unminimizes)')
        closed = cua.call('desktop_window', {'window_id': window['id'], 'action': 'close'})
        s.check(closed['still_open'] is False and events(canvas_log, 'closed'), 'close closes it in one go')
        text_fallback_checks(s, tmp)
        steps = list(s.steps)
    # Separate real compositors: the map actually differs from the default US
    # map. A fresh bus prevents a child from using the previous KWin.
    for layout in ('gb', 'de'):
        child = subprocess.run(['dbus-run-session', '--', sys.executable, __file__, '--text-layout', layout],
                               text=True, capture_output=True, timeout=90)
        rows = [json.loads(line) for line in child.stdout.splitlines() if line.startswith('{')]
        if child.returncode or not rows or not rows[-1]['passed']:
            raise harness.Failed(f'layout {layout}: {child.stdout[-2000:]} {child.stderr[-500:]}')
        steps += rows[-1]['steps']
    return steps


def text_fallback_checks(s, tmp, layout='us'):
    from rungic_cua.backend import LinuxAtspiBackend
    from rungic_cua.portal import keysym
    from gi.repository import Gio, GLib
    from rungic_cua.a11y import A11yBus
    real = LinuxAtspiBackend.__new__(LinuxAtspiBackend)
    real.bus, real.kwin, real.input = A11yBus(), s.kwin_api, s.pointer()
    was_enabled = real.bus.enabled()
    try:
        real.bus.set_enabled(True)
        layouts = Gio.bus_get_sync(Gio.BusType.SESSION).call_sync(
            'org.kde.keyboard', '/Layouts', 'org.kde.KeyboardLayouts', 'getLayoutsList', None, None, 0, 3000).unpack()[0]
        s.check(layouts and (layouts[0][0] == layout or (layout == 'us' and layouts[0][2] == 'English (US)')),
                f'actual KWin layout list={layouts!r}, expected {layout}')
        s.start(['python3', str(tmp / 'app.py'), 'rungic-test-entry', str(tmp / 'entry.log'), 'Input', 'entry'])
        s.wait_for(lambda: s.find(cls='rungic-test-entry', active=True), 10, 'the real GTK input field')
        real._window, real._root = real.active_window()
        real._origin = real.bus.origin(real._root)
        node = next(n for n in real.bus.tree(real._root) if n.name == 'fallback input')
        log = tmp / 'entry.log'
        def readback():
            rows = events(log, 'text')
            return rows[-1]['text'] if rows else ''
        def check_text(expected, label):
            s.wait_for(lambda: readback() == expected, 5, f'{label}: expected {expected!r}, actual {readback()!r}')
            actual = real.bus.call(node.bus, node.path, 'org.a11y.atspi.Text', 'GetText', GLib.Variant('(ii)', (0, -1)))[0]
            s.check(actual == expected, f'{layout}: {label}, actual text={actual!r}')
        for text in ('Rungic09AbC123 A ! @', '中文', '🙂'):
            real.input.chord(['CTRL', 'A'])
            real.input.type_text(text)
            check_text(text, 'helper case-exact field and AT-SPI readback')
        # Explicitly reject a late invalid character before sending the prefix.
        before = readback()
        for text in ('prefix\x00', 'prefix\n'):
            try:
                real.input.type_text(text)
            except RuntimeError as error:
                s.check('no text key' in str(error), f'{layout}: unsupported text reports an error')
            else:
                raise harness.Failed('unsupported text silently accepted')
            time.sleep(.1)
            s.check(readback() == before, f'{layout}: unsupported text sends no prefix')
        for command in ('text ff', 'text 0', 'text'):
            try:
                real.input._send(command)
            except RuntimeError:
                pass
            else:
                raise harness.Failed(f'malformed input accepted: {command}')
        s.check(readback() == before, f'{layout}: malformed requests leave the field unchanged')
        # Global state checks must work even though the helper never has focus.
        sym = keysym('SHIFT')
        real.input.key(sym, True)
        try:
            real.input.type_text('A!@')
        except RuntimeError as error:
            s.check('inactive modifiers' in str(error), f'{layout}: held Shift rejects text explicitly')
        else:
            raise harness.Failed('text was accepted while Shift was held')
        finally:
            real.input.key(sym, False)
        s.check(readback() == before, f'{layout}: held-Shift rejection leaves the field unchanged')
        real.input.chord(['CTRL', 'A'])
        real.input.type_text('a')
        check_text('a', 'modifier state restored after mixed text and rejection')
        # Actual D-Bus UnknownMethod, never an injected error. Backend ASCII
        # uses helper events; Unicode uses this real GTK EditableText object.
        try:
            s.kwin_api.commit_text('ignored')
        except GLib.Error as error:
            s.check(Gio.DBusError.get_remote_error(error) == 'org.freedesktop.DBus.Error.UnknownMethod',
                    f'{layout}: upstream compositor genuinely lacks commitText')
        else:
            raise harness.Failed('fixture unexpectedly has commitText; fallback untested')
        for text in ('Rungic09AbC123 A ! @', '中文'):
            real._type_text(node, text)
            check_text(text, 'backend fallback readback')
        from typesafe_computer_use import linux
        linux._state = SimpleNamespace(backend=real, stale=lambda: None)
        real.input.chord(['CTRL', 'A'])
        real.input.chord(['BACKSPACE'])
        linux.type_text('Rungic09AbC123 A ! @')
        check_text('Rungic09AbC123 A ! @', 'actual TypeSafe ASCII fallback readback')
        before = readback()
        try:
            linux.type_text('中文')
        except GLib.Error as error:
            s.check(Gio.DBusError.get_remote_error(error) == 'org.freedesktop.DBus.Error.UnknownMethod',
                    f'{layout}: TypeSafe without commitText reports Unicode unsupported')
        else:
            raise harness.Failed('TypeSafe Unicode silently accepted without commitText')
        s.check(readback() == before, f'{layout}: TypeSafe unsupported Unicode leaves text unchanged')
        if layout == 'gb':
            real._type_text(node, 'A!@£')
            check_text('A!@£', 'British map and Unicode fallback')
        if layout == 'de':
            real.input.chord(['CTRL', 'A'])
            real.input.type_text('Yz@€')
            check_text('Yz@€', 'German map including AltGr symbols')
    finally:
        real.bus.set_enabled(was_enabled)
        real.input.close()


def layout_test(layout):
    tmp = Path('/tmp/cua-text-' + layout.replace(',', '-'))
    tmp.mkdir(exist_ok=True)
    setup(tmp)
    config = tmp / 'config'
    config.mkdir()
    (config / 'kxkbrc').write_text('[Layout]\nUse=true\nLayoutList=' + layout + '\n')
    os.environ['XDG_CONFIG_HOME'] = str(config)
    os.environ['RUNGIC_WORKSPACE'] = '1'
    with harness.Session(1920, 1080, 'text-' + layout.replace(',', '-')) as s:
        text_fallback_checks(s, tmp, layout)
        return s.steps


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--text-layout':
        harness.run('text_layout', lambda: layout_test(sys.argv[2]))
    else:
        harness.run('cua_desktop', test)
