"""Computer use with GPT-6 Luna (docs/68): the model sees the screen and decides where to act.

The explicit API mode of rungic-cua. No accessibility tree and no OCR: every step is a screenshot of
the window the task is about, as KWin renders it (with its open popups and dialogs; the whole
assistant's screen when there is no window or the model asks for it: `Screen`), sent to the
Responses API with the `computer` tool, and the model answers
with a batch of mouse and keyboard actions in that screenshot's pixels. They are carried out with
the same input as everywhere in rungic-cua (the RemoteDesktop portal; text through KWin's input-method
commit, so any script types) and the next screenshot goes back, until the model stops.

API (checked 2026-09-25, developers.openai.com guides/tools-computer-use and -integration):
  tools [{"type": "computer"}]; output items `computer_call` with `actions` (click, double_click,
  drag, move, scroll, keypress, type, wait, screenshot; mouse actions may carry held `keys`);
  answered by `computer_call_output` {"type": "computer_screenshot", "image_url", "detail":
  "original"} with previous_response_id. gpt-6-luna lists computer use as supported.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import activity
from .i18n import _, language_name
from .screen import Desktop, SETTLE_S

logger = logging.getLogger('rungic-cua.luna')
MODEL = os.environ.get('RUNGIC_CUA_MODEL', 'gpt-6-luna')
EFFORT = os.environ.get('RUNGIC_CUA_EFFORT', 'low')
API = 'https://api.openai.com/v1/responses'
VIEW_WHOLE_SCREEN = {
    'type': 'function', 'name': 'view_whole_screen',
    'description': ('The screenshots show only the window you work in (with its open menus and dialogs). Call this '
                    'to see the whole screen instead: the taskbar, the desktop or other windows.'),
    'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
}
TOOLS = [{'type': 'computer'}, VIEW_WHOLE_SCREEN]
ABORT_FILE = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'rungic-clicker' / 'abort'
# Phone-mode workers have a private cancellation domain. Legacy stop files must
# never cancel an unrelated native task (the wrapper revokes its own lease).
if os.environ.get('RUNGIC_TASK_ID'):
    ABORT_FILE = ABORT_FILE.parent / ('abort-' + os.environ['RUNGIC_TASK_ID'])

INSTRUCTIONS = """You operate a Linux desktop (KDE Plasma) for the user through the computer tool. The screen \
you see is the assistant's own screen, 1920x1080; the user watches it. Coordinates are pixels of the \
screenshot you were given.

- The screenshots show the window you work in (with its open menus and dialogs), not the whole \
screen; call view_whole_screen when you need the taskbar, the desktop or another window. The image \
size changes when the view does; always use the pixels of the latest screenshot.
- Work like a careful person: look, act, look again. Keep each batch of actions short; after anything \
that changes the screen (opening, clicking a list item, sending), look before going on.
- `type` writes the text into the focused field in any language (Chinese included); click the field \
first. Use keypress for Enter, shortcuts and navigation.
- Names of people come from speech recognition and may be written with wrong characters of the same \
sound (周凯文 for 周楷雯). Search contacts by the name's pinyin without tones or spaces (zhoukaiwen), \
then take the person whose name sounds the same. If two different people fit, or none, stop and ask.
- Do only what the task asks. Send, pay, delete or change accounts only when the task says so \
explicitly (the user has confirmed it). Never type passwords; if one is needed, stop and ask.
- With every batch of actions, also write one short phrase saying what the batch does, in the \
language the task is written in; when the task is an instruction written in English by the system, \
in the desktop's language (below). A few words: at most 15 characters in Chinese, e.g. Open the File \
menu, 打开“文件”菜单 or 在搜索框输入 zhoukaiwen. The user sees it as a live caption on the screen they \
watch.
- When you stop, reply with one short message that begins with DONE, ASK or FAILED: DONE and what the \
screen now shows about the task; ASK and the one question only the user can answer; FAILED and why."""


def instructions() -> str:
    """INSTRUCTIONS and the desktop's language (for the captions), as the session has it now."""
    return f"{INSTRUCTIONS}\n\nThe desktop's language: {language_name()}."


class Aborted(RuntimeError):
    pass


def api_key() -> str:
    if os.environ.get('OPENAI_API_KEY'):
        return os.environ['OPENAI_API_KEY']
    from . import keys
    return keys.read('openai-api-key')


