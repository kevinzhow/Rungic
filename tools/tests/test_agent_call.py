#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The call with the Agent in the Agent app (Claude Design canvas "Agent 通话", docs/101): where it
starts (the conversation's top bar, the conversations panel), what the call bar says as the phone
session's state changes, the panel it opens, and the summary a call leaves. The app's real QML runs
offline (assistant_qml.py); the test plays the voice service. call.js's rules are also checked alone.
Requires PySide6.
"""
from pathlib import Path
import sys
import time
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import assistant_qml as q  # noqa: E402

CALL_JS = Path(__file__).resolve().parents[2] / 'agent/assistant/app/qml/call.js'


def call_js():
    """call.js in a JavaScript engine of its own, tr the English source (as the tests' i18n)."""
    from PySide6.QtQml import QJSEngine
    engine = QJSEngine()
    source = CALL_JS.read_text().replace('.pragma library', '')
    result = engine.evaluate(source + '\n;({mode, clock, words, tasks, panelTasks, blocked})')
    assert not result.isError(), result.toString()
    tr = engine.evaluate('(function(c, t) { const a = Array.prototype.slice.call(arguments, 2); '
                         'return t.replace(/%(\\d+)/g, (m, n) => String(a[n - 1])) })')
    return engine, result, tr


def phone(**fields):
    base = {'sessionId': 'voice_1', 'conversation': 'c1', 'phase': 'connected', 'muted': False, 'speaking': False,
            'listening': False, 'thinking': False, 'startedAt': time.time() - 252, 'tasks': []}
    base.update(fields)
    return base


class CallRules(unittest.TestCase):
    """call.js: the bar's state, the first that applies, and its words."""

    def setUp(self):
        self.engine, self.call, self.tr = call_js()

    def run_js(self, name, *args):
        value = self.call.property(name).call([self.engine.toScriptValue(a) if not hasattr(a, 'isCallable') else a for a in args])
        return value.toVariant()

    # covers: agent.phone-mode/E14
    def test_the_bar_says_the_first_state_that_applies(self):
        waiting = {'taskId': 't1', 'conversation': 'c1', 'status': 'waiting_input', 'created': time.time(), 'text': 'sort'}
        cases = [
            (None, ''),
            (phone(conversation='other'), 'elsewhere'),
            (phone(phase='connecting', speaking=True), 'connecting'),
            (phone(speaking=True, tasks=[waiting]), 'answer'),
            (phone(speaking=True, listening=True), 'agent'),
            (phone(listening=True, thinking=True), 'you'),
            (phone(thinking=True, muted=True), 'thinking'),
            (phone(muted=True), 'muted'),
            (phone(), 'idle'),
        ]
        for state, mode in cases:
            with self.subTest(mode=mode):
                self.assertEqual(self.run_js('mode', state or {}, 'c1'), mode)

    # covers: agent.phone-mode/E14
    def test_time_tasks_of_this_call_and_words(self):
        self.assertEqual(self.run_js('clock', 252), '04:12')
        self.assertEqual(self.run_js('clock', 3852), '1:04:12')
        started = 1_790_000_000.0
        now_ms = (started + 252) * 1000
        tasks = [{'taskId': 'old', 'conversation': 'c1', 'status': 'running', 'created': started - 60, 'text': 'before'},
                 {'taskId': 'a', 'conversation': 'c1', 'status': 'running', 'created': started + 5, 'text': 'pick screenshots'},
                 {'taskId': 'b', 'conversation': 'other', 'status': 'running', 'created': started + 5, 'text': 'elsewhere'}]
        state = phone(startedAt=started, speaking=True, tasks=tasks)
        self.assertEqual([t['taskId'] for t in self.run_js('tasks', state, 'c1')], ['a'], 'only what this call started here')
        label, detail = self.run_js('words', self.tr, state, 'c1', now_ms, '', '')
        self.assertEqual((label, detail), ('Answering', '04:12 · 1 task running'))
        label, detail = self.run_js('words', self.tr, phone(conversation='c2', startedAt=started), 'c1', now_ms, 'Tidy up', '')
        self.assertEqual((label, detail), ('Call in another conversation', 'Tidy up · 04:12'))
        label, detail = self.run_js('words', self.tr, phone(phase='connecting'), 'c1', now_ms, '', 'Pause, then say it again')
        self.assertEqual((label, detail), ('Connecting…', 'Pause, then say it again'))
        waiting = dict(tasks[1], status='waiting_input', question={'questions': [{'question': 'Where should they go?'}]})
        label, detail = self.run_js('words', self.tr, phone(startedAt=started, tasks=[waiting]), 'c1', now_ms, '', '')
        self.assertEqual((label, detail), ('Waiting for your answer', 'Where should they go? · 04:12'))
        rows = self.run_js('panelTasks', self.tr, phone(startedAt=started, tasks=[waiting, dict(tasks[1], taskId='c', status='completed')]), 'c1')
        self.assertEqual([(r['status'], r['statusText']) for r in rows], [('answer', 'Waiting for your answer'), ('done', 'Done')])

    # covers: agent.phone-mode/E13
    def test_why_a_call_cannot_start(self):
        self.assertEqual(self.run_js('blocked', self.tr, {}, 'c1', {'inCall': True}, False),
                         'The Agent is on a call for you: call it when that ends')
        self.assertEqual(self.run_js('blocked', self.tr, phone(conversation='c2'), 'c1', {}, False),
                         'A call is going on in another conversation')
        self.assertEqual(self.run_js('blocked', self.tr, {}, 'c1', {}, True), 'Let go of Hold to talk first')
        self.assertEqual(self.run_js('blocked', self.tr, {}, 'c1', {}, False), '')


class CallInTheApp(unittest.TestCase):
    """The conversation page with the phone session's events, as the user sees and taps it."""

    def setUp(self):
        self.stage = q.Stage()
        self.engine = self.stage.engine()
        self.root = self.stage.load(self.engine, 'Main')
        self.content = q.content(self.root)
        self.page = q.of_type(self.content, 'ChatPage')[0]
        q.spin(0.3)
        q.invoke(self.root, 'openConversation', 'c1')
        q.send(self.engine, 'conversationOpened', {'conversation': 'c1', 'title': 'Tidy up the Downloads folder', 'untitled': False, 'history': []})
        q.spin(0.3)
        q.clear(self.engine)

    def tearDown(self):
        self.root.close()
        self.engine.deleteLater()
        q.spin(0.05)
        self.stage.close()

    def state(self, **fields):
        e = phone(**fields)
        e.update(type='phone-state', time=time.time())
        q.send(self.engine, 'event', e)
        q.spin(0.3)

    def bar(self):
        return q.of_type(self.page, 'CallBar')[0]

    def button(self, name):
        found = [i for i in q.items(self.content) if i.property('text') == name and i.inherits('QQuickAbstractButton') and q.shown(i)]
        self.assertTrue(found, f'a button "{name}" on screen')
        return found[0]

    def requests(self, method):
        return [c[2] if len(c) > 2 else None for c in q.calls(self.engine) if c[0] == 'request' and c[1] == method]

    # covers: agent.phone-mode/E13
    def test_the_top_bar_calls_the_agent_in_this_conversation(self):
        self.assertNotIn('Phone mode', q.texts(self.content), 'the old row of phone mode buttons is gone')
        q.click(self.button('Call the Agent'))
        self.assertEqual(self.requests('StartPhoneMode'), [['c1']])

    # covers: agent.phone-mode/E13
    def test_a_call_that_cannot_start_says_why(self):
        q.send(self.engine, 'event', {'type': 'state', 'conversation': 'c1', 'phase': 'ready', 'call': True, 'callPhase': 'agent', 'time': time.time()})
        q.spin(0.1)
        q.click(self.button('Call the Agent'))
        self.assertEqual(self.requests('StartPhoneMode'), [])
        self.assertIn('The Agent is on a call for you: call it when that ends', q.texts(self.content))

    # covers: agent.phone-mode/E14
    def test_the_call_bar_follows_the_session(self):
        self.state(phase='connecting')
        self.assertTrue(q.shown(self.bar()))
        self.assertEqual(self.bar().property('visualState'), 'connecting')
        self.assertIn('Connecting…', q.texts(self.content))
        for fields, mode, label in [({'speaking': True}, 'agent', 'Answering'), ({'listening': True}, 'you', 'Listening'),
                                    ({'thinking': True}, 'thinking', 'Working'), ({'muted': True}, 'muted', 'Microphone off'),
                                    ({}, 'idle', 'On a call')]:
            with self.subTest(mode=mode):
                self.state(**fields)
                self.assertEqual(self.bar().property('visualState'), mode)
                self.assertEqual(self.bar().property('label'), label)
                self.assertRegex(self.bar().property('detail'), r'^04:1\d')
        self.assertNotIn('Call the Agent', [i.property('text') for i in q.items(self.content) if q.shown(i)],
                         'in a call here, the top bar has no call button')
        composer = q.of_type(self.page, 'Composer')[0]
        self.assertEqual(composer.property('phase'), 'call')
        self.assertIn('On a call · just talk', q.texts(self.content))

    # covers: agent.phone-mode/E14
    def test_mute_hang_up_and_the_panel(self):
        self.state(speaking=True)
        q.click(self.button('Mute'))
        self.assertEqual(self.requests('SetPhoneMuted'), [['voice_1', True]])
        q.js(self.engine, self.bar(), 'opened()')            # a tap on the bar's words
        q.spin(0.5)
        sheet = q.of_type(self.content, 'CallPanel')
        self.assertTrue(sheet and q.shown(sheet[0]), 'a tap on the bar opens the call panel')
        q.click(self.button('Interrupt'))
        self.assertEqual(self.requests('StopSpeaking'), [['voice_1']])
        q.click(self.button('Hang up'))
        self.assertIn(['voice_1'], self.requests('StopPhoneMode'))

    # covers: agent.phone-mode/E14
    def test_in_another_conversation_the_bar_leads_there(self):
        self.state(conversation='c2')
        self.assertEqual(self.bar().property('visualState'), 'elsewhere')
        self.assertEqual(q.of_type(self.page, 'Composer')[0].property('phase'), 'callElsewhere')
        q.click(self.button('Go there'))
        self.assertIn(['openConversation', 'c2'], q.calls(self.engine))

    # covers: agent.phone-mode/E15
    def test_the_call_leaves_its_summary(self):
        self.state()
        started = time.time() - 372
        q.send(self.engine, 'event', {'type': 'phone-state', 'sessionId': '', 'conversation': 'c1', 'phase': 'closed', 'tasks': [], 'time': time.time()})
        q.send(self.engine, 'event', {'type': 'phone-ended', 'conversation': 'c1', 'sessionId': 'voice_1', 'startedAt': started,
                                      'seconds': 372, 'tasks': [{'taskId': 'a', 'text': 'pick screenshots', 'status': 'completed'},
                                                                {'taskId': 'b', 'text': 'album', 'status': 'waiting_input'}],
                                      'time': time.time()})
        q.spin(0.3)
        texts = q.texts(self.content)
        self.assertIn('Call ended', texts)
        self.assertIn('6 min 12 s', texts)
        self.assertIn('2 tasks were started in this call, 1 done. One is waiting for your answer.', texts)
        self.assertFalse(q.shown(self.bar()), 'the bar goes with the call')
        q.click(self.button('Call again'))
        self.assertEqual(self.requests('StartPhoneMode'), [['c1']])

    # covers: agent.phone-mode/E13
    def test_the_conversations_panel_calls_in_a_new_conversation(self):
        from PySide6.QtCore import QObject
        drawer = [o for o in self.page.findChildren(QObject) if o.metaObject().className().startswith('ConversationDrawer')][0]
        from PySide6.QtCore import QMetaObject
        QMetaObject.invokeMethod(drawer, 'open')
        q.spin(0.4)
        q.click(self.button('Call the Agent'))
        q.spin(0.3)
        self.assertIn(['openConversation', ''], q.calls(self.engine), 'a new conversation is made for the call')
        q.send(self.engine, 'conversationOpened', {'conversation': 'c9', 'title': '', 'untitled': True, 'history': []})
        q.spin(0.2)
        self.assertEqual(self.requests('StartPhoneMode'), [['c9']])


if __name__ == '__main__':
    unittest.main()
