#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Device diagnostics for agents: status, merged logs, crashes and evidence bundles, plus the touches
of an agent that operates the phone like a user (tap, swipe, key, text by screenshot pixels) and
one-off commands for checking a result (docs/121).

Every function returns plain data (dict/list/str) so the same code serves the
command line and the MCP server (tools/rungic_agent_mcp.py). The diagnostics change no device
state; touches and exec do what a user or a shell would. See docs/55-agent-native-debugging.md
for the safety classes.

Timeline: Android and the LXC container share one kernel, so logcat
(`-v epoch`), journald (__REALTIME_TIMESTAMP) and dmesg (monotonic, converted
with the offset sampled in the same script) are merged on one wall clock.
"""
import argparse
import datetime
import functools
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import rungic_device
from rungic_device import DeviceError, out, run

DIAG_DIR = rungic_device.WORKSPACE / '.work/diag'
SESSION_LOG = '/var/log/plasma/session.log'
# The APK's tags; Moto* are those of the APK from before the Rungic rename (docs/70, until phase D).
LOGCAT_TAGS = ('WinlandNative', 'RungicWayland', 'RungicPlasma', 'DisplayPacer', 'MotoWayland', 'MotoPlasma')
PRIORITY = {'V': 7, 'D': 7, 'I': 6, 'W': 4, 'E': 3, 'F': 2}  # logcat -> syslog
LEVEL_NAME = {0: 'emerg', 1: 'alert', 2: 'crit', 3: 'err', 4: 'warning', 5: 'notice', 6: 'info', 7: 'debug'}

# Recurring messages already explained elsewhere. They are counted, never
# silently dropped; `include_noise=True` shows them.
KNOWN_NOISE = [
    (re.compile(r'bpf-firewall: Attaching egress BPF program'), 'systemd BPF firewall is unavailable in the Android LXC cgroup; harmless'),
    (re.compile(r'pam_unix\(runuser:session\): session (opened|closed)'), 'agent/tool user-exec sessions'),
    (re.compile(r'binder: release \d+:\d+ transaction \d+ out, still active'), 'Android binder teardown chatter'),
    (re.compile(r'MotoPrcPermissionService: noteOperationInternal code: ACCESS_CLIPBOARD'), 'Moto clipboard audit for the desktop APK'),
    (re.compile(r'AtSpiAdaptor::applicationInterface does not implement "GetApplicationBusAddress"'),
     'Qt AT-SPI bridge while accessibility is enabled (rungic-a11y); harmless'),
]


# Kernel lines relevant to the desktop when scope='plasma': GPU, memory
# pressure, crashes and SELinux denials of the container or the desktop APK.
KERNEL_RELEVANT = re.compile(
    r'kgsl|adreno|gpu|dma_heap|dma-buf|oom|lowmem|lmk|killed process|segfault|unhandled|'
    r'Kernel panic|BUG:|Oops|lxc|avc:.*scontext=u:r:(magisk|untrusted_app|traced)', re.I)


def noise_reason(message):
    for pattern, reason in KNOWN_NOISE:
        if pattern.search(message):
            return reason
    return None


def package_uid():
    return int(out(f'stat -c %u /data/data/{rungic_device.apk()}').strip())


# ---------------------------------------------------------------- status

def status():
    """Android, container, desktop, GPU and host-bridge state in one call."""
    android = out(f'''
echo "time=$(date +%s.%N)"
echo "uptime=$(cut -d' ' -f1 /proc/uptime)"
echo "selinux=$(getenforce)"
echo "apk=$(dumpsys package {rungic_device.apk()} | grep -m1 versionName | cut -d= -f2)"
echo "apk_pid=$(pidof {rungic_device.apk()})"
echo "wakefulness=$(dumpsys power | grep -m1 mWakefulness= | cut -d= -f2)"
echo "top=$(dumpsys activity activities | grep -m1 topResumedActivity | sed 's/.* u0 //;s/ .*//')"
echo "thermal=$(dumpsys thermalservice | grep -m1 'Thermal Status' | cut -d: -f2 | tr -d ' ')"
echo "battery=$(dumpsys battery | grep -m1 ' level' | cut -d: -f2 | tr -d ' ')"
echo "gpubusy=$(cat /sys/class/kgsl/kgsl-3d0/gpubusy)"
echo "gpu_freq=$(cat /sys/class/kgsl/kgsl-3d0/devfreq/cur_freq)"
echo "container=$({rungic_device.PLASMA} status | grep -m1 State | tr -s ' ' | cut -d' ' -f2)"
''')
    result = {'android': dict(line.split('=', 1) for line in android.splitlines() if '=' in line)}
    if result['android'].get('container') != 'RUNNING':
        return result
    container = run('''
echo "system=$(systemctl is-system-running)"
echo "failed=$(systemctl list-units --failed --plain --no-legend | cut -d' ' -f1 | tr '\\n' ' ')"
for p in kwin_wayland plasmashell; do
  pid=$(pgrep -xo $p) && echo "$p=$pid rss_kib=$(awk '/VmRSS/{print $2}' /proc/$pid/status) started=$(ps -o lstart= -p $pid)" || echo "$p=missing"
done
''', 'container', check=False).stdout
    result['container'] = dict(line.split('=', 1) for line in container.splitlines() if '=' in line)
    user = run('''
echo "user_failed=$(systemctl --user list-units --failed --plain --no-legend | cut -d' ' -f1 | tr '\\n' ' ')"
echo "renderer=$(qdbus6 org.kde.KWin /KWin org.kde.KWin.supportInformation | grep -m1 'OpenGL renderer string' | cut -d: -f2-)"
''', 'user', check=False).stdout
    result['desktop'] = dict(line.split('=', 1) for line in user.splitlines() if '=' in line)
    try:
        host = host_request('status')
        result['host'] = {k: host.get(k) for k in ('foreground', 'orientation', 'keepAwake', 'display', 'battery') if k in host}
        native = host_request('native-stats')  # APK 1.9+
        result['host'].update(native if 'error' not in native else {'native_stats': native['error']})
    except (DeviceError, ValueError, KeyError) as error:
        result.setdefault('host', {})['error'] = str(error)
    return result


# ---------------------------------------------------------------- logs

def _logcat(since, scope):
    uid = package_uid()
    if scope == 'all':
        selector = '-b main,system,crash'
    else:
        # Our UID, our tags, and the crash buffer of any process.
        selector = f'-b main,system,crash --uid={uid}'
    script = f'''
logcat -d -v epoch,uid,printable {selector} -T {since:.3f} 2>/dev/null
logcat -d -v epoch,uid,printable -b crash -T {since:.3f} 2>/dev/null
logcat -d -v epoch,uid,printable -b main,system -T {since:.3f} -s {' '.join(t + ':V' for t in LOGCAT_TAGS)} 2>/dev/null
'''
    entries, seen = [], set()
    line_re = re.compile(r'^\s*(\d+\.\d+)\s+(\S+)\s+(\d+)\s+(\d+)\s+([VDIWEF])\s+(.*?)\s*:\s(.*)$')
    for line in out(script, timeout=90).splitlines():
        if line in seen:
            continue
        seen.add(line)
        m = line_re.match(line)
        if m:
            entries.append({'t': float(m[1]), 'src': 'logcat', 'prio': PRIORITY[m[5]], 'tag': m[6],
                            'pid': int(m[3]), 'uid': m[2], 'msg': m[7]})
    return entries


def _journal(since, priority):
    # The container journal belongs to the desktop, so scope does not narrow it.
    script = f'journalctl -o json --no-pager --since @{int(since)} -p {priority}'
    entries = []
    for line in run(script, 'container', timeout=90, check=False).stdout.splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = e.get('MESSAGE')
        if isinstance(msg, list):  # binary payloads are byte arrays
            msg = bytes(msg).decode(errors='replace')
        entries.append({'t': int(e['__REALTIME_TIMESTAMP']) / 1e6, 'src': 'journal',
                        'prio': int(e.get('PRIORITY', 6)),
                        'tag': e.get('SYSLOG_IDENTIFIER') or e.get('_COMM', '?'),
                        'unit': e.get('_SYSTEMD_USER_UNIT') or e.get('_SYSTEMD_UNIT'),
                        'pid': int(e['_PID']) if e.get('_PID') else None, 'msg': msg or ''})
    return entries


def _dmesg(since, scope):
    text = out('echo "real=$(date +%s.%N)"; grep -m1 "now at" /proc/timer_list; dmesg -r')
    lines = text.splitlines()
    real = float(lines[0].split('=')[1])
    mono = int(re.search(r'now at (\d+)', lines[1])[1]) / 1e9
    entries = []
    for line in lines[2:]:
        # -r keeps the <facility*8+level> prefix, so the kernel's own level is used.
        m = re.match(r'^<(\d+)>\[\s*(\d+\.\d+)\]\s*(.*)$', line)
        if m:
            t = real - mono + float(m[2])
            msg = m[3]
            if t >= since and (scope == 'all' or KERNEL_RELEVANT.search(msg)):
                prio = int(m[1]) & 7
                entries.append({'t': t, 'src': 'kernel', 'prio': prio, 'tag': 'kernel', 'pid': None, 'msg': msg})
    return entries


def logs(since_seconds=300, sources=('logcat', 'journal', 'kernel'), priority=6, grep=None,
         scope='plasma', include_noise=False, limit=400):
    """Merged timeline of the last `since_seconds`.

    priority: syslog level, keep entries at or above it (3=err, 4=warning, 6=info).
    scope: 'plasma' = desktop APK UID, its tags and crash buffers; 'all' = whole phone.
    Kernel lines are all kept by scope, then filtered by priority heuristics.
    """
    now = float(out('date +%s.%N'))
    since = now - since_seconds
    entries = []
    if 'logcat' in sources:
        entries += _logcat(since, scope)
    if 'journal' in sources:
        entries += _journal(since, priority)
    if 'kernel' in sources:
        entries += _dmesg(since, scope)
    pattern = re.compile(grep, re.I) if grep else None
    kept, noise = [], {}
    for e in sorted(entries, key=lambda e: e['t']):
        if e['prio'] > priority or (pattern and not pattern.search(f"{e['tag']} {e['msg']}")):
            continue
        reason = noise_reason(f"{e['tag']}: {e['msg']}")
        if reason and not include_noise:
            noise[reason] = noise.get(reason, 0) + 1
            continue
        kept.append(e)
    return {'since': since, 'until': now, 'total': len(kept), 'truncated': max(0, len(kept) - limit),
            'suppressed_noise': noise, 'entries': kept[-limit:]}


@functools.cache
def device_timezone():
    """The phone's UTC offset, so printed times match on-device logs."""
    offset = out('date +%z').strip()
    sign = -1 if offset[0] == '-' else 1
    return datetime.timezone(sign * datetime.timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])))


