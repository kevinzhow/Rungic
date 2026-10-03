"""rungic-cua: desktop computer use for Codex, as an MCP server or a CLI (docs/60, docs/68).

Modes (`rungic-cua plan [codex|api|luna|atspi]`, ~/.config/rungic-cua/plan):
  codex (default): Codex decides each step with desktop_screenshot / desktop_act.
  api / luna: separate GPT-6 Luna Responses API goal executor (explicit API key).
  atspi: accessibility tree + OCR + JEV, retained as the alternate API path.
Window management (KWin) and the input (RemoteDesktop portal, KWin text commit) are shared.

The voice agent's Codex runs commands in a sandbox that cannot reach D-Bus or
Wayland. Codex starts MCP servers outside that sandbox, so desktop operations
(AT-SPI, KWin, the RemoteDesktop portal) and the JEV network calls live here.

  rungic-cua mcp                 MCP server on stdio (newline-delimited JSON-RPC)
  rungic-cua windows|observe     print JSON
  rungic-cua launch APP [SCREEN [ARGS...]] | activate WINDOW_ID | window WINDOW_ID ACTION
  rungic-cua run '<subtask json>'
  rungic-cua goal '{"goal": ..., "app": ...}'   whole task (Luna, or JEV per step under plan atspi)
  rungic-cua screenshot OUT.png | act '<actions json>' | plan [luna|atspi]
  rungic-cua restore-apps [N]    give apps switched into workspace N back to the user (switch.py)
  rungic-cua close-workspace [N] [--force]   ask workspace N's apps to close, then stop it (workspace.py)
"""
from __future__ import annotations

import json
import logging
import os
import select
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from arc_cua import DesktopExecutor, RuntimeConfig, result_to_dict, subtask_from_dict
from arc_cua.policies import TypeSafeJevPolicy

from . import a11y, activity, blocked, names, speech, switch, workspace
from .i18n import _
from .backend import LinuxAtspiBackend
from .luna import ComputerUse
from .screen import Desktop
from .mode import plan, save as save_plan

logger = logging.getLogger('rungic-cua')


def host_unreachable() -> bool:
    """The Android host did not answer lately (router.bridge marks it for every process)."""
    from . import router
    return router._recently_unreachable()


KEY_FILES = (Path.home() / '.config/rungic-cua/typesafe-api-key',
             Path.home() / '.config/rungic-voice-agent/typesafe-api-key')
MAX_ELEMENTS_SHOWN = 150
SUBTASK_SCHEMA = {
    'type': 'object',
    'properties': {
        'goal': {'type': 'string', 'description': 'What to achieve in the active window, one concrete UI step sequence.'},
        'verification': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'Observable conditions that mean the goal is done.'},
        'inputs': {'type': 'object', 'additionalProperties': {'type': ['string', 'number', 'boolean']},
                   'description': 'Named literal values the executor may type or set; it never invents text.'},
        'constraints': {'type': 'array', 'items': {'type': 'string'}},
        'shortcuts': {'type': 'object', 'additionalProperties': {'type': 'string'},
                      'description': 'Extra keyboard chords allowed, e.g. {"CTRL+L": "focus the address bar"}.'},
        'max_actions': {'type': 'integer', 'minimum': 1, 'maximum': 40},
        'timeout_s': {'type': 'number', 'minimum': 5, 'maximum': 300},
    },
    'required': ['goal', 'verification'],
}

TOOLS = [
    {'name': 'desktop_windows',
     'description': 'List the open desktop windows (in a workspace, the user\'s desktop included: its one screen; in the phone\'s session: the phone WL-0, a TV CAST-n) and which one is active.',
     'inputSchema': {'type': 'object', 'properties': {}}, 'annotations': {'readOnlyHint': True}},
    {'name': 'desktop_activate',
     'description': 'Bring a window (id from desktop_windows) to the front and make it active.',
     'inputSchema': {'type': 'object', 'properties': {'window_id': {'type': 'string'}}, 'required': ['window_id']},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'desktop_window',
     'description': ('Window management through the window manager (use this, not desktop_run, for these): '
                     'close (like the title-bar close button; the app may still ask to save), minimize, maximize, '
                     'restore, to_phone, to_tv (move to that screen).'),
     'inputSchema': {'type': 'object', 'properties': {
         'window_id': {'type': 'string'},
         'action': {'type': 'string', 'enum': ['close', 'minimize', 'maximize', 'restore', 'to_phone', 'to_tv']}},
         'required': ['window_id', 'action']},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'desktop_launch',
     'description': ('Open an application by desktop-file id or name (e.g. "org.kde.dolphin", "Firefox", "系统设置") '
                     'on the assistant\'s screen (turned on if needed; the user watches it in a floating window, '
                     'or on the TV while casting); an app already open is moved there and activated. Returns its '
                     'window id and screen. `args`: files to open or command-line options, e.g. '
                     '["/home/me/Pictures/a.png"] or ["--python", "/home/me/make.py"] (a new window is started). '
                     'screen "phone" only when the user asks for the phone. In your workspace, an app that runs '
                     'once per user (WeChat, a browser profile, Telegram ...) and is open on the user\'s phone '
                     'must be closed there first: the tool then returns `needs_confirmation` with a `question`; '
                     'ask the user exactly that and call again with "switch": true only after they agree. It is '
                     'given back to the user\'s phone after you are done.'),
     'inputSchema': {'type': 'object', 'properties': {
         'app': {'type': 'string'},
         'args': {'type': 'array', 'items': {'type': 'string'},
                  'description': 'Files, URLs or options passed to the application.'},
         'screen': {'type': 'string', 'enum': ['auto', 'agent', 'tv', 'phone'],
                    'description': "auto (default) = agent = tv: the assistant's screen; phone: the phone."},
         'switch': {'type': 'boolean',
                    'description': "The user agreed to close this app on their phone so it opens in your "
                                   "workspace (only after needs_confirmation)."}},
         'required': ['app']},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'desktop_observe',
     'description': 'Accessibility snapshot of the active window: controls with role, name, value and state.',
     'inputSchema': {'type': 'object', 'properties': {}}, 'annotations': {'readOnlyHint': True}},
    {'name': 'desktop_run',
     'description': ('Operate the ACTIVE window with the fast JEV executor until the verification holds: clicks, '
                     'text entry, keys, scrolling. Give literal text only through `inputs`. Returns status '
                     '(SUBTASK_COMPLETE, BLOCKED or NEEDS_AGENT), the actions taken and the final UI state. '
                     'Activate or launch the target app first.'),
     'inputSchema': SUBTASK_SCHEMA,
     # UI operation on the user's behalf, inside this device. Codex would otherwise ask for approval on
     # every call, which the voice assistant cannot answer; the agent prompt makes it confirm deletions,
     # sending and payments with the user first.
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'desktop_goal',
     'description': ('Do a whole task on the desktop: every step (what to click, type, scroll, when it is done) is '
                     'chosen by GOAL_DECIDER. Give '
                     'the goal as the user would say it, with every literal value (names, message text) in it. '
                     'With `app`, that application is opened or brought forward first. Returns outcome, achieved, '
                     'answer (what the screen shows about the goal) and the steps taken. Outcome "question": the '
                     'task needs information only the user has; ask the user `question` and call again with the '
                     'same goal and the reply in `replies`. Sending, paying or deleting still needs the user\'s OK '
                     'before you call this. Runs on the assistant\'s screen, which it turns on: the user watches it '
                     'in a floating window (or on the TV) while the phone stays theirs.'),
     'inputSchema': {'type': 'object', 'properties': {
         'goal': {'type': 'string'},
         'app': {'type': 'string', 'description': 'Application to open or activate first (as for desktop_launch; '
                                                  'it may return needs_confirmation first).'},
         'switch': {'type': 'boolean', 'description': 'As for desktop_launch: the user agreed to move `app` over.'},
         'replies': {'type': 'array', 'items': {'type': 'object', 'properties': {
             'question': {'type': 'string'}, 'answer': {'type': 'string'}}, 'required': ['question', 'answer']},
             'description': "The user's answers to questions earlier runs of this goal asked."},
         'steps': {'type': 'integer', 'minimum': 1, 'maximum': 60, 'description': 'Most actions to take (default 25).'}},
         'required': ['goal']},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'desktop_find_name',
     'description': ('Find a person, chat or item in the ACTIVE window (and its open popups, e.g. search results) '
                     'by how the name SOUNDS: speech recognition often writes a Chinese name with wrong characters '
                     'of the same sound (周凯文 for 周楷雯). Returns `search_text` (the pinyin; type it into the '
                     'app\'s search field to list same-sounding contacts) and the visible names ranked by sound: '
                     '1.0 same sound, 0.9 same apart from accent-type confusions (zh/z, n/l, an/ang ...). Each element '
                     'has the `section` heading above it: use people and chats (e.g. "Contacts", the chat list), '
                     'not search suggestions ("Internet search results"). If more than one different person '
                     'scores 0.9 or more, ask the user which one.'),
     'inputSchema': {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name']},
     'annotations': {'readOnlyHint': True}},
    {'name': 'desktop_voice_message',
     'description': ('Record and send a voice message in the ACTIVE window (a chat already open), spoken by the '
                     'assistant: the app\'s microphone is switched to the Linux microphone for the recording only, '
                     'the text is spoken into it, then the send control is pressed. `start` / `finish` are control '
                     'names from desktop_observe (WeChat: start "Send Voice", finish "Send voice message"). With '
                     '`hold`, `start` is held down while speaking and released to send (hold-to-talk apps). The '
                     'user\'s real microphone is never recorded: if the app does not start recording through the '
                     'Linux microphone, nothing is spoken and the recording is cancelled.'),
     'inputSchema': {'type': 'object', 'properties': {
         'text': {'type': 'string', 'description': 'What the voice message says.'},
         'start': {'type': 'string', 'description': 'Control that starts recording.'},
         'finish': {'type': 'string', 'description': 'Control that sends the recording (omit with hold).'},
         'cancel': {'type': 'string', 'description': 'Control that discards a recording (WeChat: "Cancel").'},
         'hold': {'type': 'boolean'},
         'voice': {'type': 'string', 'description': 'TTS voice, default marin.'}},
         'required': ['text', 'start']},
     # Sends a message on the user's behalf; the agent prompt makes it get the user's OK first.
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}},
]


