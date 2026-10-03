# SPDX-License-Identifier: MIT
"""The floating window's own side, real (agentscreen.cpp, floater.cpp, Main.qml built and installed)
in a portrait phone-sized headless KWin: what it asks of the screen's recording helper and of the
platform bridge, and what it shows, for desktop mode and for an assistant's screen.

The recording helper (rungic-workspace-stream, started through rungic-workspace-env) is a stand-in
first on PATH: it prints a node as the real one does and keeps every command the window sends it,
so the window's picture and input path is seen without a workspace's KWin and PipeWire. The
platform bridge is a stand-in of its contract that also answers the cast button and can say a TV
shows the screen. The window runs with WAYLAND_DEBUG, its Wayland requests in its error output."""
import json
import os
import signal
import time
from pathlib import Path

import contracts
import harness

ABOVE = 3
# The window's error output (harness.Session.start names it after its last argument).
DEBUG = Path('/tmp') / '--desktop.err'
HELPER = '''#!/usr/bin/python3
# rungic-workspace-env N /usr/libexec/rungic-workspace-stream [--pointer-hidden]: the stand-in.
import json, os, sys
log = os.environ["HELPER_LOG"]
with open(log, "a") as f:
    f.write(json.dumps({"pid": os.getpid(), "argv": sys.argv[1:]}) + "\\n")
print("node 42", flush=True)
for line in sys.stdin:
    with open(log, "a") as f:
        f.write(json.dumps({"pid": os.getpid(), "line": line.strip()}) + "\\n")
    if line.startswith("pointer-stream on"):
        print("pointer-node 43", flush=True)
'''


class Bridge(contracts.StandIn):
    """The contract's stand-in; the cast button (op tv with button) answered as the app does."""

    def answer(self, request):
        if request.get('op') == 'tv' and request.get('button'):
            return contracts.query(self.contract, request), self.contract_reply('tv')
        return super().answer(request)

    def contract_reply(self, name):
        return self.replies.get(name) or next(q['reply'] for q in self.contract['queries'] if q['name'] == name)


class Helper:
    def __init__(self, folder):
        self.log = folder / 'helper.log'
        path = folder / 'bin'
        path.mkdir()
        (path / 'rungic-workspace-env').write_text(HELPER)
        (path / 'rungic-workspace-env').chmod(0o755)
        os.environ['PATH'] = f'{path}:{os.environ["PATH"]}'
        os.environ['HELPER_LOG'] = str(self.log)

    def entries(self):
        try:
            return [json.loads(l) for l in self.log.read_text().splitlines()]
        except FileNotFoundError:
            return []

    def starts(self):
        return [e for e in self.entries() if 'argv' in e]

    def lines(self):
        return [e['line'] for e in self.entries() if 'line' in e]

    def alive(self):
        starts = self.starts()
        return bool(starts) and Path(f'/proc/{starts[-1]["pid"]}').exists() and \
            Path(f'/proc/{starts[-1]["pid"]}/stat').read_text().split()[2] != 'Z'


# Where things are (Main.qml, 1080x2400): the floating picture 72 % wide at (12, 110), 16:9, its
# toolbar under it (fullscreen, TV, tuck, close: 40 px buttons, 4 apart, 8 in).
WIDTH = round(1080 * 0.72)
HEIGHT = round(WIDTH * 9 / 16)


def floating_button(index, count=4):
    bar = count * 40 + (count - 1) * 4 + 16
    return 12 + (WIDTH - bar) / 2 + 8 + index * 44 + 20, 110 + HEIGHT + 10 + 20


def turned(sx, sy):
    """A point of fullscreen's stage (2400x1080, turned a quarter clockwise) on the 1080x2400 screen."""
    return 1080 - sy, sx


def full_button(index, count):
    bar = count * 40 + (count - 1) * 4 + 16
    return turned((2400 - bar) / 2 + 8 + index * 44 + 20, 1080 - 40 - 20 + 20)


def mark():
    return Path(os.environ['XDG_RUNTIME_DIR']) / 'rungic-agent-screen' / 'desktop-fullscreen'