def format_entries(entries):
    lines = []
    tz = device_timezone()
    for e in entries:
        stamp = datetime.datetime.fromtimestamp(e['t'], tz).strftime('%H:%M:%S.%f')[:-3]
        who = e['tag'] + (f"[{e['pid']}]" if e.get('pid') else '')
        lines.append(f"{stamp} {e['src'][:7]:7} {LEVEL_NAME.get(e['prio'], e['prio'])[:4]:4} {who}: {e['msg']}")
    return '\n'.join(lines)


def session_log(lines=200):
    """Tail of the desktop session's stdout/stderr file (no timestamps)."""
    return out(f'tail -n {int(lines)} {SESSION_LOG}', 'container')


# ---------------------------------------------------------------- crashes

def crashes(since_seconds=86400):
    """Android tombstones, crash-buffer entries and container crash indications."""
    now = float(out('date +%s'))
    listing = out(f'''
for f in $(ls -t /data/tombstones | grep -v '\\.pb$'); do
  p=/data/tombstones/$f
  m=$(stat -c %Y $p); [ $m -ge {int(now - since_seconds)} ] || continue
  echo "tombstone|$f|$m|$(grep -m1 '^Cmdline:' $p | cut -c10-120)|$(grep -m1 '^signal' $p | cut -c1-100)"
done
''')
    result = {'tombstones': [], 'container': []}
    for line in listing.splitlines():
        _, name, mtime, cmdline, signal = (line.split('|') + [''] * 5)[:5]
        result['tombstones'].append({'id': name, 'time': int(mtime), 'cmdline': cmdline, 'signal': signal})
    journal = logs(since_seconds, sources=('journal',), priority=6,
                   grep=r'coredump:|dumped core|code=dumped|code=killed|SIGSEGV|SIGABRT|KCrash', limit=100)
    result['container'] = journal['entries']
    # Reports of system/diagnostics/rungic-coredump-collect, and apport's Python
    # exception reports in /var/crash.
    reports = run(f'''python3 - <<'PY'
import json, pathlib
since = {now - since_seconds}
# /var/lib/moto-cores before the Rungic rename (docs/70)
store = next((p for p in map(pathlib.Path, ('/var/lib/rungic-cores', '/var/lib/moto-cores')) if p.is_dir()), pathlib.Path('/var/lib/rungic-cores'))
cores = []
for info in store.glob('*/info.json'):
    if info.stat().st_mtime >= since:
        cores.append({{'id': info.parent.name, **json.loads(info.read_text())}})
apport = [{{'id': str(p), 'time': p.stat().st_mtime}} for p in pathlib.Path('/var/crash').glob('*.crash')
          if p.stat().st_mtime >= since]
print(json.dumps({{'cores': cores, 'apport': apport}}))
PY
''', 'container', check=False).stdout
    found = json.loads(reports) if reports.strip() else {'cores': [], 'apport': []}
    result['container_cores'] = sorted(found['cores'], key=lambda r: r['time'], reverse=True)
    result['apport'] = found['apport']
    return result