ACTION_SCHEMA = {
    'type': 'object',
    'description': ('One action, as the computer-use tool of the Responses API defines it; x/y are pixels of the '
                    'latest desktop_screenshot.'),
    'properties': {
        'type': {'type': 'string', 'enum': ['click', 'double_click', 'drag', 'move', 'scroll', 'keypress', 'type',
                                            'wait']},
        'x': {'type': 'number'}, 'y': {'type': 'number'},
        'button': {'type': 'string', 'enum': ['left', 'right', 'wheel', 'back', 'forward']},
        'keys': {'type': 'array', 'items': {'type': 'string'},
                 'description': 'keypress: the chord (e.g. ["CTRL", "L"], ["ENTER"]); mouse actions: modifiers held.'},
        'path': {'type': 'array', 'items': {'type': 'object', 'properties': {'x': {'type': 'number'},
                                                                            'y': {'type': 'number'}}},
                 'description': 'drag: the points, first to last.'},
        'scroll_x': {'type': 'number'}, 'scroll_y': {'type': 'number', 'description': 'pixels, positive = down'},
        'text': {'type': 'string', 'description': 'type: text for the focused field, any language.'}},
    'required': ['type'],
}
PLAN_ONE_TOOLS = [
    {'name': 'desktop_screenshot',
     'description': ("Look at the assistant's screen: returns the image of the active window there (rendered by "
                     'the window manager, with its open menus and dialogs), or with scope "screen" the whole screen '
                     '(1920x1080). Coordinates for desktop_act are pixels of the latest image. Turns the assistant\'s '
                     'screen on if needed.'),
     'inputSchema': {'type': 'object', 'properties': {
         'scope': {'type': 'string', 'enum': ['window', 'screen'], 'description': 'window (default) or screen'}}},
     'annotations': {'readOnlyHint': True}},
    {'name': 'desktop_act',
     'description': ("Act on the assistant's screen yourself, from what you saw in desktop_screenshot: a short "
                     'batch of actions carried out in order, then the new screenshot comes back. For a whole '
                     'multi-step task continue looking and acting until the result is verified.'),
     'inputSchema': {'type': 'object', 'properties': {
         'actions': {'type': 'array', 'items': ACTION_SCHEMA},
         'note': {'type': 'string', 'description': ('What this batch does, a few words in the language you speak '
                                                    'with the user (e.g. "Open the Render menu", 打开“渲染”菜单): '
                                                    'the user sees it as a live caption.')}},
         'required': ['actions']},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}},
]
VOICE_CODEX_TOOLS = [{
    'name': 'desktop_voice_recording',
    'description': ('Local audio for a voice message; you decide every UI action with screenshots. '
        'prepare with text in the verified chat BEFORE clicking its voice-message record control. '
        'status confirms recording through the Linux microphone. play only after recording=true; '
        'it returns after playback, and must return played=true before you click Send. '
        'After verifying the sent message, close to release routing. On failure, cancel the recording '
        'with desktop_act and close. Never replay after an uncertain playback. This tool never clicks '
        'or sends, and uses the API key for speech synthesis only.'),
    'inputSchema': {'type': 'object', 'properties': {
        'phase': {'type': 'string', 'enum': ['prepare', 'status', 'play', 'close']},
        'text': {'type': 'string', 'description': 'The authorized message, required for prepare.'},
        'voice': {'type': 'string'}}, 'required': ['phase']},
    'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False},
}]
PLAN_TWO_ONLY = ('desktop_observe', 'desktop_run', 'desktop_find_name')
DECIDERS = {'luna': 'GPT-6 Luna looking at screenshots of the screen (computer use; no accessibility tree or OCR); '
                    'text is typed in any language',
            'atspi': 'JEV from the screen (OCR + accessibility), text is written only when needed'}


