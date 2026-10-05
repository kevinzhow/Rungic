---
name: rungic-phone-desktop
description: >-
  Operate this phone's Linux desktop, KDE Plasma Mobile on Android, and Android device functions.
  Use for phone controls, clipboard, display/network status, settings, TV casting, recording, SMS, calls, GUI apps, windows, and screenshots.
  Use rungic-desktop MCP tools for visible app work.
---

# Phone and desktop control

Run all commands as the desktop user inside the Linux container.
Commands return JSON unless stated otherwise.

## Android device functions

Use `rungic-platform --request '<json>'`.
The Android hosting app must be in the foreground to answer.
The error “请先返回 Plasma Mobile” means the user left that app.

| Request | Effect |
|---|---|
| `{"op":"status"}` | Android device status summary. |
| `{"op":"network-get"}` | Wi-Fi/mobile network state. |
| `{"op":"display-get"}` | Display modes, refresh rate, render size. |
| `{"op":"brightness-get"}` / `{"op":"brightness","value":0.5}` | Read/set brightness. Range 0.02-1. A value of -1 selects system control. |
| `{"op":"clipboard-get"}` / `{"op":"clipboard-set","text":"..."}` | Read/set Android clipboard. |
| `{"op":"orientation","mode":"portrait"}` | Select `system`, `portrait`, or `landscape`. |
| `{"op":"vibrate"}` | Short vibration. |
| `{"op":"settings","target":"network"}` | Open an Android settings panel: `network`, `bluetooth`, `display`, `sound`, `datetime`, `location`. |
| `{"op":"cast-desktop"}` / `{"op":"cast-desktop","enabled":true}` | Read/set whether a connected TV presents the desktop. Default on. Connect TVs with `rungic-cast`. |
| `{"op":"cast-controls","mode":"touchpad"}` | Phone control mode for TV: `phone`, `touchpad`, `keyboard`. |

Read battery, CPU, memory, and storage from Linux.
Use `upower -d`, `free -h`, `df -h /`, and `/sys/class/power_supply/*`.

## TV casting: `rungic-cast`

Casting connects through Wi-Fi Display and creates the second desktop screen, `CAST-1`.
Connect or disconnect when the user requests it, for example “投屏”, “投到电视”, or “断开投屏”.

| Command | Effect |
|---|---|
| `rungic-cast connect` | Connect the last TV. Usually 5-10 s. Up to one minute immediately after disconnection. |
| `rungic-cast connect "<name>"` | Connect a named TV from the scan results. |
| `rungic-cast disconnect` | Stop casting and return windows to the phone. |
| `rungic-cast status` | Report `active_state`, `active.name`, and `reconnecting`. State 2 means connected. |
| `rungic-cast scan` | Scan reachable TVs, about 8 s. |

A failed connection prints `{"error": ...}`.
Run `rungic-cast scan`.
If the TV is absent, report that it is off or its screen-mirroring input does not accept connections.

After a TV drops the connection, the phone reconnects automatically for up to three minutes with `reconnecting: true`.
A user disconnection never triggers automatic reconnection.
This includes the command, quick-settings “投屏” button, and Android casting controls.
The quick-settings button provides the same manual operation.

## Visible apps: `rungic-desktop` MCP tools

Prefer these tools for on-screen work with ordinary pointer and keyboard input.
By default, Codex decides each step from screenshots using its current sign-in.
The tools capture images and execute actions without a separate visual model.
Ordinary desktop work does not require an OpenAI API key.
Speech synthesis and voice services still require that key.
Keep progress and verification in the originating task.

### Select the screen

See docs/research/91.
Results identify the working screen whenever it changes.

- User desktop: use it while desktop mode or TV computer mode exposes that desktop.
  Desktop mode is “桌面模式”, workspace 0 with its own KWin, user settings/files, and floating phone window.
  The user can operate it concurrently.
  Apps open there rather than on the phone's own `WL-0` screen.
  For shell commands there, use `rungic-workspace-env 0 <command>`.
- Otherwise, use the agent workspace, visible as the assistant's screen, “助理屏”.
  It has one 1920x1080 screen, separate KWin, Xwayland, and session bus.
  Apps appear there from their first frame.
  The tools present it automatically, as a floating or fullscreen phone window.
- Use `desktop_where {"target": "desktop" | "workspace" | "auto"}` when the user specifies a screen.
  The choice persists for the conversation.
  Without `target`, it reports the current screen and reason.

Use `rungic-agent-screen on|off|status|tv|notv` for the assistant's screen.
For TV presentation, run `rungic-agent-screen tv` alone.
It selects the assistant's source first and connects a TV if necessary.
`"shown_on": "tv"` establishes the selected destination.
Do not first run `rungic-cast connect`, which would initially present the user desktop.

