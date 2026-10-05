# Shared call interface

Run these commands in the current Linux desktop as its user.
Use the transport the user requested.
Examples describe parameter shapes only.
Replace recipient and goal with the actual authorized request.
Examples do not authorize calls to their recipients.

## Phone / SIM

`rungic-voice-agent --start-call '{"backend":"cellular","number":"<verified telephone number>","contact":"<display name>","goal":"<authorized purpose>"}'`

Telecom supplies voice SIM accounts.
Omit `account` to use the configured outgoing voice default or sole account.
If the backend reports `select-voice-sim`, read accounts from `--call-capabilities` and ask the user to choose.
Do not infer the voice SIM from mobile data, slot number, handset model, or a previous session.

## App voice call

`rungic-voice-agent --start-call '{"backend":"app","app":"<actual process binary>","contact":"<verified contact>","goal":"<authorized purpose>","dial":"<voice call control>"}'`

Open the correct contact's chat on the assistant's screen first.
Check its header.
`app=wechat` and `dial=Voice Call` describe the existing WeChat path, not defaults for every call.
Another app requires its actual binary, visible call control, and working shared audio routing.
Finding the app or router alone does not establish compatibility.

For an existing app call, omit `dial`.
`incoming=true` identifies a call from the other party.
The current cellular entry makes outgoing calls.
The app API does not establish cellular incoming-answer support.

## During either call

Both transports create the same card.
It shows the requested channel and recipient.
Elapsed time begins at connection.
Transcript, errors, questions, and result remain with that card and conversation across chat changes.

`rungic-voice-agent --call-text '<instruction>'` sends private text to the active call agent.
The card also provides this control.
Read active-backend capabilities before promising private voice instructions or independent monitoring.
Another transport's capabilities do not establish support here.

`rungic-voice-agent --call-command take-over` releases agent audio for the user.
`--call-command hang-up` requests call termination.
Wait for backend confirmation before reporting that the call ended.

For cellular menus, use `rungic-voice-agent --call-dtmf '<one digit or * or #>'`.
Follow the user's goal and the actual remote prompt.
Do not invent a predetermined menu sequence.

The Realtime conversation and call controller have separate capabilities.
An OpenAI key does not establish working remote audio, private microphone isolation, or autonomous call supervision.
Use current capabilities, verification information, and actual results.
