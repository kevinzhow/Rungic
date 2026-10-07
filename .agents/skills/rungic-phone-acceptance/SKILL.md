---
name: rungic-phone-acceptance
description: >-
  Run the release acceptance checklist (docs/121) on the USB G100 like a user: look at screenshots, touch
  through ADB, check what the screen cannot show with one command, write a report with screenshots.
  Use after installing a release candidate or a development overlay, or when asked to "test on the phone".
---

# Rungic phone acceptance

The user decided on 2026-10-07: UI acceptance after a build is an agent operating the phone through the
listed checks (docs/121), not a dedicated test framework. Unit and headless system tests stay automated.

## Connect

- Phone: G100 `ZY32M9MRVP`, USB on mibook. Never touch the G100 S (`ZY32MVJS25`, the user's daily phone)
  on the same adb server; `rungic_device`'s default serial is the G100 S, so always set the serial.
- From another machine: `ssh -fN -L 127.0.0.1:15037:127.0.0.1:5037 mibook`, then `.work/device.env` with
  `RUNGIC_ADB_PORT=15037`, `RUNGIC_SERIAL=ZY32M9MRVP`, `RUNGIC_TRANSPORT=ZY32M9MRVP`.
- Check first: `adb -P 15037 devices -l` lists `ZY32M9MRVP`, and `python3 tools/rungic_agent.py exec 'id -un'` answers.

## Operate

All through `python3 tools/rungic_agent.py` (docs/121 has the table):

1. `screenshot .work/acceptance/<RUN>/<step>.png`, then look at it. The image is 1080×2400; if your viewer
   scales it, convert back to these pixels before touching.
2. `tap X Y`, `swipe X1 Y1 X2 Y2 --ms 300` (same point = long press), `key BACK|HOME|ENTER`.
3. Take a new screenshot after every action and before the next one. Do not reuse coordinates from an old
   screenshot after the screen changed.
4. `text` is for URLs and file names only. E2E-02 and E2E-08 test the on-screen keyboard: touch its keys.
5. `exec 'CMD'` (desktop user; `--as shell` for Android's shell user, `--as root`) only checks a result.
   Opening apps, typing and saving happen on the screen.

## Judge and report

- Pass only when you see what the checklist says. Unsure or not visible = fail, with the screenshot.
- Keep a failure as it happened. A retry is a second attempt, reported separately.
- Close what you opened; never close or change the user's own windows and files. Delete only `rungic-e2e-<RUN>`.
- Write `.work/acceptance/<RUN>/report.md` (template at the end of docs/121): one row per item, result,
  what you saw, screenshot file. End with failures, items not run and why, and changes since the last run.
- Destructive steps (uninstall with `--purge`, reinstall, reboot) need the user's go-ahead for this run.