Use `rungic-desktop-mode on|off|status` for the user desktop only when requested.
The main agent's shell runs in its workspace.
`rungic-user <command>` runs in the user's session, for example a notification.

A sub-agent gets its own workspace at its first desktop tool call.
Its shell remains in the parent's workspace.
Use `desktop_where` to get N, then `rungic-workspace-env N <command>` for its programs.
For example: `rungic-workspace-env N spectacle -b -n -f -o /tmp/shot.png`.
Sub-agents must call `desktop_close_workspace` when finished to release the workspace.

### Complete a task in default Codex mode

1. Launch the app.
2. Call `desktop_screenshot`.
3. Describe the next small action batch in `desktop_act.note`.
4. Execute the batch and inspect the returned image.
5. Continue until the authorized result is visible.

Coordinates are pixels in the latest image.
Take a fresh screenshot after popups, resizing, user takeover, or corrections.
A click or successful tool response does not establish completion.
Stop when the user stops the task.
Ask only for missing information or actions outside existing authorization.

### Optional API mode

`desktop_goal` appears only when the user selected a separate API executor, Luna or the alternate OCR path.
Then you can delegate the full goal with literal values, app, and existing authorization.
Check returned evidence and relay questions.
Do not change modes or silently use API mode after an error.

### Visible progress

The floating window presents captions on the working screen, as docs/88 describes.
`desktop_goal` writes one per step.
For `desktop_act`, supply a short `note` in the user's language.
The system also speaks these progress updates.
Perform desktop-app work visibly there rather than headless.

### Screenshot and action tools

`desktop_screenshot` returns the active window, including menus and dialogs.
Use `{"scope": "screen"}` for the complete 1920x1080 screen.

`desktop_act {"actions": [...], "note": "Open the Render menu"}` executes a short batch and returns a fresh screenshot.
A Chinese note can be `"note": "打开“渲染”菜单"`.
Supported actions are:

- `{"type": "click", "x": 700, "y": 400}`: `button` selects left/right and `keys` holds modifiers.
- `double_click`, `move`.
- `drag`: `path` contains points.
- `scroll`: `x`, `y`, and `scroll_y` in pixels, positive downward.
- `keypress`: `keys`, for example `["CTRL", "L"]` or `["ENTER"]`.
- `type`: `text` in any language, sent to the focused field.
- `wait`.

### Windows: both plans

1. `desktop_windows` lists workspace windows and the active window.
2. `desktop_launch {"app": "系统设置" | "org.kde.dolphin" | "Firefox"}` launches an app on the working screen.
   An existing app window comes to the foreground instead of launching twice.
   Use `args` for a file or options in a new window.
   Examples: `{"app": "Koko", "args": ["/home/…/Pictures/a.png"]}` and `{"app": "Blender", "args": ["--python", "/home/…/make.py"]}`.
   A visible Blender script lets the user watch construction and rendering.
   Use `desktop_activate {"window_id": ...}` to select a window.
3. Use `desktop_window {"window_id": ..., "action": "close" | "minimize" | "maximize" | "restore"}` for window-manager actions.
   Title bars belong to the window manager.
   If `still_open` remains true after close, inspect the app's question.
4. Launch GUI apps with `desktop_launch`, not shell commands.
   It waits for the window, returns its ID, and opens it on the working screen.
   Shell-launched windows appear in your workspace even while you work on the user desktop.
   If launch reports no window, check `desktop_windows` once and report the result.
   Do not retry through other entry points or prefer `kill` for visible apps.

Most apps, including Blender, Kalk, and Dolphin, use independent workspace instances.
If the user has the same file open, save a new file or tell them about your changes.
Nothing locks the shared file.

Unless the caller specifies a profile, Firefox uses a separate profile per workspace.
The wrapper copies user sign-ins and settings into that profile before launch, unless the workspace Firefox already runs.
This transfer is one-way.
Workspace sign-ins do not return to the user profile.
A later launch copies the user state again.
Copy errors do not prevent Firefox from launching.

See docs/research/97 section 19.12.

Apps with one instance per user, such as WeChat or Telegram, can require moving from the user's phone.
`desktop_launch`, or `desktop_goal` with `app`, returns `needs_confirmation` and a `question`.
Ask that question and wait for agreement.
Only then repeat with `"switch": true`.

The app closes on the phone, opens in the workspace, and automatically returns about two minutes after work ends.
Do not close the user's apps through `kill`, `pkill`, or `desktop_window`.
Never move them during a call: `blocked: in_call`.