def crash_groups(since_seconds=30 * 86400, release=None):
    """Container crash reports grouped by signature (system/diagnostics/rungic-coredump-collect).

    With release, also lists the signatures seen only in that release: new there, or not seen
    in the reports still kept from earlier ones."""
    now = float(out('date +%s'))
    text = run(f'''python3 - <<'PY'
import json, pathlib
since = {now - since_seconds}
# /var/lib/moto-cores before the Rungic rename (docs/70)
store = next((p for p in map(pathlib.Path, ('/var/lib/rungic-cores', '/var/lib/moto-cores')) if p.is_dir()), pathlib.Path('/var/lib/rungic-cores'))
rows = []
for info in store.glob('*/info.json'):
    try:
        d = json.loads(info.read_text())
    except ValueError:
        continue
    if d.get('time', 0) >= since:
        rows.append({{'id': info.parent.name, 'time': d['time'], 'comm': d.get('comm'), 'exe': d.get('exe'),
                     'signal': d.get('signal'), 'signature': d.get('signature'),
                     'frames': d.get('signature_frames') or d.get('top_frames'),
                     'symbolized': d.get('symbolized', False), 'core': (info.parent / 'core.zst').exists(),
                     'release': (d.get('release') or {{}}).get('version'),
                     'package': d.get('package')}})
print(json.dumps(rows))
PY
''', 'container', check=False).stdout
    rows = json.loads(text) if text.strip() else []
    groups = {}
    key_of = lambda row: row['signature'] or f"unsigned:{row['comm']}:{row['signal']}"
    for row in sorted(rows, key=lambda r: r['time']):
        key = key_of(row)
        g = groups.setdefault(key, {'signature': row['signature'], 'comm': row['comm'], 'exe': row['exe'],
                                    'signal': row['signal'], 'count': 0, 'first': row['time'],
                                    'releases': [], 'reports': []})
        g['count'] += 1
        g['last'] = row['time']
        g['frames'] = row['frames']            # the latest report: symbolized when any was
        g['symbolized'] = row['symbolized'] or g.get('symbolized', False)
        if row['release'] not in g['releases']:
            g['releases'].append(row['release'])
        g['reports'].append(row['id'])
    fmt = lambda t: datetime.datetime.fromtimestamp(t).isoformat(timespec='seconds')
    result = []
    for key, g in sorted(groups.items(), key=lambda kg: (-kg[1]['count'], -kg[1]['last'])):
        g['first'], g['last'] = fmt(g['first']), fmt(g['last'])
        g['latest_report'] = g['reports'][-1]
        g['with_core'] = sorted(r['id'] for r in rows if key_of(r) == key and r['core'])[-3:]
        del g['reports']
        result.append(g)
    answer = {'since_seconds': since_seconds, 'reports': len(rows), 'groups': result}
    if release:
        answer['new_in_release'] = [g['signature'] for g in result if g['releases'] == [release]]
    return answer


