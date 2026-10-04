// SPDX-License-Identifier: GPL-2.0-or-later
// A conversation's entries, built from the voice agent's events (docs/59): shared by
// the chat page and the Home button's overlay (docs/67).
import QtQuick

QtObject {
    id: root
    readonly property ListModel entries: ListModel {}
    // In words to show (the service puts its own titles in the desktop's language). `untitled`:
    // not named yet, the first message names it; the service says so (never a title compared).
    property string title: ""
    property bool untitled: true
    property string conversation: ""
    // Session state (the "state" events).
    property string phase: "connecting"
    property bool agentBusy: false
    property bool handsFree: false

    // The chat groups what happens in a turn, like ChatGPT/Codex (docs/59):
    // - all transcript pieces of one push-to-talk press form one user message;
    // - an agent turn is one collapsible "work" entry holding what the assistant
    //   said meanwhile, the agent's notes, commands and file changes;
    // - replies outside agent work are bubbles: the acknowledgement before it and
    //   the answer afterwards. Entries never disappear once shown.
    property int workAt: -1          // the work entry receiving agent activity
    property bool workOpen: false    // an agent turn is running
    property real lastTime: 0        // of the latest event (a turn the history left open ends there)
    property int callAt: -1          // the proxied call entry (docs/63)
    property int staleCallAt: -1     // a call the history left open: ended, unless the service says it is live
    property bool inCall: false      // talking goes to the call agent
    property string callPhase: ""    // "agent": the assistant talks; "user": the user talks, assistant paused
    property bool callMonitor: false
    property bool callCanMonitor: true
    // A press sent but never transcribed (noise only): its empty bubble goes after a while.
    property Timer sweep: Timer {
        interval: 15000
        onTriggered: {
            for (let i = root.entries.count - 1; i >= 0; i--) {
                const e = root.entries.get(i)
                if (e.kind === "live-user" && e.status === "sending" && !e.text) root.removeAt(i)
            }
        }
    }

    // A title to show: a conversation not opened yet has none.
    function titleText(title) { return title || i18nc("@title a conversation not named yet", "New conversation") }

    function entry(fields) {
        return Object.assign({ kind: "", role: "", text: "", itemId: "", command: "", output: "",
                               status: "", exitCode: "", press: 0, started: 0, finished: 0,
                               expanded: false, steps: [], task: "", callBackend: "", callNumber: "", connectedAt: 0,
                               privateVoiceInstructions: true, independentMonitor: true }, fields)
    }
    function callIndex(e) {
        if (e.callId) {
            for (let i = entries.count - 1; i >= 0; --i) {
                const row = entries.get(i)
                if (row.kind === "call" && row.itemId === e.callId) return i
            }
            return -1
        }
        return root.callAt >= 0 ? root.callAt : root.staleCallAt
    }
    function callEntry(e) {
        return entry({kind: "call", status: "running", itemId: e.callId || "", command: "connecting",
                      started: e.time || Date.now()/1000, role: e.contact || "", text: e.goal || "",
                      callBackend: e.backend || "wechat", callNumber: e.number || "",
                      privateVoiceInstructions: e.privateVoiceInstructions !== false,
                      independentMonitor: e.independentMonitor !== false, expanded: true})
    }
    function step(fields) {
        return Object.assign({ kind: "", text: "", itemId: "", command: "", output: "", status: "", exitCode: "" }, fields)
    }
    function lastOf(kind, role) {
        for (let i = entries.count - 1; i >= 0; i--) {
            const e = entries.get(i)
            if (e.kind === kind && (role === undefined || e.role === role)) return i
        }
        return -1
    }
    // Chinese needs no space between pieces; Latin words do.
    function join(a, b) {
        return /[A-Za-z0-9,.!?]$/.test(a) && /^[A-Za-z0-9]/.test(b) ? a + " " + b : a + b
    }
    function addStep(fields) {
        if (root.workAt < 0) return
        entries.get(root.workAt).steps.append(step(fields))
        if (fields.kind !== "command" || !fields.status || fields.status === "running")
            entries.setProperty(root.workAt, "text", fields.kind === "command" ? fields.command : fields.text)
    }
    // The streaming bubble of transcript segment `id`, or -1.
    function liveAt(id) {
        if (!id) return -1
        for (let i = entries.count - 1; i >= 0; i--) {
            const e = entries.get(i)
            if (e.itemId === id && (e.kind === "live-user" || e.kind === "live-assistant")) return i
        }
        return -1
    }
    // The user's bubble of press `press` (in place since the press began), or -1.
    function pressAt(press) {
        if (!press) return -1
        for (let i = entries.count - 1; i >= 0; i--) {
            const e = entries.get(i)
            if (e.role === "user" && e.press === press && (e.kind === "live-user" || e.kind === "message")) return i
        }
        return -1
    }
    // Where a user message of press time `at` (seconds) belongs: before everything that began
    // after the user began to speak (docs/87). A reply can start before the transcript of what
    // was said is done, and the history keeps messages in the order they were completed.
    function userSlot(at) {
        let i = entries.count
        while (i > 0 && at > 0 && entries.get(i - 1).started > at) i--
        return i
    }
    function removeAt(i) {
        entries.remove(i)
        if (root.workAt > i) root.workAt--
        else if (root.workAt === i) root.workAt = -1
    }
    function insertAt(i, fields) {
        entries.insert(i, entry(fields))
        if (root.workAt >= i) root.workAt++
    }

    function apply(e, live) {
        if (e.time && e.type !== "state") root.lastTime = e.time
        switch (e.type) {
        case "phone-task": {
            const t = e.task
            let at = -1
            for (let i = entries.count - 1; i >= 0; --i) if (entries.get(i).kind === "phone-task" && entries.get(i).itemId === t.taskId) { at = i; break }
            const fields = {kind: "phone-task", itemId: t.taskId, text: t.text || "", status: t.status || "interrupted", output: t.result || "", task: JSON.stringify(t.question || {}), started: t.created || 0}
            if (at < 0) entries.append(entry(fields))
            else for (const key in fields) entries.setProperty(at, key, fields[key])
            return
        }
        case "phone-notice":
            entries.append(entry({kind: "marker", text: e.text || ""})); return
        // The call with the Agent ended (docs/101): its summary stays in the conversation.
        case "phone-ended":
            entries.append(entry({kind: "call-ended", itemId: e.sessionId || "", started: e.startedAt || 0,
                                  output: JSON.stringify({seconds: e.seconds || 0, tasks: e.tasks || []})}))
            return
        case "phone-task-detail": return

        // Transcripts stream per segment (e.id): the user's transcription often ends
        // after the reply has begun, so a delta finds its own bubble by id, and the
        // finished text replaces that bubble where it stands (docs/59).
        case "delta": {
            if (!live || !e.text) return
            // The user's words go into the bubble that has been waiting since the press.
            const pressed = e.role === "user" ? pressAt(e.press) : -1
            const at = pressed >= 0 && entries.get(pressed).kind === "live-user" ? pressed : liveAt(e.id)
            if (at >= 0) {
                entries.setProperty(at, "text", root.join(entries.get(at).text, e.text))
                if (!entries.get(at).itemId) entries.setProperty(at, "itemId", e.id || "")
            }
            else if (e.role === "user")
                root.insertAt(userSlot((e.press || 0) / 1000), { kind: "live-user", role: "user", text: e.text, itemId: e.id || "",
                                                                 press: e.press || 0, started: (e.press || 0) / 1000 })
            else if (root.workOpen && root.workAt >= 0) {
                // Spoken progress streams into the work entry's status line.
                const w = entries.get(root.workAt)
                entries.setProperty(root.workAt, "text", w.status === "live" ? w.text + e.text : e.text)
                entries.setProperty(root.workAt, "status", "live")
            } else entries.append(entry({ kind: "live-assistant", role: "assistant", text: e.text, itemId: e.id || "",
                                          started: e.time || Date.now() / 1000 }))
            break
        }
        case "message": {
            let liveIndex = liveAt(e.id)
            if (e.role === "user") {
                const waiting = pressAt(e.press)
                if (waiting >= 0 && entries.get(waiting).kind === "live-user") liveIndex = waiting
                const at = lastOf("message", "user")
                if (e.press && at >= 0 && entries.get(at).press === e.press) {
                    // Another segment of the same press joins its message.
                    entries.setProperty(at, "text", root.join(entries.get(at).text, e.text))
                    if (liveIndex >= 0) root.removeAt(liveIndex)
                } else {
                    // A new request: earlier turns' work folds away again.
                    for (let i = 0; i < entries.count; i++) {
                        if (entries.get(i).kind === "work" && entries.get(i).expanded) entries.setProperty(i, "expanded", false)
                    }
                    if (liveIndex >= 0) {
                        entries.setProperty(liveIndex, "kind", "message")
                        entries.setProperty(liveIndex, "text", e.text)
                        entries.setProperty(liveIndex, "status", "")
                    } else {
                        // Spoken: where the press began (a history replay has no bubble waiting).
                        // Typed: at the end; its attachments ride in `output` (JSON), docs/87.
                        const at = e.press ? e.press / 1000 : 0
                        root.insertAt(userSlot(at), { kind: "message", role: "user", text: e.text, itemId: e.id || "", press: e.press || 0,
                                                      started: at || e.time || 0,
                                                      output: e.attachments && e.attachments.length ? JSON.stringify(e.attachments) : "" })
                    }
                }
                if (root.untitled) { root.title = e.text.slice(0, 20); root.untitled = false }
            } else if (liveIndex >= 0) {
                // Streamed as a bubble (before any agent work began): it stays one.
                entries.setProperty(liveIndex, "kind", "message")
                entries.setProperty(liveIndex, "text", e.text)
            } else if (root.workOpen && root.workAt >= 0 && e.started && e.started < entries.get(root.workAt).started) {
                // Begun before the work (a history replay has no stream): above the work, as it was shown.
                root.insertAt(root.workAt, { kind: "message", role: "assistant", text: e.text, itemId: e.id || "" })
            } else if (root.workOpen && root.workAt >= 0) {
                if (entries.get(root.workAt).status === "live") entries.setProperty(root.workAt, "status", "running")
                root.addStep({ kind: "said", text: e.text })
            } else {
                entries.append(entry({ kind: "message", role: "assistant", text: e.text, itemId: e.id || "" }))
            }
            break
        }
        case "agent-started": {
            // A turn still open here never finished (the service restarted under it, docs/87):
            // it ended when the next one began.
            if (root.workOpen && root.workAt >= 0 && entries.get(root.workAt).status !== "done") {
                entries.setProperty(root.workAt, "status", "stopped")
                entries.setProperty(root.workAt, "finished", root.lastTime || e.time || Date.now() / 1000)
            }
            // The acknowledgement before it ("OK, I'll…") stays a bubble: it was shown
            // before anyone knew work would follow, and nothing on screen should vanish.
            entries.append(entry({ kind: "work", status: "running", started: e.time || Date.now() / 1000 }))
            root.workAt = entries.count - 1
            root.workOpen = true
            break
        }
        case "task": {
            // What the turn is doing, in words (docs/89): the task card of the work entry.
            if (root.workAt < 0) return
            const card = Object.assign({}, e)
            delete card.type; delete card.time; delete card.conversation
            entries.setProperty(root.workAt, "task", JSON.stringify(card))
            break
        }
        case "team": {
            // A team led from this conversation (docs/research/91 §14): one card per team, kept current.
            const board = e.board || {}
            const id = (board.lead || "") + ":" + (board.started || 0)
            for (let i = entries.count - 1; i >= 0; i--) {
                if (entries.get(i).kind === "team" && entries.get(i).itemId === id) {
                    entries.setProperty(i, "task", JSON.stringify(board))
                    return
                }
            }
            entries.append(entry({ kind: "team", itemId: id, task: JSON.stringify(board), started: board.started || e.time || 0 }))
            break
        }
        case "agent-finished":
            if (root.workAt >= 0) {
                if (entries.get(root.workAt).status !== "stopped") entries.setProperty(root.workAt, "status", "done")
                entries.setProperty(root.workAt, "finished", e.time || Date.now() / 1000)
            }
            root.workOpen = false
            break
        case "task-stopped":
            if (root.workAt >= 0 && root.workOpen) entries.setProperty(root.workAt, "status", "stopped")
            else entries.append(entry({ kind: "marker", text: i18nc("@info a mark in the thread: the task was stopped", "Stopped") }))
            break
        case "agent-message":
            if (root.workAt < 0) {
                entries.append(entry({ kind: "work", status: "done", started: e.time || 0, finished: e.time || 0 }))
                root.workAt = entries.count - 1
            }
            root.addStep({ kind: e.final ? "answer" : "note", text: e.text, itemId: e.id || "" })
            break
        case "command": {
            if (root.workAt < 0) return
            const fields = { status: e.status, exitCode: e.exitCode === null || e.exitCode === undefined ? "" : String(e.exitCode),
                             output: e.output || "" }
            const steps = entries.get(root.workAt).steps
            let at = -1
            for (let i = steps.count - 1; i >= 0; i--) if (steps.get(i).kind === "command" && steps.get(i).itemId === e.id) { at = i; break }
            if (at >= 0) { for (const k in fields) steps.setProperty(at, k, fields[k]) }
            else root.addStep(Object.assign({ kind: "command", itemId: e.id, command: e.command }, fields))
            break
        }
        case "files": root.addStep({ kind: "files", text: (e.paths || []).join("\n") }); break
        // Codex handed the turn to another model (capacity, safety; docs/98): the task card says which.
        case "model-rerouted":
            root.addStep({ kind: "note", text: i18nc("@info a step of an agent turn; %1 and %2 are model names",
                                                      "Continued on %1 instead of %2", e.to, e.from) })
            break
        case "approval": {
            const pending = e.status === "pending" && live
            entries.append(entry({ kind: "approval", itemId: e.id, text: e.reason || "",
                                command: e.kind === "command" ? e.text : "", status: pending ? "pending" : "expired" }))
            break
        }
        case "approval-result": {
            for (let i = entries.count - 1; i >= 0; i--) {
                if (entries.get(i).kind === "approval" && entries.get(i).itemId === e.id) {
                    entries.setProperty(i, "status", e.decision === "decline" ? "decline" : "accept")
                    break
                }
            }
            break
        }
        case "call-started": {
            let at = e.callId ? root.callIndex(e) : -1
            if (at < 0) { entries.append(root.callEntry(e)); at = entries.count - 1 }
            root.callAt = at
            root.callMonitor = !!e.monitor
            root.callCanMonitor = e.independentMonitor !== false
            if (live) { root.inCall = true; root.callPhase = "agent" }
            break
        }
        case "call-transcript": case "call-owner": case "call-ask": case "call-note": case "call-error": {
            const at = root.callIndex(e)
            if (at < 0) {
                if (e.type === "call-error") entries.append(entry({kind: "error", text: e.text}))
                break
            }
            const who = e.type === "call-owner" ? "owner" : e.type === "call-ask" ? "ask"
                      : e.type === "call-note" ? "note" : e.type === "call-error" ? "error" : e.role
            entries.get(at).steps.append(root.step({ kind: who, text: e.text }))
            break
        }
        case "call-monitor":
            if (root.callIndex(e) === root.callAt) root.callMonitor = !!e.on
            break
        case "call-state": {
            const at = root.callIndex(e)
            if (at >= 0) {
                entries.setProperty(at, "command", e.state)
                if (e.state === "connected" && !entries.get(at).connectedAt)
                    entries.setProperty(at, "connectedAt", e.connectedAt || e.time || Date.now()/1000)
            }
            break
        }
        case "call-phase": {
            const at = root.callIndex(e)
            if (at >= 0 && e.phase === "user" && entries.get(at).status !== "done")
                entries.setProperty(at, "status", "user")
            if (at >= 0 && at === root.callAt) {
                if (live) { root.inCall = e.phase === "agent"; root.callPhase = e.phase }
                root.callMonitor = false
            }
            break
        }
        case "call-ended": {
            const at = root.callIndex(e)
            if (at >= 0) {
                entries.setProperty(at, "status", "done")
                entries.setProperty(at, "finished", e.time || Date.now() / 1000)
                entries.setProperty(at, "output", e.summary || "")
            }
            if (at >= 0 && (at === root.callAt || at === root.staleCallAt)) {
                root.callAt = -1; root.staleCallAt = -1; root.callMonitor = false
                if (live) { root.inCall = false; root.callPhase = "" }
            }
            break
        }
        case "error": entries.append(entry({ kind: "error", text: e.text })); break
        // A press began: the user's bubble is in place at once, listening (docs/87).
        case "talk-started":
            if (live && pressAt(e.press) < 0)
                entries.append(entry({ kind: "live-user", role: "user", text: "", press: e.press, status: "listening",
                                       started: e.press / 1000 }))
            break
        // Released and sent: the transcript is on its way.
        case "talk-sent": {
            const at = pressAt(e.press)
            if (at >= 0 && entries.get(at).kind === "live-user") {
                entries.setProperty(at, "status", "sending")
                entries.setProperty(at, "finished", e.time || Date.now() / 1000)
                sweep.restart()
            }
            break
        }
        // Dropped (or nothing was said): the waiting bubble goes.
        case "listen-cancelled": {
            const at = e.press ? pressAt(e.press) : lastOf("live-user", "user")
            if (at >= 0 && entries.get(at).kind === "live-user" && !entries.get(at).text) root.removeAt(at)
            break
        }
        // The agent is not set up yet (docs/87): what is missing and a way to the settings.
        case "setup": entries.append(entry({ kind: "setup", text: e.text || "", output: e.detail || "", command: e.page || "key" })); break
        case "state":
            root.phase = e.phase; root.agentBusy = !!e.agentBusy; root.inCall = !!e.call
            root.handsFree = !!e.handsFree
            root.callPhase = e.callPhase || ""
            const info = e.callInfo
            const belongsHere = info && (!info.conversation || info.conversation === root.conversation)
            if (root.callPhase && belongsHere) {
                let at = root.callIndex({callId: info.id})
                if (at < 0) {
                    entries.append(root.callEntry(Object.assign({}, info, {callId: info.id, time: info.started})))
                    at = entries.count - 1
                }
                entries.setProperty(at, "status", root.callPhase === "user" ? "user" : "running")
                entries.setProperty(at, "command", info.state || "connecting")
                entries.setProperty(at, "connectedAt", info.connectedAt || entries.get(at).connectedAt)
                entries.setProperty(at, "privateVoiceInstructions", info.privateVoiceInstructions !== false)
                entries.setProperty(at, "independentMonitor", info.independentMonitor !== false)
                root.callCanMonitor = info.independentMonitor !== false
                root.callAt = at
            } else if (!info && root.callPhase && root.staleCallAt >= 0
                       && !entries.get(root.staleCallAt).itemId) {
                entries.setProperty(root.staleCallAt, "status", root.callPhase === "user" ? "user" : "running")
                root.callAt = root.staleCallAt
            }
            root.staleCallAt = -1
            break
        }
    }

    // A conversation opened (history replayed, nothing running any more).
    function load(opened) {
        root.title = opened.title || ""
        root.untitled = opened.untitled === true || !opened.title
        root.conversation = opened.conversation || ""
        root.inCall = false; root.callPhase = ""; root.callMonitor = false; root.callCanMonitor = true
        entries.clear()
        root.workAt = -1
        root.workOpen = false
        root.callAt = -1
        root.staleCallAt = -1
        for (const e of opened.history) root.apply(e, false)
        // A turn that was running when the history was saved is not running now.
        if (root.workOpen && root.workAt >= 0) {
            entries.setProperty(root.workAt, "status", "done")
            entries.setProperty(root.workAt, "finished", root.lastTime)
            root.workOpen = false
        }
        // A call the history left open (its end was not recorded) has ended, unless the
        // next state says otherwise.
        if (root.callAt >= 0) {
            const call = entries.get(root.callAt)
            if (call.status === "running" || call.status === "user") {
                entries.setProperty(root.callAt, "status", "done")
                entries.setProperty(root.callAt, "finished", root.lastTime)
                root.staleCallAt = root.callAt
            }
            root.callAt = -1
        }
    }
}
