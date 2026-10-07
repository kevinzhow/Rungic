# SPDX-License-Identifier: MIT
"""The voice service as the user meets it (agent/assistant/rungic_voice_agent.py, docs/59, 67, 87,
89, 99, 101): the real VoiceAgent and its D-Bus dispatch (Service.call), with stand-ins only for what
is outside it: `codex app-server` (an in-process JSON-RPC stand-in that records every call and
answers as Codex does), the GStreamer pipelines (no microphone, no sound device: the microphone's
samples are fed in, the reply's buffers are collected), the OpenAI endpoint and the platform bridge
(its contract's stand-in). Every file of the service is in a temporary home; nothing of this
machine's home, sound or session bus is touched. Clocks are simulated where the experience is
about time (the voice's progress cadence, waiting for an idle moment)."""
import array
import base64
import json
import os
import subprocess
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _path in (ROOT / 'agent/assistant', ROOT / 'agent/computer-use'):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from rungic_cua import activity, keys  # noqa: E402  (this tree's, before the service adds /usr/lib's)
import rungic_voice_agent as v  # noqa: E402
import task_state  # noqa: E402
import phone_session  # noqa: E402
import contracts  # noqa: E402
from gi.repository import GLib, Gst  # noqa: E402

RATE = v.RATE
CHUNK = RATE * 2 * v.CHUNK_MS // 1000
MODELS = [{'id': 'gpt-6-luna', 'displayName': 'GPT-6-Luna', 'isDefault': True, 'defaultReasoningEffort': 'low',
           'supportedReasoningEfforts': [{'reasoningEffort': 'low'}, {'reasoningEffort': 'high'}]},
          {'id': 'gpt-6-astra', 'displayName': 'GPT-6-Astra', 'defaultReasoningEffort': 'medium',
           'supportedReasoningEfforts': [{'reasoningEffort': 'medium'}]}]