def tools_for(chosen: str) -> list[dict]:
    tools = []
    for tool in TOOLS:
        if chosen in ('codex', 'luna') and tool['name'] in PLAN_TWO_ONLY:
            continue
        if chosen == 'codex' and tool['name'] in ('desktop_goal', 'desktop_voice_message'):
            continue
        tool = dict(tool)
        if tool['name'] == 'desktop_goal':
            tool['description'] = tool['description'].replace('GOAL_DECIDER', DECIDERS[chosen])
        if tool['name'] == 'desktop_voice_message' and chosen == 'luna':
            tool = VOICE_MESSAGE_LUNA
        tools.append(tool)
    return tools + (PLAN_ONE_TOOLS if chosen in ('codex', 'luna') else []) + (VOICE_CODEX_TOOLS if chosen == 'codex' else [])


VOICE_MESSAGE_LUNA = {
    'name': 'desktop_voice_message',
    'description': ('Record and send a voice message in the chat open in the ACTIVE window on the assistant\'s '
                    'screen, spoken by the assistant: the app\'s microphone is switched to the Linux microphone '
                    'for the recording only; GPT-6 Luna finds and presses the record and send controls on screen. '
                    'Recording counts as started only when the app records from the Linux microphone; otherwise '
                    'nothing is spoken and nothing is sent. The user\'s real microphone is never recorded.'),
    'inputSchema': {'type': 'object', 'properties': {
        'text': {'type': 'string', 'description': 'What the voice message says.'},
        'voice': {'type': 'string', 'description': 'TTS voice, default marin.'}},
        'required': ['text']},
    'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False},
}


def localized_names(info) -> set[str]:
    """Name and GenericName in every language of the .desktop file: the request may
    be in Chinese while this process runs in another locale."""
    names: set[str] = set()
    path = info.get_filename() if hasattr(info, 'get_filename') else None
    if not path:
        return names
    try:
        section = False
        for line in open(path, encoding='utf-8', errors='replace'):
            line = line.strip()
            if line.startswith('['):
                section = line == '[Desktop Entry]'
            elif section and (line.startswith('Name') or line.startswith('GenericName')) and '=' in line:
                names.add(line.split('=', 1)[1].strip().casefold())
    except OSError:
        pass
    return names


def find_application(query: str) -> dict | None:
    """Installed .desktop entry by id or (localized) name, with the names its window may carry."""
    from gi.repository import Gio
    query_folded = query.casefold().removesuffix('.desktop')
    best = None
    for info in Gio.AppInfo.get_all():
        if not info.should_show():
            continue
        app_id = (info.get_id() or '').removesuffix('.desktop')
        names = {app_id.casefold(), (info.get_name() or '').casefold(), (info.get_display_name() or '').casefold()}
        names |= localized_names(info)
        classes = {app_id.casefold(), app_id.split('.')[-1].casefold(),
                   os.path.basename(info.get_executable() or '').casefold()}
        if isinstance(info, Gio.DesktopAppInfo) and info.get_startup_wm_class():
            classes.add(info.get_startup_wm_class().casefold())
        entry = {'id': app_id, 'name': info.get_display_name(), 'classes': sorted(c for c in classes if c),
                 'exec': info.get_commandline() or ''}
        if query_folded in names:
            return entry
        if best is None and any(query_folded in n for n in names if n):
            best = entry
    return best


def api_key() -> str:
    if os.environ.get('TYPESAFE_API_KEY'):
        return os.environ['TYPESAFE_API_KEY']
    for path in KEY_FILES:
        if path.exists():
            return path.read_text().strip()
    raise RuntimeError('No JEV (TypeSafe) API key: put it in ~/.config/rungic-cua/typesafe-api-key')


IDLE_A11Y_OFF_S = 600
GOAL_TIMEOUT_S = 290  # inside Codex's tool timeout (320 s, install.sh)


