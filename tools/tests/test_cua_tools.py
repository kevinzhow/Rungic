#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""rungic-cua's tools as Codex calls them (docs/60, docs/64, docs/68, docs/88), without a desktop.

The real server.py, luna.py, activity.py, switch.py and the plan-two OCR client run; what they would
reach is a stand-in: the model (scripted Responses API replies), KWin and the input (recorders), the
screenshot (an image of known size), busctl (a script printing the systemd manager's environment), the
Android OCR (a socket answering as OcrBridge does). arc_cua (plan two's JEV executor, an upstream
package not in this checkout) and pypinyin (when the development venv lacks it) are empty stand-ins:
nothing here uses them. Names by sound (pypinyin) and the desktop itself are the system tests'
(tools/system/tests/cua_*.py)."""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
CUA = ROOT / 'agent/computer-use'

# Stand-ins for modules server.py imports that this checkout or venv does not have.
STUBS = Path(tempfile.mkdtemp(prefix='rungic-cua-stubs-'))
ARC = {
    '__init__.py': 'class DesktopExecutor: pass\nclass RuntimeConfig: pass\n'
                   'def result_to_dict(r): return r\ndef subtask_from_dict(d): return d\n',
    'policies.py': 'class TypeSafeJevPolicy: pass\n',
    'errors.py': 'class StaleDesktopState(Exception): pass\nclass UnsupportedDesktopAction(Exception): pass\n',
    'keyboard.py': 'def parse_hotkey(text): return [], text\n',
    'models.py': 'import enum\nclass ActionKind(enum.Enum):\n    WAIT = "wait"\n'
                 'class DesktopElement: pass\nclass DesktopSnapshot: pass\nclass ExecutableAction: pass\n',
}
for name, text in ARC.items():
    (STUBS / 'arc_cua').mkdir(exist_ok=True)
    (STUBS / 'arc_cua' / name).write_text(text)
try:
    import pypinyin  # noqa: F401
except ImportError:
    (STUBS / 'pypinyin.py').write_text('def lazy_pinyin(text): return [text]\n')
for path in (CUA / 'typesafe', CUA, STUBS):
    sys.path.insert(0, str(path))

from rungic_cua import activity, luna, mode, screen as screen_module, server, switch  # noqa: E402

pytestmark = pytest.mark.filterwarnings('ignore::DeprecationWarning')


@pytest.fixture(autouse=True)
def private(tmp_path, monkeypatch):
    """Everything a tool writes goes to the test's own directory: captions, the plan, the abort
    file, the switched-apps record; nothing reaches a platform bridge."""
    monkeypatch.setattr(activity, 'DIR', tmp_path / 'rungic-agent-screen')
    monkeypatch.setattr(mode, 'PLAN_FILE', tmp_path / 'plan')
    monkeypatch.setattr(luna, 'ABORT_FILE', tmp_path / 'rungic-clicker' / 'abort')
    monkeypatch.setattr(luna, 'SETTLE_S', 0)
    monkeypatch.setattr(switch, 'STATE', tmp_path / 'switched.json')
    monkeypatch.setenv('RUNGIC_PLATFORM_SOCKET', str(tmp_path / 'no-bridge.sock'))
    monkeypatch.delenv('RUNGIC_WORKSPACE', raising=False)
    monkeypatch.delenv('RUNGIC_TASK_ID', raising=False)
    yield tmp_path


def mcp(lines):
    """server.serve() over stdio: the replies to `lines` (JSON-RPC requests)."""
    out = io.StringIO()
    with mock.patch.object(sys, 'stdin', io.StringIO(''.join(json.dumps(l) + '\n' for l in lines))), \
            mock.patch.object(sys, 'stdout', out):
        server.serve()
    return [json.loads(l) for l in out.getvalue().splitlines()]


def tool_list():
    reply = mcp([{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}])[0]['result']['tools']
    return {t['name']: t for t in reply}


# ---- plan two: switching the tool set (rungic-cua plan) -----------------------------------------

# covers: agent.plan-two/E1
def test_plan_atspi_switches_the_tools_and_luna_brings_them_back(monkeypatch):
    codex_tools = tool_list()
    assert server.plan() == 'codex'
    assert {'desktop_screenshot', 'desktop_act'} <= set(codex_tools)
    assert 'desktop_goal' not in codex_tools
    mode.save('luna')
    luna_tools = tool_list()
    assert server.plan() == 'luna'
    assert {'desktop_screenshot', 'desktop_act', 'desktop_goal'} <= set(luna_tools)
    assert not set(server.PLAN_TWO_ONLY) & set(luna_tools)
    assert 'GPT-6 Luna' in luna_tools['desktop_goal']['description']

    monkeypatch.setattr(server, 'import_session_environment', lambda: None)
    with mock.patch.object(sys, 'argv', ['rungic-cua', 'plan', 'atspi']), mock.patch('builtins.print') as said:
        server.main()
    assert json.loads(said.call_args.args[0])['plan'] == 'atspi'
    assert 'restart' in json.loads(said.call_args.args[0])['note']
    # Codex reads the list when it starts the MCP server: the next start (the voice service's restart) sees it.
    two = tool_list()
    assert {'desktop_observe', 'desktop_run', 'desktop_find_name', 'desktop_goal'} <= set(two)
    assert not {'desktop_screenshot', 'desktop_act'} & set(two)
    assert 'JEV' in two['desktop_goal']['description'] and 'GPT-6 Luna' not in two['desktop_goal']['description']

    with mock.patch.object(sys, 'argv', ['rungic-cua', 'plan', 'luna']), mock.patch('builtins.print'):
        server.main()
    assert set(tool_list()) == set(luna_tools)
    with mock.patch.object(sys, 'argv', ['rungic-cua', 'plan', 'nonsense']), pytest.raises(SystemExit):
        server.main()
    assert server.plan() == 'luna'


# covers: agent.plan-two/E1
def test_under_luna_plan_two_tools_are_refused_and_under_atspi_goal_is_jev(monkeypatch):
    mode.save('luna')
    cua = server.Cua()
    with pytest.raises(ValueError, match='plan two'):
        cua.call('desktop_observe', {})
    mode.save('atspi')
    monkeypatch.setattr(server.Cua, 'agent_output', lambda self: 'Virtual-1')
    ran = []

    def run(command, **kwargs):
        ran.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps({'outcome': 'done'}), '')
    monkeypatch.setattr(server.subprocess, 'run', run)
    monkeypatch.setattr(server.Cua, 'backend', property(lambda self: mock.Mock()))
    assert cua.call('desktop_goal', {'goal': 'open settings', 'replies': [{'question': 'Q', 'answer': 'A'}]}) \
        == {'outcome': 'done'}
    assert ran[0][:3] == ['rungic-clicker', 'run', 'open settings'] and ran[0][-2:] == ['--reply', 'Q=A']


# ---- desktop_launch in a workspace: asking before taking the user's app ---------------------------

class FakeKWin:
    """What launch asks KWin; place_next starts the app, as KWin's script does once armed."""

    def __init__(self, windows=()):
        self.list = list(windows)
        self.activated = []
        self.started = 0

    def windows(self):
        return {'screens': ['Virtual-1'], 'outputs': [{'name': 'Virtual-1'}], 'windows': self.list, 'active': None}

    def place_next(self, classes, prefix, start, timeout=12.0):
        start()
        self.started += 1
        return {'placed': True, 'id': '{new}', 'screen': 'Virtual-1'}

    def activate(self, window_id):
        self.activated.append(window_id)
        return True

    def window_action(self, window_id, action):
        return {'found': True}


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    """Cua with a fake KWin, Popen recorded; the workspace's screen counts as on."""
    kwin = FakeKWin()
    cua = server.Cua()
    cua._backend = mock.Mock(kwin=kwin)
    monkeypatch.setattr(server.Cua, 'agent_output', lambda self: 'Virtual-1')
    started = []
    real = subprocess.Popen

    def popen(argv, **kwargs):
        if argv[0] in ('systemd-run', 'kstart'):    # the app's start: recorded, not run
            started.append(argv)
            return mock.Mock()
        return real(argv, **kwargs)
    monkeypatch.setattr(server.subprocess, 'Popen', popen)
    return cua, kwin, started


def sleeper(tmp_path, name, display):
    """A program named `name` running with `display` (the user's session or a workspace)."""
    binary = tmp_path / name
    source = tmp_path / f'{name}.c'
    source.write_text('#include <unistd.h>\nint main(void) { sleep(60); return 0; }\n')
    subprocess.run(['cc', '-o', str(binary), str(source)], check=True)
    process = subprocess.Popen([str(binary)], env={**os.environ, 'WAYLAND_DISPLAY': display})
    time.sleep(0.2)
    return binary, process


# covers: agent.app-switching/E1
@pytest.mark.skipif(not shutil.which('cc'), reason='no C compiler for the test program')
def test_a_single_instance_app_is_closed_on_the_phone_only_after_the_user_agreed(tmp_path, monkeypatch, launcher):
    cua, kwin, started = launcher
    monkeypatch.setenv('RUNGIC_WORKSPACE', '1')
    binary, theirs = sleeper(tmp_path, 'rungic-test-messenger', 'wayland-0')
    try:
        monkeypatch.setattr(switch, 'SINGLE_INSTANCE', switch.SINGLE_INSTANCE | {binary.name})
        monkeypatch.setattr(switch, 'in_call', lambda names: False)
        entry = {'id': 'com.example.Messenger', 'name': 'Messenger', 'classes': ['messenger'], 'exec': str(binary)}
        monkeypatch.setattr(server, 'find_application', lambda query: dict(entry))
        # Through the MCP tool, as Codex calls it: no "switch" -> a question, nothing closed or started.
        asked = cua.call('desktop_launch', {'app': 'Messenger'})
        assert asked['needs_confirmation'] is True and asked['launched'] is None
        assert 'Messenger' in asked['question'] and 'OK' in asked['question']
        assert 'switch' in asked['note'] and theirs.poll() is None and not started
        assert not switch.STATE.exists()
        # The user said yes: "switch": true closes theirs (SIGTERM), records it to give back, opens it here.
        done = cua.call('desktop_launch', {'app': 'Messenger', 'switch': True})
        assert theirs.wait(5) == -15
        assert done['window'] == {'id': '{new}', 'screen': 'Virtual-1'}
        assert 'com.example.Messenger' in json.loads(switch.STATE.read_text())
        assert started and started[0][0] == 'systemd-run'
    finally:
        theirs.kill()


# covers: agent.computer-use/E6
def test_launch_in_a_workspace_runs_the_app_in_a_scope_of_its_own_bound_to_the_workspace(monkeypatch, launcher):
    cua, kwin, started = launcher
    monkeypatch.setenv('RUNGIC_WORKSPACE', '2')
    entry = {'id': 'org.kde.kalk', 'name': 'Kalk', 'classes': ['org.kde.kalk', 'kalk'], 'exec': 'kalk'}
    monkeypatch.setattr(server, 'find_application', lambda query: dict(entry))
    result = cua.launch('计算器')
    assert result['window']['id'] == '{new}'
    argv = started[0]
    # Its own transient scope in the user manager, not the caller's cgroup (the voice service's):
    # the app outlives a restart of the service; the scope is tied to workspace 2 and stops with it.
    assert argv[:5] == ['systemd-run', '--user', '--scope', '--collect', '--quiet']
    unit = argv[argv.index('--unit') + 1]
    assert unit.startswith('app-rungicws2-org.kde.kalk-') and unit.endswith('.scope')
    assert 'BindsTo=rungic-workspace@2.service' in argv
    assert argv[argv.index('--') + 1:] == ['kstart', '--application', 'org.kde.kalk']


# covers: agent.computer-use/E6
def test_an_app_already_open_is_activated_not_started_again(monkeypatch, launcher):
    cua, kwin, started = launcher
    kwin.list = [{'id': '{kalk}', 'caption': 'Kalk', 'resource_class': 'org.kde.kalk', 'output': 'Virtual-1',
                  'minimized': False}]
    entry = {'id': 'org.kde.kalk', 'name': 'Kalk', 'classes': ['org.kde.kalk', 'kalk'], 'exec': 'kalk'}
    monkeypatch.setattr(server, 'find_application', lambda query: dict(entry))
    result = cua.launch('org.kde.kalk', 'phone')
    assert result['already_open'] is True and result['window']['id'] == '{kalk}'
    assert kwin.activated == ['{kalk}'] and not started and kwin.started == 0


# ---- the graphical session's environment with Codex's minimal one ---------------------------------

BUSCTL = '''#!/bin/sh
printf '%s\\n' "$*" "$DBUS_SESSION_BUS_ADDRESS" > "$BUSCTL_LOG"
echo '{"type":"as","data":["MOZ_ENABLE_WAYLAND=1","GDK_BACKEND=wayland","XDG_DATA_DIRS=/usr/share:/usr/local/share",
"WAYLAND_DISPLAY=wayland-0","PLASMA_PLATFORM=phone:grid","QT_QUICK_CONTROLS_MOBILE=1"]}'
'''
PROBE = ('import json, os, sys; sys.path[:0] = sys.argv[1:]; from rungic_cua import server; '
         'server.import_session_environment(); print(json.dumps(dict(os.environ)))')


def environment_after_import(tmp_path, extra):
    """The environment rungic-cua ends up with when started with only `extra` (as Codex does)."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / 'busctl').write_text(BUSCTL)
    (bin_dir / 'busctl').chmod(0o755)
    log = tmp_path / 'busctl.log'
    env = {'PATH': f'{bin_dir}:/usr/bin:/bin', 'HOME': str(tmp_path), 'XDG_RUNTIME_DIR': str(tmp_path),
           'BUSCTL_LOG': str(log), **extra}
    out = subprocess.run([sys.executable, '-c', PROBE, str(STUBS), str(CUA)], env=env, capture_output=True,
                         text=True, timeout=60, check=True).stdout
    return json.loads(out), log.read_text().splitlines()


# covers: agent.computer-use/E7
def test_tools_take_the_graphical_session_environment_from_systemd(tmp_path):
    env, (args, bus) = environment_after_import(tmp_path, {})
    assert 'get-property org.freedesktop.systemd1' in args and args.endswith('Environment')
    assert env['MOZ_ENABLE_WAYLAND'] == '1' and env['GDK_BACKEND'] == 'wayland'
    assert env['XDG_DATA_DIRS'] == '/usr/share:/usr/local/share' and env['WAYLAND_DISPLAY'] == 'wayland-0'
    # What the server was given stays.
    env, _ = environment_after_import(tmp_path, {'GDK_BACKEND': 'x11'})
    assert env['GDK_BACKEND'] == 'x11'


# covers: agent.computer-use/E7
def test_in_a_workspace_the_user_bus_is_asked_and_the_workspace_display_stays(tmp_path):
    env, (_args, bus) = environment_after_import(tmp_path, {
        'RUNGIC_WORKSPACE': '1', 'WAYLAND_DISPLAY': 'wayland-ws-1', 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/ws1-bus',
        'RUNGIC_USER_DBUS_SESSION_BUS_ADDRESS': 'unix:path=/user-bus'})
    assert bus == 'unix:path=/user-bus'
    assert env['WAYLAND_DISPLAY'] == 'wayland-ws-1' and env['DBUS_SESSION_BUS_ADDRESS'] == 'unix:path=/ws1-bus'
    assert env['MOZ_ENABLE_WAYLAND'] == '1' and env['PLASMA_PLATFORM'] == 'phone:grid'
    # The independent desktop (workspace 0) is a desktop: the phone's form factor stays out.
    env, _ = environment_after_import(tmp_path, {'RUNGIC_WORKSPACE': '0', 'WAYLAND_DISPLAY': 'wayland-ws-0'})
    assert 'PLASMA_PLATFORM' not in env and 'QT_QUICK_CONTROLS_MOBILE' not in env
    assert env['MOZ_ENABLE_WAYLAND'] == '1'


# ---- desktop_goal with Luna: outcomes, questions, stop, captions ----------------------------------

class Recorder:
    """The input: what reached it, in order."""

    def __init__(self):
        self.events = []

    def __getattr__(self, name):
        return lambda *a, **kw: self.events.append((name, a, kw))


def scripted_model(monkeypatch, replies):
    """The Responses API answering `replies` in order; each request and the caption the user saw
    when it was sent are kept."""
    mode.save('luna')
    seen = []

    def respond(self, body):
        seen.append({'body': body, 'caption': activity.read()})
        return replies[len(seen) - 1]
    monkeypatch.setattr(luna.ComputerUse, '_respond', respond)
    monkeypatch.setattr(screen_module.Screen, 'capture', lambda self: (
        setattr(self, 'scope', 'only the "Chat" window') or ('data:image/jpeg;base64,', mock.Mock(width=800, height=600),
                                                             False)))
    return seen


def call(actions, text=None, n=1):
    output = [{'type': 'computer_call', 'call_id': f'c{n}', 'actions': actions}]
    if text:
        output.append({'type': 'message', 'content': [{'type': 'output_text', 'text': text}]})
    return {'id': f'r{n}', 'output': output}


def answer(text, n=9):
    return {'id': f'r{n}', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}


def computer():
    backend = mock.Mock(input=Recorder())
    use = luna.ComputerUse(backend, 'Virtual-1', '{w}')
    use.screen.point = lambda x, y: (x, y)
    return use, backend


# covers: agent.computer-use/E3 agent.watch-work/E3
def test_goal_runs_until_the_model_says_done_and_captions_each_batch(monkeypatch):
    seen = scripted_model(monkeypatch, [
        call([{'type': 'click', 'x': 40, 'y': 50}], '打开“文件”菜单', 1),
        call([{'type': 'type', 'text': '周楷雯'}], None, 2),
        call([{'type': 'keypress', 'keys': ['ENTER']}], None, 3),
        answer('DONE: 周楷雯的聊天已打开')])
    use, backend = computer()
    result = use.run('打开和周楷雯的聊天')
    assert result['outcome'] == 'done' and result['achieved'] is True and result['answer'] == '周楷雯的聊天已打开'
    assert len(result['steps']) == 3
    assert backend.kwin.commit_text.call_args.args == ('周楷雯',)
    # The caption the user watched before each request: looking first, then the model's own phrase,
    # else what the batch does (the model wrote none for the 2nd and 3rd).
    shown = [s['caption'].get('text') for s in seen]
    assert shown == [activity._('Look at the screen'), '打开“文件”菜单',
                     activity.describe({'type': 'type', 'text': '周楷雯'}), activity.describe({'type': 'keypress', 'keys': ['ENTER']})]
    assert all(s['caption']['state'] == 'working' and s['caption']['task'] == '打开和周楷雯的聊天' for s in seen)
    assert activity.read()['state'] == 'done' and activity.read()['text'] == '周楷雯的聊天已打开'
    # The model is told to write the caption itself, short, in the task's language.
    assert 'at most 15 characters in Chinese' in seen[0]['body']['instructions']


# covers: agent.watch-work/E3
def test_fallback_captions_name_the_action_type():
    assert activity.describe({'type': 'click', 'x': 1, 'y': 2}) == activity._('Click')
    assert activity.describe({'type': 'double_click'}) == activity._('Double-click')
    assert activity.describe({'type': 'scroll'}) == activity._('Scroll the page')
    assert activity.describe({'type': 'keypress', 'keys': ['enter']}) == activity._('Press Enter')
    assert activity.describe({'type': 'keypress', 'keys': ['CTRL', 'L']}) == activity._('Press {keys}').format(keys='CTRL+L')
    long = activity.describe({'type': 'type', 'text': '一二三四五六七八九十一二三四五六七八九十一二三四五六'})
    assert long.endswith('…”') or long.endswith('…"')


# covers: agent.computer-use/E3
def test_a_question_ends_the_run_and_the_reply_comes_back_with_the_goal(monkeypatch):
    seen = scripted_model(monkeypatch, [answer('ASK: 有两个周楷雯，要找哪一个？')])
    use, _backend = computer()
    result = use.run('给周楷雯发消息')
    assert result['outcome'] == 'question' and result['question'] == '有两个周楷雯，要找哪一个？'
    assert activity.read()['state'] == 'question'
    # The assistant asked the user and calls desktop_goal again with the answer.
    scripted_model(monkeypatch, [answer('DONE ok')])
    cua = server.Cua()
    cua._backend = mock.Mock()
    monkeypatch.setattr(server.Cua, 'agent_output', lambda self: 'Virtual-1')
    bodies = []
    original = luna.ComputerUse._respond
    monkeypatch.setattr(luna.ComputerUse, '_respond', lambda self, body: bodies.append(body) or original(self, body))
    done = cua.call('desktop_goal', {'goal': '给周楷雯发消息', 'replies': [
        {'question': '有两个周楷雯，要找哪一个？', 'answer': '同事那个'}]})
    assert done['outcome'] == 'done'
    task = bodies[0]['input'][0]['content'][0]['text']
    assert task.startswith('给周楷雯发消息') and '有两个周楷雯，要找哪一个？' in task and '同事那个' in task


# covers: agent.computer-use/E3
def test_failed_and_unfinished_runs_say_so(monkeypatch):
    scripted_model(monkeypatch, [answer('FAILED: 找不到发送按钮')])
    use, _ = computer()
    result = use.run('发送')
    assert result['outcome'] == 'failed' and result['achieved'] is False and result['answer'] == '找不到发送按钮'
    assert activity.read()['state'] == 'failed'
    scripted_model(monkeypatch, [call([{'type': 'wait'}], None, n) for n in range(1, 4)])
    monkeypatch.setattr(luna.time, 'sleep', lambda s: None)
    result = computer()[0].run('wait forever', max_steps=2)
    assert result['outcome'] == 'unfinished' and activity.read()['state'] == 'failed'


# covers: agent.computer-use/E3
def test_the_users_stop_ends_the_run_before_the_next_action(monkeypatch):
    def stop_then_act(self, body):
        luna.ABORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        luna.ABORT_FILE.touch()          # the voice agent's stop (rungic_voice_agent.luna_goal)
        return call([{'type': 'click', 'x': 1, 'y': 1}], None, 1)
    scripted_model(monkeypatch, [])
    monkeypatch.setattr(luna.ComputerUse, '_respond', stop_then_act)
    use, backend = computer()
    result = use.run('删除所有文件')
    assert result['outcome'] == 'stopped' and backend.input.events == []
    assert activity.read()['state'] == 'stopped' and not luna.ABORT_FILE.exists()


def test_the_voice_agents_stop_writes_the_file_lunas_loop_watches(tmp_path):
    """rungic_voice_agent.luna_goal touches XDG_RUNTIME_DIR/rungic-clicker/abort; luna watches that file."""
    probe = 'import sys; sys.path[:0] = sys.argv[1:]; from rungic_cua import luna; print(luna.ABORT_FILE)'
    out = subprocess.run([sys.executable, '-c', probe, str(STUBS), str(CUA)], capture_output=True, text=True, check=True,
                         env={**os.environ, 'XDG_RUNTIME_DIR': str(tmp_path)}).stdout.strip()
    assert out == str(tmp_path / 'rungic-clicker' / 'abort')
    assert "'rungic-clicker' / 'abort'" in (ROOT / 'agent/assistant/rungic_voice_agent.py').read_text()


# ---- captions on each screen (activity.py) ---------------------------------------------------------

# covers: agent.watch-work/E1 agent.watch-work/E2
def test_each_workspace_has_its_own_caption_and_a_silent_writer_goes_stale(monkeypatch):
    activity.report('打开 Dolphin', workspace=1)
    activity.report('在桌面上输入', workspace='')
    monkeypatch.setenv('RUNGIC_WORKSPACE', '2')
    activity.report('  点击   “保存”  ')            # the writer's own workspace, from its environment
    assert activity.read(1)['text'] == '打开 Dolphin' and activity.read('')['text'] == '在桌面上输入'
    assert activity.read(2)['text'] == '点击 “保存”' and activity.read(3) == {}
    assert activity.path(2).name == 'activity-ws2.json' and activity.path('').name == 'activity.json'
    # A "working" caption nobody updated for 120 s is gone; an ending stays readable.
    old = json.loads(activity.path(1).read_text())
    old['time'] -= activity.STALE_S + 1
    activity.path(1).write_text(json.dumps(old))
    assert activity.read(1) == {}
    activity.report('', state='done', workspace=1)
    assert activity.read(1)['state'] == 'done'
    activity.report('x' * 200, workspace=1)
    assert len(activity.read(1)['text']) == 80


# covers: agent.watch-work/E2
def test_the_voice_service_tells_the_newer_of_its_workspace_and_the_desktop(monkeypatch):
    sys.path.insert(0, str(ROOT / 'agent/assistant'))
    import rungic_voice_agent as voice
    activity.report('在工作区里点击', workspace=voice.WORKSPACE)
    time.sleep(0.01)
    activity.report('在桌面上输入', workspace='')
    assert voice.screen_activity()['text'] == '在桌面上输入' and voice.working_screen() == ''
    time.sleep(0.01)
    activity.report('在工作区里滚动', workspace=voice.WORKSPACE)
    assert voice.screen_activity()['text'] == '在工作区里滚动' and voice.working_screen() == voice.WORKSPACE
    # Another workspace's caption (a team member's) is not this agent's.
    time.sleep(0.01)
    activity.report('成员在 3 号工作区', workspace=3)
    assert voice.screen_activity()['text'] == '在工作区里滚动'


# covers: agent.watch-work/E1
def test_a_workspace_caption_also_goes_to_the_app_for_the_directors_tiles(tmp_path, monkeypatch):
    import contracts
    with contracts.StandIn('platform-bridge') as bridge:
        monkeypatch.setenv('RUNGIC_PLATFORM_SOCKET', bridge.path)
        activity.report('打开 Krita', workspace=2)
        activity.report('桌面上的字幕', workspace='')
        deadline = time.monotonic() + 3
        while not bridge.requests and time.monotonic() < deadline:
            time.sleep(0.05)
    assert bridge.requests == [{'op': 'director', 'caption': {'slot': 2, 'state': 'working', 'text': '打开 Krita'}}]


# ---- plan two: OCR on the phone's GPU, or the CPU at once -----------------------------------------

def ocr_server(path, reply):
    """A socket answering one OCR request as the Rungic app's OcrBridge does; returns what it got."""
    got = {}
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        with conn:
            stream = conn.makefile('rb')
            header = json.loads(stream.readline())
            got['header'] = header
            got['pixels'] = len(stream.read(header['bytes']))
            conn.sendall(json.dumps(reply).encode() + b'\n')
        srv.close()
    threading.Thread(target=serve, daemon=True).start()
    return got


# covers: agent.plan-two/E2
def test_ocr_falls_back_to_the_cpu_at_once_while_the_phone_cannot(tmp_path, monkeypatch):
    from PIL import Image
    import linux_ocr
    image = Image.new('RGB', (64, 32), 'white')
    cpu = [('cpu', 0.9, (0, 0, 1, 1))]
    monkeypatch.setattr(linux_ocr, 'ENGINE', 'android')
    monkeypatch.setattr(linux_ocr, 'local', lambda img: cpu)
    monkeypatch.setenv('https_proxy', 'http://192.0.2.1:3128')
    # The models are still downloading: the app answers with an error at once; the CPU reads the text.
    monkeypatch.setattr(linux_ocr, 'SOCKET', str(tmp_path / 'ocr1.sock'))
    got = ocr_server(tmp_path / 'ocr1.sock', {'available': False, 'downloading': True, 'error': 'OCR models downloading'})
    started = time.monotonic()
    assert linux_ocr.recognize(image, scale=3.0) == cpu
    assert time.monotonic() - started < 2
    assert got['header']['op'] == 'ocr' and got['pixels'] == 64 * 32 * 3 and got['header']['det_scale'] == 0.5
    assert got['header']['proxy'] == 'http://192.0.2.1:3128'      # the app downloads through this side's proxy
    # No app answering at all (frozen, not installed): the CPU too.
    monkeypatch.setattr(linux_ocr, 'SOCKET', str(tmp_path / 'absent.sock'))
    assert linux_ocr.recognize(image) == cpu
    # The GPU's answer is used as it is.
    monkeypatch.setattr(linux_ocr, 'SOCKET', str(tmp_path / 'ocr2.sock'))
    ocr_server(tmp_path / 'ocr2.sock', {'lines': [{'text': '设置', 'score': 0.98, 'box': [1, 2, 30, 12]}], 'ms': {}})
    assert linux_ocr.recognize(image) == [('设置', 0.98, (1.0, 2.0, 30.0, 12.0))]


# ---- plan two: where a control is, from the accessibility tree --------------------------------------

# covers: agent.plan-two/E5
def test_control_positions_come_from_screen_extents_relative_to_the_window_root():
    from rungic_cua import a11y, backend as backend_module
    # Like WeChat's Qt: "window" coordinates garbled (y and height), screen extents consistent.
    extents = {('w', 0): (500, 300, 800, 600), ('b', 0): (620, 340, 80, 30),
               ('w', 1): (0, -9000, 800, 7), ('b', 1): (120, -8960, 80, 3)}
    bus = object.__new__(a11y.A11yBus)
    bus.call = lambda name, path, interface, method, args=None: (extents[(path, args.unpack()[0])],)
    window = a11y.Node(':1.5', 'w', None, interfaces=('org.a11y.atspi.Component',))
    button = a11y.Node(':1.5', 'b', window, interfaces=('org.a11y.atspi.Component',))
    origin = bus.origin(window)
    assert origin == (500, 300)
    assert bus.extents(button, origin) == (120, 40, 80, 30)
    # The click goes to KWin's client geometry plus that: shadows and the title bar (the frame) don't
    # shift it, whatever the toolkit thinks its window's position is.
    linux = object.__new__(backend_module.LinuxAtspiBackend)
    linux.bus, linux._origin = bus, origin
    linux._window = {'client': (1000, 220, 800, 600), 'frame': (976, 160, 848, 684)}
    assert linux._global_center(button) == (1000 + 120 + 40, 220 + 40 + 15)


# ---- what the model sees: the task's window, its popups, in the screen's own pixels -----------------

SHOT = '''#!/usr/bin/env python3
# rungic-screenshot as KWin answers it: CaptureWindow at the output's scale, CaptureArea at the largest
# scale of all outputs (the phone's 3). One JSON line, then BGRA pixels.
import json, os, sys
args = sys.argv[1:]
open(os.environ["SHOT_LOG"], "a").write(" ".join(args) + "\\n")
frames = json.loads(os.environ["SHOT_FRAMES"])
scale = 3 if args[0] == "area" else 1
w, h = (int(args[3]), int(args[4])) if args[0] == "area" else frames[args[1]]
w, h = w * scale, h * scale
sys.stdout.buffer.write(json.dumps({"width": w, "height": h, "stride": w * 4, "format": 5}).encode() + b"\\n")
sys.stdout.buffer.write(bytes([40, 80, 200, 255]) * (w * h))
'''


class TargetKWin:
    """KWin's view of the task's window (kwin.target): the window, the active one, its popups."""

    def __init__(self):
        self.window = {'id': '{chat}', 'pid': 10, 'caption': 'Chat', 'resource_class': 'chat', 'normal': True,
                       'dialog': False, 'minimized': False, 'output': 'Virtual-1', 'frame': [100, 50, 640, 480]}
        self.active = dict(self.window)
        self.related = []

    def target(self, window_id):
        target = self.window if window_id in ('', self.window['id']) else self.active
        return {'target': target, 'active': self.active, 'related': self.related if target is self.window else [],
                'outputs': [{'name': 'Virtual-1', 'geometry': [0, 0, 1920, 1080], 'scale': 1}]}


# covers: agent.computer-use/E2
def test_the_screenshot_is_the_window_then_its_menus_in_the_screens_own_pixels(tmp_path, monkeypatch):
    shot = tmp_path / 'rungic-screenshot'
    shot.write_text(SHOT)
    shot.chmod(0o755)
    monkeypatch.setattr(screen_module, 'SCREENSHOT', str(shot))
    monkeypatch.setenv('SHOT_LOG', str(tmp_path / 'shot.log'))
    monkeypatch.setenv('SHOT_FRAMES', json.dumps({'{chat}': [640, 480], '{dialog}': [300, 200]}))
    kwin = TargetKWin()
    screen = screen_module.Screen(mock.Mock(kwin=kwin), 'Virtual-1', '{chat}')
    url, image, changed = screen.capture()
    assert url.startswith('data:image/jpeg;base64,') and (image.width, image.height) == (640, 480) and changed
    assert screen.note().startswith('(This screenshot shows only the "Chat" window')
    assert screen.point(0, 0) == (100, 50) and screen.point(320.4, 240) == (420.4, 290)    # its pixels, 1:1
    assert screen.capture()[2] is False                       # the same view: nothing to tell the model
    # A menu opens (a separate window in Wayland, reaching outside the window): the area of both, rendered
    # by KWin at the phone's scale 3 and brought back to this screen's pixels.
    kwin.related = [{'id': '{menu}', 'frame': [680, 120, 200, 300]}]
    _url, image, changed = screen.capture()
    assert changed and screen.scope == 'the "Chat" window with its open menus and dialogs'
    assert (image.width, image.height) == (780, 480)
    assert screen.point(780 - 1, 70) == (879, 120)            # the menu's right edge, where it is
    # Another app's dialog becomes active on this screen: the task moved there.
    kwin.related = []
    kwin.active = {**kwin.window, 'id': '{dialog}', 'pid': 11, 'caption': 'Save', 'dialog': True, 'normal': False,
                   'frame': [800, 300, 300, 200]}
    _url, image, changed = screen.capture()
    assert screen.window_id == '{dialog}' and (image.width, image.height) == (300, 200) and changed
    with pytest.raises(ValueError):
        screen.point(-1200, 10)                                # off the screen: refused, not clicked
    assert (tmp_path / 'shot.log').read_text().splitlines() == [
        'window {chat}', 'window {chat}', 'area 100 50 780 480', 'window {dialog}']


# covers: agent.computer-use/E1
@pytest.mark.parametrize('message', ['Rungic09AbC123 A ! @', '中文🙂', 'a\nkey 65 1'])
def test_workspace_text_is_one_framed_request(message, monkeypatch):
    from rungic_cua.fakeinput import WorkspaceInput
    source = WorkspaceInput()
    sent = []
    monkeypatch.setattr(source, '_send', sent.append)
    source.type_text(message)
    assert len(sent) == 1 and '\n' not in sent[0]
    assert bytes.fromhex(sent[0].removeprefix('text ')).decode() == message
    sent.clear()
    source.type_text('')
    assert sent == []


# covers: agent.computer-use/E1 agent.plan-two/E1
@pytest.mark.parametrize('remote_name', [
    'org.freedesktop.DBus.Error.UnknownMethod',
    'org.freedesktop.DBus.Error.NoReply',
    'org.freedesktop.DBus.Error.ServiceUnknown',
    'org.freedesktop.DBus.Error.AccessDenied',
])
@pytest.mark.parametrize('text', ['Rungic09AbC123 A ! @', '中文'])
def test_text_fallback_only_when_commit_method_is_missing(remote_name, text, monkeypatch):
    from types import SimpleNamespace
    from gi.repository import Gio
    from rungic_cua import backend as backend_module
    import types
    ax = types.ModuleType('typesafe.ax_walk')
    models = types.ModuleType('typesafe.models')
    for name in ('AX_PRESS', 'AxAttrs', 'Frame', 'walk_actionable'):
        setattr(ax, name, mock.Mock())
    for name in ('TEXT_ROLES', 'Abort', 'AxNode', 'Field', 'Missed'):
        setattr(models, name, mock.Mock())
    # Upstream observation types are absent offline; text routing uses none.
    with mock.patch.dict(sys.modules, {'typesafe.ax_walk': ax, 'typesafe.models': models}):
        from typesafe import linux
    error = Gio.DBusError.new_for_dbus_error(remote_name, 'deliberate failure')
    backend = backend_module.LinuxAtspiBackend.__new__(backend_module.LinuxAtspiBackend)
    backend.input = mock.Mock()
    backend.kwin = mock.Mock()
    backend.kwin.commit_text.side_effect = error
    backend._pointer = mock.Mock()
    backend._no_virtual_keyboard = mock.Mock()
    backend._set_text = mock.Mock()
    monkeypatch.setattr(backend_module.time, 'sleep', lambda _: None)
    node = mock.Mock()
    missing = remote_name.endswith('UnknownMethod')
    if missing:
        backend._type_text(node, text)
        if text.isascii():
            backend.input.type_text.assert_called_once_with(text)
            backend._set_text.assert_not_called()
        else:
            backend._set_text.assert_called_once_with(node, text)
            backend.input.type_text.assert_not_called()
    else:
        with pytest.raises(type(error)):
            backend._type_text(node, text)
        backend.input.type_text.assert_not_called()
        backend._set_text.assert_not_called()
    backend.input.reset_mock()
    monkeypatch.setattr(linux, '_desktop', lambda: SimpleNamespace(backend=backend))
    monkeypatch.setattr(linux, '_input', lambda: backend.input)
    if missing and text.isascii():
        linux.type_text(text)
        backend.input.type_text.assert_called_once_with(text)
    else:
        with pytest.raises(type(error)):
            linux.type_text(text)
        backend.input.type_text.assert_not_called()


# covers: agent.computer-use/E1
def test_text_commit_success_never_calls_compatibility_input(monkeypatch):
    from rungic_cua.backend import LinuxAtspiBackend
    backend = LinuxAtspiBackend.__new__(LinuxAtspiBackend)
    backend._pointer = mock.Mock()
    backend._no_virtual_keyboard = mock.Mock()
    backend._set_text = mock.Mock()
    backend.input = mock.Mock()
    backend.kwin = mock.Mock()
    monkeypatch.setattr(time, 'sleep', lambda _: None)
    backend._type_text(mock.Mock(), '中文Rungic09AbC123!@')
    backend.kwin.commit_text.assert_called_once_with('中文Rungic09AbC123!@')
    backend.input.type_text.assert_not_called()
    backend._set_text.assert_not_called()
