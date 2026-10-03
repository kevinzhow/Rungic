"""Credential routing and tool boundaries, without a phone or network."""
import ast
import json
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'agent/computer-use'))
from rungic_cua import mode


@pytest.fixture
def selection(tmp_path, monkeypatch):
    monkeypatch.setattr(mode, 'PLAN_FILE', tmp_path / 'plan')
    return mode


def definitions(path, selected, namespace):
    tree = ast.parse((ROOT / path).read_text())
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in selected:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in selected for t in node.targets):
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, 'exec'), namespace)
    return namespace


def tools():
    ns = definitions('agent/computer-use/rungic_cua/server.py', {
        'SUBTASK_SCHEMA', 'TOOLS', 'ACTION_SCHEMA', 'PLAN_ONE_TOOLS', 'VOICE_CODEX_TOOLS',
        'PLAN_TWO_ONLY', 'DECIDERS', 'tools_for', 'VOICE_MESSAGE_LUNA'}, {})
    return ns['tools_for']


# covers: agent.computer-use/E8
def test_no_setting_and_invalid_setting_use_codex(selection):
    assert selection.plan() == 'codex'
    selection.PLAN_FILE.write_text('unknown\n')
    assert selection.plan() == 'codex'


# covers: agent.computer-use/E8
def test_explicit_api_and_ocr_choices_are_preserved(selection):
    for chosen in ('luna', 'atspi', 'codex'):
        selection.save(chosen)
        assert selection.plan() == chosen
    assert selection.save('api') == 'luna'
    with pytest.raises(ValueError):
        selection.save('unknown')
    assert selection.plan() == 'luna'


# covers: agent.computer-use/E8
def test_codex_tools_cannot_delegate_to_a_hidden_api():
    names = {t['name'] for t in tools()('codex')}
    assert {'desktop_screenshot', 'desktop_act', 'desktop_voice_recording'} <= names
    assert not names & {'desktop_goal', 'desktop_voice_message', 'desktop_observe', 'desktop_run'}


# covers: agent.computer-use/E8
def test_api_mode_keeps_goal_and_voice_executor():
    schemas = {t['name']: t for t in tools()('luna')}
    assert {'desktop_goal', 'desktop_voice_message', 'desktop_screenshot', 'desktop_act'} <= schemas.keys()
    assert 'desktop_voice_recording' not in schemas
    assert 'GPT-6 Luna' in schemas['desktop_goal']['description']
    assert schemas['desktop_voice_message']['inputSchema']['required'] == ['text']


# covers: agent.computer-use/E3
def test_api_loop_still_sends_computer_tools_and_action_screenshot(monkeypatch, tmp_path):
    from rungic_cua import luna
    from PIL import Image
    monkeypatch.setattr(luna.activity, 'report', Mock())
    monkeypatch.setattr(luna, 'SETTLE_S', 0)
    monkeypatch.setattr(luna, 'ABORT_FILE', tmp_path / 'abort')
    computer = luna.ComputerUse(Mock(), 'test-output')
    computer.screen.capture = Mock(return_value=('data:image/jpeg;base64,test', Image.new('RGB', (20, 10)), False))
    computer.screen.note = Mock(return_value='test screenshot')
    computer.execute = Mock(return_value='click')
    computer._respond = Mock(side_effect=[
        {'id': 'first', 'output': [{'type': 'computer_call', 'call_id': 'action',
            'actions': [{'type': 'click', 'x': 3, 'y': 4}]}]},
        {'id': 'second', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': 'DONE visible result'}]}]},
    ])
    assert computer.run('Click the authorized control')['answer'] == 'visible result'
    first, second = [c.args[0] for c in computer._respond.call_args_list]
    assert {'type': 'computer'} in first['tools']
    assert second['previous_response_id'] == 'first'
    assert second['input'][0]['type'] == 'computer_call_output'
    computer.execute.assert_called_once_with({'type': 'click', 'x': 3, 'y': 4})


# covers: agent.computer-use/E8
def test_direct_tools_reject_hidden_goal_calls(selection):
    ns = definitions('agent/computer-use/rungic_cua/server.py', {'Cua'}, {
        'plan': selection.plan, 'Desktop': Mock, 'ComputerUse': Mock(),
        'PLAN_TWO_ONLY': ('desktop_observe', 'desktop_run', 'desktop_find_name'),
    })
    cua = ns['Cua'].__new__(ns['Cua'])
    for name in ('desktop_goal', 'desktop_voice_message', 'desktop_run'):
        with pytest.raises(ValueError, match='Codex mode'):
            cua.call(name, {})
    ns['ComputerUse'].assert_not_called()


def agent_methods(names, namespace):
    tree = ast.parse((ROOT / 'agent/assistant/rungic_voice_agent.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'VoiceAgent')
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=[cls], type_ignores=[]), 'rungic_voice_agent.py', 'exec'), namespace)
    return namespace['VoiceAgent']