Close apps you opened in your workspace when finished, with `desktop_window` close.
Each app consumes phone memory.

### Teams and plan two

For large independent parts requiring different skills/apps, or a requested team, read `rungic-agent-team` before using `spawn_agent`.

Plan two uses accessibility, OCR, and JEV.
Its `desktop_observe`, `desktop_run`, and `desktop_find_name` tools are absent unless selected through `rungic-cua plan atspi`.
Restart the voice assistant after selection.
Read [plan-two.md](plan-two.md) only when the user requests it or it is active.

## Blender

Use Cycles on the CPU, the phone's system default from docs/90.
New scenes use CPU Cycles, with at most half the CPU cores for each render.
Retain both defaults.
Do not select EEVEE, a GPU device, or `--gpu-backend` unless the user requests GPU rendering.
The GPU shares phone memory.
EEVEE used about 0.9 GB more than Cycles in a small scene, and memory previously ran out during rendering.

Use the user's default 512×512 resolution and 64 samples.
This took about 20 s and 0.3 GB here.
`rungic_render` does not yet denoise.
Use larger images, 900 or more, only when requested.
Time grows with pixel count.

Render visibly with the phone's `rungic_render` module rather than `bpy.ops.render.render`:

```python
import rungic_render
rungic_render.render('/home/…/Pictures/篮球.png')   # use the scene's Cycles samples in passes
```

Build the scene before calling the renderer.
For a script executed at startup, use a timer:
`bpy.app.timers.register(lambda: rungic_render.render(path) and None, first_interval=1)`.
Launch it visibly with `desktop_launch {"app": "Blender", "args": ["--python", "/home/…/make.py"]}`.

Rendering uses a background Blender to keep the visible window responsive.
The render window and chat task card present successive passes at 4, 12, 28, and 64 samples.
Wait for `<image>.status.json` with `"phase": "done"` or `"error"` before answering.
For example:
`timeout 600 sh -c 'until grep -q "\"done\"\|\"error\"" /home/…/篮球.png.status.json; do sleep 2; done'`.
Then include the image as `![…](<path>)`.

For “投到电视上看” from your workspace, run only `rungic-agent-screen tv`.
It presents the assistant's screen and render window, connecting if necessary.
From the user desktop, use `rungic-cast connect` instead.
Never close or kill a working Blender because it reports “Not Responding”.
Use the status file to judge progress.

## WeChat

Use the app's English control names instead of guessing.
A narrow window hides the chat list and search field.
The TV uses desktop-sized WeChat.
If `list 'Chats'` is absent, use `desktop_window ... maximize` first.

To open a chat:

1. Type into `Search` above the chat list.
2. Select the person under `Contacts` in the results popup.
   Alternatively, select the matching `list 'Chats'` item, whose name has the chat name as its prefix, such as `File Transfer`.
3. Check the visible chat header.

The navigation-bar `Search` button is web search, “搜一搜”, rather than contact search.
Speech recognition can substitute same-sounding characters, such as 周凯文 for 周楷雯.
Search spoken names by pinyin, for example `zhoukaiwen`, instead of recognized characters.
Select the contact whose name sounds the same.

In API mode, `desktop_goal` does this when the goal identifies a spoken name.
Its `answer` identifies the actual opened contact.
If two people match or none matches, ask the user with the found names.

The message field is editable text named after the open chat, for example `周楷雯`.
ENTER sends the message.
Check the header before typing.
Type only when authorized to send that text to that chat.

Chat controls are `Voice Call`, `Send Voice`, `Send File`, `Send`, and `Chat Info`.
`Voice Call` is in the header.
`Voice Input (Hold Ctrl+Super)` is dictation, not a voice message.
Test only with `File Transfer`, “文件传输助手”, which sends to the user's devices.
Do not test with a real contact.

## Voice messages for the user

Use this flow for requests such as “发语音” or “用语音告诉…”.

1. Resolve the spoken contact name with screenshots/actions and pinyin search.
   Check the visible chat header before sending.
   Report the contact's actual name.
2. Use only the authorized content and requested language.
   By default, use the language of the request.
   Use a short opening statement that the assistant sends the message for the user.
   Examples: `This is Kevin's AI assistant with a voice message from him: …` or `我是凯文的 AI 助理，替他发一条语音：……`.
   Do not add other content.
3. In Codex mode, follow the recording procedure below.
   In API mode, use listed `desktop_voice_message` with authorized text and the verified open chat.
   That tool uses the existing Luna/OCR workflow.
4. Report the recipient and successful sending.
   The tool returns the length.

### Codex recording procedure