def crash_symbolize(report_ids=(), recent=0):
    """Install debug symbols for crash reports and redo their backtraces, on the build host
    (tools/rungic_crash_symbolize.py): gdb with debug information on the phone starved Android.
    Can take minutes the first time."""
    ids = [r for r in report_ids if re.fullmatch(r'\d{8}-\d{6}-[^/\s]+-\d+', r)]
    if len(ids) != len(report_ids):
        raise ValueError('report ids look like YYYYmmdd-HHMMSS-comm-pid')
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('rungic_crash_symbolize.py')), *ids,
                             *(['--recent', str(int(recent))] if recent else [])],
                            capture_output=True, text=True, timeout=3600)
    return json.loads(result.stdout) if result.returncode == 0 else result.stderr[-3000:]


def crash_get(crash_id, lines=160):
    """Android tombstone head, container core backtrace, or apport report (without its binary fields)."""
    if re.fullmatch(r'tombstone_\d+', crash_id):
        return out(f'head -n {int(lines)} /data/tombstones/{crash_id}')
    if re.fullmatch(r'\d{8}-\d{6}-[^/\s]+-\d+', crash_id):
        store = rungic_device.first_path('/var/lib/rungic-cores', '/var/lib/moto-cores')
        return out(f'head -n {int(lines) * 4} {store}/{crash_id}/backtrace.txt', 'container')
    if re.fullmatch(r'/var/crash/[^/\s]+\.crash', crash_id):
        return out(f'grep -v "^ " {shlex.quote(crash_id)} | head -n {int(lines)}', 'container')
    raise ValueError('crash_id: tombstone_NN, a container core id (YYYYmmdd-HHMMSS-comm-pid) '
                     'or /var/crash/<name>.crash')


# ---------------------------------------------------------------- integrity

def integrity():
    """Drift of the container rootfs against dpkg, the release and the local-config manifest
    (system/diagnostics/rungic-integrity, docs/61). Read-only; takes about a minute (dpkg --verify)."""
    text = run('for p in /usr/bin/rungic-integrity /usr/bin/moto-integrity; do '
               '[ -x $p ] && exec $p --json; done; echo null', 'container', timeout=300, check=False).stdout
    report = json.loads(text)
    if report is None:
        raise DeviceError('rungic-integrity is not installed in the container')
    return report


# ---------------------------------------------------------------- desktop and host

