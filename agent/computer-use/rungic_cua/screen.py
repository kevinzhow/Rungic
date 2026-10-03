"""Shared KWin screenshots and portal input; no model or network calls."""
from __future__ import annotations
import base64
import io
import json
import os
import subprocess
import time
from PIL import Image
from .portal import BTN_LEFT, BTN_RIGHT, KEYSYMS
SCREENSHOT = os.environ.get('RUNGIC_SCREENSHOT', '/usr/libexec/rungic-screenshot')
SETTLE_S = 0.5          # after a batch, before the screenshot: animations and repaints
BTN_MIDDLE, BTN_FORWARD, BTN_BACK = 0x112, 0x115, 0x116
RAW_MODES = {4: 'BGRX', 5: 'BGRA', 6: 'BGRA', 16: 'RGBX', 17: 'RGBA', 18: 'RGBA'}   # rungic-screenshot's QImage formats
KEYSYM_EXTRA = {'SUPER': 0xffeb, 'CAPSLOCK': 0xffe5, 'INSERT': 0xff63, 'PRINTSCREEN': 0xff61}
KEY_ALIASES = {
    'RETURN': 'ENTER', 'ESC': 'ESCAPE', 'CONTROL': 'CTRL', 'OPTION': 'ALT', 'META': 'SUPER', 'CMD': 'SUPER',
    'COMMAND': 'SUPER', 'WIN': 'SUPER', 'WINDOWS': 'SUPER', 'ARROWUP': 'ARROW_UP', 'ARROWDOWN': 'ARROW_DOWN',
    'ARROWLEFT': 'ARROW_LEFT', 'ARROWRIGHT': 'ARROW_RIGHT', 'UP': 'ARROW_UP', 'DOWN': 'ARROW_DOWN',
    'LEFT': 'ARROW_LEFT', 'RIGHT': 'ARROW_RIGHT', 'PAGEUP': 'PAGE_UP', 'PAGEDOWN': 'PAGE_DOWN', 'DEL': 'DELETE',
    'SPACEBAR': 'SPACE',
}


def keysym_for(name: str) -> int:
    """OpenAI key names (ENTER, CTRL, ARROWUP, a, ...) to X keysyms, for the portal."""
    if len(name) == 1:
        return ord(name.lower()) if name.isalpha() else ord(name)
    key = name.upper().replace(' ', '')
    key = KEY_ALIASES.get(key, key)
    if key in KEYSYMS:
        return KEYSYMS[key]
    if key in KEYSYM_EXTRA:
        return KEYSYM_EXTRA[key]
    if key.startswith('F') and key[1:].isdigit():
        return 0xffbe + int(key[1:]) - 1
    raise ValueError(f'unsupported key {name!r}')


class Screen:
    """What the model sees and where its pixels are: the window it works in, rendered by KWin
    alone (CaptureWindow; whatever covers it), with its open popups and dialogs when there are
    some (CaptureArea over them all: they are separate windows in Wayland), or the whole output.
    The window follows the task: when it closes or another app's window becomes active on this
    output (a system dialog, a second app), that one is the window."""

    def __init__(self, backend, output_name: str, window_id: str | None = None) -> None:
        self.backend = backend
        self.output_name = output_name
        self.window_id = window_id      # None: the active window on this output
        self.whole = False              # the model asked for the whole screen
        self.origin = (0.0, 0.0)        # the image's top-left, global logical
        self.scale = 1.0                # image pixels per logical point
        self.clip = (0.0, 0.0, 1.0, 1.0)  # the output: where a click may land
        self.scope = ''                 # what the image shows, told to the model when it changes

    def _region(self) -> tuple[list[str], tuple[float, float, float, float], str]:
        kwin = self.backend.kwin
        info = kwin.target(self.window_id or '')
        outputs = {o['name']: o for o in info['outputs']}
        if self.output_name not in outputs:
            raise RuntimeError(f"no output {self.output_name} (is the assistant's screen on?)")
        screen = tuple(outputs[self.output_name]['geometry'])
        self.clip = screen
        self.output_scale = float(outputs[self.output_name].get('scale') or 1.0)
        target, active = info['target'], info['active']
        usable = lambda w: bool(w) and not w['minimized'] and w['output'] == self.output_name \
            and (w['normal'] or w['dialog'])
        if usable(active) and (not usable(target) or active['pid'] != target['pid']):
            self.window_id = active['id']            # the task moved to another window
            info = kwin.target(self.window_id)
            target = info['target']
        if self.whole or not usable(target):
            return ['screen', self.output_name], screen, 'the whole screen'
        name = target['caption'] or target['resource_class']
        if not info['related']:
            return ['window', target['id']], tuple(target['frame']), f'only the "{name}" window'
        frames = [target['frame']] + [w['frame'] for w in info['related']]
        x1 = max(min(f[0] for f in frames), screen[0])
        y1 = max(min(f[1] for f in frames), screen[1])
        x2 = min(max(f[0] + f[2] for f in frames), screen[0] + screen[2])
        y2 = min(max(f[1] + f[3] for f in frames), screen[1] + screen[3])
        x1, y1, x2, y2 = int(x1), int(y1), int(x2 + 0.999), int(y2 + 0.999)
        return (['area', str(x1), str(y1), str(x2 - x1), str(y2 - y1)], (x1, y1, x2 - x1, y2 - y1),
                f'the "{name}" window with its open menus and dialogs')

    def capture(self) -> tuple[str, Image.Image, bool]:
        """The image as a data URL, the image, and whether what it shows changed."""
        args, region, scope = self._region()
        done = subprocess.run([SCREENSHOT, *args], capture_output=True, timeout=15)
        if done.returncode != 0:
            raise RuntimeError(f'rungic-screenshot: {done.stderr.decode(errors="replace").strip()}')
        end = done.stdout.index(b'\n')
        header = json.loads(done.stdout[:end])
        mode = RAW_MODES.get(header.get('format'))
        if mode is None:
            raise RuntimeError(f"unsupported capture format {header.get('format')}")
        image = Image.frombuffer('RGBA', (header['width'], header['height']), done.stdout[end + 1:], 'raw', mode,
                                 header['stride'], 1).convert('RGB')
        # CaptureArea renders at the largest scale of all outputs (the phone's 3): back to this
        # output's own pixels, or the model would pay for 3x as many for nothing.
        wanted = (round(region[2] * self.output_scale), round(region[3] * self.output_scale))
        if image.width > wanted[0] * 1.05:
            image = image.resize(wanted, Image.LANCZOS)
        self.origin = (float(region[0]), float(region[1]))
        self.scale = image.width / region[2]
        changed = scope != self.scope
        self.scope = scope
        # JPEG q85 without chroma subsampling (docs/68): 190 KB against 1.3 MB of PNG for the whole
        # screen, 26 ms to encode on the phone, requests about half as long; accuracy no worse.
        buffer = io.BytesIO()
        image.save(buffer, 'JPEG', quality=85, subsampling=0)
        return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode(), image, changed

    def point(self, x: float, y: float) -> tuple[float, float]:
        gx, gy = self.origin[0] + x / self.scale, self.origin[1] + y / self.scale
        cx, cy, cw, ch = self.clip
        if not (cx <= gx < cx + cw and cy <= gy < cy + ch):
            raise ValueError(f'({x}, {y}) is outside the screen')
        return gx, gy

    def note(self) -> str:
        return (f'(This screenshot shows {self.scope}; coordinates are pixels of it. '
                'Call view_whole_screen to see everything.)' if not self.whole else
                '(This screenshot shows the whole screen; coordinates are pixels of it.)')