1. Call `desktop_voice_recording` with `phase: "prepare"` and authorized `text` before selecting the app's recording control.
2. Inspect and select that control, such as WeChat's round Send Voice icon rather than dictation.
3. Call `phase: "status"`.
   Require audio-backend confirmation of `recording: true`.
4. Call `phase: "play"` exactly once.
   Require `played: true`.
5. Inspect the screen before selecting the recording's Send control.
   Check that the message appears.
6. Call `phase: "close"` to release routing.

On failure or uncertain playback, cancel the recording without sending and close routing.
Never retry uncertain playback.
The helper performs speech synthesis, not UI decisions.
This mode does not support hold-to-record gestures.
Stop and explain if the app requires one.

## SMS: `rungic-sms`

Use only the authorized recipient and text.
Ask when either is missing or ambiguous.
Do not ask again when the user already specified both.
Do not add recipients, promotions, or subscription commands.
An explicitly requested test can use a simple inquiry, never a purchase or subscription.

- `rungic-sms send NUMBER "TEXT"` uses the active default SMS SIM.
  `--subscription ID` selects an explicitly requested active SIM.
  Without a default, use the sole active SIM.
  Multiple active SIMs require a choice.
- `status: sent` means the radio sent every part, not that the recipient received them.
  Only `delivery: delivered` and `delivered: true` establish all delivery receipts.
  `unconfirmed` can mean the carrier supplies no receipts.
- `failed` can still include `sentParts > 0`.
  `pending`, socket timeouts, and lost responses leave the result uncertain.
  Inspect existing sent messages and report the uncertainty.
  Never resend automatically.
- `rungic-sms list --from NUMBER --box inbox --after TIMESTAMP_MS --wait 60` waits for replies at or after `submittedAt`.
  This includes replies received before the send call returned.
  `--since 120` covers the preceding 120 seconds.
  No reply produces exit 1 and `timedOut: true`, not successful receipt.
- Query only the relevant number/time range.
  Reading preserves unread flags and the Android messaging app.
  Do not mark/remove messages or change the default SMS app.
  `truncated: true` means the bounded scan did not cover everything.
  Narrow the time range before concluding there are no messages.
- Require Android SMS permissions and a live platform bridge, APK 2.31+.
  Report explicit errors.
  Do not silently select another SIM or app.

## Calls for the user

Use the requested transport.
“打电话” means SIM/telephone, and “打微信电话” means a WeChat voice call.
Retain another explicitly named app.
Check whether the requested transport works.
Do not substitute a transport because it is available or worked previously.
Resolve an ambiguous contact/number without asking for a transport choice again.

1. Run `rungic-voice-agent --call-capabilities` in the current desktop session.
   It checks backend/SIM/key prerequisites without dialing.
   An unreachable backend does not establish unsupported hardware.
   An available interface does not establish that the remote party hears the agent.
   Read capability/verification fields and report limits affecting the call.
   Do not hard-code handset, host, SIM, test number, or permanent feature absence.
2. Use `--start-call` with the authorized recipient, purpose, and transport.
   It prepares Realtime before dialing.
   Read [calls.md](calls.md) for parameters and controls.
   Do not click a dial button first or invent a telephone number.
3. Keep SIM and app calls in a card in the originating voice-assistant conversation.
   The card owns status, transcript, questions, private text, takeover/hang-up controls, and the final result.
   Leaving the assistant can retain a compact call bar.
   Returning restores the card.
   Do not replace it with a fullscreen workflow.
4. `dialed: true` acknowledges the request only.
   Live call state and remote responses establish connection and conversation success.
   Briefly report the recipient and transport after the call request.
   Then let the call agent speak.
   After timeout or lost connection, inspect the existing call.
   Never automatically redial or select another transport.

## Shell screenshots and notifications

Use `spectacle -b -n -f -o /tmp/shot.png` for a workspace screenshot.
Inspect the image before interpreting screen state.

Open URLs/files with `desktop_launch`, passing the path/URL in `args`.
Then use `desktop_act`, or `desktop_goal` when available.

Notify the user in their language with `rungic-user notify-send "Title" "Text"`.
Plain `notify-send` remains in your workspace, where the user does not read it.
Use `rungic-a11y` only for low-level debugging: apps/tree/find/act/text/windows.

## Screen recording

The quick-settings “录屏” button records the phone and the TV when casting.
It uses hardware H.264 and saves files in `~/Videos`.
There is no command-line trigger.
Direct the user to the button.

## Authorization rules

Ask before changing brightness, orientation, or casting unless the user requested the change.
Do not uninstall apps, remove user files, or change system configuration without an explicit request.
