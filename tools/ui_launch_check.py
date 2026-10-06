#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Launch and close an app through the Plasma Mobile UI by accessible names only.

Replaces the fixed-coordinate taps of the doc 51 launch scenario. Each step is
checked against the process table and AT-SPI registration, so a missed tap
fails the run instead of silently measuring the wrong thing. `--search` types
the name into the drawer search first, which moves the icon: the same code
must still find it.

  ui_launch_check.py [--app Calculator] [--process kalk] [--rounds 2] [--search]
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rungic_agent  # noqa: E402
from rungic_device import run  # noqa: E402


def running(process):
    return run(f'pgrep -x {process}', 'container', check=False).returncode == 0


def wait_for(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.3)
    return False


def press(name):
    buttons = [b for b in rungic_agent.ui_find('plasmashell', role='button', name=f'^{name}$')]
    if not buttons:
        raise RuntimeError(f'no plasmashell button named {name!r}')
    return rungic_agent.ui_press('plasmashell', buttons[0]['path'])


# One AT-SPI query through adb takes about 3 s (two container round trips): a position counts as
# settled after two equal queries, so waits leave room for at least three.
SETTLE_TIMEOUT = 15


def drawer_search_fields():
    """Folio search is its sole visible editable field; its label is translated.

    AT-SPI exposes role/states even when Kirigami has no EditableText interface.
    Never pick an arbitrary field if the shell exposes more than one candidate.
    """
    fields = []
    for field in rungic_agent.ui_find('plasmashell', role='text'):
        extents = field.get('extents', [])
        states = set(field.get('states', []))
        if {'showing', 'visible', 'enabled', 'editable'} <= states and len(extents) == 4 and extents[0] >= 0 and extents[1] >= 0 and extents[2] > 0 and extents[3] > 0:
            fields.append(field)
    if len(fields) > 1:
        raise RuntimeError('app drawer search is ambiguous: multiple visible editable fields')
    return fields


def open_drawer(timeout=SETTLE_TIMEOUT):
    """Swipe the drawer open and wait until its search field sits still on screen."""
    sizes = re.findall(r'(\d+)x(\d+)', run('wm size', 'shell').stdout)
    if not sizes:
        raise RuntimeError('Android display size unavailable')
    width, height = map(int, sizes[-1])
    # Start within the desktop. At some scales y=2000 is already in the
    # navigation panel and the shell never receives the drawer gesture.
    if not drawer_search_fields():
        run(f'input swipe {width // 2} {int(height * .70)} '
            f'{width // 2} {int(height * .25)} 350', 'shell')
    previous, deadline = None, time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.3)
        fields = drawer_search_fields()
        current = (fields[0]['path'], tuple(fields[0]['extents'])) if fields else None
        if current and current == previous:
            return
        previous = current
    raise RuntimeError('app drawer did not open')


def icon_for(app, timeout=SETTLE_TIMEOUT):
    """The launcher delegate of `app`, once its position stops changing (drawer animations)."""
    previous, deadline = None, time.monotonic() + timeout
    while time.monotonic() < deadline:
        labels = [n for n in rungic_agent.ui_find('plasmashell', role='label', name=f'^{app}$')
                  if n.get('extents', [0, 0, 0, 0])[2] > 0]
        current = (labels[0]['path'], tuple(labels[0]['extents'])) if labels else None
        if current and current == previous:
            return current[0].rsplit('/', 1)[0]  # the icon delegate that owns the label
        previous = current
        time.sleep(0.3)
    raise RuntimeError(f'launcher entry {app!r} not visible or not settling')


def scroll_drawer_to_top(app, attempts=3):
    """The drawer keeps its scroll position (benchmarks swipe it). An entry scrolled
    under the search field still reports extents, and a tap there hits the field."""
    for _ in range(attempts):
        fields = drawer_search_fields()
        labels = [n for n in rungic_agent.ui_find('plasmashell', role='label', name=f'^{app}$')
                  if n.get('extents', [0, 0, 0, 0])[2] > 0]
        if not fields or not labels:
            return
        field_bottom = fields[0]['extents'][1] + fields[0]['extents'][3]
        if labels[0]['extents'][1] > field_bottom:
            return
        run('input swipe 540 900 540 1500 400', 'shell')  # one short drag towards the top
        time.sleep(0.8)


def launch(app, process, search):
    press('Home')
    time.sleep(0.8)
    open_drawer()
    if not search:
        scroll_drawer_to_top(app)
    if search:
        # Kirigami's search field exposes no EditableText interface: focus it, type through Android input.
        field = [f for f in drawer_search_fields()]
        if not field:
            raise RuntimeError('drawer search field not showing')
        rungic_agent.ui_press('plasmashell', field[0]['path'], 'SetFocus')
        run(f'input text {app[:4]}', 'shell')
        time.sleep(1.0)
    tap = rungic_agent.ui_tap('plasmashell', icon_for(app))
    started = wait_for(lambda: running(process), 10)
    registered = wait_for(lambda: any(a['name'] == process for a in rungic_agent.a11y('apps')), 10)
    step = {'tap': tap['tap'], 'started': started, 'registered': registered}
    if not started:
        step['screenshot'] = rungic_agent.screenshot()  # evidence of what the tap hit
    return step


def close(process):
    press('Close app')
    return {'exited': wait_for(lambda: not running(process), 10)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--app', default='Calculator')
    parser.add_argument('--process', default='kalk')
    parser.add_argument('--rounds', type=int, default=2)
    parser.add_argument('--search', action='store_true')
    args = parser.parse_args()
    rungic_agent.ui_enable(True)
    time.sleep(2)
    if running(args.process):
        close(args.process)
    results = []
    for i in range(args.rounds):
        step = launch(args.app, args.process, args.search) | close(args.process)
        results.append(step)
        print(json.dumps(step), flush=True)
    ok = all(r['started'] and r['registered'] and r['exited'] for r in results)
    print(json.dumps({'ok': ok, 'rounds': len(results)}))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