class ComputerUse(Desktop):
    # ---- the model ----------------------------------------------------------------------
    def _respond(self, body: dict) -> dict:
        request = urllib.request.Request(API, data=json.dumps(body).encode(), headers={
            'Authorization': 'Bearer ' + api_key(), 'Content-Type': 'application/json'})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as error:
                detail = error.read().decode(errors='replace')[:600]
                if error.code >= 500 and attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise RuntimeError(f'Responses API {error.code}: {detail}') from error
            except urllib.error.URLError as error:
                if attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise RuntimeError(f'Responses API unreachable: {error.reason}') from error
        raise RuntimeError('Responses API failed')

    # ---- the loop -------------------------------------------------------------------------
    def run(self, task: str, *, max_steps: int = 40, timeout_s: float = 280, stop=None, gate=None) -> dict:
        """Work on `task` until the model stops. `stop()` returning true ends the run early
        (a caller's own completion signal); the abort file ends it too (the user said stop).
        `gate` (a threading.Event): the model may look and decide meanwhile, but no action is
        carried out before it is set (a voice message's send waits for the speech to end)."""
        started = time.monotonic()
        steps: list[dict] = []
        caption_task = task.strip().splitlines()[0] if task.strip() else ''
        activity.report(_('Look at the screen'), task=caption_task)
        self.screen.whole = False
        image_url, _image, _changed = self.screen.capture()
        body = {'model': MODEL, 'tools': TOOLS, 'instructions': instructions(),
                'reasoning': {'effort': EFFORT}, 'truncation': 'auto',
                'input': [{'role': 'user', 'content': [
                    {'type': 'input_text', 'text': f'{task}\n\n{self.screen.note()}'},
                    {'type': 'input_image', 'image_url': image_url, 'detail': 'original'}]}]}
        model_s = 0.0

        def elapsed() -> float:
            return round(time.monotonic() - started, 1)

        while True:
            t0 = time.monotonic()
            response = self._respond(body)
            model_s += time.monotonic() - t0
            output = response.get('output', [])
            calls = [item for item in output if item.get('type') == 'computer_call']
            functions = [item for item in output if item.get('type') == 'function_call']
            text = ' '.join(c.get('text', '') for item in output if item.get('type') == 'message'
                            for c in item.get('content', []) if c.get('type') == 'output_text').strip()
            if not calls and not functions:
                result = self._result(text, steps, started, model_s)
                activity.report(result.get('answer') or result.get('question') or '', state=result['outcome'],
                                task=caption_task)
                return result
            follow: list[dict] = []
            for function in functions:
                if function.get('name') == 'view_whole_screen':
                    self.screen.whole = True
                    steps.append({'actions': ['view whole screen']})
                follow.append({'type': 'function_call_output', 'call_id': function['call_id'],
                               'output': 'The next screenshots show the whole screen; take one to look.'})
            call = calls[0] if calls else None
            if call and call.get('pending_safety_checks'):
                # The API wants the user to confirm this action; we cannot ask mid-run.
                activity.report(_('Needs your confirmation'), state='question', task=caption_task)
                return {'outcome': 'question', 'question': '; '.join(c.get('message', '') for c in call['pending_safety_checks']),
                        'safety_checks': call['pending_safety_checks'], 'steps': steps, 'elapsed_s': elapsed()}
            if call:
                done_actions = []
                actions = call.get('actions') or ([call['action']] if call.get('action') else [])
                # The caption the user watches (docs/88): the model's phrase, else what the batch does.
                shown = [a for a in actions if a.get('type') != 'screenshot'] or actions
                activity.report(text or (activity.describe(shown[0]) if shown else _('Look at the screen')),
                                task=caption_task)
                for action in actions:
                    if gate is not None and action.get('type') != 'screenshot':
                        gate.wait(timeout_s)
                    if ABORT_FILE.exists():
                        ABORT_FILE.unlink(missing_ok=True)
                        activity.report(_('Stopped'), state='stopped', task=caption_task)
                        return {'outcome': 'stopped', 'steps': steps, 'elapsed_s': elapsed()}
                    try:
                        done_actions.append(self.execute(action))
                    except (ValueError, KeyError) as error:
                        done_actions.append(f'{action.get("type")}: skipped ({error})')
                    if stop and stop():
                        steps.append({'actions': done_actions})
                        activity.report('', state='done', task=caption_task)
                        return {'outcome': 'signalled', 'steps': steps, 'elapsed_s': elapsed()}
                steps.append({'actions': done_actions, 'note': text} if text else {'actions': done_actions})
            if len(steps) >= max_steps or time.monotonic() - started > timeout_s:
                activity.report(_("Didn't finish within the step limit"), state='failed', task=caption_task)
                return {'outcome': 'unfinished', 'note': f'stopped after {len(steps)} steps', 'steps': steps,
                        'elapsed_s': elapsed()}
            if not call:
                # Only view_whole_screen was called: after the first request the API takes images only
                # as computer_call_output, so the model asks for the screenshot next.
                body = {'model': MODEL, 'tools': TOOLS, 'instructions': instructions(), 'reasoning': {'effort': EFFORT},
                        'truncation': 'auto', 'previous_response_id': response['id'], 'input': follow}
                continue
            time.sleep(SETTLE_S)
            if stop and stop():
                activity.report('', state='done', task=caption_task)
                return {'outcome': 'signalled', 'steps': steps, 'elapsed_s': elapsed()}
            image_url, image, changed = self.screen.capture()
            steps[-1]['saw'] = f'{self.screen.scope} {image.width}x{image.height}' if steps else ''
            screenshot = {'type': 'computer_screenshot', 'image_url': image_url, 'detail': 'original'}
            items = list(follow)
            if call:
                items.insert(0, {'type': 'computer_call_output', 'call_id': call['call_id'], 'output': screenshot})
                if changed:
                    items.append({'role': 'user', 'content': [{'type': 'input_text', 'text': self.screen.note()}]})
            body = {'model': MODEL, 'tools': TOOLS, 'instructions': instructions(),
                    'reasoning': {'effort': EFFORT}, 'truncation': 'auto', 'previous_response_id': response['id'],
                    'input': items}

    @staticmethod
    def _result(text: str, steps: list, started: float, model_s: float) -> dict:
        match = re.match(r'\s*(DONE|ASK|FAILED)\b\s*[:：,，.。\-—–]*\s*(.*)', text, re.S | re.I)
        word = match.group(1).upper() if match else 'DONE'
        outcome = {'DONE': 'done', 'ASK': 'question', 'FAILED': 'failed'}[word]
        body = match.group(2).strip() if match else text
        result = {'outcome': outcome, 'achieved': outcome == 'done', 'steps': steps,
                  'elapsed_s': round(time.monotonic() - started, 1), 'model_s': round(model_s, 1)}
        result['question' if outcome == 'question' else 'answer'] = body
        return result