# covers[system]: desktop-mode.floating-window/E7
def disabled_assistant(s, bridge):
    """A stale launch while the assistant screen is off must never map a window."""
    bridge.replies['agent-screen'] = {'enabled': False, 'workspace': 1, 'width': 1920, 'height': 1080,
                                     'tv': False, 'tvShown': [], 'tvHeard': -1, 'directorFocus': 1}
    for label, args in [('assistant screen', ['--workspace', '1']), ('desktop mode', ['--desktop'])]:
        process = s.start(['/usr/libexec/rungic-agent-screen-window', *args])
        mapped = []
        deadline = time.monotonic() + 5
        while process.poll() is None and time.monotonic() < deadline:
            mapped.extend(w for w in s.stack() if w['cls'] == 'rungic-agent-screen-window')
            time.sleep(.02)
        process.wait(5)
        s.check(process.returncode == 0 and not mapped,
                f'disabled {label} exits without ever mapping its black placeholder')


def desktop(s, bridge, helper):
    (s.runtime / 'wayland-ws-0').touch()         # the independent desktop runs: desktop mode is on
    window = s.start(['/usr/libexec/rungic-agent-screen-window', '--desktop'],
                     env={**os.environ, 'WAYLAND_DEBUG': '1'})
    s.wait_for(lambda: s.find(cls='rungic-agent-screen-window'), 20, 'the floating window')
    s.wait_for(helper.starts, 10, 'its picture asked for')
    s.check(helper.starts()[0]['argv'] == ['0', '/usr/libexec/rungic-workspace-stream', '--pointer-hidden'],
            "desktop mode's picture is recorded without the pointer: the touches are the pointer there")

    # The cast button casts this window's screen: computer mode.
    s.tap(12 + WIDTH / 2, 110 + HEIGHT / 2)
    s.tap(*floating_button(1))
    s.wait_for(lambda: any(r.get('button') for r in bridge.requests), 10, 'the cast button')
    cast = next(r for r in bridge.requests if r.get('button'))
    s.check(cast == {'op': 'tv', 'button': True, 'source': 0, 'content': 'desktop'},
            "the floating window's cast button asks the TV for computer mode")

    # Fullscreen (the toolbar still out), marked for the quick setting; a tap works the desktop
    # through the helper; Esc leaves.
    s.tap(*floating_button(0))
    s.wait_for(lambda: s.find(cls='com.rungic.DesktopMode', full=True), 10, 'fullscreen')
    s.wait_for(mark().exists, 5, 'the fullscreen mark')
    s.check(True, 'fullscreen is marked for rungic-desktop-mode status (the quick setting says Full screen)')
    time.sleep(1.5)                               # the toolbar shown on arrival has gone again
    before = len(helper.lines())
    s.tap(540, 1200)                              # the middle of the picture
    s.wait_for(lambda: 'button 272 0' in helper.lines()[before:], 5, 'a click')
    sent = helper.lines()[before:]
    point = next(l for l in sent if l.startswith('pointer '))
    fx, fy = (float(v) for v in point.split()[1:])
    s.check(abs(fx - 0.5) < 0.02 and abs(fy - 0.5) < 0.02 and sent.index('button 272 1') < sent.index('button 272 0'),
            'a tap in fullscreen clicks the desktop where the finger is (the pointer, then the left button)')
    s.key('ESCAPE')
    s.wait_for(lambda: not s.find(cls='com.rungic.DesktopMode'), 10, 'fullscreen left')
    s.wait_for(lambda: not mark().exists(), 5, 'the mark gone')
    s.check(True, 'out of fullscreen the mark goes')

    # The keyboard button: the window's own keyboard, never the phone's (no text-input of its own).
    s.tap(12 + WIDTH / 2, 110 + HEIGHT / 2)
    s.tap(*floating_button(0))
    s.wait_for(lambda: s.find(cls='com.rungic.DesktopMode', full=True), 10, 'fullscreen again')
    time.sleep(2)
    s.tap(540, 100)                               # the black bar: the toolbar
    s.tap(*full_button(2, 5))
    time.sleep(1.5)
    debug = DEBUG.read_text(errors='replace')
    s.check('zwp_text_input_manager_v3' in debug, 'KWin offers text-input (the phone keyboard would come with it)')
    s.check(not any('text_input' in l and ('.enable(' in l or '.activate(' in l) for l in debug.splitlines()),
            "the floating keyboard's typing asks no text-input of KWin: the phone's keyboard does not come up")
    s.check(mark().exists(), 'still fullscreen, with its keyboard')

    # A TV shows it (computer mode): the floating window goes, its picture goes on; back after.
    bridge.replies['desktop-mode'] = {'enabled': False, 'width': 1920, 'height': 1080, 'tv': True, 'watched': False}
    s.wait_for(lambda: not s.find(cls='rungic-agent-screen-window'), 10, 'the floating window put away')
    s.wait_for(lambda: not s.find(cls='com.rungic.DesktopMode'), 5, 'fullscreen dropped')
    s.check(not mark().exists(), 'fullscreen gives way to the TV at once')
    time.sleep(2)
    s.check(helper.alive() and len(helper.starts()) == 1, "on the TV the desktop's picture goes on (the same stream)")
    bridge.replies['desktop-mode'] = {'enabled': False, 'width': 1920, 'height': 1080, 'tv': False, 'watched': False}
    s.wait_for(lambda: s.find(cls='rungic-agent-screen-window'), 10, 'the floating window back')
    s.check(True, 'the TV gone, the floating window is back')
    window.send_signal(signal.SIGTERM)
    window.wait(10)


