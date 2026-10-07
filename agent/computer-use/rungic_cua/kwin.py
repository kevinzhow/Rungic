"""Window information from KWin (docs/60).

KWin's D-Bus API has no "active window" query. A one-shot KWin script reads
`workspace` and calls back into this process with callDBus, which avoids
reading KWin's journal output as rungic-a11y does (seconds instead of ~0.1 s).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from gi.repository import Gio, GLib

def missing_commit_text(error: GLib.Error) -> bool:
    """Only an absent method permits compatibility input; transport failures do not."""
    return Gio.DBusError.get_remote_error(error) == 'org.freedesktop.DBus.Error.UnknownMethod'


INTERFACE = '''
<node><interface name="com.rungic.Cua"><method name="Report"><arg type="s" direction="in"/></method></interface></node>
'''
OBJECT_PATH = '/com/rungic/Cua'

WINDOW_JS = '''
function info(w) {
  if (!w) return null;
  const c = w.clientGeometry, f = w.frameGeometry;
  return {pid: w.pid, caption: w.caption, resource_class: String(w.resourceClass), active: w.active,
    minimized: w.minimized, normal: w.normalWindow, output: w.output ? w.output.name : "",
    client: [c.x, c.y, c.width, c.height], frame: [f.x, f.y, f.width, f.height], id: String(w.internalId)};
}
const wins = workspace.windowList();
const out = {active: info(workspace.activeWindow), windows: [], dialogs: [], screens: [], outputs: []};
for (let i = 0; i < workspace.screens.length; i++) {
  const s = workspace.screens[i], g = s.geometry;
  out.screens.push(String(s.name));
  out.outputs.push({name: String(s.name), geometry: [g.x, g.y, g.width, g.height], scale: s.devicePixelRatio});
}
for (let i = 0; i < wins.length; i++) {
  if (wins[i].normalWindow) out.windows.push(info(wins[i]));
  else if (wins[i].dialog) out.dialogs.push(info(wins[i]));
}
callDBus("SERVICE", "/com/rungic/Cua", "com.rungic.Cua", "Report", JSON.stringify(out));
'''

# The window an agent works in and what belongs to it (docs/68): the popups, menus and dialogs
# whose transientFor chain leads to it are separate windows in Wayland, often reaching outside it.
TARGET_JS = '''
function info(w) {
  if (!w) return null;
  const f = w.frameGeometry;
  return {id: String(w.internalId), pid: w.pid, caption: w.caption, resource_class: String(w.resourceClass),
    normal: w.normalWindow, dialog: w.dialog, minimized: w.minimized, output: w.output ? w.output.name : "",
    frame: [f.x, f.y, f.width, f.height]};
}
function belongs(w, target) {
  for (let p = w.transientFor, n = 0; p && n < 10; p = p.transientFor, n++) if (p === target) return true;
  return false;
}
const wins = workspace.stackingOrder;
let target = null;
for (let i = 0; i < wins.length; i++) if (String(wins[i].internalId) === "TARGET") target = wins[i];
const out = {target: info(target), active: info(workspace.activeWindow), related: [], outputs: []};
for (let i = 0; i < workspace.screens.length; i++) {
  const s = workspace.screens[i], g = s.geometry;
  out.outputs.push({name: String(s.name), geometry: [g.x, g.y, g.width, g.height], scale: s.devicePixelRatio});
}
if (target) for (let i = 0; i < wins.length; i++) {
  const w = wins[i];
  if (w !== target && !w.minimized && belongs(w, target)) out.related.push(info(w));
}
callDBus("SERVICE", "/com/rungic/Cua", "com.rungic.Cua", "Report", JSON.stringify(out));
'''

# An application's topmost window (a call app puts its call window on top of its others).
TOP_JS = '''
const wins = workspace.stackingOrder;
let found = null;
for (let i = wins.length - 1; i >= 0 && !found; i--) {
  const w = wins[i];
  if (!w.minimized && (w.normalWindow || w.dialog) && String(w.resourceClass).toLowerCase() === "CLASS") {
    const f = w.frameGeometry;
    found = {id: String(w.internalId), caption: w.caption, output: w.output ? w.output.name : "",
             frame: [f.x, f.y, f.width, f.height]};
  }
}
callDBus("SERVICE", "/com/rungic/Cua", "com.rungic.Cua", "Report", JSON.stringify({window: found}));
'''

CURSOR_JS = '''
const c = workspace.cursorPos;
callDBus("SERVICE", "/com/rungic/Cua", "com.rungic.Cua", "Report", JSON.stringify({x: c.x, y: c.y}));
'''

ACTIVATE_JS = '''
const wins = workspace.windowList();
let done = false;
for (let i = 0; i < wins.length; i++) {
  if (String(wins[i].internalId) === "TARGET") { wins[i].minimized = false; workspace.activeWindow = wins[i]; done = true; }
}
callDBus("SERVICE", "/com/rungic/Cua", "com.rungic.Cua", "Report", JSON.stringify({activated: done}));
'''


WINDOW_ACTION_JS = '''
const wins = workspace.windowList();
let result = {found: false};
for (let i = 0; i < wins.length; i++) {
  const w = wins[i];
  if (String(w.internalId) !== "TARGET") continue;
  result.found = true;
  const action = "ACTION";
  if (action === "close") w.closeWindow();
  else if (action === "minimize") w.minimized = true;
  else if (action === "maximize") { w.minimized = false; w.setMaximize(true, true); }
  else if (action === "restore") { w.minimized = false; w.setMaximize(false, false); workspace.activeWindow = w; }
  else if (action === "to_phone" || action === "to_tv") {
    const screens = workspace.screens;
    for (let j = 0; j < screens.length; j++) {
      const name = String(screens[j].name);
      if ((action === "to_tv") === (name.indexOf("CAST") === 0)) { workspace.sendClientToScreen(w, screens[j]); result.screen = name; break; }
    }
  }
}
callDBus("SERVICE", "/com/rungic/Cua", "com.rungic.Cua", "Report", JSON.stringify(result));
'''

# Armed before an app starts: its first normal window goes to the chosen screen
# (the TV while casting) and becomes active, so the phone is not interrupted.
PLACE_JS = '''
const classes = CLASSES;
let target = null;
for (let i = 0; i < workspace.screens.length; i++) {
  if (String(workspace.screens[i].name).indexOf("PREFIX") === 0) target = workspace.screens[i];
}
function added(w) {
  if (!w.normalWindow || classes.indexOf(String(w.resourceClass).toLowerCase()) < 0) return;
  workspace.windowAdded.disconnect(added);
  if (target && w.output !== target) workspace.sendClientToScreen(w, target);
  workspace.activeWindow = w;
  callDBus("SERVICE", "/com/rungic/Cua", "com.rungic.Cua", "Report",
           JSON.stringify({placed: true, id: String(w.internalId), screen: String(target ? target.name : w.output.name)}));
}
workspace.windowAdded.connect(added);
callDBus("SERVICE", "/com/rungic/Cua", "com.rungic.Cua", "Report", JSON.stringify({armed: true}));
'''

WINDOW_ACTIONS = ('close', 'minimize', 'maximize', 'restore', 'to_phone', 'to_tv')


class KWin:
    def __init__(self) -> None:
        self.session = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.context = GLib.MainContext.default()
        self.reports: list[str] = []
        node = Gio.DBusNodeInfo.new_for_xml(INTERFACE)
        self.session.register_object(OBJECT_PATH, node.interfaces[0], self._on_call, None, None)
        runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'rungic-cua'
        runtime.mkdir(mode=0o700, exist_ok=True)
        self.dir = runtime

    def _on_call(self, connection, sender, path, interface, method, args, invocation):
        self.reports.append(args.unpack()[0])
        invocation.return_value(None)

    def _kwin(self, method: str, path: str, interface: str, args=None):
        return self.session.call_sync('org.kde.KWin', path, interface, method, args, None, 0, 3000).unpack()

    def _script(self, source: str, timeout: float = 3.0, *, then=None, then_timeout: float = 0.0) -> dict:
        """Run a one-shot script and return its report. With `then`, call it after the
        first report and return the second one (None if it does not come in time)."""
        name = f'rungiccua{os.getpid()}x{time.monotonic_ns()}'
        script = self.dir / f'{name}.js'
        script.write_text(source.replace('SERVICE', self.session.get_unique_name()))
        self.reports.clear()
        try:
            number = self._kwin('loadScript', '/Scripting', 'org.kde.kwin.Scripting',
                                GLib.Variant('(ss)', (str(script), name)))[0]
            self._kwin('run', f'/Scripting/Script{number}', 'org.kde.kwin.Script')
            if not self._wait(1, timeout):
                raise RuntimeError('KWin script did not report')
            if then is None:
                return json.loads(self.reports[0])
            then()
            return json.loads(self.reports[1]) if self._wait(2, then_timeout) else None
        finally:
            try:
                self._kwin('unloadScript', '/Scripting', 'org.kde.kwin.Scripting', GLib.Variant('(s)', (name,)))
            except GLib.Error:
                pass
            script.unlink(missing_ok=True)

    def _wait(self, count: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while len(self.reports) < count and time.monotonic() < deadline:
            self.context.iteration(False) or time.sleep(0.01)
        return len(self.reports) >= count

    def windows(self) -> dict:
        """{'active': window or None, 'windows': [normal windows], 'dialogs': [dialog windows], 'screens': [names],
        'outputs': [{name, geometry, scale}]}; geometry is global logical."""
        return self._script(WINDOW_JS)

    def target(self, window_id: str) -> dict:
        """{'target': the window (None if gone), 'active', 'related': its popups and dialogs,
        'outputs'}; geometry is global logical."""
        if window_id and not window_id.replace('-', '').strip('{}').isalnum():
            raise ValueError(f'bad window id {window_id!r}')
        return self._script(TARGET_JS.replace('TARGET', window_id or '-'))

    def top_window(self, resource_class: str) -> dict | None:
        """The application's topmost unminimized window (stacking order), or None."""
        if not resource_class.replace('.', '').replace('-', '').replace('_', '').isalnum():
            raise ValueError(f'bad resource class {resource_class!r}')
        return self._script(TOP_JS.replace('CLASS', resource_class.lower())).get('window')

    def cursor(self) -> tuple[float, float]:
        position = self._script(CURSOR_JS)
        return float(position['x']), float(position['y'])

    def place_next(self, classes: list[str], screen_prefix: str, start, timeout: float = 12.0) -> dict | None:
        """Start an app with `start()`; its first window opens on the screen whose name
        starts with `screen_prefix` and becomes active. None if no window appeared."""
        source = PLACE_JS.replace('CLASSES', json.dumps([c.lower() for c in classes])).replace('PREFIX', screen_prefix)
        return self._script(source, then=start, then_timeout=timeout)

    def window_action(self, window_id: str, action: str) -> dict:
        """Window management through KWin itself. `close` is the title bar's close
        button: the application may still ask to save."""
        if action not in WINDOW_ACTIONS or not window_id.replace('-', '').strip('{}').isalnum():
            raise ValueError(f'Unsupported window action {action!r}')
        return self._script(WINDOW_ACTION_JS.replace('TARGET', window_id).replace('ACTION', action))

    def commit_text(self, text: str) -> None:
        """Commit text to the focused field as an input method would (KWin moto15)."""
        self._kwin('commitText', '/VirtualKeyboard', 'org.kde.kwin.VirtualKeyboard', GLib.Variant('(s)', (text,)))

    def activate(self, window_id: str) -> bool:
        return bool(self._script(ACTIVATE_JS.replace('TARGET', window_id)).get('activated'))
