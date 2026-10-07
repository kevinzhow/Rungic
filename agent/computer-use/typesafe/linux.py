"""Linux adapter: KDE Plasma on Wayland, through rungic-cua's AT-SPI, KWin and portal access (docs/64).

The same functions macos.py and windows.py provide, for the Rungic desktop (Plasma Mobile on the phone).
Wayland gives a client no global coordinates and no input injection, so each part goes through the
desktop's own service:

  - capture: KWin ScreenShot2, through the small rungic-screenshot helper that holds the permission;
  - windows: KWin scripting (rungic_cua.kwin), one query per capture;
  - accessibility: AT-SPI over D-Bus (rungic_cua.a11y), the active window's tree read once per capture;
  - input: the RemoteDesktop portal (pointer, keys) and KWin's input-method commit for text.

"The display" is the output the active window is on, the phone's panel or the TV, and screen points
are that output's logical coordinates from its top-left corner. A capture is in native pixels, so
the scale is the output's device pixel ratio.

A press on an accessibility element is a real pointer click on its current position, as rungic-cua
does everywhere: Qt applications, WeChat among them, accept pointer input far more reliably than
AT-SPI actions. The action interface is only for elements without a position.

The escape hatch is a file instead of a screen corner: creating $XDG_RUNTIME_DIR/rungic-clicker/abort
stops the run at the next check (the voice assistant does this when the user says stop).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from PIL import Image

from .ax_walk import AX_PRESS, AxAttrs, Frame, walk_actionable
from .models import TEXT_ROLES, Abort, AxNode, Field, Missed

from rungic_cua import a11y  # noqa: I001 - rungic-cua is on PYTHONPATH (/usr/local/lib/rungic-cua)
from rungic_cua.a11y import Node
from rungic_cua.kwin import missing_commit_text
from rungic_cua.backend import CLICK_ACTIONS, LinuxAtspiBackend

SCREENSHOT = os.environ.get("RUNGIC_SCREENSHOT", "/usr/local/libexec/rungic-screenshot")
ABORT_FILE = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "rungic-clicker" / "abort"
MIN_WINDOW_SIDE_PT = 50.0
CLICK_TOLERANCE_PT = 2.0
TEXT_SETTLE = 0.15  # seconds for committed text to reach the client and its accessibility object
LINES_PER_NOTCH = 3  # the actions ask for lines, as on macOS; the portal scrolls in wheel notches

# AT-SPI role names on the "AX*" vocabulary ax_walk.py and models.py speak, as windows.py does for
# UI Automation. Unlisted roles are containers or decoration and map to "".
ROLE_TO_AX = {
    "push button": "AXButton",
    "button": "AXButton",
    "toggle button": "AXButton",
    "button menu": "AXMenuButton",
    "check box": "AXCheckBox",
    "radio button": "AXRadioButton",
    "combo box": "AXComboBox",
    "menu item": "AXMenuBarItem",
    "check menu item": "AXMenuBarItem",
    "radio menu item": "AXMenuBarItem",
    "page tab": "AXTab",
    "list item": "AXRow",
    "tree item": "AXRow",
    "table cell": "AXCell",
    "link": "AXLink",
    "entry": "AXTextField",
    "password text": "AXTextField",
    "editbar": "AXTextField",
    "spin button": "AXIncrementor",
    "slider": "AXSlider",
    "icon": "AXImage",
    "image": "AXImage",
    "label": "AXStaticText",
    "static": "AXStaticText",
    "heading": "AXStaticText",
    "panel": "AXGroup",
    "filler": "AXGroup",
    "section": "AXGroup",
}
# Keys the actions press, on rungic_cua.portal's key names. Command becomes Control, except
# Command-[, a browser's Back, which is Alt-Left here as on Windows.
KEYS = {"return": "ENTER", "tab": "TAB", "escape": "ESCAPE", "a": "A", "delete": "DELETE", "left": "ARROW_LEFT", "[": "LEFT_BRACKET"}
COMMAND_CHORDS = {"[": ["ALT", "ARROW_LEFT"]}
# QImage::Format values ScreenShot2 reports, as PIL raw modes (little-endian memory order).
RAW_MODES = {4: "BGRX", 5: "BGRA", 6: "BGRA", 16: "RGBX", 17: "RGBA", 18: "RGBA"}


# ------------------------------------------------------------------ pure rules


def role_for(atspi_role: str, editable: bool = False) -> str:
    """The AX role an AT-SPI role stands for. Plain "text" is a field only when it is editable."""
    if atspi_role in ("text", "paragraph"):
        return "AXTextArea" if editable else "AXStaticText"
    return ROLE_TO_AX.get(atspi_role, "")


def key_names(key: str, command: bool = False) -> list[str]:
    if command:
        return COMMAND_CHORDS.get(key, ["CTRL", KEYS[key]])
    return [KEYS[key]]


def scroll_notches(lines: int) -> tuple[str, int]:
    """Direction and wheel notches for `lines` lines; positive scrolls up, as on macOS."""
    return ("UP" if lines > 0 else "DOWN"), max(1, round(abs(lines) / LINES_PER_NOTCH))


def to_points(global_xy: tuple[float, float], output: dict) -> tuple[float, float]:
    ox, oy, _, _ = output["geometry"]
    return global_xy[0] - ox, global_xy[1] - oy


def to_global(point: tuple[float, float], output: dict) -> tuple[float, float]:
    ox, oy, _, _ = output["geometry"]
    return point[0] + ox, point[1] + oy


def on_output(point: tuple[float, float], output: dict) -> bool:
    _, _, w, h = output["geometry"]
    return 0 <= point[0] < w and 0 <= point[1] < h


def image_from_raw(header: dict, data: bytes) -> Image.Image:
    mode = RAW_MODES.get(header.get("format"))
    if mode is None:
        raise RuntimeError(f"unsupported capture format {header.get('format')}")
    size = (header["width"], header["height"])
    return Image.frombuffer("RGBA", size, data, "raw", mode, header["stride"], 1).convert("RGB")


# ------------------------------------------------------------------ session state


class _Desktop:
    """rungic-cua's objects, and what the latest capture saw.

    One LinuxAtspiBackend supplies the accessibility bus, KWin and the portal session, so a run asks
    for the RemoteDesktop grant once. The AT-SPI tree is read at most once per capture and dropped
    by any input, which may change it.
    """

    def __init__(self) -> None:
        self.backend = LinuxAtspiBackend()
        self.info: dict = {}
        self.output: dict | None = None
        self.tree: list[Node] | None = None
        self.root: Node | None = None
        self.origin: tuple | None = None
        self.frames: dict[int, Frame] = {}

    @property
    def active(self) -> dict | None:
        return self.info.get("active")

    def refresh(self) -> None:
        """KWin's windows and outputs; the display is the active window's output."""
        self.info = self.backend.kwin.windows()
        outputs = {o["name"]: o for o in self.info.get("outputs", [])}
        active = self.active
        name = active.get("output") if active else None
        self.output = outputs.get(name) or next(iter(outputs.values()), None)
        self.backend._window = active  # for the backend's own helpers (the virtual keyboard)
        self.stale()

    def stale(self) -> None:
        self.tree = None
        self.frames = {}

    def read_tree(self) -> list[Node]:
        """The active window's showing accessibility nodes, its popups, their details and frames."""
        if self.tree is not None:
            return self.tree
        self.tree, self.root, self.origin, self.frames = [], None, None, {}
        active = self.active
        if not active:
            return self.tree
        bus = self.backend.bus
        root = _window_node(bus, active)
        if root is None:
            return self.tree
        nodes = bus.tree(root, max_nodes=self.backend.max_elements, max_depth=self.backend.max_depth)
        self.origin = bus.origin(root)
        popups = self.backend._popups(root, self.backend.max_elements - len(nodes))
        nodes += popups
        bus.details([n for n in nodes if n.interfaces])
        self.root = _Root([root, *[p for p in popups if p.parent is None]])
        self.frames = self._frames(nodes)
        self.tree = nodes
        return nodes

    def _frames(self, nodes: list[Node]) -> dict[int, Frame]:
        """Frames in screen points, all in one batch of GetExtents calls."""
        if self.origin is None or self.output is None or not self.active:
            return {}
        with_component = [n for n in nodes if "org.a11y.atspi.Component" in n.interfaces]
        requests = [(n.bus, n.path, "org.a11y.atspi.Component", "GetExtents", a11y.GLib.Variant("(u)", (0,))) for n in with_component]
        cx, cy, _, _ = self.active["client"]
        frames = {}
        for node, result in zip(with_component, self.backend.bus.batch(requests), strict=True):
            if isinstance(result, Exception):
                continue
            x, y, w, h = result[0]
            if w <= 0 or h <= 0:
                continue
            gx, gy = cx + x - self.origin[0], cy + y - self.origin[1]
            px, py = to_points((gx, gy), self.output)
            frames[id(node)] = (float(px), float(py), float(w), float(h))
        return frames

    def frame_now(self, node: Node) -> Frame | None:
        """A node's current frame in screen points, read again (it may have moved since the capture)."""
        if self.output is None or not self.active:
            return None
        extents = self.backend.bus.extents(node, self.origin)
        if extents is None:
            return None
        cx, cy, _, _ = self.active["client"]
        x, y, w, h = extents
        px, py = to_points((cx + x, cy + y), self.output)
        return float(px), float(py), float(w), float(h)


