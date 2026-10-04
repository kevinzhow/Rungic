---
name: rungic-phone-desktop
description: "Operate this phone's Linux desktop (KDE Plasma Mobile on Android) and Android-side device functions - brightness, clipboard, orientation, vibration, network/display info, Android settings panels, TV casting, screen recording, phone/SIM and application voice calls, operating GUI apps (rungic-desktop MCP tools: launch, observe, run UI subtasks), windows and screenshots. Use whenever a request is about the phone, the desktop, apps on screen, or the TV."
---

# Phone and desktop control

All commands run as the desktop user inside the Linux container. Output is JSON unless noted.

## Android-side device functions: `rungic-platform --request '<json>'`

The Android app that hosts the desktop answers these (it must be in the foreground; an error "请先返回 Plasma Mobile" means the user left the app).

| Request | Effect |
|---|---|
| `{"op":"status"}` | Android device status summary |
| `{"op":"network-get"}` | Wi-Fi / mobile network state |
| `{"op":"display-get"}` | phone display modes, refresh rate, render size |
| `{"op":"brightness-get"}` / `{"op":"brightness","value":0.5}` | read / set screen brightness (0.02-1, -1 = system) |
| `{"op":"clipboard-get"}` / `{"op":"clipboard-set","text":"..."}` | Android clipboard |
| `{"op":"orientation","mode":"portrait"}` | `system`, `portrait` or `landscape` |
| `{"op":"vibrate"}` | short vibration |
| `{"op":"settings","target":"network"}` | open an Android settings panel: `network`, `bluetooth`, `display`, `sound`, `datetime`, `location` |
| `{"op":"cast-desktop"}` / `{"op":"cast-desktop","enabled":true}` | whether a connected TV shows the Linux desktop (on by default; to connect a TV use `rungic-cast`) |
| `{"op":"cast-controls","mode":"touchpad"}` | phone as TV touchpad: `phone`, `touchpad`, `keyboard` |

Battery, CPU, memory and storage come from Linux: `upower -d`, `free -h`, `df -h /`, `/sys/class/power_supply/*`.

## TV casting: `rungic-cast`

Casting connects the phone to the TV over Wi-Fi Display; the TV then becomes the desktop's second screen (`CAST-1`). Run it yourself when the user asks to cast or stop casting ("投屏", "投到电视", "断开投屏").

| Command | Effect |
|---|---|
| `rungic-cast connect` | connect the TV used last (about 5-10 s; up to a minute if the TV was just disconnected) |
| `rungic-cast connect "<name>"` | a specific TV, by name as `rungic-cast scan` lists it |
| `rungic-cast disconnect` | stop casting; windows return to the phone |
| `rungic-cast status` | `active_state` 2 = connected, `active.name`, `reconnecting` |
| `rungic-cast scan` | TVs in reach (about 8 s) |