def kwin_info():
    """KWin supportInformation: version, compositing backend, renderer, options, effects."""
    return out('qdbus6 org.kde.KWin /KWin org.kde.KWin.supportInformation', 'user')


READ_OPS = {'status', 'display-get', 'network-get', 'capture-info', 'brightness-get', 'native-stats'}


def host_request(op):
    """Read-only request to the Android host bridge (platform.sock)."""
    if op not in READ_OPS:
        raise ValueError(f'Read-only ops: {sorted(READ_OPS)}')
    text = out(rungic_device.prog('platform') + ' --request ' + shlex.quote(json.dumps({'op': op})), 'user')
    return json.loads(text)


def screenshot(path=None):
    """Android framebuffer capture (what the user sees). Returns the PNG path.

    Captured to a file and pulled: over a high-latency wireless link `exec-out`
    streaming took 36 s for 1.5 MB, `adb pull` of the same file 5 s.
    """
    path = Path(path) if path else DIAG_DIR / f"screen-{time.strftime('%Y%m%d-%H%M%S')}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    remote = '/data/local/tmp/rungic-agent-screen.png'
    run(f'screencap -p {remote}', 'shell')
    try:
        subprocess.run(rungic_device.adb('pull', remote, str(path)), check=True, capture_output=True,
                       timeout=120, stdin=subprocess.DEVNULL)
    finally:
        run(f'rm -f {remote}', 'shell', check=False)
    return str(path)


# ---------------------------------------------------------------- desktop UI (AT-SPI)

def a11y(*args, timeout=120):
    """Run system/diagnostics/rungic-a11y as the desktop user; returns parsed JSON."""
    return json.loads(out(rungic_device.prog('a11y') + ' ' + shlex.join(map(str, args)), 'user', timeout=timeout))


def ui_enable(enabled=True):
    """Turn AT-SPI registration on/off for all Qt apps (costs CPU while on)."""
    return a11y('enable' if enabled else 'disable')


def ui_find(app, role=None, name=None, include_hidden=False):
    if not a11y('state')['enabled']:
        ui_enable(True)
        time.sleep(2)  # applications register asynchronously
    args = ['find', app] + (['--role', role] if role else []) + (['--name', name] if name else [])
    return a11y(*args, *(['--all'] if include_hidden else []))


def ui_press(app, path, action=None):
    """Invoke an element's AT-SPI action (default: its first, normally Press)."""
    return a11y('act', app, path, *(['--action', action] if action else []))


def ui_windows():
    """KWin's window list with global logical geometry (rungic-a11y windows)."""
    return a11y('windows')


def ui_tap(app, path):
    """Tap the centre of an element through Android input, for elements without actions.

    AT-SPI extents are window-relative on Wayland; the window origin comes from
    KWin, matched by process id and size. Scale = physical / logical output width.
    """
    pid = next(x['pid'] for x in a11y('apps') if x['name'] == app or str(x['pid']) == str(app))
    tree = a11y('tree', app, '--all')
    frame = next(n for n in tree if n['path'] == path.split('/')[0])
    element = next(n for n in tree if n['path'] == path)
    fw, fh = frame['extents'][2], frame['extents'][3]
    windows = ui_windows()
    matches = [w for w in windows if w['pid'] == pid and abs(w['w'] - fw) <= 1 and abs(w['h'] - fh) <= 1]
    if not matches:
        raise ValueError(f'no KWin window of pid {pid} with size {fw}x{fh}')
    origin = matches[0]
    scale = host_request('display-get')['physicalWidth'] / max(w['x'] + w['w'] for w in windows)
    x, y, w, h = element['extents']
    if w <= 0 or h <= 0 or not (0 <= x + w / 2 < fw and 0 <= y + h / 2 < fh):
        raise ValueError(f'element {path} is not on screen (extents {element["extents"]}, window {fw}x{fh})')
    tx, ty = int((origin['x'] + x + w / 2) * scale), int((origin['y'] + y + h / 2) * scale)
    run(f'input tap {tx} {ty}', 'shell')
    return {'element': element, 'window': origin, 'tap': [tx, ty], 'scale': round(scale, 3)}


# ---------------------------------------------------------------- touch by screenshot coordinates
# For an agent that looks at screenshot() and acts like a user (docs/121). Coordinates are the
# screenshot's own pixels, which are also Android's touch coordinates: no conversion.

def _ints(*values):
    numbers = [int(v) for v in values]
    if any(n < 0 for n in numbers):
        raise ValueError(f'coordinates and durations are not negative: {numbers}')
    return numbers


def tap(x, y):
    x, y = _ints(x, y)
    run(f'input tap {x} {y}', 'shell')
    return {'tap': [x, y]}