class _Root:
    """The window and its popups under one parent, since the walk starts from a single node."""

    def __init__(self, children: list) -> None:
        self.children = children


def _window_node(bus, active: dict) -> Node | None:
    """The AT-SPI window of KWin's active window: the active frame of that process, else the one
    whose title matches KWin's caption (rungic_cua.backend.active_window, without asking KWin again)."""
    candidates = []
    for app in bus.applications():
        if bus.pid(app[0]) == active["pid"]:
            candidates += bus.windows(app)
    if not candidates:
        return None
    return (
        next((w for w in candidates if w.state(a11y.ACTIVE)), None)
        or next((w for w in candidates if w.name and w.name in active.get("caption", "")), None)
        or candidates[0]
    )


_state: _Desktop | None = None


def _desktop() -> _Desktop:
    global _state
    if _state is None:
        _state = _Desktop()
    return _state


# ------------------------------------------------------------------ escape hatch


def check_abort() -> None:
    if ABORT_FILE.exists():
        raise Abort("stopped by the user")


def sleep_watching(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        check_abort()
        time.sleep(0.1)


def accessibility_trusted() -> bool:
    """No permission gate here: the accessibility bus is switched on for the session (rungic-cua)."""
    try:
        return _desktop().backend.bus.enabled()
    except Exception:
        return False


# ------------------------------------------------------------------ input


def _input():
    d = _desktop()
    d.stale()
    return d.backend.input


def click_at(point: tuple[float, float]) -> None:
    """Glide there, confirm the pointer arrived, then press and release (rungic_cua.portal)."""
    d = _desktop()
    if d.output is None or not on_output(point, d.output):
        raise Missed(f"{point} is not on the display")
    target = to_global(point, d.output)
    source = _input()
    source.glide(*target)
    actual = d.backend.kwin.cursor()
    if abs(actual[0] - target[0]) > CLICK_TOLERANCE_PT or abs(actual[1] - target[1]) > CLICK_TOLERANCE_PT:
        raise Missed(f"the pointer went to {actual}, not {target}")
    source.click(*target)


def press(key: str, command: bool = False) -> None:
    _input().chord(key_names(key, command))


def type_text(text: str) -> None:
    """Commit the text as an input method would: any script, whatever the keyboard layout."""
    d = _desktop()
    source = _input()
    d.backend._no_virtual_keyboard()
    try:
        d.backend.kwin.commit_text(text)
    except a11y.GLib.Error as error:
        if not missing_commit_text(error) or not (text.isascii() and text.isprintable()):
            raise
        source.type_text(text)  # a KWin without commitText: Latin text as key events
    time.sleep(TEXT_SETTLE)


def clear_field() -> None:
    source = _input()
    source.chord(["CTRL", "A"])
    time.sleep(0.05)
    source.chord(["BACKSPACE"])


def scroll(lines: int) -> None:
    """Scroll events go to the view under the pointer: scroll over the active window's centre."""
    d = _desktop()
    active = d.active
    if not active:
        return
    x, y, w, h = active["client"]
    direction, notches = scroll_notches(lines)
    _input().scroll(x + w / 2, y + h / 2, direction, notches)


# ------------------------------------------------------------------ apps and windows


def frontmost_app_and_pid() -> tuple[str, int]:
    active = _desktop().active
    if not active:
        return "", None
    return active.get("resource_class") or active.get("caption", ""), active["pid"]


def _find_window(app: str) -> dict | None:
    wanted = app.strip().lower()
    windows = _desktop().backend.kwin.windows().get("windows", [])
    return next((w for w in windows if str(w.get("resource_class", "")).lower() == wanted), None)


def activate(app: str, timeout: float = 3.0) -> bool:
    """Bring an app's window forward (on whichever screen it is) and confirm KWin made it active."""
    d = _desktop()
    window = _find_window(app)
    if window is None:
        return False
    d.stale()
    d.backend.kwin.activate(window["id"])
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        active = d.backend.kwin.windows().get("active")
        if active and active.get("id") == window["id"]:
            return True
        time.sleep(0.15)
    return False


def open_url(browser: str, url: str) -> bool:
    subprocess.Popen([browser.lower(), "--new-tab", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    end = time.monotonic() + 8
    while time.monotonic() < end:
        if activate(browser, timeout=1.0):
            return True
    return False


def browser_url(browser: str) -> str | None:
    """Not read: Firefox exposes the address bar only as an unlabelled entry deep in its chrome."""
    return None


def open_path(path: Path, as_text: bool = False) -> None:
    subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def frontmost_window_bounds(pid: int | None = None) -> tuple[float, float, float, float] | None:
    d = _desktop()
    active = d.active
    if not active or d.output is None or (pid is not None and active["pid"] != pid):
        return None
    x, y, w, h = active["client"]
    px, py = to_points((x, y), d.output)
    if w > MIN_WINDOW_SIDE_PT and h > MIN_WINDOW_SIDE_PT:
        return float(px), float(py), float(w), float(h)
    return None


# ------------------------------------------------------------------ capture, OCR, and accessibility


def screenshot() -> Image.Image:
    """The display: the active window's output, in native pixels, without the cursor."""
    d = _desktop()
    d.refresh()
    if d.output is None:
        raise RuntimeError("KWin reports no output")
    result = subprocess.run([SCREENSHOT, "screen", d.output["name"]], capture_output=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(f"rungic-screenshot failed: {result.stderr.decode(errors='replace').strip()}")
    header_end = result.stdout.index(b"\n")
    header = json.loads(result.stdout[:header_end])
    return image_from_raw(header, result.stdout[header_end + 1 :])


def display_scale(image: Image.Image) -> float:
    output = _desktop().output
    return image.width / output["geometry"][2] if output else 1.0


def recognize_text(image: Image.Image) -> list[tuple[str, float, tuple[float, float, float, float]]]:
    from .linux_ocr import recognize

    output = _desktop().output
    return recognize(image, output["scale"] if output else 1.0)


def _label(node: Node) -> str:
    return (node.name or "").strip() or (node.description or "").strip()[:120]


def _editable(node: Node) -> bool:
    return "org.a11y.atspi.EditableText" in node.interfaces or node.state(a11y.EDITABLE)


def focused_field() -> Field | None:
    """The focused node of the active window's tree, read afresh if an action has happened since."""
    d = _desktop()
    # Toolkits mark the containers of the focus FOCUSED too (Qt Quick: the view, the panel): take a
    # text control if one is focused, else the deepest focused node (read breadth first, so the last).
    focused = [n for n in d.read_tree() if n.state(a11y.FOCUSED) and n.role not in ("frame", "window", "dialog")]
    texts = [n for n in focused if _editable(n) or role_for(n.role, _editable(n)) in TEXT_ROLES]
    if not focused:
        return None
    node = (texts or focused)[-1]
    x, y, w, h = d.frames.get(id(node), (0.0, 0.0, 0.0, 0.0))
    return Field(
        role=role_for(node.role, _editable(node)),
        label=_label(node),
        placeholder="",
        value=node.value if isinstance(node.value, str) else "",
        x=x,
        y=y,
        w=w,
        h=h,
        ref=node,
    )


def actionable_elements(pid: int, display_w_pt: float, display_h_pt: float) -> tuple[list[AxNode], list[AxNode], bool]:
    """Labelled controls of the active window and its popups, in screen points. Hidden nodes are not
    read (rungic_cua reads showing nodes only), so the off-screen list stays empty."""
    d = _desktop()
    active = d.active
    if not active or active["pid"] != pid:
        return [], [], False
    d.read_tree()
    if d.root is None:
        return [], [], False
    return walk_actionable(d.root, _children, _attrs, _actions, display_w_pt, display_h_pt)


def _children(node) -> list:
    return [c for c in node.children if isinstance(node, _Root) or c.state(a11y.SHOWING)]


def _attrs(node) -> AxAttrs:
    """An unnamed container reports no frame. Qt Quick nests fillers with one and the same frame,
    and the walk keys controls by role, label and frame, so the inner filler, and every control
    under it, would be skipped as a duplicate of the outer one."""
    if isinstance(node, _Root):
        return AxAttrs("AXGroup", "", None)
    role, label = role_for(node.role, _editable(node)), _label(node)
    container = role in ("", "AXGroup") and not label
    return AxAttrs(role, label, None if container else _desktop().frames.get(id(node)))


def _actions(node) -> list[str]:
    if isinstance(node, _Root):
        return []
    return [AX_PRESS] if any(a in CLICK_ACTIONS for a in node.actions) else []


# ------------------------------------------------------------------ acting on an element


def _click_node(ref) -> bool:
    """A pointer click on the element's current position, when it has one on the display."""
    d = _desktop()
    frame = d.frame_now(ref)
    if frame is None or d.output is None:
        return False
    x, y, w, h = frame
    center = (x + w / 2, y + h / 2)
    if not on_output(center, d.output):
        return False
    _input().click(*to_global(center, d.output))
    return True


def ax_press(ref) -> bool:
    """A pointer click on the element's current position; its AT-SPI action when it has none."""
    if _click_node(ref):
        return True
    index = next((i for i, name in enumerate(ref.actions) if name in CLICK_ACTIONS), None)
    if index is None:
        return False
    d = _desktop()
    d.stale()
    try:
        return bool(d.backend.bus.call(ref.bus, ref.path, "org.a11y.atspi.Action", "DoAction", a11y.GLib.Variant("(i)", (index,)))[0])
    except a11y.GLib.Error:
        return False


def ax_focus(ref) -> bool:
    """Click into the element, as a user would; GrabFocus for one without a position."""
    if _click_node(ref):
        time.sleep(0.1)
        return True
    try:
        return bool(_desktop().backend.bus.call(ref.bus, ref.path, "org.a11y.atspi.Component", "GrabFocus")[0])
    except a11y.GLib.Error:
        return False


def ax_set_value(ref, text: str) -> bool:
    """Replace the focused element's text: select all, then commit the text through KWin. The runner
    reads the value back, so a field that ignored it is caught there."""
    try:
        _input().chord(["CTRL", "A"])
        time.sleep(0.05)
        type_text(text)
    except Exception:
        return False
    return True


def ax_value(ref) -> str | None:
    bus = _desktop().backend.bus
    try:
        if "org.a11y.atspi.Text" in ref.interfaces:
            return bus.call(ref.bus, ref.path, "org.a11y.atspi.Text", "GetText", a11y.GLib.Variant("(ii)", (0, -1)))[0]
        if "org.a11y.atspi.Value" in ref.interfaces:
            value = bus.call(ref.bus, ref.path, a11y.PROPS, "Get", a11y.GLib.Variant("(ss)", ("org.a11y.atspi.Value", "CurrentValue")))[0]
            return str(value)
    except a11y.GLib.Error:
        return None
    return None