# ---- audio -------------------------------------------------------------------------------------
def tone(ms, value=6000):
    """Speech as far as the service can tell: a 200 Hz square wave of ±value."""
    half = RATE // 400
    return array.array('h', (value if (i // half) % 2 == 0 else -value for i in range(RATE * ms // 1000))).tobytes()


def silence(ms):
    return bytes(RATE * 2 * ms // 1000)


def samples(data):
    return array.array('h', data)


def longest_silence_ms(data):
    longest = run = 0
    for s in samples(data):
        run = run + 1 if s == 0 else 0
        longest = max(longest, run)
    return longest * 1000 // RATE


class MicSink:
    """The appsink's new-sample, as GStreamer hands it to on_microphone."""

    def __init__(self, data):
        self.sample = Gst.Sample.new(Gst.Buffer.new_wrapped(data), None, None, None)

    def emit(self, signal):
        assert signal == 'pull-sample'
        return self.sample


def feed(agent, audio):
    for i in range(0, len(audio), CHUNK):
        agent.on_microphone(MicSink(audio[i:i + CHUNK]))


# ---- stand-ins -----------------------------------------------------------------------------------
class Proxy:
    """A module with some of its names replaced (the rest are the real module's)."""

    def __init__(self, real, **replaced):
        self._real = real
        self.__dict__.update(replaced)

    def __getattr__(self, name):
        return getattr(self._real, name)


class Element:
    def __init__(self):
        self.props, self.pushed = {}, []

    def set_property(self, name, value):
        self.props[name] = value

    def connect(self, *args):
        pass

    def emit(self, signal, buffer):
        assert signal == 'push-buffer'
        ok, info = buffer.map(Gst.MapFlags.READ)
        self.pushed.append(bytes(info.data))
        buffer.unmap(info)
        return Gst.FlowReturn.OK


class Pipeline:
    """Gst.parse_launch's pipeline: the microphone (pulsesrc) or the reply's player (pulsesink)."""

    def __init__(self, description):
        self.description, self.states, self.elements = description, [], {}

    def get_by_name(self, name):
        return self.elements.setdefault(name, Element())

    def set_state(self, state):
        self.states.append(state)

    def get_bus(self):
        return types.SimpleNamespace(add_signal_watch=lambda: None, connect=lambda *a: None)


class Codex:
    """`codex app-server` as the service drives it: replies as Codex's, notifications delivered from
    another thread as its reader thread does. Records every call."""
    started = []

    def __init__(self, on_notification, on_request):
        self.on_notification, self.on_request = on_notification, on_request
        self.calls, self.threads = [], 0
        self.account = {'type': 'chatgpt', 'planType': 'team'}
        self.proc = types.SimpleNamespace(terminate=lambda: setattr(self, 'terminated', True))
        self.terminated = self.retired = False
        Codex.started.append(self)

    def call(self, method, params, timeout=60):
        self.calls.append((method, params))
        if method == 'thread/start':
            self.threads += 1
            return {'thread': {'id': f'new-{len(Codex.started)}-{self.threads}'}}
        if method == 'thread/resume':
            return {'model': params.get('model'), 'reasoningEffort': params['config'].get('model_reasoning_effort')}
        if method == 'model/list':
            return {'data': MODELS}
        if method == 'account/read':
            return {'account': self.account}
        return {}

    def notify(self, method, params=None):
        self.calls.append((method, params))

    def respond(self, request_id, result):
        self.calls.append(('respond', {'id': request_id, **result}))

    def of(self, method):
        return [p for m, p in list(self.calls) if m == method]



class Coordinator:
    """The voice's coordinator (agent/assistant/session) as VoiceAgent drives it (docs/115): push-to-talk
    in press mode. Records commands; its state, replies and transcripts come back as its events
    (Service.voice)."""
    made = []

    def __init__(self, server, settings, emit, foreground, prompt, language, history=None, executor=None):
        self.emit, self.calls, self.threads = emit, [], {}
        self.snapshot = {'sessionId': '', 'phase': 'closed', 'tasks': [], 'conversation': ''}
        self.voice = {'sessionId': '', 'phase': 'closed', 'conversation': ''}
        Coordinator.made.append(self)

    def alive(self):
        return True

    def start(self, conversation, mode='call', instructions=None):
        self.calls.append(('start', {'conversation': conversation, 'mode': mode, 'instructions': instructions}))
        self.voice.update(sessionId=f'voice-{len(self.calls)}', phase='connected', conversation=conversation)
        threading.Timer(0.01, self.emit, args=({'type': 'voice-state', **self.voice, 'mode': mode}, False)).start()
        return {'sessionId': self.voice['sessionId']}

    def command(self, method, args=None, timeout=30):
        self.calls.append((method, dict(args or {})))
        if method == 'StopPhoneMode' and (args or {}).get('sessionId') == self.voice['sessionId']:
            self.voice.update(sessionId='', phase='closed')
            self.emit({'type': 'voice-state', **self.voice, 'mode': 'press'}, False)
        return {}

    def post(self, method, args=None):
        self.calls.append((method, dict(args or {}, conversation=self.voice['conversation'])))

    def notification(self, method, params):
        return False

    def request(self, rid, method, params):
        return False

    def work_of(self, thread):
        return None

    def of(self, method):
        return [a for m, a in list(self.calls) if m == method]

    def audio(self, conversation):
        """All push-to-talk audio sent in `conversation`."""
        return b''.join(base64.b64decode(a['pcm']) for a in self.of('PressAudio') if a['conversation'] == conversation)

    def press(self):
        """The last press: [its audio], committed or cancelled or None."""
        calls = list(self.calls)
        starts = [i for i, (m, _) in enumerate(calls) if m == 'PressStart']
        if not starts:
            return b'', None
        start = starts[-1]
        audio = b''.join(base64.b64decode(a['pcm']) for m, a in calls[start:] if m == 'PressAudio')
        end = next((m for m, _ in calls[start:] if m in ('PressCommit', 'PressCancel')), None)
        return audio, end


class Invocation:
    def __init__(self):
        self.done, self.result, self.error = threading.Event(), None, None

    def return_value(self, value):
        self.result = json.loads(value.unpack()[0]) if value is not None else None
        self.done.set()

    def return_dbus_error(self, name, message):
        self.error = message
        self.done.set()


def pump(seconds=0.0):
    """Run what the service queued on its main loop (GLib.idle_add, timeouts that are due)."""
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while True:
        while context.pending():
            context.iteration(False)
        if time.monotonic() >= end:
            return
        time.sleep(0.01)


def wait_until(condition, timeout=5.0, what='condition'):
    end = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > end:
            raise AssertionError(f'timed out waiting for {what}')
        pump(0.02)
    pump()


class Service:
    """The service in a temporary home: make() starts a VoiceAgent, dbus() calls it as the app does."""

    def __init__(self, tmp, monkeypatch):
        self.tmp, self.mp = tmp, monkeypatch
        home = tmp / 'home'
        runtime = tmp / 'run'
        runtime.mkdir(mode=0o700, parents=True)
        for name, value in {'HOME': home, 'XDG_RUNTIME_DIR': runtime, 'XDG_STATE_HOME': home / '.local/state',
                            'CODEX_HOME': home / '.codex', 'RUNGIC_PLATFORM_SOCKET': tmp / 'no-bridge.sock'}.items():
            monkeypatch.setenv(name, str(value))
        for name in ('LANGUAGE', 'LC_ALL', 'LC_MESSAGES'):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv('LANG', 'en_US.UTF-8')
        data, config = home / '.local/share/rungic-voice-agent', home / '.config/rungic-voice-agent'
        for name, value in {'DATA': data, 'CONFIG': config, 'USER_PROMPTS': config / 'prompts',
                            'USER_SKILLS': home / '.codex/skills', 'USER_SKILL': home / '.codex/skills/rungic-phone-desktop',
                            'SEEDED': data / 'instructions-seeded.json', 'PROMPTS': ROOT / 'agent/assistant/prompts',
                            'SKILLS': ROOT / 'agent/assistant/skills',
                            'SWITCHED_APPS': runtime / 'rungic-workspace-switched.json'}.items():
            monkeypatch.setattr(v, name, value)
        monkeypatch.setattr(keys, 'FILES', config)
        monkeypatch.setattr(activity, 'DIR', runtime / 'rungic-agent-screen')
        self.pipelines = []

        def launch(description):
            self.pipelines.append(Pipeline(description))
            return self.pipelines[-1]
        self.timers = []
        monkeypatch.setattr(v, 'Gst', Proxy(Gst, parse_launch=launch))
        # Periodic timers (every 30 s, 2 s, 60 s, hours) are noted, not run: a test calls what they call.
        monkeypatch.setattr(v, 'GLib', Proxy(GLib, timeout_add_seconds=lambda s, f, *a: self.timers.append((s, f)) or 0))
        self.sinks = ''
        monkeypatch.setattr(v, 'subprocess', Proxy(subprocess, check_output=lambda *a, **k: self.sinks))
        monkeypatch.setattr(v.VoiceAgent, 'usage_push', lambda self, *a: None)    # the desktop's usage service
        monkeypatch.setattr(phone_session, 'PhoneSession', Coordinator)
        Codex.started = []
        Coordinator.made = []
        self.agent = None

    def make(self, key=True, codex=True):
        self.mp.setattr(v.codex_install, 'installed', lambda: codex)
        if key:
            keys.store('openai-api-key', 'sk-test-key-0123456789')
        if codex:
            self.mp.setattr(v, 'AppServer', Codex)
        else:
            self.mp.setattr(v.codex_install, 'command', lambda: None)     # not installed
        self.events = []
        self.agent = v.VoiceAgent(self.events.append)
        self.agent.codex_update = types.SimpleNamespace(check=lambda force=False: {'available': False},
                                                        result={'available': False})
        pump()
        return self.agent

    @property
    def codex(self):
        return self.agent.server

    @property
    def voice(self):
        """The coordinator push-to-talk's voice runs on (one with nothing done before it is made)."""
        return self.agent.phone or Coordinator(None, None, lambda *a: None, None, None, None)

    def recorder(self):
        return next(p for p in self.pipelines if 'pulsesrc' in p.description)

    def players(self):
        return [p for p in self.pipelines if 'pulsesink' in p.description]

    def kinds(self, kind=None):
        return [e for e in self.events if kind is None or e.get('type') == kind]

    def ready(self, thread='A'):
        """Conversation `thread` open, as the app opens one, with its voice connected."""
        self.agent.store.touch(thread, f'conversation {thread}')
        opened = self.agent.open_conversation(thread)
        wait_until(lambda: self.agent.realtime, what='the realtime session')
        return opened

    def dbus(self, method, *args):
        service = v.Service.__new__(v.Service)
        service.agent, service.watched, service.connection = self.agent, {}, None
        signature = '(' + ''.join('b' if isinstance(a, bool) else 's' for a in args) + ')'
        invocation = Invocation()
        service.call(None, ':1.42', v.OBJECT_PATH, v.BUS_NAME, method, GLib.Variant(signature, args), invocation)
        assert invocation.done.wait(10), method
        pump()
        assert invocation.error is None, invocation.error
        return invocation.result

    def reply_audio(self, ms, thread='A', aloud=False):
        """The voice's reply, as the coordinator sends it (voice-audio)."""
        self.agent.phone_emit({'type': 'voice-audio', 'data': base64.b64encode(tone(ms, 3000)).decode(),
                               'aloud': aloud}, False)
        pump()

    def pushed(self):
        return b''.join(b for p in self.players() for b in p.get_by_name('src').pushed)


@pytest.fixture
def service(tmp_path, monkeypatch):
    s = Service(tmp_path, monkeypatch)
    yield s
    if s.agent:
        s.agent.thread_id = None       # its background threads find nothing open any more
    pump()


def uploaded_after_release(s, thread=None):
    """The last press's audio as the voice got it, once it is committed (docs/115: sent while held)."""
    wait_until(lambda: s.voice.of('PressStart') and s.voice.press()[1] == 'PressCommit', what='the press committed')
    audio, _end = s.voice.press()
    if thread is not None:
        assert s.voice.of('PressCommit')[-1]['conversation'] == thread
    return audio


# ---- one press, one message (agent.voice/E1, E2) -------------------------------------------------
# covers: agent.voice/E1
def test_one_hold_is_one_message_and_stops_the_answer(service):
    s = service
    agent = s.make()
    s.ready('A')
    s.reply_audio(2000)                         # an answer is playing
    player = s.players()[-1]
    assert agent.state()['phase'] == 'speaking'
    agent.start_talking(None)
    pump()
    assert player.states[-1] == Gst.State.NULL, 'pressing stops the answer at once'
    assert agent.state()['phase'] == 'listening'
    played = len(s.pushed())
    s.reply_audio(500)
    assert len(s.pushed()) == played, 'reply audio arriving while held is not played'
    said = silence(300) + tone(1000) + silence(1600) + tone(1000)
    feed(agent, said)
    # docs/115: the press goes up while held (the release commits it; a cancel clears it).
    wait_until(lambda: len(s.voice.press()[0]) >= len(said) - CHUNK, what='the press sent while held')
    assert s.voice.press()[1] is None, 'not committed while held'
    assert s.voice.of('PressStart')[-1]['press'] == agent.press
    agent.release_talking()
    pump()
    sent = uploaded_after_release(s, 'A')
    # The press says where the sentence ends: its pauses stay as they were.
    assert longest_silence_ms(sent) >= 1500
    assert sum(1 for x in samples(sent) if abs(x) == 6000) >= 1.9 * RATE, 'both parts of the sentence were sent'
    started, sent_events = s.kinds('talk-started'), s.kinds('talk-sent')
    assert len(started) == len(sent_events) == 1 and started[0]['press'] == sent_events[0]['press']
    assert not s.kinds('listen-cancelled')
    assert s.recorder().states[-1] == Gst.State.READY, 'the microphone closes on release'


# covers: agent.voice/E2
def test_cancelled_press_sends_nothing_and_does_not_join_the_next(service):
    s = service
    agent = s.make()
    s.ready('A')
    agent.start_talking(None)
    feed(agent, silence(300) + tone(1200, 7777))
    s.dbus('CancelTalking')
    wait_until(lambda: s.voice.press()[1] == 'PressCancel', what='the press cleared')
    assert not s.voice.of('PressCommit'), 'a cancelled press is never committed: the voice acts on nothing'
    assert len(s.kinds('listen-cancelled')) == 1 and not s.kinds('talk-sent')
    assert s.recorder().states[-1] == Gst.State.READY
    agent.start_talking(None)
    feed(agent, silence(300) + tone(800, 3333))
    agent.release_talking()
    sent = samples(uploaded_after_release(s, 'A'))
    assert 3333 in sent and 7777 not in sent, 'the next press carries only its own words'


# ---- where the answer plays (agent.voice/E3) -----------------------------------------------------
# covers: agent.voice/E3
def test_the_answer_plays_where_the_press_was(service):
    s = service
    agent = s.make()
    s.ready('A')
    s.sinks = '0\tandroid_phone\tmodule-x\ts16le 1ch 48000Hz\tIDLE\n1\tandroid_cast\tmodule-y\ts16le 2ch 48000Hz\tIDLE\n'
    assert v.reply_sink('WL-0') == 'android_phone', 'a press on the phone answers on the phone, even while casting'
    assert v.reply_sink('CAST-1') is None, "a press on the TV follows Android's routing to the TV"
    s.sinks = '1\tandroid_cast\tmodule-y\ts16le 2ch 48000Hz\tIDLE\n'
    assert v.reply_sink('WL-0') is None, 'without the phone sink: the default one'
    s.sinks = '0\tandroid_phone\tm\ts16le 1ch 48000Hz\tIDLE\n'
    for screen, device in (('WL-0', 'android_phone'), ('CAST-1', None)):
        s.dbus('StartTalking', screen)
        feed(agent, silence(300) + tone(600))
        s.dbus('StopTalking')
        agent.release_player()
        agent.player = None
        s.reply_audio(300)
        out = s.players()[-1].get_by_name('out').props
        assert out.get('device') == device, (screen, out)


# ---- the answer plays in full (agent.voice/E4: the Linux side) ------------------------------------
# covers: agent.voice/E4
def test_reply_audio_plays_in_order_without_loss(service):
    s = service
    s.make()
    s.ready('A')
    parts = [tone(120 + 40 * i, 1000 + i) for i in range(12)]          # bursts, as the realtime API sends
    for part in parts:
        s.agent.phone_emit({'type': 'voice-audio', 'data': base64.b64encode(part).decode()}, False)
    pump()
    assert len(s.players()) == 1, 'one stream for the whole answer'
    player = s.players()[0]
    assert 'sync=false' in player.description and 'do-timestamp=false' in player.description, \
        'samples are played strictly in order, paced by the sound server (no resync drops)'
    assert s.pushed() == b''.join(parts), 'every piece, in order'
    s.agent.release_player()
    assert player.states[-1] != Gst.State.NULL, 'the stream stays open while its tail plays'


# ---- stop (agent.voice/E5) ------------------------------------------------------------------------
# covers: agent.voice/E5
def test_stop_ends_the_task_and_the_answer_and_nothing_trails(service):
    s = service
    agent = s.make()
    s.ready('A')
    agent.on_notification('turn/started', {'threadId': 'A', 'turn': {'id': 'T1'}})
    s.reply_audio(3000)
    player = s.players()[-1]
    s.dbus('StopTask')
    for _ in range(100):
        if s.codex.of('turn/interrupt'):
            break
        time.sleep(0.02)
    assert s.codex.of('turn/interrupt') == [{'threadId': 'A', 'turnId': 'T1'}]
    pump()
    assert player.states[-1] == Gst.State.NULL, 'the answer stops at once'
    # docs/114: stopped is said when Codex says the turn ended, not when it was asked.
    assert not s.kinds('task-stopped'), 'not "stopped" before it has'
    before = len(s.pushed())
    s.reply_audio(800)                           # the rest of the cut reply still arrives
    assert len(s.pushed()) == before, 'no trailing half sentence'
    agent.phone_emit({'type': 'message', 'role': 'assistant', 'id': 'S1', 'text': 'cut'})   # the cut reply's end
    agent.on_notification('turn/completed', {'threadId': 'A', 'turn': {'id': 'T1', 'status': 'interrupted'}})
    for _ in range(100):
        pump()
        if s.kinds('task-stopped'):
            break
        time.sleep(0.02)
    assert s.kinds('task-stopped'), 'the task shows as stopped once it has'
    assert not agent.agent_busy and s.kinds('agent-finished')
    s.reply_audio(300)
    assert len(s.pushed()) > before, 'the next answer plays again'


# covers: agent.voice/E10
def test_the_voice_s_own_words_start_no_task(service):
    # 2026-10-06: the agent ended with "要再画一颗月亮来配它吗？"; push-to-talk's voice took it for a
    # request and started a turn to draw a moon, the user having said nothing. Such a turn is stopped
    # and never shown; one after the user spoke goes on.
    s = service
    agent = s.make()
    s.ready('A')
    s.dbus('SendText', '画一颗星星', '[]')

    def turn(turn_id, text):
        agent.on_notification('turn/started', {'threadId': 'A', 'turn': {'id': turn_id}})
        agent.on_notification('item/started', {'threadId': 'A', 'turnId': turn_id, 'item': {
            'type': 'userMessage', 'id': f'u-{turn_id}', 'content': [{'type': 'text', 'text': text}]}})
        pump()
    turn('T1', '画一颗星星')
    agent.on_notification('turn/completed', {'threadId': 'A', 'turn': {'id': 'T1', 'status': 'completed'}})
    pump()
    assert len(s.kinds('agent-started')) == 1 and len(s.kinds('agent-finished')) == 1
    turn('T2', '<realtime_delegation>\n  <input>用户说：画好了！要再画一颗月亮来配它吗？请自动画一颗月亮</input>')
    wait_until(lambda: s.codex.of('turn/interrupt'), what='the self-started turn stopped')
    assert s.codex.of('turn/interrupt') == [{'threadId': 'A', 'turnId': 'T2'}]
    agent.on_notification('turn/completed', {'threadId': 'A', 'turn': {'id': 'T2', 'status': 'interrupted'}})
    pump()
    assert len(s.kinds('agent-started')) == 1, 'never shown as a task'
    assert len(s.kinds('agent-finished')) == 1 and not s.kinds('error')
    # The user presses and says "好，画吧": the voice's turn for it goes on.
    s.dbus('StartTalking', '')
    s.dbus('CancelTalking')
    turn('T3', '<realtime_delegation>\n  <input>画一颗月亮</input>')
    assert len(s.codex.of('turn/interrupt')) == 1, 'a request of the user goes on'
    assert len(s.kinds('agent-started')) == 2


# covers: agent.voice/E10
def test_the_result_is_said_outside_the_conversation_and_a_question_is_asked(service):
    # docs/115: push-to-talk's voice is the coordinator's press mode. A result is said with no tools,
    # out of the conversation; the user's words come back as the press's message.
    s = service
    agent = s.make()
    s.ready('A')
    started = s.voice.of('start')[-1]
    assert started['mode'] == 'press' and 'push-to-talk' in started['instructions']
    agent.on_notification('turn/started', {'threadId': 'A', 'turn': {'id': 'T1'}})
    agent.on_notification('item/completed', {'threadId': 'A', 'turnId': 'T1', 'item': {
        'type': 'agentMessage', 'id': 'm1', 'phase': 'final_answer', 'text': '画好了。要再画一颗月亮来配它吗？'}})
    pump()
    assert not s.voice.of('Narrate'), 'said when the turn ends: the coordinator knows it ended then'
    agent.on_notification('turn/completed', {'threadId': 'A', 'turn': {'id': 'T1', 'status': 'completed'}})
    pump()
    said = s.voice.of('Narrate')[-1]
    assert '要再画一颗月亮来配它吗？' in said['text'] and 'ask the user that question' in said['text']
    assert said['quiet'] == 0 and not said.get('exact')
    # The user's press, transcribed: one bubble with the press's id, in the record.
    agent.phone_emit({'type': 'voice-delta', 'role': 'user', 'id': 'press-77', 'text': '好'}, False)
    agent.phone_emit({'type': 'message', 'role': 'user', 'id': 'press-77', 'text': '好，画吧'})
    pump()
    delta = [e for e in s.kinds('delta') if e['id'] == 'press-77']
    message = [e for e in s.kinds('message') if e['id'] == 'press-77']
    assert delta and message and message[-1]['press'] == 77 and message[-1]['text'] == '好，画吧'
    assert any(e.get('id') == 'press-77' for e in agent.store.history('A'))


# ---- reading aloud and the speak switch (agent.voice/E6) -----------------------------------------
# covers: agent.voice/E6
def test_read_aloud_only_sounds_and_speak_off_is_silent(service):
    s = service
    agent = s.make()
    s.ready('A')
    s.dbus('ReadAloud', 'The answer, read again.')
    reading = s.voice.of('Narrate')
    assert reading and reading[-1]['exact'] is True and reading[-1]['text'] == 'The answer, read again.'
    assert reading[-1]['conversation'] == 'A'
    history = len(agent.store.history('A'))
    agent.phone_emit({'type': 'voice-delta', 'role': 'assistant', 'id': 'R1', 'text': 'The answer', 'aloud': True}, False)
    s.reply_audio(600, aloud=True)
    agent.phone_emit({'type': 'aloud', 'id': 'R1'}, False)
    pump()
    assert s.pushed(), 'the reading is heard'
    assert not [e for e in s.events if e.get('id') == 'R1'], 'no new message, no transcript on screen'
    assert len(agent.store.history('A')) == history, 'nothing written to the record'
    # Speak off ("朗读回答"): replies are not played.
    s.dbus('SetPreferences', json.dumps({'speak': False}))
    agent.release_player()
    heard = len(s.pushed())
    s.reply_audio(600)
    assert len(s.pushed()) == heard
    assert json.loads((v.CONFIG / 'preferences.json').read_text())['speak'] is False, 'kept across restarts'
    # "朗读" on one answer still sounds with speak off: the user asked for that one.
    s.dbus('ReadAloud', 'Another answer.')
    s.reply_audio(600, aloud=True)
    pump()
    assert len(s.pushed()) > heard, 'a reading asked for is heard with speak off'
    agent.phone_emit({'type': 'aloud', 'id': 'R2'}, False)
    heard = len(s.pushed())
    s.reply_audio(600)
    assert len(s.pushed()) == heard, 'after the reading, replies are silent again'


# ---- the conversation the user looks at (agent.voice/E7) -----------------------------------------
# covers: agent.voice/E7
def test_talk_text_and_reading_go_to_the_conversation_on_screen(service):
    s = service
    agent = s.make()
    s.dbus('OpenConversation', 'A')                       # the app shows A
    agent.store.touch('A', 'conversation A')
    wait_until(lambda: agent.realtime, what='A connected')
    s.dbus('AssistantTalk', 'WL-0')                       # Home held: the overlay talks in its own
    assistant = agent.assistant_id()
    assert assistant and assistant != 'A' and agent.thread_id == assistant
    wait_until(lambda: agent.talking, what='the overlay press')
    wait_until(lambda: agent.realtime, what='the assistant connected')
    feed(agent, silence(300) + tone(600, 1111))
    s.dbus('ReleaseTalking')
    uploaded_after_release(s, assistant)
    # Back in the app: its next press, message and reading act on A, the one it shows.
    s.dbus('Use', 'A')
    assert agent.thread_id == 'A'
    s.dbus('SendText', 'typed in A', '[]')
    assert s.codex.of('turn/start')[-1]['threadId'] == 'A'
    wait_until(lambda: agent.realtime, what='A connected again')
    s.dbus('StartTalking', 'WL-0')
    feed(agent, silence(300) + tone(600, 2222))
    s.dbus('StopTalking')
    a_audio = samples(uploaded_after_release(s, 'A'))
    assert 2222 in a_audio and 1111 not in a_audio
    assert 2222 not in samples(s.voice.audio(assistant))
    s.dbus('ReadAloud', 'an answer of A')
    assert s.voice.of('Narrate')[-1]['conversation'] == 'A'


# ---- what is missing is said, the rest works (agent.voice/E8) ------------------------------------
# covers: agent.voice/E8
def test_without_an_api_key_only_voice_is_off(service):
    s = service
    agent = s.make(key=False)
    agent.store.touch('A', 'conversation A')
    agent.open_conversation('A')
    s.dbus('StartTalking', 'WL-0')
    assert not agent.talking, 'no microphone without a key'
    setup = s.kinds('setup')
    assert setup and setup[-1]['page'] == 'key', 'the conversation says to set up the key'
    assert not any(p.states and p.states[-1] == Gst.State.PLAYING for p in s.pipelines if 'pulsesrc' in p.description)
    s.dbus('SendText', 'typing still works', '[]')
    assert s.codex.of('turn/start')[-1]['input'][0]['text'] == 'typing still works'


# covers: agent.voice/E8
def test_without_codex_the_service_stays_and_says_install(service):
    s = service
    agent = s.make(codex=False)
    assert agent.server is None, 'the service runs without Codex'
    s.dbus('ListConversations')
    agent.store.touch('A', 'conversation A')
    agent.open_conversation('A', connect=False)
    s.dbus('SendText', 'hello', '[]')
    setup = s.kinds('setup')
    assert setup and setup[-1]['page'] == 'codex', 'the conversation says to install Codex first'
    with pytest.raises(RuntimeError):
        agent.open_conversation('')                 # a new one needs Codex: it says so, the service goes on
    assert agent.setup()['codex']['installed'] is False
    assert s.dbus('Setup')['accountStatus'] == 'not-installed'
    usage = s.dbus('Usage')
    assert usage['installed'] is False and usage['status'] == 'not-installed'


# ---- hands-free after a quick press (agent.home-hold/E2) -----------------------------------------
# covers: agent.home-hold/E2
def test_released_before_speaking_listens_on_and_sends_or_gives_up(service):
    s = service
    agent = s.make()
    s.dbus('AssistantTalk', 'WL-0')
    assistant = agent.assistant_id()
    wait_until(lambda: agent.talking and agent.realtime, what='the overlay press')
    feed(agent, silence(300))
    s.dbus('ReleaseTalking')
    assert agent.talking and agent.state()['handsFree'], 'released before speech: still listening'
    assert s.recorder().states[-1] == Gst.State.PLAYING, 'the microphone stays open'
    feed(agent, tone(800) + silence(1100))
    pump()
    assert not agent.talking, 'the end of speech sends by itself'
    sent = uploaded_after_release(s, assistant)
    assert len(s.kinds('talk-sent')) == 1 and len(sent) > RATE * 2
    # Nothing said: 8 seconds on, it gives up and sends nothing.
    commits = len(s.voice.of('PressCommit'))
    s.dbus('AssistantTalk', 'WL-0')
    wait_until(lambda: agent.talking, what='the second press')
    feed(agent, silence(300))
    s.dbus('ReleaseTalking')
    agent.talk_started -= v.HANDS_FREE_NO_SPEECH_S + 0.5
    feed(agent, silence(200))
    pump()
    assert not agent.talking and len(s.kinds('listen-cancelled')) == 1
    assert s.recorder().states[-1] == Gst.State.READY
    wait_until(lambda: s.voice.press()[1] == 'PressCancel', what='the press cleared')
    assert len(s.voice.of('PressCommit')) == commits, 'nothing sent'


# ---- dismissing the overlay: the service's side (agent.home-hold/E3) ------------------------------
# covers: agent.home-hold/E3
def test_dismissed_overlay_drops_the_sentence_and_the_sound_but_not_the_work(service):
    s = service
    agent = s.make()
    s.dbus('OpenAssistant')
    assistant = agent.assistant_id()
    agent.on_notification('turn/started', {'threadId': assistant, 'turn': {'id': 'T9'}})
    s.reply_audio(3000, assistant)
    player = s.players()[-1]
    s.dbus('Interrupt')                          # the overlay dismissed while it speaks
    assert player.states[-1] == Gst.State.NULL, 'what was playing stops'
    s.dbus('AssistantTalk', 'WL-0')
    wait_until(lambda: agent.talking)
    feed(agent, silence(300) + tone(700))
    s.dbus('CancelTalking')                      # dismissed while listening
    wait_until(lambda: s.voice.press()[1] == 'PressCancel', what='the press cleared')
    assert not s.voice.of('PressCommit'), 'the sentence being heard is dropped'
    assert not s.codex.of('turn/interrupt') and agent.agent_busy, 'the agent keeps working'
    opened = len([st for st in s.recorder().states if st == Gst.State.PLAYING])
    agent.on_notification('turn/completed', {'threadId': assistant, 'turn': {'id': 'T9', 'status': 'completed'}})
    pump()
    finished = s.kinds('agent-finished')
    assert finished and finished[-1]['conversation'] == assistant, 'its result comes for the overlay to show'
    assert len([st for st in s.recorder().states if st == Gst.State.PLAYING]) == opened, 'no microphone for it'


# ---- the voice's progress reports (agent.progress/E3) ----------------------------------------------
class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def time(self):
        return self.now


def run_turn(s, monkeypatch, watched, seconds=240):
    """A long turn (3-step plan, a 150 s step) ticked second by second on a simulated clock;
    returns (time, instruction) of everything handed to the voice."""
    agent = s.make()
    agent.store.touch('A')
    agent.thread_id, agent.realtime = 'A', True
    clock = Clock()
    monkeypatch.setattr(v, 'time', Proxy(time, monotonic=clock.monotonic, time=clock.time))
    monkeypatch.setattr(task_state, 'time', Proxy(time, time=clock.time))
    spoken = []
    monkeypatch.setattr(agent, 'speak_progress', lambda text: spoken.append((clock.now - 1000, text)))
    monkeypatch.setattr(v, 'threading', Proxy(threading, Thread=lambda target, args=(), daemon=None, **k:
                                              types.SimpleNamespace(start=lambda: target(*args))))
    if watched:
        agent.set_watching(':1.7', True)
    agent.on_notification('turn/started', {'threadId': 'A', 'turn': {'id': 'T'}})
    plan = lambda now: [{'step': f'step {i}', 'status': 'completed' if i < now else 'inProgress' if i == now else 'pending'}
                        for i in range(1, 4)]
    for second in range(seconds):
        clock.now = 1000 + second
        if second == 3:
            agent.on_notification('turn/plan/updated', {'threadId': 'A', 'plan': plan(1)})
            agent.on_notification('item/started', {'threadId': 'A', 'item': {'type': 'commandExecution', 'id': 'c1',
                                                                            'command': 'python3 prepare.py'}})
        if second == 30:
            agent.on_notification('item/completed', {'threadId': 'A', 'item': {'type': 'commandExecution', 'id': 'c1',
                                                                              'command': 'python3 prepare.py', 'exitCode': 0}})
            agent.on_notification('turn/plan/updated', {'threadId': 'A', 'plan': plan(2)})
            agent.on_notification('item/started', {'threadId': 'A', 'item': {'type': 'commandExecution', 'id': 'c2',
                                                                            'command': 'blender -b scene.blend'}})
        agent.progress_tick()
    return spoken


def check_cadence(spoken, gap):
    times = [t for t, _ in spoken]
    said = lambda words: [t for t, text in spoken if words in text]
    assert times and times[0] >= v.PROGRESS_AFTER_S, 'a quick task gets nothing'
    assert len(said('steps you plan')) == 1, 'the plan is told once'
    assert len(said('which step you are on now')) == 1 and said('which step you are on now')[0] >= 30, \
        'the next step gets one sentence'
    assert 1 <= len(said('taking a while')) <= 2 and said('taking a while')[0] >= 30 + v.LONG_STEP_S, \
        'only a long step gets word of how far it is'
    assert len(said('apologize briefly')) <= 1 and all(t >= v.APOLOGY_AFTER_S for t in said('apologize briefly')), \
        'one apology, after a long wait'
    assert len(said('still working on it')) <= 1, 'no "still working" every few dozen seconds'
    assert all(b - a >= gap for a, b in zip(times, times[1:])), times
    assert len(spoken) <= 5, spoken


# covers: agent.progress/E3
def test_voice_reports_milestones_not_a_ticker(service, monkeypatch):
    with contracts.StandIn('platform-bridge', {'status': {**contracts.query(contracts.load('platform-bridge'),
                                                          {'op': 'status'})['reply'], 'foreground': False}}) as bridge:
        monkeypatch.setenv('RUNGIC_PLATFORM_SOCKET', bridge.path)
        spoken = run_turn(service, monkeypatch, watched=True)     # the app is up, the phone shows Android
    check_cadence(spoken, v.GAP_AWAY_S)


# covers: agent.progress/E3
def test_voice_says_less_while_the_user_watches(service, monkeypatch):
    with contracts.StandIn('platform-bridge') as bridge:
        monkeypatch.setenv('RUNGIC_PLATFORM_SOCKET', bridge.path)
        spoken = run_turn(service, monkeypatch, watched=True)
        assert {'op': 'status'} in bridge.requests
    check_cadence(spoken, v.GAP_WATCHED_S)
    assert len([t for t, text in spoken if 'taking a while' in text]) >= 1


# ---- the usage panel's model (agent.model-choice/E5) ----------------------------------------------
# covers: agent.model-choice/E5
def test_usage_reports_the_model_tasks_run_with(service):
    s = service
    agent = s.make()
    wait_until(lambda: agent.catalog().models is not None, what='the catalog')
    assert s.dbus('Usage')['model'] == 'GPT-6-Luna', "the account's default, by its name"
    s.dbus('SetAgentModel', json.dumps({'model': 'gpt-6-astra', 'effort': ''}))
    assert s.dbus('Usage')['model'] == 'GPT-6-Astra'
    agent.model_choice['codex'] = {'model': 'gpt-retired', 'effort': ''}     # no longer offered
    assert s.dbus('Usage')['model'] == 'GPT-6-Luna', 'what really runs, not the stale choice'


# ---- edited instructions reach the open conversation (agent.instructions/E2) ----------------------
# covers: agent.instructions/E2
def test_edited_instructions_reach_the_open_conversation_when_idle(service):
    s = service
    agent = s.make()
    s.ready('A')
    wait_until(lambda: agent.store.index['A'].get('instructions'), what='instructions noted')
    assert (30, agent.idle_check) in s.timers, 'looked at every 30 seconds'
    injected = lambda: s.codex.of('thread/inject_items')
    first = len(injected())
    (v.USER_PROMPTS / 'agent.md').write_text('Always answer in haiku.\n')
    agent.agent_busy = True                        # busy: not now
    agent.last_activity = time.monotonic() - 11
    agent.idle_check()
    pump(0.3)
    assert len(injected()) == first
    agent.agent_busy = False
    agent.talking = True                           # talking: not now
    agent.idle_check()
    pump(0.3)
    assert len(injected()) == first
    agent.talking = False
    agent.idle_check()
    wait_until(lambda: len(injected()) > first, what='the new instructions')
    item = injected()[-1]
    assert item['threadId'] == 'A' and item['items'][0]['role'] == 'developer'
    assert 'Always answer in haiku.' in item['items'][0]['content'][0]['text']
    # A skill edited: the agent is told to read it again.
    skill = v.USER_SKILLS / 'rungic-phone-desktop/SKILL.md'
    skill.write_text(skill.read_text() + '\nOne more rule.\n')
    agent.last_activity = time.monotonic() - 11
    agent.idle_check()
    wait_until(lambda: len(injected()) > first + 1, what='the skill note')
    assert 'skill has changed' in injected()[-1]['items'][0]['content'][0]['text']
    # The voice's prompt (phone.md) edited: its session restarts with it, when idle.
    starts = len(s.voice.of('start'))
    (v.USER_PROMPTS / 'phone.md').write_text('Speak softly.\n')
    agent.last_activity = time.monotonic() - 11
    agent.idle_check()
    wait_until(lambda: len(s.voice.of('start')) > starts, what='the voice restarted')
    assert s.voice.of('StopPhoneMode')
    started = s.voice.of('start')[-1]
    assert started['mode'] == 'press' and started['instructions'].startswith('Speak softly.')


# ---- an update restarts Codex only when nobody needs it (agent.codex-install/E3) ------------------
def install(s, monkeypatch, busy_for):
    """Runs the installer (a stand-in script) with a task running for `busy_for` simulated seconds."""
    agent = s.make()
    clock = Clock()

    def sleep(seconds):
        clock.now += seconds
        if clock.now - 1000 >= busy_for:
            agent.agent_busy = False
    monkeypatch.setattr(v, 'time', Proxy(time, monotonic=clock.monotonic, sleep=sleep))
    agent.agent_busy = True
    first = s.codex
    script = 'echo "==> Downloading Codex CLI"; echo "==> Installing standalone package"'
    agent.run_installer(['sh', '-c', script])
    return agent, first, clock


# covers: agent.codex-install/E3
def test_codex_restarts_after_the_task_and_reads_the_models_again(service, monkeypatch):
    s = service
    agent, first, clock = install(s, monkeypatch, busy_for=300)
    assert clock.now - 1000 >= 300, 'the app-server waited for the task'
    assert first.terminated and agent.server is not first, 'then the new Codex runs'
    wait_until(lambda: agent.server.of('model/list'), what='the models read again')
    states = [e.get('state') for e in s.kinds('install')]
    assert states[-1] == 'done' and 'failed' not in states
    assert s.kinds('agent-restarted'), 'the app reopens its conversation'


# covers: agent.codex-install/E3
def test_codex_restart_waits_at_most_half_an_hour(service, monkeypatch):
    s = service
    agent, first, clock = install(s, monkeypatch, busy_for=10 ** 9)
    assert 1800 <= clock.now - 1000 < 1810 and first.terminated


# covers: agent.codex-install/E3
def test_codex_restart_waits_while_someone_talks(service, monkeypatch):
    s = service
    agent = s.make()
    clock = Clock()

    def sleep(seconds):
        clock.now += seconds
        if clock.now - 1000 >= 20:
            agent.talking = False
    monkeypatch.setattr(v, 'time', Proxy(time, monotonic=clock.monotonic, sleep=sleep))
    agent.talking = True
    agent.wait_idle()
    assert 20 <= clock.now - 1000 < 30


# ---- the OpenAI API key (agent.sign-in/E5: the service's side) ------------------------------------
class OpenAI:
    """api.openai.com as urllib reaches it: /v1/models with the key accepted or refused."""

    def __init__(self, valid):
        self.valid, self.requests = valid, []

    def __call__(self, request, timeout=None):
        self.requests.append((request.full_url, request.get_header('Authorization')))
        if request.get_header('Authorization') != f'Bearer {self.valid}':
            raise urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {}, None)
        return self

    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# covers: agent.sign-in/E5
def test_api_key_is_tested_before_it_is_kept_in_a_private_file(service, monkeypatch):
    s = service
    agent = s.make(key=False)
    openai = OpenAI('sk-good-0123456789')
    monkeypatch.setattr(urllib.request, 'urlopen', openai)
    assert s.dbus('Setup')['key'] == {'set': False, 'masked': '', 'store': '', 'working': None}
    refused = s.dbus('SetApiKey', 'sk-bad-0123456789')
    assert refused['ok'] is False and refused['error'] == 'Invalid key'
    assert openai.requests[-1] == ('https://api.openai.com/v1/models', 'Bearer sk-bad-0123456789')
    assert keys.read('openai-api-key') == '', 'a key that does not work is not kept'
    assert s.dbus('SetApiKey', '  sk-good-0123456789 ')['ok'] is True
    path = v.CONFIG / 'openai-api-key'
    assert path.read_text() == 'sk-good-0123456789\n', 'kept as it is, in plain text'
    assert oct(path.stat().st_mode & 0o777) == '0o600' and oct(path.parent.stat().st_mode & 0o777) == '0o700'
    key = s.dbus('Setup')['key']
    assert key['working'] is True and key['set'] and key['store'] == 'file' and 'good' not in key['masked']
    # A key the service has not tested since it started is "not tested" (the page then tests it).
    agent.key_working = None
    assert s.dbus('Setup')['key']['working'] is None
    assert s.dbus('TestApiKey') == {'ok': True, 'error': ''}