def swipe(x1, y1, x2, y2, ms=300):
    """A finger from (x1, y1) to (x2, y2); a long press is a swipe that does not move."""
    x1, y1, x2, y2, ms = _ints(x1, y1, x2, y2, ms)
    run(f'input swipe {x1} {y1} {x2} {y2} {ms}', 'shell')
    return {'swipe': [x1, y1, x2, y2], 'ms': ms}


def key(name):
    """An Android key event: BACK, HOME, ENTER, ... (KEYCODE_ prefix optional) or a number."""
    code = str(name).upper()
    if not re.fullmatch(r'(KEYCODE_)?[A-Z0-9_]+', code):
        raise ValueError(f'not a key name: {name!r}')
    run(f'input keyevent {code}', 'shell')
    return {'key': code}


def text(value):
    """Android text input, delivered to the focused field as key events. Not the on-screen keyboard:
    a check of the keyboard itself must touch its keys (docs/121 E2E-02)."""
    if not value or not re.fullmatch(r'[\x21-\x7e ]+', value):
        raise ValueError('input text takes printable ASCII only')
    run('input text ' + shlex.quote(value.replace(' ', '%s')), 'shell')
    return {'text': value}


def execute(script, level='user', timeout=60):
    """One command at a run level (shell, root, container, user): {exit, stdout, stderr}, never raises
    on the command's own failure."""
    if level not in rungic_device.LEVELS:
        raise ValueError(f'levels: {sorted(rungic_device.LEVELS)}')
    reply = run(script, level, timeout, check=False)
    return {'exit': reply.returncode, 'stdout': reply.stdout, 'stderr': reply.stderr}


# ---------------------------------------------------------------- the on-screen keyboard by its keys
# Real touches on the visible keys, found by their accessible identifiers (packages/plasma-keyboard
# accessible-keys.patch, docs/41): key:<text>, shift, symbol, language, space, enter, backspace, ...,
# candidate (name: the candidate's text). The key layout is read once per keyboard page and the touches
# of one page go to the phone in one command, so a line takes seconds, not a minute (docs/121).

KEYBOARD = 'plasma-keyboard'
_PAGE_KEYS = ('shift', 'symbol', 'mode')     # keys that change which keys are shown


_placement = {}     # app -> (dx, dy, scale, root size): where its keys are, read once per process


def keyboard_keys(app=KEYBOARD):
    """The showing keys: [{id, name, enabled, at: [x, y] in screenshot pixels}].

    plasma-keyboard's root window is as tall as the output and draws its keys at its bottom, but KWin
    shows only the input panel, which ends above the navigation bar: the keys are where the panel's
    bottom says (on the G100 36 logical px above where the accessibility tree puts them). Another app
    (Rungic's floating keyboard) is a window of its own: its keys are relative to that window."""
    tree = a11y('tree', app)
    if not tree:
        raise ValueError(f'{app} has no accessible tree (is the keyboard shown and accessibility on?)')
    root = tree[0]['extents']
    placed = _placement.get(app)
    if not placed or placed[3] != tuple(root[2:]):
        windows = ui_windows()
        scale = host_request('display-get')['physicalWidth'] / max(w['x'] + w['w'] for w in windows)
        pid = next((x['pid'] for x in a11y('apps') if x['name'] == app), None)
        own = [w for w in windows if w['pid'] == pid and abs(w['w'] - root[2]) <= 1 and abs(w['h'] - root[3]) <= 1]
        if own:
            dx, dy = own[0]['x'], own[0]['y']
        else:
            panels = [w for w in windows if w['resource_class'] == 'kwin_wayland' and not w['normal']
                      and abs(w['w'] - root[2]) <= 1 and 0 < w['h'] < root[3]]
            if len(panels) != 1:
                raise ValueError(f'the keyboard panel is not shown ({len(panels)} candidates)')
            dx, dy = 0, panels[0]['y'] + panels[0]['h'] - root[3]
        placed = _placement[app] = (dx, dy, scale, tuple(root[2:]))
    dx, dy, scale, _ = placed
    keys = []
    for n in tree[1:]:
        ident, (x, y, w, h) = n.get('id', ''), n.get('extents', [0, 0, 0, 0])
        if not ident or ident.startswith('QGuiApplication') or w <= 0 or h <= 0 or 'showing' not in n.get('states', []):
            continue
        keys.append({'id': ident, 'name': n.get('name', ''), 'enabled': 'enabled' in n.get('states', []),
                     'at': [int((dx + x + w / 2) * scale), int((dy + y + h / 2) * scale)]})
    return keys


def _key(keys, ident, name=None):
    found = [k for k in keys if k['id'] == ident and (name is None or k['name'] == name)]
    return found[0] if found else None


def _touch(points):
    if points:
        run('\n'.join(f'input tap {x} {y}' for x, y in points), 'shell', timeout=30 + 2 * len(points))
        time.sleep(0.3)


