"""arc-cua DesktopBackend for KDE Plasma on Wayland (docs/60).

Observation: AT-SPI tree of KWin's active window (pipelined D-Bus, a11y.py).
Execution uses ordinary input events, so applications see a user's input:
  - pointer clicks, scrolling and keys through the RemoteDesktop portal (KWin
    fake input), at global positions = KWin client geometry + window-relative
    AT-SPI extents, gliding instead of jumping;
  - text committed by KWin the way an input method does (VirtualKeyboard
    commitText, moto15): text-input for any language, key events otherwise.
Clients receive these as plain Wayland keyboard/pointer events, the same as a
finger on the phone or a real (uinput/HID) device would produce in this nested
compositor. AT-SPI actions (Action, EditableText, Value) are only a fallback
for controls without a screen position, and for sliders.
Every action re-reads its target first and refuses a changed one.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

from arc_cua.errors import StaleDesktopState, UnsupportedDesktopAction
from arc_cua.keyboard import parse_hotkey
from arc_cua.models import ActionKind, DesktopElement, DesktopSnapshot, ExecutableAction

from . import a11y
from .a11y import A11yBus, Node
from .kwin import KWin, missing_commit_text
from .portal import BTN_LEFT, BTN_RIGHT, RemoteInput

CLICK_ACTIONS = ('press', 'click', 'activate', 'toggle', 'jump', 'open', 'showmenu', 'expand or collapse')
POINTER_ROLES = {'push button', 'toggle button', 'check box', 'radio button', 'menu item', 'check menu item',
                 'radio menu item', 'page tab', 'list item', 'table cell', 'tree item', 'link', 'icon',
                 'combo box', 'button', 'button menu'}
DOUBLE_CLICK_ROLES = {'list item', 'table cell', 'tree item', 'icon'}
TEXT_ROLES = {'entry', 'text', 'password text', 'editbar', 'terminal', 'paragraph', 'combo box', 'spin button'}
STRUCTURAL_ROLES = {'frame', 'dialog', 'window', 'page tab list', 'tool bar', 'menu bar', 'menu', 'alert'}


def _element_id(node: Node) -> str:
    return 'at_' + hashlib.sha1(f'{node.bus}{node.path}'.encode()).hexdigest()[:14]


class LinuxAtspiBackend:
    """observe / is_fresh / execute over AT-SPI, KWin and the RemoteDesktop portal."""

    def __init__(self, *, max_elements: int = 600, max_depth: int = 40) -> None:
        # An agent workspace (docs/research/91) has no accessibility bus of its own; plan one
        # (screenshots, docs/68) does not need it.
        try:
            self.bus = A11yBus()
        except Exception:  # noqa: BLE001 (GLib.Error: no org.a11y.Bus on this session)
            self.bus = None
        self.kwin = KWin()
        if os.environ.get('RUNGIC_WORKSPACE'):
            from .fakeinput import WorkspaceInput
            self.input = WorkspaceInput(cursor=self.kwin.cursor)
        else:
            self.input = RemoteInput(cursor=self.kwin.cursor)
        self.max_elements = max_elements
        self.max_depth = max_depth
        self._nodes: dict[str, Node] = {}
        self._window: dict[str, Any] | None = None
        self._root: Node | None = None
        self._origin: tuple | None = None   # the window's own screen extents (a11y.origin)
        self.enabled_by_us = False
        self.set_accessibility(True)

    def set_accessibility(self, on: bool) -> None:
        """org.a11y.Status IsEnabled; running Qt applications register within ~2 s."""
        if self.bus is None:
            return
        if on and not self.bus.enabled():
            self.bus.set_enabled(True)
            self.enabled_by_us = True
            self._root = None
            time.sleep(2)
        elif not on and self.enabled_by_us:
            self.bus.set_enabled(False)
            self.enabled_by_us = False
            self._root = None

    # ---- active window ------------------------------------------------------------------
    def active_window(self) -> tuple[dict[str, Any], Node]:
        """KWin's active window and the AT-SPI window node of the same process."""
        info = self.kwin.windows()['active']
        if not info:
            raise RuntimeError('No active window')
        for _ in range(6):
            candidates = []
            for app in self.bus.applications():
                if self.bus.pid(app[0]) != info['pid']:
                    continue
                candidates += self.bus.windows(app)
            if candidates:
                # The active frame of that process, else one whose title matches KWin's caption.
                best = next((w for w in candidates if w.state(a11y.ACTIVE)), None) \
                    or next((w for w in candidates if w.name and w.name in info['caption']), None) \
                    or candidates[0]
                return info, best
            time.sleep(0.5)  # the application registers once accessibility is on
        raise RuntimeError(f"{info['resource_class']} ({info['caption']}) exposes no accessibility tree")

    # ---- DesktopBackend ------------------------------------------------------------------
    def observe(self) -> DesktopSnapshot:
        if self._root is None or self._window is None or not self._still_active():
            self._window, self._root = self.active_window()
        root = Node(self._root.bus, self._root.path, None)
        nodes = self.bus.tree(root, max_nodes=self.max_elements, max_depth=self.max_depth)
        self._origin = self.bus.origin(root)
        nodes += self._popups(root, self.max_elements - len(nodes))
        self.bus.details([n for n in nodes if n.interfaces])
        elements, refs = [], {}
        parent_ids: dict[int, str | None] = {}
        for node in nodes:
            parent_id = parent_ids.get(id(node.parent)) if node.parent else None
            element = self._element(node, parent_id)
            if element is None:
                parent_ids[id(node)] = parent_id
                continue
            parent_ids[id(node)] = element.id
            elements.append(element)
            refs[element.id] = node
        self._nodes = refs
        revision = hashlib.sha256(json.dumps([
            [e.id, e.role, e.name, e.value, e.enabled, e.focused, e.selected, e.expanded] for e in elements
        ], default=str).encode()).hexdigest()
        return DesktopSnapshot(
            application=self._window.get('resource_class') or root.name,
            window=root.name or self._window.get('caption', ''),
            revision=revision,
            elements=tuple(elements),
            context={'backend': 'linux_atspi', 'pid': self._window['pid'], 'window': self._window['id'],
                     'output': self._window.get('output'), 'bus': root.bus, 'path': root.path},
            captured_at_ms=round(time.time() * 1000),
        )

    def is_fresh(self, snapshot: DesktopSnapshot, action: ExecutableAction) -> bool:
        if snapshot.context.get('path') != (self._root.path if self._root else None) or not self._still_active():
            return False
        for target, guard in ((action.target_id, action.target_guard),
                              (action.secondary_target_id, action.secondary_target_guard)):
            if not target:
                continue
            node = self._nodes.get(target)
            if node is None:
                return False
            try:
                expected = snapshot.element(target)
            except KeyError:
                return False
            current = Node(node.bus, node.path, node.parent)
            self.bus._fill([current])
            self.bus.details([current])
            element = self._element(current, expected.parent_id)
            if element is None or element.semantic_guard() != guard:
                return False
        return True

    def execute(self, snapshot: DesktopSnapshot, action: ExecutableAction) -> None:
        if not self.is_fresh(snapshot, action):
            raise StaleDesktopState('Target changed before execution')
        kind = action.kind
        if kind == ActionKind.WAIT:
            time.sleep(0.2)
            return
        if kind == ActionKind.PRESS_KEY:
            self.input.chord([action.key or ''])
            return
        if kind == ActionKind.HOTKEY:
            modifiers, key = parse_hotkey(action.hotkey or '')
            self.input.chord([*modifiers, key])
            return
        if kind == ActionKind.SCROLL:
            x, y = self._window_center()
            self.input.scroll(x, y, action.scroll_direction or 'DOWN')
            return
        node = self._nodes.get(action.target_id or '')
        if node is None:
            raise StaleDesktopState('Target no longer exists')
        if kind == ActionKind.CLICK:
            # A real pointer click first: applications then see ordinary input events.
            # The accessibility action is only for controls without a screen position.
            if self.bus.extents(node, self._origin) is not None:
                self._pointer(node, BTN_LEFT, 1)
                return
            index = next((i for i, name in enumerate(node.actions) if name in CLICK_ACTIONS), None)
            if index is None or not self.bus.call(node.bus, node.path, 'org.a11y.atspi.Action', 'DoAction',
                                                  a11y.GLib.Variant('(i)', (index,)))[0]:
                raise UnsupportedDesktopAction('Target has neither a screen position nor a click action')
            return
        if kind in (ActionKind.DOUBLE_CLICK, ActionKind.RIGHT_CLICK):
            self._pointer(node, BTN_RIGHT if kind == ActionKind.RIGHT_CLICK else BTN_LEFT,
                          2 if kind == ActionKind.DOUBLE_CLICK else 1)
            return
        if kind == ActionKind.SET_VALUE and 'org.a11y.atspi.Value' in node.interfaces:
            try:
                number = float(action.value)
            except (TypeError, ValueError) as error:
                raise UnsupportedDesktopAction('SET_VALUE on a value control needs a number') from error
            self.bus.call(node.bus, node.path, a11y.PROPS, 'Set', a11y.GLib.Variant(
                '(ssv)', ('org.a11y.atspi.Value', 'CurrentValue', a11y.GLib.Variant('d', number))))
            return
        if kind in (ActionKind.TYPE_TEXT, ActionKind.SET_VALUE):
            self._type_text(node, str(action.value if action.value is not None else ''))
            return
        raise UnsupportedDesktopAction(f'LinuxAtspiBackend cannot execute {kind.value}')

    # ---- helpers --------------------------------------------------------------------------
    def _popups(self, root: Node, budget: int) -> list[Node]:
        """Showing popups of the same application: search suggestions, menus, combo
        lists. On Wayland they are xdg_popups, not windows KWin reports as active,
        and toolkits list them as separate accessible windows (WeChat's search
        results: an unnamed filler). Other frames and dialogs are separate windows
        and stay out. Their screen extents share the root window's origin."""
        found: list[Node] = []
        try:
            windows = self.bus.windows((root.bus, a11y.ROOT_PATH))
        except Exception:  # noqa: BLE001 - the application went away
            return found
        for window in windows:
            if budget <= 0:
                break
            if window.path == root.path or not window.state(a11y.SHOWING) or window.role in ('frame', 'dialog'):
                continue
            tree = self.bus.tree(Node(window.bus, window.path, None), max_nodes=budget, max_depth=self.max_depth)
            found += tree
            budget -= len(tree)
        return found

    def _still_active(self) -> bool:
        """Cheap check: the observed window still has the ACTIVE state."""
        if self._root is None:
            return False
        try:
            states = self.bus.call(self._root.bus, self._root.path, a11y.ACC, 'GetState')[0]
        except a11y.GLib.Error:
            return False
        return a11y.has_state(states, a11y.ACTIVE)

    def _element(self, node: Node, parent_id: str | None) -> DesktopElement | None:
        role = node.role
        actions: list[ActionKind] = []
        editable = 'org.a11y.atspi.EditableText' in node.interfaces or (
            node.state(a11y.EDITABLE) and role in TEXT_ROLES)
        clickable = any(a in CLICK_ACTIONS for a in node.actions) or (
            role in POINTER_ROLES and 'org.a11y.atspi.Component' in node.interfaces and bool(node.actions or node.name))
        if clickable:
            actions.append(ActionKind.CLICK)
        if role in DOUBLE_CLICK_ROLES:
            actions.append(ActionKind.DOUBLE_CLICK)
        if clickable and role in POINTER_ROLES:
            actions.append(ActionKind.RIGHT_CLICK)
        if editable:
            actions += [ActionKind.TYPE_TEXT, ActionKind.SET_VALUE]
        elif 'org.a11y.atspi.Value' in node.interfaces and role != 'scroll bar':
            actions.append(ActionKind.SET_VALUE)
        name = node.name or node.description
        value = node.value
        if isinstance(value, str):
            value = value[:200]
        if not (name or actions or value not in (None, '') or role in STRUCTURAL_ROLES):
            return None
        metadata = {}
        if node.accessible_id:
            # Qt builds it from the object path; the tail tells the controls apart.
            metadata['id'] = node.accessible_id[-48:]
        if node.description and node.description != name:
            metadata['description'] = node.description[:200]
        if node.state(a11y.CHECKED):
            metadata['checked'] = True
        return DesktopElement(
            id=_element_id(node),
            role=role,
            name=name[:200],
            value=value,
            actions=tuple(dict.fromkeys(actions)),
            enabled=node.state(a11y.ENABLED) or node.state(a11y.SENSITIVE),
            visible=node.state(a11y.SHOWING),
            focused=node.state(a11y.FOCUSED),
            selected=True if node.state(a11y.SELECTED) else None,
            expanded=node.state(a11y.EXPANDED) if node.state(a11y.EXPANDABLE) else None,
            parent_id=parent_id,
            source='linux_atspi',
            metadata=metadata,
        )

    def _global_center(self, node: Node) -> tuple[float, float]:
        extents = self.bus.extents(node, self._origin)
        if extents is None or self._window is None:
            raise UnsupportedDesktopAction('Target has no screen position')
        cx, cy, _, _ = self._window['client']
        x, y, w, h = extents
        return cx + x + w / 2, cy + y + h / 2

    def _window_center(self) -> tuple[float, float]:
        if self._window is None:
            raise UnsupportedDesktopAction('No active window')
        x, y, w, h = self._window['client']
        return x + w / 2, y + h / 2

    def _pointer(self, node: Node, button: int, count: int) -> None:
        x, y = self._global_center(node)
        self.input.click(x, y, button=button, count=count)

    def _type_text(self, node: Node, text: str) -> None:
        """Type like a user: click into the field, select all, then commit the text
        the way an input method does (KWin, any language). Accessibility text
        replacement is the last resort."""
        try:
            self._pointer(node, BTN_LEFT, 1)
        except UnsupportedDesktopAction:
            if 'org.a11y.atspi.Component' not in node.interfaces or not self.bus.call(
                    node.bus, node.path, 'org.a11y.atspi.Component', 'GrabFocus')[0]:
                self._set_text(node, text)
                return
        time.sleep(0.12)
        self._no_virtual_keyboard()
        self.input.chord(['CTRL', 'A'])
        time.sleep(0.05)
        try:
            self.kwin.commit_text(text)
        except a11y.GLib.Error as error:
            if not missing_commit_text(error):
                raise
            # Upstream/older KWin: keymap-based text or actual EditableText.
            if text.isascii() and text.isprintable():
                self.input.type_text(text)
            else:
                self._set_text(node, text)

    def _no_virtual_keyboard(self) -> None:
        """A text field on the phone's own screen summons the on-screen keyboard
        (KWIN_IM_SHOW_ALWAYS), which pushes the window up. The agent commits text
        directly, so put the keyboard away; the user's next touch on a text field
        brings it back."""
        if self._window is None or not str(self._window.get('output', '')).startswith('WL'):
            return
        try:
            self.bus.session.call_sync('org.kde.KWin', '/VirtualKeyboard', a11y.PROPS, 'Set', a11y.GLib.Variant(
                '(ssv)', ('org.kde.kwin.VirtualKeyboard', 'active', a11y.GLib.Variant('b', False))), None, 0, 2000)
        except a11y.GLib.Error:
            pass

    def _set_text(self, node: Node, text: str) -> None:
        if 'org.a11y.atspi.EditableText' not in node.interfaces or not self.bus.call(
                node.bus, node.path, 'org.a11y.atspi.EditableText', 'SetTextContents', a11y.GLib.Variant('(s)', (text,)))[0]:
            raise UnsupportedDesktopAction('Cannot enter text into this target')
