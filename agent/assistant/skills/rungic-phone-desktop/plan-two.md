# Plan two: accessibility, OCR, and JEV

Plan two is not the default.
Its `rungic-desktop` tools appear only after `rungic-cua plan atspi` and a voice-assistant restart.
`rungic-cua plan luna` restores plan one, with screenshots and GPT-6 Luna.

Plan two reads the accessibility tree, AT-SPI, and OCR.
The JEV executor selects each step.
Apps must expose their controls.
Some Electron/Flatpak apps and games do not expose them and remain invisible to this path.

## Whole tasks: `desktop_goal`

Prefer `desktop_goal` for tasks with several steps.
Give the user's goal, every literal value, and the app.
For example: `{"goal": "在文件传输助手里发一条消息：今晚七点见", "app": "微信"}`.
JEV selects clicks, scrolling, and keys from the screen.
A writer model supplies text.

Results include `outcome`, `achieved`, `answer`, and `steps`.
`answer` describes visible evidence for the goal.
For outcome `question`, ask the returned question.
Then repeat the same goal with `replies: [{"question": ..., "answer": ...}]`.

Require user authorization for goals that send, pay, remove data, or change accounts.
Do not ask again when existing explicit authorization covers the action.
Use step tools for one known action or after `desktop_goal` cannot complete the task.

## Step tools

1. `desktop_windows` lists windows, the active window, and screens: `WL-0` phone and `CAST-1` TV.
2. Start apps with `desktop_launch {"app": "系统设置" | "org.kde.dolphin" | "Firefox"}`.
   While the assistant's screen is active, apps open there.
   Run `rungic-agent-screen on` first if necessary.
   `"screen": "phone"` overrides this selection.
   An existing app comes to the foreground on that screen.
   Alternatively, select it with `desktop_activate {"window_id": ...}`.

   UI tools act on the active window.
3. Use optional `desktop_observe` to inspect active-window controls: role, name, value, and state.
4. Use `desktop_run` for one bounded UI subtask with JEV.
5. Use screenshots and report visible limits when apps expose few controls.

For window close/minimize/maximize/restore or movement to phone/TV, use `desktop_window {"window_id": ..., "action": "close"}` with the appropriate action.
Do not use `desktop_run` for title-bar buttons.
They belong to the window manager rather than app controls.
If `still_open` remains true, inspect the app's question.

A bounded subtask example:

```json
{"goal": "Search System Settings for the query", "verification": ["The search field contains the query", "Results are listed"], "inputs": {"query": "声音"}, "max_actions": 8}
```

Put every literal text value in `inputs`.
The executor does not invent text.
Make `verification` observable in the UI.
Divide long tasks into bounded subtasks.

- `SUBTASK_COMPLETE`: the subtask finished.
- `NEEDS_AGENT`: inspect `final_window` or a screenshot before choosing the next subtask.
- `BLOCKED`: there is no available continuation.

Require explicit user authorization before a subtask removes data, sends, publishes, pays, or changes an account.
Do not add an unauthorized action to the goal.

Do not start GUI apps through shell commands such as `firefox &`, `xdg-open`, or `kstart`.
Those windows open on the active screen, usually the phone showing the assistant.
Use `desktop_launch`.
If it reports no window, check `desktop_windows` once and report the result instead of retrying another method.
Prefer desktop tools over `kill` for visible apps.

## WeChat

Speech recognition can substitute same-sounding names, such as 周凯文 for 周楷雯.
Do not search recognized characters directly.

1. Call `desktop_find_name {"name": "<as heard>"}`.
2. Type returned `search_text` into `Search`.
   It contains pinyin, for example `zhoukaiwen`, which WeChat can search.
3. Call `desktop_find_name` again on the results.
4. Select a matching entry with `section` equal to `Contacts`.
   Score 1.0 indicates the same sound.
   Score 0.9 allows accent differences.
   Ignore `Internet search results`.
5. If two different people score at least 0.9, or none matches, ask the user with the found names.

For voice messages, use `desktop_voice_message {"text": ..., "start": "Send Voice", "finish": "Send voice message", "cancel": "Cancel"}`.
The tool switches only this app's microphone to the Linux microphone for recording, then restores it.
It never sends the user's real microphone.
For hold-to-record apps, pass `"hold": true` and only `start`.