def keyboard_press(*idents, app=KEYBOARD):
    """Touch keys by identifier, in order, from one reading of the layout; candidate=TEXT picks a candidate."""
    keys, points = keyboard_keys(app), []
    for ident in idents:
        ident, _, name = ident.partition('=')
        k = _key(keys, ident, name or None)
        if not k:
            raise ValueError(f'no key {ident}{"=" + name if name else ""}; shown: {sorted({k["id"] for k in keys})}')
        points.append(k['at'])
    _touch(points)
    return {'pressed': list(idents)}


def _wanted(c):
    if c == ' ':
        return 'space', None
    if c == '\n':
        return 'enter', None
    if c.isascii() and c.isalpha():
        return f'key:{c.lower()}', c
    return f'key:{c}', None


def keyboard_type(value, app=KEYBOARD, attempts=4):
    """Type value by touching the keys a user would: Shift for the other case, the symbol pages for
    digits and punctuation. Lowercase and digit runs go in one command; after a key that can change
    the page (Enter, Space, an uppercase letter, punctuation) the layout is read again: the floating
    keyboard goes back from the symbols to the letters after a space. A character no
    page has is an error, never typed another way."""
    keys, batch, typed = keyboard_keys(app), [], 0
    for c in value:
        ident, name = _wanted(c)
        for _ in range(attempts):
            k = _key(keys, ident, name)
            if k and k['enabled']:
                break
            _touch(batch)
            batch = []
            keys = keyboard_keys(app)
            k = _key(keys, ident, name)
            if k and k['enabled']:
                break
            letter_here = _key(keys, ident)
            flip = 'shift' if letter_here and name else next((p for p in _PAGE_KEYS[1:] if _key(keys, p)), None)
            if not flip:
                break
            _touch([_key(keys, flip)['at']])
            keys = keyboard_keys(app)
        else:
            k = None
        if not k or not k['enabled']:
            raise ValueError(f'no key types {c!r} after {typed} characters; shown: {sorted({k["id"] for k in keys})}')
        batch.append(k['at'])
        typed += 1
        if not (c.islower() or c.isdigit()):
            _touch(batch)
            batch = []
            keys = keyboard_keys(app)
    _touch(batch)
    return {'typed': typed}


def keyboard_pinyin(pinyin, pick, app=KEYBOARD):
    """Chinese through the pinyin layout: touch the letters, then the shown candidate whose text is pick."""
    if not re.fullmatch(r"[a-z']+", pinyin):
        raise ValueError('pinyin is lowercase letters (and the separator \')')
    keys = keyboard_keys(app)
    missing = [c for c in pinyin if not _key(keys, f'key:{c}')]
    if missing:
        raise ValueError(f'the shown layout has no keys for {missing} (switch to the pinyin layout first)')
    _touch([_key(keys, f'key:{c}')['at'] for c in pinyin])
    keys = keyboard_keys(app)
    k = _key(keys, 'candidate', pick)
    if not k:
        shown = [x['name'] for x in keys if x['id'] == 'candidate'][:10]
        raise ValueError(f'no candidate {pick!r} after {pinyin!r}; shown: {shown}')
    _touch([k['at']])
    return {'pinyin': pinyin, 'picked': pick}


# ---------------------------------------------------------------- evidence bundle

def snapshot(label='manual', since_seconds=300, with_screenshot=True):
    """Write status, full logs, crashes, KWin info and a screenshot to .work/diag/<time>-<label>/."""
    label = re.sub(r'[^A-Za-z0-9_.-]+', '-', label)[:60] or 'manual'
    folder = DIAG_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}"
    folder.mkdir(parents=True)
    manifest = {'label': label, 'created': time.time(), 'transport': rungic_device.transport(), 'files': {}, 'errors': {}}

    def save(name, producer, text=False):
        try:
            value = producer()
            data = value if text else json.dumps(value, indent=1, ensure_ascii=False)
            (folder / name).write_text(data)
            manifest['files'][name] = len(data)
            return value
        except Exception as error:  # keep collecting the rest of the evidence
            manifest['errors'][name] = f'{type(error).__name__}: {error}'
            return None

    state = save('status.json', status)
    timeline = save('logs.json', lambda: logs(since_seconds, priority=7, include_noise=True, limit=100000))
    if timeline:
        (folder / 'logs.txt').write_text(format_entries(timeline['entries']))
    save('crashes.json', lambda: crashes(max(since_seconds, 3600)))
    save('kwin-support.txt', kwin_info, text=True)
    save('session.log', lambda: session_log(1000), text=True)
    if with_screenshot:
        save('screen.png.path', lambda: screenshot(folder / 'screen.png'), text=True)
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    problems = logs(since_seconds, priority=4, limit=40) if timeline else None
    return {'folder': str(folder), 'errors': manifest['errors'], 'status': state,
            'warnings_and_errors': problems}