class Cua:
    def __init__(self) -> None:
        self._backend: LinuxAtspiBackend | None = None
        self._policy: TypeSafeJevPolicy | None = None
        self._idle: threading.Timer | None = None

    def _touch(self) -> None:
        """Accessibility costs every registered app: switch it off after a quiet period."""
        if self._idle:
            self._idle.cancel()
        self._idle = threading.Timer(IDLE_A11Y_OFF_S, self._idle_off)
        self._idle.daemon = True
        self._idle.start()

    def _idle_off(self) -> None:
        if self._backend is not None and self._backend.enabled_by_us:
            self._backend.set_accessibility(False)

    @property
    def backend(self) -> LinuxAtspiBackend:
        if self._backend is None:
            self._backend = LinuxAtspiBackend()
        return self._backend

    def windows(self) -> dict:
        info = self.backend.kwin.windows()
        active = (info.get('active') or {}).get('id')
        screens = info.get('screens', [])
        return {'casting': any(s.startswith('CAST') for s in screens), 'screens': screens,
                'windows': [{'id': w['id'], 'caption': w['caption'], 'app': w['resource_class'], 'screen': w['output'],
                             'active': w['id'] == active, 'minimized': w['minimized']} for w in info['windows']]}

    def window(self, window_id: str, action: str) -> dict:
        result = self.backend.kwin.window_action(window_id, action)
        if not result.get('found'):
            raise ValueError(f'No window {window_id}; list them with desktop_windows')
        time.sleep(0.5)
        still_open = any(w['id'] == window_id for w in self.backend.kwin.windows()['windows'])
        result['still_open'] = still_open
        if action == 'close' and still_open:
            result['note'] = 'The window is still open: it may be asking something (e.g. to save); observe it.'
        return result

    def activate(self, window_id: str) -> dict:
        ok = self.backend.kwin.activate(window_id)
        time.sleep(0.3)
        return {'activated': ok}

    def launch(self, app: str, screen: str = 'auto', args: list[str] | None = None, confirmed: bool = False) -> dict:
        """Open `app` on the assistant's screen (the phone only when asked), or bring its open
        window there. With `args` a new window is started with them. In a workspace an app that
        runs once per user and is open in the user's session is switched over, only with the
        user's agreement (`confirmed`, switch.py)."""
        entry = find_application(app)
        if entry is None:
            raise ValueError(f'No installed application matches {app!r}')
        if os.environ.get('RUNGIC_WORKSPACE') and switch.single_instance(entry):
            names = switch.programs(entry)
            theirs = switch.processes(names, None)
            if theirs:
                if switch.in_call(names):
                    return {'launched': None, 'blocked': 'in_call',
                            'note': f"{entry['name']} is in a call on the user's phone: it cannot be moved now. "
                                    'Tell the user; try again after the call.'}
                if not confirmed:
                    return {'launched': None, 'needs_confirmation': True,
                            # For the user, in the desktop's language.
                            'question': _("{app} is running on your phone. I'd close it there, open it again on "
                                          "the assistant's screen, and bring it back to you when I'm done. "
                                          'Is that OK?').format(app=entry['name']),
                            'note': 'Ask the user this question and wait for the answer. Only if they agree, call '
                                    'desktop_launch again with "switch": true. Never close it any other way '
                                    '(kill, pkill, desktop_window).'}
                activity.report(_("Move {app} to the assistant's screen").format(app=entry['name']))
                left = switch.close_in_user_session(entry, theirs)
                if left:
                    return {'launched': None, 'blocked': 'still_running',
                            'note': f"{entry['name']} did not close on the user's phone (it may be asking "
                                    'something there). Tell the user; do not force it.'}
                time.sleep(0.5)
        classes = entry.pop('classes')
        exec_line = entry.pop('exec', '')
        kwin = self.backend.kwin
        # The assistant's screen (docs/65) and the TV are the same output, CAST-n: the agent works
        # there, where the user watches, and the phone stays the user's (docs/88). In a workspace
        # of its own (docs/research/91) there is nothing else: the phone is not reachable from it.
        to_tv = screen != 'phone' or bool(os.environ.get('RUNGIC_WORKSPACE'))
        if to_tv:
            self.agent_output()
        info = kwin.windows()
        prefix = 'CAST' if to_tv else 'WL'
        target_screen = next((n for n in info.get('screens', []) if n.startswith(prefix)), prefix)
        activity.report(_('Open {app}').format(app=entry['name']))
        existing = None if args else next(
            (w for w in info['windows'] if (w['resource_class'] or '').casefold() in classes), None)
        if existing:
            if not existing['output'].startswith(prefix):
                kwin.window_action(existing['id'], 'to_tv' if to_tv else 'to_phone')
            kwin.activate(existing['id'])
            time.sleep(0.3)
            return {'launched': entry, 'already_open': True,
                    'window': {'id': existing['id'], 'caption': existing['caption'], 'screen': target_screen}}
        # kstart runs it in its own app scope, so it outlives this server and the voice agent.
        command = ['kstart', '--application', entry['id']]
        if args:
            program = (shlex.split(exec_line) or [entry['id']])[0]
            command = ['kstart', '--desktopfile', entry['id'], '--', program, *[str(a) for a in args]]
        if os.environ.get('RUNGIC_WORKSPACE'):
            # In a workspace KIO finds no systemd on the session bus and forks: the app stayed in the
            # voice agent's cgroup and ended when it restarted. A scope of its own, as in the user's
            # session (systemd-run reaches the user manager directly; the app keeps this display).
            # The scope is bound to the workspace: it stops when the workspace does (workspace.py).
            slot = os.environ['RUNGIC_WORKSPACE']
            unit = workspace.scope_name(slot, entry['id'], f'{os.getpid()}{time.monotonic_ns()}')
            command = ['systemd-run', '--user', '--scope', '--collect', '--quiet', '--unit', unit,
                       *workspace.scope_properties(slot), '--', *command]
        placed = kwin.place_next(classes, prefix, lambda: subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True), timeout=25)
        if placed is None:
            return {'launched': entry, 'window': None,
                    'note': ('No window within 25 s. Check desktop_windows once; do not start the app from the '
                             'shell (its window would open on the phone). Tell the user if it did not start.')}
        time.sleep(0.3)
        return {'launched': entry, 'window': {'id': placed['id'], 'screen': placed['screen']}}

    def observe(self) -> dict:
        snapshot = self.backend.observe()
        data = snapshot.compact()
        data.pop('revision', None)
        if len(data['elements']) > MAX_ELEMENTS_SHOWN:
            data['elements_truncated'] = len(data['elements']) - MAX_ELEMENTS_SHOWN
            data['elements'] = data['elements'][:MAX_ELEMENTS_SHOWN]
        return data

    def run(self, payload: dict) -> dict:
        payload = dict(payload)
        timeout = float(payload.pop('timeout_s', 120))
        payload.setdefault('max_actions', 20)
        subtask = subtask_from_dict(payload)
        if self._policy is None:
            self._policy = TypeSafeJevPolicy(api_key=api_key())
        executor = DesktopExecutor(self.backend, self._policy, config=RuntimeConfig(timeout_s=timeout))
        started = time.perf_counter()
        result = result_to_dict(executor.run(subtask))
        result['elapsed_ms'] = round((time.perf_counter() - started) * 1000)
        final = result.pop('final_snapshot')
        elements = final.get('elements', [])
        result['final_window'] = {'application': final.get('application'), 'window': final.get('window'),
                                  'elements': elements[:MAX_ELEMENTS_SHOWN]}
        for record in result['history']:
            for key in ('before_revision', 'after_revision', 'target_bounds'):
                record.pop(key, None)
        return result

    # ---- plan one: GPT-6 Luna computer use (luna.py) ------------------------------------------
    def agent_output(self) -> str:
        """The assistant's screen's output (turned on if needed). In a workspace of its own
        (RUNGIC_WORKSPACE, docs/research/91) this KWin is the agent's: its output is the screen."""
        if os.environ.get('RUNGIC_WORKSPACE'):
            self.show_workspace()
            outputs = [o['name'] for o in self.backend.kwin.windows().get('outputs', [])]
            if outputs:
                return outputs[0]
            raise RuntimeError('the workspace has no output')
        outputs = [o['name'] for o in self.backend.kwin.windows().get('outputs', []) if o['name'].startswith('CAST')]
        if not outputs:
            # On the user's desktop (router.py) its second screen is desktop mode's.
            screen = subprocess.run(['rungic-desktop-mode', 'on'], capture_output=True, text=True, timeout=30)
            if screen.returncode != 0:
                raise RuntimeError(f'desktop mode: {screen.stderr.strip() or screen.stdout.strip()}')
            time.sleep(1)
            outputs = [o['name'] for o in self.backend.kwin.windows().get('outputs', []) if o['name'].startswith('CAST')]
            if not outputs:
                raise RuntimeError("the assistant's screen did not come up")
        return outputs[0]

    def show_workspace(self) -> None:
        """The user sees the agent work: the assistant's screen on, showing this workspace (the
        floating window on the phone, or the TV or fullscreen already showing it). At most every
        10 s: the user may have closed it on purpose meanwhile, and the next task opens it again."""
        now = time.monotonic()
        if now - getattr(self, '_shown_at', -60.0) < 10 or workspace.dismissed(os.environ['RUNGIC_WORKSPACE']):
            return
        self._shown_at = now
        # Only the user's view: the agent's action never waits for it (a frozen Rungic app, the phone
        # asleep, made each action wait 15 s here, docs/research/97 §13), and none while the host
        # is known not to answer.
        if host_unreachable():
            return
        threading.Thread(target=self._show_workspace, daemon=True).start()

    def _show_workspace(self) -> None:
        try:
            slot = int(os.environ['RUNGIC_WORKSPACE'])
            if slot == 0:
                # The user's desktop (desktop mode, docs/research/97 §19): its floating window back if gone.
                subprocess.run(['rungic-desktop-mode', 'ensure'], capture_output=True, timeout=30)
                return
            state = json.loads(subprocess.run(['rungic-agent-screen', 'status'], capture_output=True, text=True,
                                              timeout=15).stdout)
            if not state.get('enabled') or (str(state.get('workspace')) != str(slot)
                                            and slot not in state.get('windows', [])):
                # Its own floating window; others' stay (a team's workspaces, all shown at once).
                subprocess.run(['rungic-agent-screen', 'on'], capture_output=True, timeout=30)
            elif not state.get('window_running') and state.get('shown_on') == 'floating window':
                # On, but its floating window is gone (closed, or ended with the desktop).
                subprocess.run(['rungic-agent-screen', 'ensure'], capture_output=True, timeout=30)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            logger.warning('assistant screen: %s', error)

    def agent_screen(self) -> Desktop:
        """The session desktop_screenshot and desktop_act share: its latest image says where
        desktop_act's pixels are. It follows the active window on the assistant's screen."""
        output = self.agent_output()
        if getattr(self, '_computer', None) is None or self._computer.screen.output_name != output:
            self._computer = Desktop(self.backend, output)
        return self._computer

    def goal_luna(self, args: dict) -> dict:
        output = self.agent_output()
        window_id = args.get('window')          # a window the caller knows (e.g. a call window)
        if args.get('app'):
            # The window the task is about, from the window manager: the model sees that window.
            launched = self.launch(str(args['app']), 'agent', confirmed=bool(args.get('switch'))) or {}
            if launched.get('needs_confirmation') or launched.get('blocked'):
                return launched     # the user decides first (switch.py)
            window_id = (launched.get('window') or {}).get('id')
            time.sleep(0.8)
        computer = ComputerUse(self.backend, output, window_id)
        task = str(args['goal'])
        for reply in args.get('replies') or []:
            task += f"\n(Earlier you asked: {reply['question']} The user answered: {reply['answer']})"
        return computer.run(task, max_steps=int(args.get('steps') or 30), timeout_s=GOAL_TIMEOUT_S - 10)

    def screenshot(self, scope: str = 'window') -> dict:
        computer = self.agent_screen()
        computer.screen.whole = scope == 'screen'
        computer.screen.window_id = None           # the active window, whichever it is now
        url, image, _changed = computer.screen.capture()
        result = {'screen': computer.screen.output_name, 'shows': computer.screen.scope, 'width': image.width,
                  'height': image.height, '__image__': url.split(',', 1)[1]}
        try:
            held = self.held()
        except Exception as error:  # noqa: BLE001 (a check beside the screenshot never costs it)
            logger.warning('dialog check: %s', error)
            held = None
        if held:
            result['note'] = held
        return result

    def held(self) -> str | None:
        """A note when the active window's program is held by a dialog nobody can see, or waits for
        one in another window (a portal file dialog, docs/103); None otherwise."""
        bus = self.backend.bus
        if bus is None:
            return None
        info = self.backend.kwin.windows()
        active = info.get('active')
        if not active:
            return None
        app_windows = []
        for app in bus.applications():
            if bus.pid(app[0]) == active['pid']:
                app_windows += bus.windows(app)
        hidden = blocked.hidden_dialogs(app_windows, info['windows'] + info.get('dialogs', []), active['pid'])
        name = (active.get('caption') or '').split(' — ')[-1].strip() or active.get('resource_class', '')
        return blocked.note(name, hidden)

    def act(self, actions: list[dict], note: str = '') -> dict:
        computer = self.agent_screen()
        shown = [a for a in actions if a.get('type') != 'screenshot'] or actions
        activity.report(note or (activity.describe(shown[0]) if shown else _('Look at the screen')))
        if not computer.screen.scope:
            raise ValueError('take desktop_screenshot first: the actions are in its pixels')
        done = []
        for action in actions:
            try:
                done.append(computer.execute(action))
            except (ValueError, KeyError) as error:
                done.append(f"{action.get('type')}: not done ({error})")
                break
        time.sleep(0.5)
        return {'done': done, **self.screenshot('screen' if computer.screen.whole else 'window')}

    def voice_recording(self, args: dict) -> dict:
        """Transport for the native audio lifecycle; Codex owns all GUI decisions."""
        phase = args['phase']
        process = getattr(self, '_voice_recording', None)
        if phase == 'prepare':
            if process is not None:
                raise RuntimeError('Close the previous recording first')
            text = str(args.get('text') or '').strip()
            if not text:
                raise ValueError('prepare needs the authorized message text')
            output = self.agent_output()
            active = self.backend.kwin.windows().get('active') or {}
            if not active.get('pid') or active.get('output') != output:
                raise RuntimeError('Open and verify the chat on your screen first')
            binary = os.path.basename(os.readlink(f"/proc/{active['pid']}/exe"))
            audio = speech.synthesize(text, voice=str(args.get('voice') or 'marin'))
            with tempfile.NamedTemporaryFile(prefix='rungic-voice-', dir=os.environ.get('XDG_RUNTIME_DIR')) as pcm:
                pcm.write(audio)
                pcm.flush()
                process = self._voice_recording = subprocess.Popen(
                    ['/usr/libexec/rungic-voice-recording', binary, pcm.name], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, text=True)
                result = self._recording_reply(15)
            return {**result, 'app': binary}
        if process is None:
            if phase == 'close':
                return {'closed': True}
            raise RuntimeError('Prepare the voice recording first')
        try:
            process.stdin.write(json.dumps({'phase': phase}) + '\n')
            process.stdin.flush()
            return self._recording_reply(125 if phase == 'play' else 10)
        finally:
            if phase == 'close':
                self.close_recording()

    def _recording_reply(self, timeout: float) -> dict:
        process = self._voice_recording
        if not select.select([process.stdout], [], [], timeout)[0]:
            self.close_recording()
            raise RuntimeError('Voice recording timed out; cancel its UI without sending')
        line = process.stdout.readline()
        result = json.loads(line) if line else {'error': 'Voice recording stopped; cancel without sending'}
        if result.get('error'):
            self.close_recording()
            raise RuntimeError(result['error'])
        return result

    def close_recording(self) -> None:
        process = getattr(self, '_voice_recording', None)
        self._voice_recording = None
        if process is not None:
            process.stdin.close()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)

    def voice_message_luna(self, args: dict) -> dict:
        """Speak `text` into a recording the model starts and sends on screen (docs/62, docs/68)."""
        text = str(args['text']).strip()
        if not text:
            raise ValueError('voice message needs text')
        audio = speech.synthesize(text, voice=str(args.get('voice') or 'marin'))
        output = self.agent_output()
        active = self.backend.kwin.windows().get('active') or {}
        # On the screen the agent works on (a workspace's output, the user's desktop's included).
        if not active.get('pid') or str(active.get('output', '')) != output:
            raise RuntimeError("open the chat on the assistant's screen first (desktop_goal), then call this")
        binary = os.path.basename(os.readlink(f"/proc/{active['pid']}/exe"))
        computer = ComputerUse(self.backend, output, active['id'])   # the chat's window
        router = subprocess.Popen(['rungic-audio-route', '--binary', binary, '--microphone'], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True)
        routed = threading.Event()

        def watch():
            for line in router.stdout:
                if line.startswith('routed source-output'):
                    routed.set()
        try:
            if router.stdout.readline().strip() != 'ready':
                raise RuntimeError('audio routing did not start')
            threading.Thread(target=watch, daemon=True).start()
            start = computer.run(
                'In the chat that is open, start recording a voice message: press the control that records an audio '
                'message to send (WeChat calls it "Send Voice"; usually a microphone next to the message box). Not '
                'voice input or dictation, which turns speech into text (WeChat "Voice Input"), and not a voice or '
                'video call. If unsure which icon is which, hover or look closely first. Do not send anything. As '
                'soon as the recording has started, stop and reply DONE.',
                max_steps=6, timeout_s=60, stop=routed.is_set)
            if not routed.wait(3):
                # The app may be recording the real microphone: never leave that to be sent (docs/62).
                computer.run('A voice message recording may still be open. Cancel it without sending, then reply DONE.',
                             max_steps=4, timeout_s=60)
                return {'sent': False, 'app': binary, 'start': start,
                        'note': f'{binary} did not start recording through the Linux microphone; nothing was spoken, '
                                'the recording was cancelled'}
            # Finding the send control takes the model a few seconds: it looks while the speech plays,
            # and presses only after it ended (else the message ends in seconds of silence).
            spoken = threading.Event()
            found: dict = {}
            finder = threading.Thread(target=lambda: found.update(computer.run(
                'A voice message is being recorded in this chat. Press the control that sends this recording '
                '(not cancel). Then reply DONE and say whether it appears in the chat.',
                max_steps=4, timeout_s=90, gate=spoken)), daemon=True)
            time.sleep(0.2)
            finder.start()
            try:
                subprocess.run(['pacat', '--device=linux_microphone_input', '--raw', '--format=s16le',
                                f'--rate={speech.RATE}', '--channels=1', '--latency-msec=30'], input=audio,
                               check=True, timeout=120)
                time.sleep(0.4)
            finally:
                spoken.set()
            finder.join(100)
            send = found or {'outcome': 'failed', 'answer': 'no result from the send step'}
            if send.get('outcome') != 'done':
                computer.run('A voice message recording may still be open. Cancel it without sending, then reply DONE.',
                             max_steps=4, timeout_s=60)
                return {'sent': False, 'app': binary, 'send': send}
            return {'sent': True, 'app': binary, 'seconds': round(len(audio) / 2 / speech.RATE, 1),
                    'screen': send.get('answer'), 'steps': start.get('steps', []) + send.get('steps', [])}
        finally:
            router.stdin.close()
            try:
                router.wait(5)
            except subprocess.TimeoutExpired:
                router.kill()

    def goal(self, args: dict) -> dict:
        """A whole task through rungic-clicker (typesafe-computer-use, JEV per step; docs/64), on the
        assistant's screen (docs/65): turned on first, and the app opened there."""
        self.agent_output()     # the screen it works on, turned on if needed
        if args.get('app'):
            launched = self.launch(str(args['app']), 'agent', confirmed=bool(args.get('switch'))) or {}
            if launched.get('needs_confirmation') or launched.get('blocked'):
                return launched
            time.sleep(0.8)
        command = ['rungic-clicker', 'run', str(args['goal']), '--steps', str(int(args.get('steps') or 25))]
        for reply in args.get('replies') or []:
            command += ['--reply', f"{reply['question']}={reply['answer']}"]
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=GOAL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            subprocess.run(['rungic-clicker', 'stop'], capture_output=True, timeout=30)
            return {'outcome': 'timeout', 'note': f'Stopped after {GOAL_TIMEOUT_S} s; check the screen.'}
        if done.returncode != 0:
            raise RuntimeError(f'rungic-clicker failed: {done.stderr.strip()[-800:]}')
        return json.loads(done.stdout)

    def press_control(self, binary: str, names: list[str]) -> dict:
        """Click the first control named one of `names` in any window of the app
        (e.g. a call window's hang-up button), whichever window is active."""
        backend = self.backend
        bus = backend.bus
        windows = [w for w in backend.kwin.windows()['windows']
                   if os.path.basename(os.readlink(f"/proc/{w['pid']}/exe")) == binary]
        for app in bus.applications():
            pid = bus.pid(app[0])
            if not pid or pid not in {w['pid'] for w in windows}:
                continue
            for window in bus.windows(app):
                if not window.state(a11y.SHOWING):
                    continue
                origin = bus.origin(window)
                target = next((w for w in windows if w['pid'] == pid and w['caption'] == window.name), None)
                if origin is None or target is None:
                    continue
                for node in bus.tree(window, max_nodes=400):
                    if not node.name or not any(node.name == n or node.name.startswith(n) for n in names):
                        continue
                    extents = bus.extents(node, origin)
                    if extents is None:
                        continue
                    cx, cy = target['client'][0], target['client'][1]
                    x, y, w, h = extents
                    backend.kwin.activate(target['id'])
                    backend.input.click(cx + x + w / 2, cy + y + h / 2)
                    return {'pressed': node.name, 'window': target['caption']}
        return {'pressed': None, 'looked_for': names, 'windows': [w['caption'] for w in windows]}

    def focus_showing(self, binary: str, text: str) -> dict:
        """Activate the app's window that shows `text` as a label or heading (e.g.
        the chat header of a contact): chats may be open in several windows."""
        backend = self.backend
        bus = backend.bus
        windows = [w for w in backend.kwin.windows()['windows']
                   if os.path.basename(os.readlink(f"/proc/{w['pid']}/exe")) == binary]
        for app in bus.applications():
            pid = bus.pid(app[0])
            if not pid or pid not in {w['pid'] for w in windows}:
                continue
            for window in bus.windows(app):
                if not window.state(a11y.SHOWING):
                    continue
                target = next((w for w in windows if w['pid'] == pid and w['caption'] == window.name), None)
                if target is None:
                    continue
                if any(n.name == text and n.role in ('label', 'heading', 'text', 'filler')
                       for n in bus.tree(window, max_nodes=400)):
                    backend.kwin.activate(target['id'])
                    backend._root = None
                    return {'activated': target['id'], 'window': target['caption']}
        return {'activated': None, 'looked_for': text}

    def find_name(self, name: str) -> dict:
        snapshot = self.backend.observe()
        found: dict[str, list] = {}
        section: dict[str, str] = {}      # element id -> heading above it in the same list
        heading: dict[str | None, str] = {}
        for element in snapshot.elements:
            first = (element.name or '').split('\n')[0].strip()
            # Result lists group items under one-line headings without a sound match
            # ("Contacts", "Group Chats", "Internet search results").
            if first and '\n' not in (element.name or '') and names.score(name, first) < 0.5:
                heading[element.parent_id] = first
            section[element.id] = heading.get(element.parent_id, '')
            if first:
                found.setdefault(first, []).append(element)
        ranked = names.rank(name, list(found))
        return {'search_text': names.search_text(name), 'window': snapshot.window,
                'matches': [{'name': n, 'score': score,
                             'elements': [{'id': e.id, 'role': e.role, 'section': section[e.id], 'name': e.name[:80]}
                                          for e in found[n][:3]]}
                            for n, score in ranked]}

    def voice_message(self, args: dict) -> dict:
        """Speak `text` into the active app's recording (docs/62)."""
        text = str(args['text']).strip()
        start, finish = str(args['start']), args.get('finish')
        cancel, hold = str(args.get('cancel') or 'Cancel'), bool(args.get('hold'))
        if not text or (not hold and not finish):
            raise ValueError('voice message needs text and a finish control (or hold)')
        audio = speech.synthesize(text, voice=str(args.get('voice') or 'marin'))
        backend = self.backend
        snapshot = backend.observe()
        pid = backend._window['pid']
        binary = os.path.basename(os.readlink(f'/proc/{pid}/exe'))

        def control(snap, name):
            match = next((e for e in snap.elements if e.name == name), None) \
                or next((e for e in snap.elements if e.name.startswith(name)), None)
            if match is None:
                raise ValueError(f'No control named {name!r} in {snap.window!r}')
            return backend._global_center(backend._nodes[match.id])

        start_at = control(snapshot, start)
        router = subprocess.Popen(['rungic-audio-route', '--binary', binary, '--microphone'], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True)
        routed = started = False
        try:
            if router.stdout.readline().strip() != 'ready':
                raise RuntimeError('audio routing did not start')
            if hold:
                backend.input.press(*start_at)
            else:
                backend.input.click(*start_at)
            started = True
            # The recording stream appears when recording starts; speak only once it
            # records from the Linux microphone.
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and not routed:
                ready = select.select([router.stdout], [], [], 0.2)[0]
                if ready:
                    routed = router.stdout.readline().startswith('routed source-output')
            if not routed:
                if hold:
                    backend.input.release()
                raise RuntimeError(f'{binary} did not start recording through the Linux microphone; nothing was spoken')
            time.sleep(0.2)
            subprocess.run(['pacat', '--device=linux_microphone_input', '--raw', '--format=s16le',
                            f'--rate={speech.RATE}', '--channels=1', '--latency-msec=30'], input=audio,
                           check=True, timeout=120)
            time.sleep(0.4)
            if hold:
                backend.input.release()
            else:
                backend._root = None
                backend.input.click(*control(backend.observe(), str(finish)))
        except Exception:
            # Never leave a recording that could be sent later: not routed in time, it may be
            # recording the real microphone (docs/62).
            if started and not hold:
                try:
                    backend._root = None
                    backend.input.click(*control(backend.observe(), cancel))
                except Exception:  # noqa: BLE001
                    pass
            raise
        finally:
            router.stdin.close()
            try:
                router.wait(5)
            except subprocess.TimeoutExpired:
                router.kill()
        time.sleep(1)
        backend._root = None
        after = backend.observe()
        return {'sent': True, 'app': binary, 'seconds': round(len(audio) / 2 / speech.RATE, 1),
                'window': after.window,
                'recording_controls_left': [e.name for e in after.elements if e.name in (finish, cancel)]}

    def call(self, name: str, arguments: dict) -> dict:
        chosen = plan()
        if chosen == 'codex':
            if name == 'desktop_voice_recording':
                return self.voice_recording(arguments)
            if name in ('desktop_goal', 'desktop_voice_message', *PLAN_TWO_ONLY):
                raise ValueError('Codex mode: use desktop_screenshot and desktop_act; no API goal executor')
        if chosen in ('codex', 'luna'):
            if name == 'desktop_goal':
                return self.goal_luna(arguments)
            if name == 'desktop_screenshot':
                return self.screenshot(str(arguments.get('scope') or 'window'))
            if name == 'desktop_act':
                return self.act(list(arguments['actions']), str(arguments.get('note') or ''))
            if name == 'desktop_voice_message':
                return self.voice_message_luna(arguments)
            if name in PLAN_TWO_ONLY:
                raise ValueError(f'{name} belongs to plan two (rungic-cua plan atspi)')
        # Plan two and the shared window tools; accessibility only where plan two needs it.
        if chosen == 'atspi' or name not in ('desktop_windows', 'desktop_activate', 'desktop_window', 'desktop_launch'):
            self.backend.set_accessibility(True)
            self._touch()
        if name == 'desktop_windows':
            return self.windows()
        if name == 'desktop_activate':
            return self.activate(str(arguments['window_id']))
        if name == 'desktop_window':
            return self.window(str(arguments['window_id']), str(arguments['action']))
        if name == 'desktop_launch':
            return self.launch(str(arguments['app']), str(arguments.get('screen') or 'auto'),
                               [str(a) for a in arguments.get('args') or []], bool(arguments.get('switch')))
        if name == 'desktop_observe':
            return self.observe()
        if name == 'desktop_run':
            return self.run(arguments)
        if name == 'desktop_voice_message':
            return self.voice_message(arguments)
        if name == 'desktop_goal':
            return self.goal(arguments)
        if name == 'desktop_find_name':
            return self.find_name(str(arguments['name']))
        raise ValueError(f'Unknown tool {name}')