class Desktop:
    def __init__(self, backend, output_name: str, window_id: str | None = None) -> None:
        self.backend = backend
        self.screen = Screen(backend, output_name, window_id)

    # ---- actions ------------------------------------------------------------------------
    def _hold(self, keys, pressed: bool) -> None:
        for key in (keys or []) if pressed else reversed(keys or []):
            self.backend.input.key(keysym_for(key), pressed)

    def execute(self, action: dict) -> str:
        """Carry out one action; returns a short description for the step log."""
        kind = action.get('type')
        source = self.backend.input
        keys = action.get('keys') if kind != 'keypress' else None
        if kind in ('click', 'double_click', 'move', 'scroll', 'drag'):
            self._hold(keys, True)
        try:
            if kind in ('click', 'double_click'):
                button = {'left': BTN_LEFT, 'right': BTN_RIGHT, 'wheel': BTN_MIDDLE, 'middle': BTN_MIDDLE,
                          'back': BTN_BACK, 'forward': BTN_FORWARD}[action.get('button') or 'left']
                source.click(*self.screen.point(action['x'], action['y']), button=button,
                             count=2 if kind == 'double_click' else 1)
                return f"{kind} {action.get('button') or 'left'} ({action['x']}, {action['y']})"
            if kind == 'move':
                source.glide(*self.screen.point(action['x'], action['y']))
                return f"move ({action['x']}, {action['y']})"
            if kind == 'drag':
                path = [(p['x'], p['y']) if isinstance(p, dict) else tuple(p) for p in action['path']]
                if len(path) < 2:
                    raise ValueError('drag needs two points')
                source.press(*self.screen.point(*path[0]))
                for point in path[1:]:
                    source.glide(*self.screen.point(*point))
                source.release()
                return f'drag {path[0]} -> {path[-1]}'
            if kind == 'scroll':
                source.glide(*self.screen.point(action['x'], action['y']))
                for axis, delta in ((0, action.get('scroll_y') or 0), (1, action.get('scroll_x') or 0)):
                    if delta:
                        # About 100 px a wheel notch, as the guide's desktop handler counts.
                        notches = max(1, round(abs(delta) / 100))
                        source.scroll_notches(axis, notches if delta > 0 else -notches)
                return f"scroll ({action.get('scroll_x', 0)}, {action.get('scroll_y', 0)})"
            if kind == 'keypress':
                syms = [keysym_for(k) for k in action['keys']]
                for sym in syms:
                    source.key(sym, True)
                    time.sleep(0.02)
                for sym in reversed(syms):
                    source.key(sym, False)
                    time.sleep(0.01)
                return 'keys ' + '+'.join(action['keys'])
            if kind == 'type':
                self.backend._no_virtual_keyboard()
                self.backend.kwin.commit_text(action['text'])
                time.sleep(0.15)
                return f"type {action['text']!r}"
            if kind == 'wait':
                time.sleep(2)
                return 'wait'
            if kind == 'screenshot':
                return 'look'
            raise ValueError(f'unsupported action {kind!r}')
        finally:
            if kind in ('click', 'double_click', 'move', 'scroll', 'drag'):
                self._hold(keys, False)