# ---------------------------------------------------------------- CLI

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status')
    p = sub.add_parser('logs')
    p.add_argument('--since', type=float, default=300, help='seconds back')
    p.add_argument('--priority', type=int, default=6)
    p.add_argument('--grep')
    p.add_argument('--source', action='append', choices=['logcat', 'journal', 'kernel'])
    p.add_argument('--scope', choices=['plasma', 'all'], default='plasma')
    p.add_argument('--noise', action='store_true')
    p.add_argument('--limit', type=int, default=400)
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('session-log'); p.add_argument('--lines', type=int, default=200)
    p = sub.add_parser('crashes'); p.add_argument('--since', type=float, default=86400)
    p = sub.add_parser('crash'); p.add_argument('id')
    p = sub.add_parser('crash-groups'); p.add_argument('--since', type=float, default=30 * 86400)
    p.add_argument('--release')
    p = sub.add_parser('crash-symbolize'); p.add_argument('ids', nargs='*'); p.add_argument('--recent', type=int, default=0)
    sub.add_parser('kwin-info')
    sub.add_parser('integrity')
    p = sub.add_parser('host'); p.add_argument('op')
    p = sub.add_parser('screenshot'); p.add_argument('path', nargs='?')
    p = sub.add_parser('snapshot'); p.add_argument('label', nargs='?', default='manual')
    p.add_argument('--since', type=float, default=300)
    p = sub.add_parser('tap', help='touch at screenshot pixels'); p.add_argument('x'); p.add_argument('y')
    p = sub.add_parser('swipe', help='finger from x1 y1 to x2 y2 (same point: long press)')
    for name in ('x1', 'y1', 'x2', 'y2'):
        p.add_argument(name)
    p.add_argument('--ms', default=300)
    p = sub.add_parser('key', help='Android key event: BACK, HOME, ENTER ...'); p.add_argument('name')
    p = sub.add_parser('text', help='ASCII into the focused field (not through the on-screen keyboard)')
    p.add_argument('value')
    p = sub.add_parser('exec', help='one command on the phone; prints stdout, exits with its status')
    p.add_argument('script'); p.add_argument('--as', dest='level', default='user', choices=sorted(rungic_device.LEVELS))
    p.add_argument('--timeout', type=float, default=60)
    p = sub.add_parser('keyboard-keys', help='the shown keys of the on-screen keyboard and where they are')
    p.add_argument('--app', default=KEYBOARD)
    p = sub.add_parser('keyboard-press', help='touch keys by identifier (shift, symbol, language, candidate=TEXT, ...)')
    p.add_argument('idents', nargs='+'); p.add_argument('--app', default=KEYBOARD)
    p = sub.add_parser('keyboard-type', help='type by touching the keys (\\n for Enter)')
    p.add_argument('value'); p.add_argument('--app', default=KEYBOARD)
    p = sub.add_parser('keyboard-pinyin', help='touch the pinyin letters, then the candidate PICK')
    p.add_argument('pinyin'); p.add_argument('pick'); p.add_argument('--app', default=KEYBOARD)
    a = parser.parse_args()
    if a.cmd == 'exec':
        r = execute(a.script, a.level, a.timeout)
        sys.stdout.write(r['stdout'])
        sys.stderr.write(r['stderr'])
        sys.exit(r['exit'])
    if a.cmd == 'logs':
        r = logs(a.since, tuple(a.source or ('logcat', 'journal', 'kernel')), a.priority, a.grep, a.scope, a.noise, a.limit)
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1))
        else:
            print(format_entries(r['entries']))
            print(f"-- {r['total']} entries, {r['truncated']} truncated, noise suppressed: {r['suppressed_noise']}", file=sys.stderr)
        return
    value = {'status': status, 'kwin-info': kwin_info, 'integrity': integrity,
             'session-log': lambda: session_log(a.lines), 'crashes': lambda: crashes(a.since),
             'crash': lambda: crash_get(a.id),
             'crash-groups': lambda: crash_groups(a.since, a.release),
             'crash-symbolize': lambda: crash_symbolize(a.ids, a.recent), 'host': lambda: host_request(a.op),
             'screenshot': lambda: screenshot(a.path), 'snapshot': lambda: snapshot(a.label, a.since),
             'tap': lambda: tap(a.x, a.y), 'swipe': lambda: swipe(a.x1, a.y1, a.x2, a.y2, a.ms),
             'key': lambda: key(a.name), 'text': lambda: text(a.value),
             'keyboard-keys': lambda: keyboard_keys(a.app),
             'keyboard-press': lambda: keyboard_press(*a.idents, app=a.app),
             'keyboard-type': lambda: keyboard_type(a.value.replace('\\n', '\n'), a.app),
             'keyboard-pinyin': lambda: keyboard_pinyin(a.pinyin, a.pick, a.app)}[a.cmd]()
    print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