def serve() -> None:
    """MCP over stdio: one JSON-RPC message per line. Started in the agent's workspace (the voice
    agent's Codex), it routes each call to the user's desktop or the workspace (router.py)."""
    router = None
    if os.environ.get('RUNGIC_WORKSPACE') and not os.environ.get('RUNGIC_CUA_CHILD'):
        from .router import CLOSE_TOOL, WHERE_TOOL, Router
        from .team import TOOL as TEAM_TOOL
        router = Router()
    cua = None if router else Cua()
    out = sys.stdout

    def send(message: dict) -> None:
        out.write(json.dumps(message, ensure_ascii=False) + '\n')
        out.flush()

    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        method, rid = request.get('method'), request.get('id')
        if rid is None:
            continue  # notifications (initialized, cancelled)
        try:
            if method == 'initialize':
                version = request.get('params', {}).get('protocolVersion', '2025-06-18')
                result = {'protocolVersion': version, 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'rungic-cua', 'version': '0.1.0'}}
            elif method == 'tools/list':
                result = {'tools': tools_for(plan()) + ([WHERE_TOOL, CLOSE_TOOL, TEAM_TOOL] if router else [])}
            elif method == 'tools/call' and router:
                params = request.get('params', {})
                try:
                    result = router.call(params.get('name', ''), params.get('arguments') or {}, params.get('_meta'))
                except Exception as error:  # reported to the model, not a protocol error
                    logger.exception('tool %s failed', params.get('name'))
                    result = {'content': [{'type': 'text', 'text': f'{type(error).__name__}: {error}'}],
                              'isError': True}
            elif method == 'tools/call':
                params = request.get('params', {})
                try:
                    data = cua.call(params.get('name', ''), params.get('arguments') or {})
                    image = data.pop('__image__', None) if isinstance(data, dict) else None
                    content = [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}]
                    if image:
                        content.append({'type': 'image', 'data': image, 'mimeType': 'image/jpeg'})
                    result = {'content': content}
                except Exception as error:  # reported to the model, not a protocol error
                    logger.exception('tool %s failed', params.get('name'))
                    result = {'content': [{'type': 'text', 'text': f'{type(error).__name__}: {error}'}],
                              'isError': True}
            elif method == 'ping':
                result = {}
            else:
                send({'jsonrpc': '2.0', 'id': rid, 'error': {'code': -32601, 'message': f'Unknown method {method}'}})
                continue
            send({'jsonrpc': '2.0', 'id': rid, 'result': result})
        except Exception as error:
            send({'jsonrpc': '2.0', 'id': rid, 'error': {'code': -32603, 'message': str(error)}})
    if cua is not None:
        cua.close_recording()
    if router:
        router.close()