def assistant(s, bridge, helper):
    reply = {'enabled': True, 'workspace': 1, 'width': 1920, 'height': 1080, 'tv': False, 'tvShown': [],
             'tvHeard': -1, 'directorFocus': 1}
    bridge.replies['agent-screen'] = reply
    s.start(['/usr/libexec/rungic-agent-screen-window', '--workspace', '1'])
    s.wait_for(lambda: s.find(cls='rungic-agent-screen-window'), 20, "the assistant's floating window")
    s.wait_for(lambda: len(helper.starts()) == 2, 10, 'its picture asked for')
    s.check(helper.starts()[1]['argv'] == ['1', '/usr/libexec/rungic-workspace-stream'],
            "the assistant's picture has its pointer (where the agent points)")
    # Its window is below desktop mode's place; the cast button asks for the director, focused on it.
    py = 330
    s.tap(12 + WIDTH / 2, py + HEIGHT / 2)
    x, y = floating_button(1)
    s.tap(x, y - 110 + py)
    s.wait_for(lambda: [r for r in bridge.requests if r.get('button') and r.get('source') == 1], 10, 'the cast button')
    cast = [r for r in bridge.requests if r.get('button') and r.get('source') == 1][-1]
    s.check(cast == {'op': 'tv', 'button': True, 'source': 1, 'content': 'director'},
            "an assistant's screen casts the director, with it in focus")
    bridge.replies['agent-screen'] = {**reply, 'tv': True, 'tvShown': [1]}
    s.wait_for(lambda: not helper.alive(), 10, 'the picture stopped')
    s.check(not s.find(cls='rungic-agent-screen-window'),
            "while the TV shows the assistant's screen, the window is put away and receives no picture")


# covers[system]: desktop-mode.floating-window/E4 desktop-mode.floating-window/E5
# covers[system]: desktop-mode.tv-computer-mode/E4 desktop-mode.tv-computer-mode/E5
# covers[system]: desktop-mode.on-off/E4 desktop-mode.fullscreen-touch/E1 desktop-mode.floating-keyboard/E5
# covers[consumer]: iface:platform-bridge
def test():
    folder = Path('/tmp/desktop-mode-window')
    folder.mkdir(exist_ok=True)
    helper = Helper(folder)
    with Bridge('platform-bridge') as bridge, harness.Session(1080, 2400) as s:
        os.environ['RUNGIC_PLATFORM_SOCKET'] = bridge.path
        disabled_assistant(s, bridge)
        desktop(s, bridge, helper)
        assistant(s, bridge, helper)
        return s.steps


if __name__ == '__main__':
    harness.run('desktop_mode_window', test)