- A failed connect prints `{"error": ...}`: run `rungic-cast scan`; if the TV is not listed, it is off or its screen-mirroring input is closed; say so.
- When the TV drops the session by itself, the phone reconnects automatically for up to three minutes (`reconnecting: true`). A disconnect by the user (this command, the quick-settings "投屏" button, Android's cast controls) is never reconnected.
- The quick-settings "投屏" button does the same by hand.

## Operating apps on screen: the `rungic-desktop` MCP tools (preferred)

Use these tools for anything on screen; they act with ordinary pointer/keyboard input. By default **you decide each step from screenshots**, using Codex and its current sign-in. Keep progress and verification in the originating task. The desktop tools capture and execute; they do not call a separate visual model in this mode. Do not require an OpenAI API key for ordinary desktop work. Speech synthesis and voice services still need that key.

**Which screen** (docs/research/91): the tools below act on one of two screens, and a result says where whenever that changes.
- **The user's desktop** while the user has it out: **desktop mode** (桌面模式: the user's own full desktop, workspace 0, a KWin of its own with the user's settings and files, in a floating window on the phone) is on, or the TV shows it (computer mode). The user is at that screen and may use it too. Apps open there, never on the phone's own screen (`WL-0`); from your shell, `rungic-workspace-env 0 <command>`.
- Otherwise your own **workspace**, which the user sees as the **assistant's screen** (助理屏): one screen (1920x1080), its own KWin, Xwayland and session bus, in a floating window on the phone or fullscreen. Whatever you open appears there from its first frame; the tools show it by themselves.
- `desktop_where {"target": "desktop" | "workspace" | "auto"}` when the user says where to work; it holds for this conversation. Without `target` it tells where you work and why.
- `rungic-agent-screen on|off|status|tv|notv` for the assistant's screen. `tv`: on the TV instead of the desktop, in one step: it sets the TV's source first and connects the TV if none is, so run it alone (not after `rungic-cast connect`, which would show the desktop first); its `"shown_on": "tv"` confirms it. `rungic-desktop-mode on|off|status` for desktop mode: the user's, only when they ask. Your shell always runs in your workspace; `rungic-user <command>` runs a command in the user's session (a notification, say). A sub-agent (`spawn_agent`) gets a workspace of its own at its first desktop tool call, but its shell stays in the parent's: `desktop_where` tells its number N, and `rungic-workspace-env N <command>` runs a program there (a screenshot: `rungic-workspace-env N spectacle -b -n -f -o /tmp/shot.png`). A sub-agent closes its workspace (`desktop_close_workspace`) when its part is done, which frees it for other agents.

**Whole tasks (Codex, default):** launch the app, call `desktop_screenshot`, explain the next small batch with `desktop_act.note`, execute it and inspect the returned image. Continue until the authorized result is visible. Coordinates are pixels of the latest image; after a popup, resize, user takeover or correction, take a fresh screenshot. A click or successful tool call does not establish completion. Stop when the user stops the task. Ask only for missing information or an action outside existing authorization.

**Optional API mode:** only when `desktop_goal` is listed, the user has selected a separate API executor (Luna, or the alternate OCR path). You may then delegate a whole goal, including literal values, app and existing authorization. Verify the returned evidence and relay any question. Do not change modes or silently use API mode to recover from an error.

**The user watches** (docs/88): the floating window shows a live caption of what is being done on the screen you work on (`desktop_goal` writes one per step by itself; give `desktop_act` a `note`, a few words in the user's language), and it is spoken as progress. So do desktop-app work there, visibly, not headless.

**Look and act yourself** (a single known step, or checking a result):
- `desktop_screenshot` returns the active window on the screen you work on as an image (with its open menus and dialogs); `{"scope": "screen"}` for the whole screen (1920x1080).
- `desktop_act {"actions": [...], "note": "Open the Render menu"}` (in Chinese `"note": "打开“渲染”菜单"`) carries out a short batch in that image's pixels and returns the new screenshot. Actions: `{"type": "click", "x": 700, "y": 400}` (`button` left/right, `keys` held modifiers), `double_click`, `move`, `drag` (`path` of points), `scroll` (`x`, `y`, `scroll_y` in pixels, positive = down), `keypress` (`keys`: `["CTRL", "L"]`, `["ENTER"]`), `type` (`text`, any language, into the focused field), `wait`.

**Windows** (the window manager, not the app; same in both plans):
1. `desktop_windows` - the open windows of your workspace and which one is active.
2. `desktop_launch {"app": "系统设置" | "org.kde.dolphin" | "Firefox"}` starts an app on the screen you work on and shows it; an app already open is brought forward instead of starting twice. `"args"` opens files or passes options in a new window: `{"app": "Koko", "args": ["/home/…/Pictures/a.png"]}`, `{"app": "Blender", "args": ["--python", "/home/…/make.py"]}` (a script run in the visible Blender: the user watches it build and render). `desktop_activate {"window_id": ...}` brings one to the front.
   - Most apps simply start an instance of their own in your workspace, whatever runs on the user's phone (Blender, Kalk, Dolphin ...). If the user has the same file open there, save your changes to a new file or tell them: nothing locks it.
   - An app that runs once per user (WeChat, a browser profile, Telegram) and is open on the user's phone has to move: `desktop_launch` (and `desktop_goal` with `app`) then returns `needs_confirmation` with a `question`. Ask the user exactly that and wait; only if they agree, call again with `"switch": true`. It is closed on the phone, opened in your workspace, and given back to the phone by itself about two minutes after your work ends. Never close the user's apps any other way (`kill`, `pkill`, `desktop_window`), and never during a call (`blocked: in_call`).
   - Close apps you opened in your workspace when you are done with them (`desktop_window` close): each costs the phone memory.
3. `desktop_window {"window_id": ..., "action": "close" | "minimize" | "maximize" | "restore"}`. The title bar belongs to the window manager: close windows this way. If `still_open` stays true after close, the app is asking something: look at it.
4. Start GUI apps with `desktop_launch`, not from the shell: it waits for the window, returns its id and opens it where you work (a program started from the shell opens in your workspace, also while you work on the user's desktop). If it reports no window, check `desktop_windows` once and tell the user instead of retrying other ways. Also prefer these tools over `kill` or similar for apps on screen.

**A team of sub-agents**: when a task has parts that need different skills or apps and can be made at the same time (a game's code, art and sound) and is large enough, or the user asks for a team, use the `rungic-agent-team` skill before using `spawn_agent`.

**Plan two** (accessibility tree + OCR + JEV: `desktop_observe`, `desktop_run`, `desktop_find_name`) is not the default and its tools are not listed unless it was chosen (`rungic-cua plan atspi`, then the voice assistant restarts). Only when the user asks for it or it is active: read `plan-two.md` next to this file.

## Blender (3D models, rendering)

- Render with **Cycles on the CPU**. That is this phone's system default (docs/90): new scenes are Cycles on the CPU, and every render uses at most half the CPU cores so the phone stays usable; leave both as they are. Do not switch to EEVEE, a GPU device or pass `--gpu-backend` unless the user asks for GPU rendering: the GPU shares the phone's memory (EEVEE took about 0.9 GB more than Cycles for a small scene, and memory once ran out mid-render).
- Keep it light: render at **512×512** by default (the user's choice), 64 samples: about 20 s and 0.3 GB here (rungic_render does not denoise yet). Go larger (900 or more) only when the user asks for a bigger image; the time grows with the pixel count.
- Run it where the user watches, and render with `rungic_render` (a module of this phone's Blender, docs/90), never with `bpy.ops.render.render`:
  ```python
  import rungic_render
  rungic_render.render('/home/…/Pictures/篮球.png')   # the scene's Cycles samples, in passes
  ```
  Build the scene in the script, then call it (from a timer if the script runs at start: `bpy.app.timers.register(lambda: rungic_render.render(path) and None, first_interval=1)`), and start the script in the visible Blender: `desktop_launch {"app": "Blender", "args": ["--python", "/home/…/make.py"]}`.
  - The render runs in a background Blender, so the window stays responsive. Blender's render window on the screen you work on shows each pass as it comes (the picture sharpens: 4, 12, 28, 64 samples), and so does the task card in this chat, at the same time.
  - Wait for `<image>.status.json` to say `"phase": "done"` (or `"error"`) before you answer, e.g. `timeout 600 sh -c 'until grep -q "\"done\"\|\"error\"" /home/…/篮球.png.status.json; do sleep 2; done'`. Then show the image in your answer as `![…](<path>)`.
- "投到电视上看": working in your workspace (the render is on the assistant's screen), run only `rungic-agent-screen tv`: the TV shows that screen, render window included, connected if needed. Working on the user's desktop, `rungic-cast connect` (the TV shows the desktop).
- Never close or kill a Blender that shows "Not Responding" while it works; judge by the status file.

## WeChat (微信) on screen

WeChat exposes its controls; use these names instead of guessing (English UI names):

- Layout: a narrow window hides the chat list and its search box. On the TV WeChat opens desktop-sized; if the chat list (`list 'Chats'`) is missing, `desktop_window ... maximize` first.
- Open a chat: type into the text field named `Search` at the top of the chat list, then choose the person under `Contacts` in the results popup (or click the item in `list 'Chats'`, whose name starts with the chat's name, e.g. `File Transfer`). The navigation-bar button `Search` is WeChat's web search (搜一搜), not contact search.
- Names the user SAID are unreliable: speech recognition picks characters of the same sound (周凯文 for 周楷雯). Never search by the recognized characters: search the pinyin (`zhoukaiwen`; WeChat searches pinyin) and take the contact whose name sounds the same. `desktop_goal` does this by itself when the goal says the name came from speech; its `answer` names the contact actually opened. If two different people fit, or none, ask the user (say the names you found).
- The message box is the editable text named after the open chat (e.g. `周楷雯`). ENTER there sends. Check the chat header name first, and never type into it unless the goal is to send that text to that chat.
- In a chat: `Voice Call` (chat header), `Send Voice`, `Send File`, `Send`, `Chat Info`. `Voice Input (Hold Ctrl+Super)` is speech-to-text, not a voice message.
- Test on `File Transfer` (文件传输助手, messages go to the user's own devices), never on a real contact.

## Voice messages on the user's behalf (语音代发)

When the user asks you to send a voice message (发语音, 用语音告诉…):

1. Open the chat by the SOUND of the name using screenshots and actions (see the WeChat section). Search by pinyin and verify the contact in the visible chat header before sending. Report the contact's real name.
2. The content is what the user asked to say, in the language they want it said (by default the one they asked in). Begin it with a short note in that language that the assistant sends it for the user, e.g. `This is Kevin's AI assistant with a voice message from him: …` or `我是凯文的 AI 助理，替他发一条语音：……`. Do not add anything the user did not ask for.
3. **Codex mode:** call `desktop_voice_recording` with `phase: "prepare"` and the authorized `text` before clicking the voice-message record control. Look and click that control (WeChat's round Send Voice icon, not dictation). Call `phase: "status"` and require `recording: true`, confirmed by the audio backend. Call `phase: "play"` exactly once and require `played: true`. Then inspect the screen, click the recording's Send control and verify that the message appears. Finally call `phase: "close"` to release routing. On failure or uncertain playback, cancel the app's recording without sending and close routing; never retry uncertain playback. The helper never decides UI actions; its API use is speech synthesis only. Hold-to-record gestures are unsupported here: stop and explain if the app needs one.
   **API mode:** when `desktop_voice_message` is listed, it runs the existing Luna/OCR recording workflow. Use it with the authorized text and verified chat open.
4. Report that it was sent (the tool returns the length) and to whom.

## SMS through Android: `rungic-sms`

Use the recipient and text the user authorized. Ask for either when missing or ambiguous; do not ask again when both are already established. Never add recipients, promotional text, or subscription commands. An explicitly requested test may use a simple inquiry, never a purchase or subscription.

- `rungic-sms send NUMBER "TEXT"` uses the active default SMS SIM. `--subscription ID` selects an explicitly requested active SIM; with no default, the only active SIM is used, and multiple active SIMs require a choice.
- `status: sent` means all parts were sent by the radio. It does not mean delivery: only `delivery: delivered` with `delivered: true` confirms all receipts. `unconfirmed` can mean the carrier does not provide delivery receipts.
- `failed` may still have `sentParts > 0`; `pending`, socket timeouts and lost responses leave the outcome uncertain. Inspect the existing sent messages and tell the user; never resend automatically.
- `rungic-sms list --from NUMBER --box inbox --after TIMESTAMP_MS --wait 60` waits for a reply at or after the send result's `submittedAt`, including a reply arriving before the send call returned. `--since 120` means the preceding 120 seconds. A wait with no reply exits 1 with `timedOut: true`; it is not successful receipt.
- Query only the relevant number/time range. Reading preserves unread flags and the Android messaging app; do not mark, delete or change the default SMS app. `truncated: true` means the bounded scan did not cover all messages; narrow the time range before concluding none exist.
- These commands require the Rungic Android app's SMS permissions and a live platform bridge (APK 2.31+). Errors are explicit; do not silently use another SIM or messaging app.

## Calls on the user's behalf (通话代理)

Choose the transport from the user's request: **“打电话” means a SIM/telephone call; “打微信电话” means a WeChat voice call.** An explicitly named other app stays that app. Check capabilities to see whether that choice can work; never switch transports because another one is available, was used last time, or worked on an earlier device. If the requested contact/number is ambiguous, resolve that detail; do not ask the user to choose again when they already specified it.

1. Run `rungic-voice-agent --call-capabilities` in the current desktop session. This reads current backend/SIM/key prerequisites without dialing. Unreachable is not proof of unsupported hardware; interface availability is not proof the remote party can hear the agent. Consult the returned verification/capability fields and report a limitation when it affects the requested call. Do not hard-code a handset, host, SIM, test number, or a permanent lack of a feature into your decision.
2. Use `--start-call` for the requested transport, with the recipient and purpose the user authorized. It prepares Realtime before dialing. See [calls.md](calls.md) for parameters and controls. Do not click a dial button first or fabricate a contact's phone number.
3. A call appears as a **card in the originating voice-assistant conversation**, for both SIM and app calls. The card owns its status, transcript, questions, private text instructions, takeover/hang-up controls and final result. Leaving the assistant can keep a compact call bar; returning restores the card. Do not replace the card with a full-screen workflow.
4. `dialed: true` acknowledges the outgoing request, not connection or a successful conversation. Let the live call state and remote responses establish those. After starting, briefly tell the user which recipient and transport is calling, then let the call agent speak. On a timeout or lost connection inspect the existing call; never automatically redial or fall back to a different transport.

## Desktop windows and screenshots (shell)

- Screenshot of your workspace: `spectacle -b -n -f -o /tmp/shot.png`. Look at the image to understand what is on screen.
- To open a URL or file, launch the app with `desktop_launch` (`args` with the path or URL) and use `desktop_goal` or `desktop_act`.
- Send the user a notification, in the user's language: `rungic-user notify-send "Title" "Text"` (plain `notify-send` would stay in your workspace, where nobody reads it).
- Low-level AT-SPI tool for debugging only: `rungic-a11y` (apps/tree/find/act/text/windows).

## Screen recording

The quick-settings "录屏" button records the phone and, if cast, the TV (hardware H.264, files in `~/Videos`). There is no command-line trigger; tell the user to use the button.

## Rules

- Ask before changing brightness/orientation or casting state unless the user asked for it.
- Do not uninstall apps, delete user files or change system configuration without an explicit request.