# Plasma Mobile's form factor, set in the phone's session (startplasmamobile).
PHONE_ONLY = ('PLASMA_DEFAULT_SHELL', 'PLASMA_PLATFORM', 'QT_QUICK_CONTROLS_MOBILE')


def import_session_environment() -> None:
    """Codex starts MCP servers with a handful of variables. Take the graphical
    session's environment from the systemd user manager, as the desktop does when
    it launches apps: without MOZ_ENABLE_WAYLAND/GDK_BACKEND, Firefox started from
    here found no display and never showed a window. In an agent workspace (docs/research/91)
    the session bus is the workspace's own, without systemd: ask on the user's (without
    XDG_DATA_DIRS kstart found no application and hung); the workspace's display and bus,
    already set, stay. In the independent desktop (workspace 0) the phone's form factor stays out,
    as rungic-workspace-env leaves it."""
    env = dict(os.environ)
    phone_only = PHONE_ONLY if env.get('RUNGIC_WORKSPACE') == '0' else ()
    if env.get('RUNGIC_USER_DBUS_SESSION_BUS_ADDRESS') or env.get('RUNGIC_WORKSPACE'):
        runtime = env.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'
        env['DBUS_SESSION_BUS_ADDRESS'] = env.get('RUNGIC_USER_DBUS_SESSION_BUS_ADDRESS') or f'unix:path={runtime}/bus'
    try:
        out = subprocess.run(['busctl', '--user', '-j', 'get-property', 'org.freedesktop.systemd1',
                              '/org/freedesktop/systemd1', 'org.freedesktop.systemd1.Manager', 'Environment'],
                             capture_output=True, text=True, timeout=5, env=env).stdout
        for item in json.loads(out)['data']:
            key, _sep, value = item.partition('=')
            if key not in phone_only:
                os.environ.setdefault(key, value)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        logger.warning('session environment unavailable: %s', error)


