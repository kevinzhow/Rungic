---
name: rungic-agent-team
description: >-
  Lead sub-agents with spawn_agent on a large task with independent parts that need different skills or apps.
  Examples include games, videos, reports, and websites.
  Write a brief, collect member reviews, decide, coordinate parallel work, and check the integrated result.
  Use when the user requests a team or the task clearly justifies one.
---

# Lead a team of sub-agents

The lead owns the brief, decisions, integration, and final delivery.
Each member created with `spawn_agent` owns one part.
This process addresses unclear briefs, conflicting assumptions, and missing checks of the complete result.

## When to form a team

Use a team when parts can proceed independently and each needs its own specialty or app.
The result must justify several times a single agent's token cost.
For smaller tasks, work alone.
Use two to four members.
Additional members usually increase coordination cost.

On this phone, members with desktop apps each need a separate workspace.
See the phone-specific instructions below.

## Visible updates: `team_post`

The user sees member roles, states, and latest words in tiles on the director's phone and TV screens.
Posts also remain in `<folder>/.team/journal.jsonl`.
Agent messages do not replace these visible updates.
Use desktop tool `team_post` with `role`, `kind`, `text`, and `project`.
Write one short sentence in the user's language, at most 80 characters.

The lead posts:

- `brief` after writing BRIEF.md: the product and direction.
- `decision` after review, for example “Accepted 5 of 6 points: pixel art, 34×24 bird, 3 frames”.
- `done` at final delivery.

Members post:

- `review`: main point and point count, for example “3 points, blocker: missing style, propose pixel art”.
- `progress`: completed parts or passed checks, no more frequently than once per half minute.
- `blocked` or `question`: immediately, with the required input.
- `done`: the completed deliverable.

Do not post every command or raw logs.
Exclude paths and personal details because the TV can show posts to people in the room.

## 1. Write the brief

Create `~/Projects/<name>/` and write `BRIEF.md` as the shared specification.
Include these sections:

1. Goal: product, audience, and the user's definition of success.
2. Direction: decisions that make parts compatible and determine the user's first judgment.
3. Parts and owners: one part and directory per member.
4. Deliverables: exact files, names, formats, sizes/lengths, limits, and checks.
5. Interfaces: required paths, names, data formats, timing, and placeholders for parallel work.
6. Constraints: tools, time, memory, and prohibited actions.
7. Open questions: undecided details.
8. Status: `.team/<role>.md`, beginning with `STATUS: reviewing | working | done | blocked`.

For images or audio, Direction identifies style, references, palette, tone, detail, and size/length.
For example: original Flappy Bird style, hard pixels, dark outlines, three-frame wing movement, 34×24 bird.
For text, specify audience, tone, length, and structure.
Use a reference whenever the user names one.
Create original work in its recognizable style instead of copying reference files.

Members write only their own directories and status files.
Each status file records completed work, checks, and remaining problems.

## 2. Review the brief before work

Start each member with review only:

```text
You own the <role> part of <product> in <folder>.
Read BRIEF.md. Do not start implementation.
Review your part and its interfaces with the other parts.
Identify missing, unclear, or contradictory requirements.
Identify decisions you would otherwise make alone, risks, and unavailable tools.
Inspect the available tools, but create no deliverables.
Reply with at most 8 points.
Format each point as [blocker|should|could] problem -> proposal.
Say "no objections" if there are none.
Write the same review into .team/<role>.md with STATUS: reviewing.
Post your main point with team_post: role <role>, kind review, project <folder>.
Wait for the final brief and the lead's authorization to start.
```

Use `wait_agent` for every member.
Each member submits one review message rather than a discussion.
This can take minutes.
The review catches direction gaps before implementation, such as cartoon art when the user expects pixel art.

## 3. Decide

Address every point.
Accept it and update the brief, or reject it with a one-line reason.
Do not leave points unanswered.

Bundle questions that only the user can answer into one short request.
Examples are taste, scope, priorities, or criteria the user will judge.
Give a recommended default for each question.
If the user prohibits questions or cannot be reached, use defaults and record assumptions.

Add `## Decisions` to `BRIEF.md` with each point, decision, and reason.
Update affected sections.
Later changes go through the lead and reach every affected member.

Use one review round only.
A member who disagrees records that in its status file and follows the final brief.
Only a blocker with new information returns to the lead, once.
Decide that blocker before continuing.
Members communicate with the lead, not each other.

Explain the members, deliverables, and direction to the user in one or two sentences.
Keep the plan in `update_plan`.

## 4. Execute

Authorize each member with `send_input`:

```text
BRIEF.md is final. Read Decisions and start your deliverables.
Check them against the brief.
Keep .team/<role>.md current.
Post actual progress with team_post, kind progress.
When finished, set STATUS: done and post kind done.
Report what you produced and how you checked it.
If you cannot meet the brief, set STATUS: blocked.
Post kind blocked with the reason and report the problem instead of bypassing it.
```

Use `wait_agent` for members.
Answer questions and blockers with `send_input`.
Do not perform a member's work or edit its directory.

## 5. Integrate and check

After every member finishes:

1. Check each deliverable against the brief.
   Check names, sizes, and formats with a small project script.
   Inspect images, listen to or measure audio, and read text against Direction and its references.
   Return unsuitable work to its owner with the required correction.
2. Integrate the parts yourself or through the member who owns the target.
3. Test the complete result as the user will use it.
   Save screenshots, logs, and test output in the project.
4. Use `close_agent` for each member after acceptance.

## 6. Deliver

Show the result and state what exists, where it is, checks performed, and remaining problems.
Follow the phone-specific presentation instructions below.
Send feedback to the member who owns the part.
If that member closed, start another sub-agent with the brief.
Check revised work before delivering it again.

## On this phone: Rungic

### Workspaces

A member's first desktop tool call allocates its own workspace.
The phone has four workspaces.
One belongs to the lead, leaving at most three desktop members.
A member's shell remains in the parent's workspace.
Get its workspace number N from `desktop_where`.
Run windowed programs with `rungic-workspace-env N <command>` and open apps with `desktop_launch`.

Tell members to save their work and close their apps.
They must call `desktop_close_workspace` when finished.
The user sees each active workspace in its own floating window.
See `rungic-phone-desktop` for details.

### Memory and apps

Check `free -m` before starting.
Krita and Godot each need about 0.5 GB.
Blender needs much more.
With less than about 1.5 GB available, run the heavy part alone first.

If the brief requires an app such as Krita, Ardour, or Godot, make and save the part there.
The app's scripting interface, such as Krita's Scripter, counts.
The brief defines results rather than individual button presses.

### Show the result on the user's screen

Show it on the user's own screen only after agreement.
For example, ask “做好了，现在在你的桌面打开给你试玩吗？”.
Create `~/.local/share/applications/<id>.desktop` with Type=Application, Name, Exec, Icon, and Categories.
For Godot, use `Exec=<the Godot binary> --path <project dir>`.
Find the binary in the Exec entry of godot*.desktop under `/usr/share/applications` or `~/.local/share/applications`.

Open the launcher with `rungic-user kstart --application <id> </dev/null >/dev/null 2>&1`.
It opens on the phone's own screen with user touch input.
Then run `rungic-agent-screen off` so the floating workspace does not cover it.
Check only that the process runs, with `pgrep -af <its command>`.

Your desktop tools cannot see the phone's own screen.
Do not enable desktop mode, move the window, or work on the user's desktop merely to inspect it.
That moves the result away from the user's screen.
The user reports what they see.
