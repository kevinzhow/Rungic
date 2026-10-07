#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Launch from the Plasma Mobile drawer by desktop ID and its actual localized label.

Replaces the fixed-coordinate taps of the doc 51 launch scenario. Each step is
checked against the process table and AT-SPI registration, so a missed tap
fails the run instead of silently measuring the wrong thing. `--search` types
the localized name into the drawer search first, which moves the icon: the same code
must still find it.

  ui_launch_check.py [--app org.kde.kalk] [--process kalk] [--rounds 2] [--search]
"""
import argparse
import json
import re
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rungic_agent  # noqa: E402
from rungic_device import run  # noqa: E402


def process_ids(process):
    reply = run(f'pgrep -x -u "$(id -u)" {shlex.quote(process)}', 'user', check=False)
    if reply.returncode not in (0, 1):
        raise RuntimeError('process table unavailable')
    return {int(pid) for pid in reply.stdout.split()}


def running(process):
    return bool(process_ids(process))


def desktop_entry(app):
    """Resolve the desktop ID and collect every translated Name/GenericName from its file."""
    code = ('import json,sys; from gi.repository import Gio; '
            'from rungic_cua.applications import find_application,localized_names; '
            'entry=find_application(sys.argv[1]); '
            'entry and entry.update(labels=sorted(localized_names(Gio.DesktopAppInfo.new(entry["id"]+".desktop")))); '
            'print(json.dumps(entry,ensure_ascii=False))')
    reply = run(f'PYTHONPATH=/usr/lib/rungic-cua python3 -c {shlex.quote(code)} {shlex.quote(app)}', 'user')
    entry = json.loads(reply.stdout)
    if not entry or entry['id'] != app.removesuffix('.desktop'):
        raise RuntimeError(f'installed desktop ID {app!r} not found exactly')
    if not entry.get('labels'):
        raise RuntimeError(f'installed desktop ID {app!r} has no label candidates')
    return entry


def application_windows(entry, process):
    pids = process_ids(process)
    return [w for w in rungic_agent.ui_windows() if w.get('normal') and w.get('id')
            and w['pid'] in pids and w['resource_class'].casefold() in entry['classes']]


def window_action(window_id, action):
    code = ('import json,sys; from rungic_cua.kwin import KWin; '
            'print(json.dumps(KWin().window_action(sys.argv[1],sys.argv[2])))')
    command = f'PYTHONPATH=/usr/lib/rungic-cua python3 -c {shlex.quote(code)} {shlex.quote(window_id)} {shlex.quote(action)}'
    reply = json.loads(run(command, 'user').stdout)
    if not reply.get('found'):
        raise RuntimeError(f'window {window_id!r} disappeared before {action}')
    return reply


def home():
    # The same public shell action used by Android navigation, independent of button labels.
    return run('qdbus6 org.kde.plasmashell /Mobile org.kde.plasmashell.openHomeScreen', 'user')


def wait_for(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.3)
    return False


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


def launcher_label(entry, timeout=SETTLE_TIMEOUT):
    """Find the actual drawer label among all translations, regardless of our locale."""
    candidates = set(entry['labels'])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        labels = [node for node in rungic_agent.ui_find('plasmashell', role='label')
                  if node.get('name', '').casefold() in candidates
                  and node.get('extents', [0, 0, 0, 0])[2] > 0]
        if len(labels) > 1:
            raise RuntimeError(f'ambiguous launcher labels for {entry["id"]!r}')
        if labels:
            return labels[0]['name']
        time.sleep(0.3)
    raise RuntimeError(f'launcher labels for {entry["id"]!r} not visible')


def icon_for(app, timeout=SETTLE_TIMEOUT):
    """The launcher delegate of `app`, once its position stops changing (drawer animations)."""
    previous, deadline = None, time.monotonic() + timeout
    while time.monotonic() < deadline:
        labels = [n for n in rungic_agent.ui_find('plasmashell', role='label', name=f'^{re.escape(app)}$')
                  if n.get('extents', [0, 0, 0, 0])[2] > 0]
        if len(labels) > 1:
            raise RuntimeError(f'ambiguous launcher label {app!r}')
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
        labels = [n for n in rungic_agent.ui_find('plasmashell', role='label', name=f'^{re.escape(app)}$')
                  if n.get('extents', [0, 0, 0, 0])[2] > 0]
        if not fields or not labels:
            return
        field_bottom = fields[0]['extents'][1] + fields[0]['extents'][3]
        if labels[0]['extents'][1] > field_bottom:
            return
        run('input swipe 540 900 540 1500 400', 'shell')  # one short drag towards the top
        time.sleep(0.8)


def launch(app, process, search):
    entry = desktop_entry(app)
    home()
    time.sleep(0.8)
    open_drawer()
    label = launcher_label(entry)
    if not search:
        scroll_drawer_to_top(label)
    if search:
        # Localized search uses the existing KWin text-commit path, including non-ASCII labels.
        field = [f for f in drawer_search_fields()]
        if not field:
            raise RuntimeError('drawer search field not showing')
        rungic_agent.ui_press('plasmashell', field[0]['path'], 'SetFocus')
        code = 'import sys; from rungic_cua.kwin import KWin; KWin().commit_text(sys.argv[1])'
        run(f'PYTHONPATH=/usr/lib/rungic-cua python3 -c {shlex.quote(code)} {shlex.quote(label[:4])}', 'user')
        time.sleep(1.0)
    tap = rungic_agent.ui_tap('plasmashell', icon_for(label))
    started = wait_for(lambda: running(process), 10)
    visible = wait_for(lambda: bool(application_windows(entry, process)), 10)
    registered = wait_for(lambda: any(a['pid'] == w['pid'] for a in rungic_agent.a11y('apps')
                                     for w in application_windows(entry, process)), 10)
    windows = application_windows(entry, process) if visible else []
    registered_pids = {a['pid'] for a in rungic_agent.a11y('apps')}
    registered = registered and any(w['pid'] in registered_pids for w in windows)
    step = {'desktop_id': entry['id'], 'label': label, 'tap': tap['tap'], 'started': started,
            'registered': registered, 'window': bool(windows), 'windows': windows}
    if not (started and registered and windows):
        step['screenshot'] = rungic_agent.screenshot()  # evidence of what the tap hit
    return step


def close(process, app='org.kde.kalk'):
    windows = application_windows(desktop_entry(app), process)
    if len(windows) != 1:
        raise RuntimeError(f'expected one {app} window to close, found {len(windows)}')
    window_id = windows[0]['id']
    window_action(window_id, 'close')
    exited = wait_for(lambda: not running(process), 10)
    gone = wait_for(lambda: not any(w.get('id') == window_id for w in rungic_agent.ui_windows()), 10)
    return {'exited': exited and gone, 'closed_window': window_id}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--app', default='org.kde.kalk')
    parser.add_argument('--process', default='kalk')
    parser.add_argument('--rounds', type=int, default=2)
    parser.add_argument('--search', action='store_true')
    args = parser.parse_args()
    rungic_agent.ui_enable(True)
    time.sleep(2)
    if running(args.process):
        close(args.process, args.app)
    results = []
    for i in range(args.rounds):
        step = launch(args.app, args.process, args.search) | close(args.process, args.app)
        results.append(step)
        print(json.dumps(step), flush=True)
    ok = all(r['started'] and r['registered'] and r['window'] and r['exited'] for r in results)
    print(json.dumps({'ok': ok, 'rounds': len(results)}))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