def main() -> None:
    import_session_environment()
    logging.basicConfig(level=os.environ.get('RUNGIC_CUA_LOG', 'WARNING'), stream=sys.stderr,
                        format='%(asctime)s %(name)s %(message)s')
    command = sys.argv[1] if len(sys.argv) > 1 else 'mcp'
    if command == 'mcp':
        serve()
        return
    if command == 'close-workspace':     # rungic-cua close-workspace [N] [--force]
        rest = [a for a in sys.argv[2:] if a != '--force']
        slot = int(rest[0] if rest else os.environ.get('RUNGIC_WORKSPACE') or 1)
        print(json.dumps(workspace.close(slot, force='--force' in sys.argv), ensure_ascii=False))
        return
    if command == 'close-windows':      # inside a workspace (rungic-workspace-env N): its apps' windows
        timeout = float(sys.argv[2]) if len(sys.argv) > 2 else workspace.CLOSE_TIMEOUT_S
        slot = os.environ.get('RUNGIC_WORKSPACE') or '1'
        from .kwin import KWin
        remaining = workspace.close_windows(KWin(), workspace.own_pids(slot), timeout,
                                            unaskable=workspace.x11_without_close())
        print(json.dumps({'remaining': remaining}, ensure_ascii=False))
        return
    if command == 'restore-apps':
        slot = int(sys.argv[2] if len(sys.argv) > 2 else os.environ.get('RUNGIC_WORKSPACE') or 1)
        print(json.dumps({'restored': switch.restore(slot), 'pending': sorted(switch.switched())}, ensure_ascii=False))
        return
    cua = Cua()
    if command == 'windows':
        data = cua.windows()
    elif command == 'observe':
        data = cua.observe()
    elif command == 'activate':
        data = cua.activate(sys.argv[2])
    elif command == 'launch':
        data = cua.launch(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else 'auto', sys.argv[4:])
    elif command == 'window':
        data = cua.window(sys.argv[2], sys.argv[3])
    elif command == 'run':
        data = cua.run(json.loads(sys.argv[2]))
    elif command == 'voice':
        args = json.loads(sys.argv[2])
        data = cua.call('desktop_voice_message', args)
    elif command == 'find-name':
        data = cua.find_name(sys.argv[2])
    elif command == 'press-control':
        data = cua.press_control(sys.argv[2], sys.argv[3:])
    elif command == 'goal':
        args = json.loads(sys.argv[2])
        data = cua.call('desktop_goal', args)
    elif command == 'screenshot':   # OUT gets the JPEG the model sees; SCOPE window (default) or screen
        data = cua.screenshot(sys.argv[3] if len(sys.argv) > 3 else 'window')
        Path(sys.argv[2]).write_bytes(__import__('base64').b64decode(data.pop('__image__')))
    elif command == 'act':
        data = cua.act(json.loads(sys.argv[2]))
        data.pop('__image__', None)
    elif command == 'plan':
        if len(sys.argv) > 2:
            try:
                save_plan(sys.argv[2])
            except ValueError as error:
                raise SystemExit(str(error)) from error
        data = {'plan': plan(), 'note': 'restart Codex (rungic-voice-agent) for its tool list to change'}
    elif command == 'top-window':       # rungic-cua top-window RESOURCE_CLASS
        data = {'window': cua.backend.kwin.top_window(sys.argv[2])}
    elif command == 'focus-showing':
        data = cua.focus_showing(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(__doc__)
    print(json.dumps(data, ensure_ascii=False, indent=1))
    if cua._backend is not None:
        cua._backend.set_accessibility(False)  # only if this run switched it on
