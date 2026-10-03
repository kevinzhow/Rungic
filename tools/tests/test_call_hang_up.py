# SPDX-License-Identifier: GPL-2.0-or-later
"""Hanging up a proxied call does not wait for a call step still running (docs/63): in Codex mode the
call's desktop steps (VoiceAgent.desktop_goal) take turns on desktop_lock, and dialing or a timer check
may hold it for up to its timeout. The real desktop_goal and cancel_desktop_steps run here against a
stand-in of Codex's app-server."""
import ast
import json
import os
from pathlib import Path
import sys
import threading
import time
import types
import uuid
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'agent/assistant/rungic_voice_agent.py'
tree = ast.parse(SOURCE.read_text())
WANTED = {'desktop_goal', 'cancel_desktop_steps'}
agent_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'VoiceAgent')
agent_class.body = [n for n in agent_class.body if isinstance(n, ast.FunctionDef) and n.name in WANTED]
turn_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'BackgroundTurn')


class Server:
    """Codex's app-server: a step's turn ends when the model is done (`finish`) or is interrupted."""

    def __init__(self, agent):
        self.agent, self.lock, self.count = agent, threading.Lock(), 0
        self.finish = {}          # goal fragment -> final text, else the turn runs until interrupted
        self.calls = []

    def call(self, method, params, timeout=None):
        self.calls.append(method)
        with self.lock:
            self.count += 1
            n = self.count
        if method == 'thread/start':
            return {'thread': {'id': f'thread-{n}'}}
        if method == 'turn/start':
            turn = self.agent.background[params['threadId']]
            goal = params['input'][0]['text']
            for fragment, text in self.finish.items():
                if fragment in goal:
                    threading.Timer(0.05, lambda: (turn.on('item/completed', {'item': {
                        'type': 'agentMessage', 'text': text, 'phase': 'final_answer'}}),
                        turn.on('turn/completed', {'turn': {'status': 'completed'}}))).start()
            return {'turn': {'id': f'turn-{n}'}}
        if method == 'turn/interrupt':
            turn = self.agent.background.get(params['threadId'])
            if turn:
                turn.on('turn/completed', {'turn': {'status': 'interrupted'}})
            return {}
        return {}


def make_agent(tmp_path):
    namespace = dict(json=json, os=os, time=time, uuid=uuid, threading=threading, Path=Path,
                     log=lambda *a: None, app_env=lambda app: {}, workspace_env=lambda: {},
                     luna_goal=Mock(side_effect=AssertionError('Codex mode never runs the API executor')))
    exec(compile(ast.Module(body=[turn_class, agent_class], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    agent = namespace['VoiceAgent']()
    agent.desktop_lock, agent.desktop_jobs, agent.desktop_generation = threading.Lock(), {}, 0
    agent.background, agent.phone, agent.call, agent.thread_id = {}, None, None, 'conversation'
    agent.agent_busy = False
    agent.emit = lambda event: None
    agent.thread_settings = lambda: {'config': {}}
    agent.server = Server(agent)
    return agent


# covers: agent.call-proxy/E5
def test_hang_up_stops_a_running_step_instead_of_waiting_for_it(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))          # no ~/.codex/config.toml of this computer
    mode = types.SimpleNamespace(plan=lambda: 'codex')
    with patch.dict(sys.modules, {'rungic_cua.mode': mode, 'rungic_cua': types.SimpleNamespace(mode=mode)}):
        agent = make_agent(tmp_path)
        agent.server.finish = {'End the call': 'DONE the call has ended'}
        results = {}
        dialing = threading.Thread(target=lambda: results.update(dial=agent.desktop_goal(
            'Dial the contact and wait for the call to connect.', timeout=120)))
        dialing.start()
        deadline = time.monotonic() + 5
        while not agent.desktop_jobs and time.monotonic() < deadline:
            time.sleep(0.01)
        assert agent.desktop_jobs, 'the dial step runs and holds desktop_lock'
        # What hang_up() does (the closure in _start_call): stop the running step, then hang up.
        started = time.monotonic()
        agent.cancel_desktop_steps()
        hung_up = agent.desktop_goal('End the call that is in progress: press the hang-up control.', timeout=60)
        took = time.monotonic() - started
        dialing.join(5)
    assert hung_up['outcome'] == 'done', hung_up
    assert took < 5, f'hanging up waited {took:.1f} s for the dial step'
    assert results['dial']['outcome'] == 'stopped', results
    assert not list(tmp_path.glob('rungic-task-leases/*.json')), 'every tool lease is gone'


# covers: agent.call-proxy/E5
def test_hang_up_cancels_the_running_steps_first():
    source = SOURCE.read_text()
    body = source[source.index('        def hang_up():'):]
    body = body[:body.index('result = self.desktop_goal(')]
    assert 'self.cancel_desktop_steps()' in body, 'hang_up() stops the running step before its own'
