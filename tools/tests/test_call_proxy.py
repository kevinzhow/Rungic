#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The WeChat call proxy (docs/63) with the real CallProxy and the voice agent's own call methods; what
is outside Linux's logic is a stand-in: the Realtime model (the messages it is sent are recorded), the
audio router (rungic-audio-route's lines), the call window (the moving-timer check's answer) and the
clock. Nothing reaches OpenAI, PulseAudio, WeChat or the phone."""
import ast
import base64
from pathlib import Path
import sys
import threading
import time
import types
import unittest
import uuid
import json
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'agent/assistant'))
gi = types.ModuleType('gi')
gi.require_version = lambda *args: None
repository = types.ModuleType('gi.repository')
repository.GLib = types.SimpleNamespace(idle_add=lambda fn, *args: fn(*args))
repository.Gst = types.SimpleNamespace()
with patch.dict(sys.modules, {'gi': gi, 'gi.repository': repository, 'websocket': types.ModuleType('websocket')}):
    import call_proxy


class Clock:
    """time for the proxy: sleeping moves the clock on instead of waiting."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.01)

    def time(self):
        return time.time()


class SyncThread:
    """Threads run when started, in order: the call's steps become deterministic."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


class ProxyTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.events, self.sent = [], []
        self.owner_heard = []
        patches = [patch.object(call_proxy, 'time', self.clock),
                   patch.object(call_proxy, 'threading', types.SimpleNamespace(
                       Thread=SyncThread, Lock=threading.Lock, Event=threading.Event)),
                   patch.object(call_proxy, 'JEV_KEYS', ())]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.proxy = call_proxy.CallProxy(lambda event, keep=True: self.events.append(event), self.owner_heard.append,
                                          app='wechat', contact='周楷雯', goal='约周六晚饭的时间', owner='凯文',
                                          hang_up=Mock())
        self.proxy._send = self.sent.append
        self.proxy.player = Mock(busy=Mock(return_value=False))
        self.proxy.router = Mock()
        self.proxy.active, self.proxy.phase = True, 'agent'

    def said(self):
        """The [系统]/[主人…] messages the voice model was given."""
        return [m['item']['content'][0]['text'] for m in self.sent if m.get('type') == 'conversation.item.create']

    def kinds(self):
        return [e['type'] for e in self.events]

    def heard(self, text):
        self.proxy._on_message(None, json.dumps({'type': 'conversation.item.input_audio_transcription.completed',
                                                 'transcript': text}))

    def agent_said(self, text):
        self.proxy._on_message(None, json.dumps({'type': 'response.output_audio_transcript.done', 'transcript': text}))

    # ---- connecting: a moving call timer, never the ringback ----------------------------
    # covers: agent.call-proxy/E2
    def test_ringback_gets_no_answer(self):
        session = []
        self.proxy._send = session.append
        self.proxy._capture_remote = Mock()
        self.proxy._on_open(None)
        detection = session[0]['session']['audio']['input']['turn_detection']
        self.assertFalse(detection['create_response'], 'no automatic answers before the opening')
        self.proxy._send = self.sent.append
        self.proxy.confirm_connected = Mock(return_value=False)       # the call screen still says "calling"
        self.proxy.responding = True
        self.proxy._answered()
        self.proxy.player.hold.assert_called_once()
        self.proxy.player.drop.assert_called_once()                    # the opening generated meanwhile is never played
        self.proxy.player.release.assert_not_called()
        self.assertIn({'type': 'response.cancel'}, self.sent)
        self.assertFalse(self.proxy.opened)
        self.assertFalse(self.proxy.answered, 'a later trigger or the ringing check tries again')
        # Ringback music heard as speech before the opening does not cut the held opening.
        self.proxy._on_message(None, json.dumps({'type': 'input_audio_buffer.speech_started'}))
        self.proxy.player.flush.assert_not_called()

    # covers: agent.call-proxy/E2
    def test_opening_plays_once_the_timer_runs_and_is_not_cut_by_a_hum(self):
        self.proxy.confirm_connected = Mock(side_effect=[False, False, True])
        self.proxy._answered()
        self.proxy.player.release.assert_called_once()
        self.proxy.player.drop.assert_not_called()
        self.assertTrue(self.proxy.opened)
        self.assertEqual(sum('电话已经接通' in m for m in self.said()), 1, 'the opening is asked for once')
        updates = [m for m in self.sent if m.get('type') == 'session.update']
        self.assertTrue(updates[-1]['session']['audio']['input']['turn_detection']['create_response'],
                        'after the opening the session answers by itself')
        # The other side spoke while the opening played: a "嗯" gets no extra answer, a question does.
        for text, answered in (('嗯', False), ('请问你是哪位', True)):
            with self.subTest(text=text):
                self.sent.clear()
                self.proxy.opened = False
                self.proxy.answer_at = len(self.proxy.transcript)
                self.proxy.transcript.append({'who': 'other', 'text': text, 'during_opening': True})
                self.proxy._open(spoken=True)
                self.assertEqual(any('你说开场白时对方说了' in m for m in self.said()), answered)
        # Once the opening is out, the other side's speech interrupts the agent.
        self.proxy._on_message(None, json.dumps({'type': 'input_audio_buffer.speech_started'}))
        self.proxy.player.flush.assert_called_once()

    # ---- the owner decides commitments --------------------------------------------------
    # covers: agent.call-proxy/E3
    def test_commitments_go_to_the_owner_once_and_the_answer_is_relayed(self):
        self.proxy._decide = Mock()
        self.proxy._finish = Mock()
        self.heard('周六晚上七点可以吗？')
        self.proxy._act('ASK_OWNER', 0.9)
        self.proxy._act('ASK_OWNER', 0.9)                  # the same sentence: asked once
        self.assertEqual(self.kinds().count('call-ask'), 1)
        self.assertEqual(len(self.owner_heard), 1)
        self.assertIn('周六晚上七点可以吗', self.owner_heard[0])
        self.assertTrue(any(m.startswith('[系统] 已把对方的话转给凯文') for m in self.said()))
        # Not allowed to end while the owner's answer is pending or not yet told.
        self.proxy._act('END_CALL', 0.99)
        self.assertIsNone(self.proxy.ending)
        self.proxy.instruct('可以，七点见。')
        self.assertIn('[主人答复] 可以，七点见。', self.said())
        self.assertTrue(self.proxy.unrelayed)
        self.proxy._act('END_CALL', 0.99)
        self.assertIsNone(self.proxy.ending, 'the answer has not reached the other side')
        self.agent_said('凯文说可以，周六晚上七点见。')
        self.proxy._act('CONTINUE', 0.9)                    # JEV no longer asks to relay it: it was told
        self.assertFalse(self.proxy.unrelayed)
        self.proxy._act('END_CALL', 0.99)
        self.assertIsNone(self.proxy.ending, 'the other side has not spoken since the answer')
        self.heard('好的，那就这么定了，拜拜。')
        self.proxy._act('END_CALL', 0.99)
        self.assertEqual(self.proxy.ending, 'end')
        self.proxy._finish.assert_called_once()
        # A low-confidence ask is not an ask.
        self.heard('那你们几个人来？')
        self.proxy.ending = None
        self.proxy._act('ASK_OWNER', 0.4)
        self.assertEqual(self.kinds().count('call-ask'), 1)

    # ---- the owner takes over -----------------------------------------------------------
    # covers: agent.call-proxy/E4
    def test_take_over_moves_the_call_to_the_phone_and_keeps_it(self):
        streams = iter([True, True, False, False, False])
        self.proxy._app_streams = lambda: next(streams)
        self.proxy._loopbacks = Mock()
        router = self.proxy.router
        router.stdin.close.side_effect = lambda: self.assertEqual(self.proxy.phase, 'user', 'devices kept while the user talks')
        self.proxy.take_over()
        router.stdin.write.assert_called_once_with('phone\n')   # the app on the phone's own mic and speaker
        router.stdin.close.assert_called_once()                  # given back once the call is over
        self.proxy.player.close.assert_called_once()                       # the agent falls silent
        self.assertIn({'type': 'call-phase', 'phase': 'user', 'summary': ''}, self.events)
        # The call went on with the user until the app closed its call audio; then the devices go back.
        self.assertEqual(self.proxy.phase, 'ended')
        self.assertEqual(self.events[-1]['type'], 'call-ended')
        self.proxy.take_over()                                             # nothing more after the end
        self.assertEqual(self.kinds().count('call-phase'), 1)

    # ---- hanging up ----------------------------------------------------------------------
    # covers: agent.call-proxy/E5
    def test_hang_up_is_silent_at_once_and_confirmed_by_the_call_audio(self):
        streams = iter([True, True, False])
        self.proxy._app_streams = lambda: next(streams)
        self.proxy.responding = True
        self.proxy._teardown_agent = Mock()
        self.proxy.hang_up()
        self.proxy.player.flush.assert_called_once()
        self.assertIn({'type': 'response.cancel'}, self.sent)
        self.assertEqual(self.proxy.hang_up_ui.call_count, 1)
        self.assertEqual(self.events[-1], {'type': 'call-ended', 'reason': 'hung up', 'summary': ''})

    # covers: agent.call-proxy/E5
    def test_three_failed_taps_hand_the_call_to_the_phone(self):
        self.proxy._app_streams = lambda: True                 # the app's call audio never closes
        self.proxy._loopbacks = Mock()
        self.proxy._watch_user_call = Mock()
        self.proxy.hang_up()
        self.assertEqual(self.proxy.hang_up_ui.call_count, 3)
        states = [e['state'] for e in self.events if e['type'] == 'call-state']
        self.assertEqual(states, ['hanging-up', 'hangup-failed'])
        error = next(e for e in self.events if e['type'] == 'call-error')
        self.assertIn('hang up in WeChat', error['text'])
        self.assertEqual(self.proxy.phase, 'user', 'the call is on the phone now')
        self.proxy.router.stdin.write.assert_called_once_with('phone\n')
        self.assertNotIn('call-ended', self.kinds())


# The voice agent's own call methods, run as they are in rungic_voice_agent.py.
source = ROOT / 'agent/assistant/rungic_voice_agent.py'
tree = ast.parse(source.read_text())
agent_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'VoiceAgent')
agent_class.body = [n for n in agent_class.body if isinstance(n, ast.FunctionDef) and n.name in
                    {'dial', '_start_call', 'emit', 'play', 'progress_tick', 'call_in_progress', 'speak_call_result',
                     'tell_owner'}]


def agent_namespace(**extra):
    namespace = dict(json=json, time=time, uuid=uuid, base64=base64, _=lambda message: message, log=lambda *a: None,
                     GLib=types.SimpleNamespace(idle_add=lambda fn, *a: fn(*a), timeout_add=Mock()),
                     Gst=types.SimpleNamespace(Buffer=types.SimpleNamespace(new_wrapped=lambda data: data)),
                     threading=Mock(), RATE=24000, PROGRESS_AFTER_S=8, APOLOGY_AFTER_S=90,
                     screen_activity=lambda: {}, call_window=lambda app: 'window-1', call_snapshot=Mock())
    namespace.update(extra)
    exec(compile(ast.Module(body=[agent_class], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace


class DialTest(unittest.TestCase):
    def setUp(self):
        self.goals = []
        self.namespace = agent_namespace(luna_goal=self.luna)
        self.agent = self.namespace['VoiceAgent']()
        self.agent.desktop_goal = self.luna  # decision executor; dialing still checks the audio signal
        self.agent.call = Mock(streams_seen=0)
        self.opens_audio = False

    def luna(self, goal, timeout=120, stop_when=None, window=None, app=None):
        """The model on the chat screen; when the app opens its call audio, the router counts a stream."""
        self.goals.append(goal)
        if self.opens_audio:
            self.agent.call.streams_seen += 1
            self.assertTrue(stop_when(), 'the model is stopped the moment the call audio opens')
        return {'outcome': 'done', 'answer': 'calling', 'steps': []}

    def states(self):
        return [c.args[0]['state'] for c in self.agent.call.emit.call_args_list]

    # covers: agent.call-proxy/E1
    def test_the_chat_header_is_checked_and_only_call_audio_counts(self):
        clock = Clock()
        self.namespace['time'] = clock
        result = self.agent.dial('wechat', '周楷雯', 'Voice Call')
        goal = self.goals[0]
        self.assertIn('check the name in the chat header: it must be 周楷雯', goal)
        self.assertIn('If it is not, press nothing and reply FAILED', goal)
        # The model said it was done, but the app opened no call audio: not placed, and said so.
        self.assertFalse(result['dialed'])
        self.assertIsNone(result['confirmed_by'])
        self.assertEqual(self.states(), ['dialing', 'dial-failed'])
        self.assertGreaterEqual(clock.now - 1000, 8, 'it waited for the call audio before reporting')

    # covers: agent.call-proxy/E1
    def test_placed_when_the_app_opens_its_call_audio(self):
        self.opens_audio = True
        result = self.agent.dial('wechat', '周楷雯', 'Voice Call')
        self.assertTrue(result['dialed'])
        self.assertEqual(result['confirmed_by'], 'call audio opened')
        self.assertEqual(self.states(), ['dialing', 'ringing'])

    # covers: agent.call-proxy/E1
    def test_a_call_not_dialed_is_ended_not_left_waiting(self):
        call = Mock(phase='agent', active=True, streams_seen=0, ready=Mock(wait=Mock(return_value=True)))
        proxy_module = types.SimpleNamespace(CallProxy=Mock(return_value=call))
        self.agent.call = None
        self.agent.thread_id = 'origin'
        self.agent.set_state = Mock()
        self.agent.dial = Mock(return_value={'dialed': False})
        with patch.dict(sys.modules, {'call_proxy': proxy_module}):
            result = self.agent._start_call({'backend': 'app', 'app': 'wechat', 'contact': '周楷雯', 'dial': 'Voice Call'})
        self.assertFalse(result['dialed'])
        call.stop.assert_called_once_with('dial failed')

    # covers: agent.call-proxy/E1
    def test_the_router_counts_a_stream_when_the_app_opens_call_audio(self):
        proxy = call_proxy.CallProxy(Mock(), Mock())
        proxy.router = Mock(stdout=iter(['routed sink-input 12 (wechat)\n']))
        proxy._router_log()
        self.assertEqual(proxy.streams_seen, 1)
        self.assertFalse(proxy.answered, 'a ringing tone is not an answered call')


class QuietDuringCallTest(unittest.TestCase):
    def setUp(self):
        self.namespace = agent_namespace()
        self.agent = self.namespace['VoiceAgent']()
        a = self.agent
        a.talking = a.muted = False
        a.prefs = {'speak': True}
        a.aloud_pending, a.aloud_items = False, set()     # no reading asked for (朗读)
        a.playing_until = 0.0
        a.reply_audio_ms = 0
        a.player_src = Mock()
        a.ensure_player = Mock()
        a.set_state = Mock()
        a.speaking_check = Mock()
        a.call = Mock(phase='agent', active=True, private_voice_instructions=True)

    def audio(self, seconds=0.5):
        return {'data': base64.b64encode(bytes(int(24000 * 2 * seconds))).decode(), 'sampleRate': 24000}

    # covers: agent.call-proxy/E6
    def test_only_the_call_assistants_questions_are_heard(self):
        a = self.agent
        self.assertFalse(a.play(self.audio()))
        a.player_src.emit.assert_not_called()                   # the assistant's own reply: not over the call
        a.play(self.audio(), True)                              # the call assistant asks the user
        a.player_src.emit.assert_called_once()

    # covers: agent.call-proxy/E6
    def test_no_progress_reports_during_a_call(self):
        a = self.agent
        a.agent_busy = a.realtime = True
        a.screen_seen = 0
        a.turn_started = time.monotonic() - 600                 # a long task: it would report by now
        a.last_voice = 0
        a.speak_progress = Mock()
        self.assertTrue(a.progress_tick())
        self.namespace['threading'].Thread.assert_not_called()
        self.assertGreater(a.last_voice, 0, 'the call is what the user hears now')

    # covers: agent.call-proxy/E6
    def test_question_pauses_the_listen_in_and_the_result_is_short(self):
        a = self.agent
        with patch.dict(sys.modules, {'call_proxy': types.SimpleNamespace(
                synthesize=lambda text: bytes(24000 * 2), RATE=24000)}):
            a.tell_owner('对方问：周六晚上七点可以吗？')
        a.call.pause_monitor.assert_called_once()
        a.player_src.emit.assert_called_once()
        a.server = Mock()
        a.thread_id = 'origin'
        a.realtime_ready = Mock(wait=Mock(return_value=True))
        self.namespace['time'] = types.SimpleNamespace(sleep=lambda s: None, monotonic=time.monotonic, time=time.time)
        a.speak_call_result('hung up', '对方同意周六晚上七点。')
        method, params = a.server.call.call_args.args
        self.assertEqual(method, 'thread/realtime/appendSpeech')
        self.assertIn('one or two sentences', params['text'])
        self.assertIn('对方同意周六晚上七点', params['text'])

    # covers: agent.call-proxy/E4 agent.call-proxy/E6
    def test_assistant_pauses_while_the_user_talks_and_comes_back_after(self):
        a = self.agent
        a.call = None
        a.thread_id = 'origin'
        a.store = Mock(index={})
        a.emit_raw = Mock()
        for name in ('stop_realtime', 'start_realtime', 'watch_user_audio', 'speak_call_result'):
            setattr(a, name, Mock())
        started = []
        self.namespace['threading'] = types.SimpleNamespace(
            Thread=lambda target=None, args=(), daemon=None: types.SimpleNamespace(start=lambda: started.append(target)))

        class Call:
            def __init__(self, emit, tell_owner, **kwargs):
                self.emit, self.app, self.phase, self.active = emit, kwargs['app'], 'agent', True
                self.ready = Mock(wait=Mock(return_value=True))
                self.window_id = None

            def start(self):
                pass
        with patch.dict(sys.modules, {'call_proxy': types.SimpleNamespace(CallProxy=Call)}):
            a._start_call({'backend': 'app', 'app': 'wechat', 'contact': '周楷雯'})
        a.call.emit({'type': 'call-phase', 'phase': 'user'})
        self.assertIn(a.stop_realtime, started)                    # its session would hear the user's call
        a.call.emit({'type': 'call-ended', 'reason': 'ended', 'summary': ''})
        self.assertIn(a.start_realtime, started)
        self.assertIn(a.speak_call_result, started)


if __name__ == '__main__':
    unittest.main()