# covers: agent.computer-use/E9
def test_mode_change_does_not_cancel_busy_tasks(selection):
    cls = agent_methods({'set_desktop_mode'}, {'threading': threading, '_': lambda x: x, 'openai_key': lambda: 'set'})
    agent = cls()
    agent.lock = threading.RLock()
    agent.agent_busy = False
    agent.background = {}
    agent.call = None
    agent.phone = Mock(snapshot={'tasks': [{'status': 'running'}]})
    assert 'error' in agent.set_desktop_mode('api')
    assert not selection.PLAN_FILE.exists()


# covers: agent.computer-use/E9
@pytest.mark.parametrize('starting,session', [(True, ''), (False, 'phone-session')])
def test_mode_change_preserves_a_starting_or_idle_phone_session(selection, starting, session):
    restart = Mock()
    cls = agent_methods({'set_desktop_mode'}, {'threading': threading, '_': lambda x: x,
                                            'openai_key': lambda: 'set'})
    agent = cls()
    agent.lock = threading.RLock()
    agent.agent_busy = False
    agent.background = {}
    agent.call = None
    agent.phone_starting = starting
    agent.phone = Mock(snapshot={'sessionId': session, 'tasks': []})
    agent.restart_server = restart
    selection.save('codex')
    assert 'error' in agent.set_desktop_mode('api')
    assert selection.plan() == 'codex'
    restart.assert_not_called()


# covers: agent.computer-use/E8
def test_call_step_reuses_codex_and_revokes_its_lease(selection, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
    ns = definitions('agent/assistant/rungic_voice_agent.py', {'BackgroundTurn'}, {'threading': threading})
    import uuid
    cls = agent_methods({'desktop_goal'}, {**ns, 'Path': Path, 'os': os, 'json': json,
        'time': time, 'threading': threading, 'uuid': uuid, 'app_env': lambda app: {},
        'workspace_env': lambda: {}, 'log': lambda *a: None,
        'luna_goal': Mock(side_effect=AssertionError('separate API must not run'))})
    agent = cls()
    agent.desktop_lock = threading.Lock()
    agent.desktop_jobs = {}
    agent.desktop_generation = 0
    agent.thread_id = 'origin'
    agent.call = None
    agent.background = {}
    agent.phone = None
    agent.agent_busy = False
    agent.emit = Mock()
    agent.thread_settings = lambda: {'config': {}, 'model': 'gpt-6-luna'}
    calls = []
    def rpc(method, params, timeout=30):
        calls.append((method, params))
        if method == 'thread/start':
            assert params['model'] == 'gpt-6-luna'
            assert params['config']['mcp_servers.rungic-desktop.command'] == 'rungic-task-tools'
            assert len(list((tmp_path / 'rungic-task-leases').glob('*.json'))) == 1
            return {'thread': {'id': 'helper'}}
        if method == 'turn/start':
            turn = agent.background['helper']
            turn.on('item/completed', {'item': {'type': 'agentMessage', 'text': 'DONE call ended', 'phase': 'final_answer'}})
            turn.on('turn/completed', {'turn': {'status': 'completed'}})
            return {'turn': {'id': 'turn'}}
        return {}
    agent.server = Mock(call=rpc)
    assert agent.desktop_goal('End the call')['outcome'] == 'done'
    assert calls[-1][0] == 'thread/unsubscribe'
    assert agent.background == agent.desktop_jobs == {}
    assert not list((tmp_path / 'rungic-task-leases').glob('*.json'))
