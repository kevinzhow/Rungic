// SPDX-License-Identifier: GPL-2.0-or-later
// The call with the Agent as the conversation shows it (Claude Design canvas "Agent 通话", docs/101):
// from the phone session's state (phone-state / PhoneSnapshot), the call bar's state and words, the
// call's time, and what was started in it. tr is the page's i18nc, passed in (a library has no
// context to find it in).
.pragma library

const ACTIVE = ["queued", "starting", "running", "stopping", "waiting_input"]

// The tasks of `conversation` started during this call (created since it began).
function tasks(phone, conversation) {
    const since = phone && phone.startedAt ? phone.startedAt : 0
    return ((phone && phone.tasks) || []).filter(t => t.conversation === conversation && (t.created || 0) >= since)
}

// The bar's state, the first that applies (CallBar's states); "" without a call.
function mode(phone, conversation) {
    if (!phone || !phone.sessionId) return ""
    if (phone.conversation !== conversation) return "elsewhere"
    if (phone.phase === "connecting") return "connecting"
    if (tasks(phone, conversation).some(t => t.status === "waiting_input")) return "answer"
    if (phone.speaking) return "agent"
    if (phone.listening) return "you"
    if (phone.thinking) return "thinking"
    if (phone.muted) return "muted"
    return "idle"
}

// 4:12, 1:04:12: the call's time so far.
function clock(seconds) {
    const s = Math.max(0, Math.floor(seconds))
    const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, r = s % 60
    const two = n => (n < 10 ? "0" : "") + n
    return h > 0 ? h + ":" + two(m) + ":" + two(r) : two(m) + ":" + two(r)
}

function elapsed(phone, now) {
    return phone && phone.startedAt ? now / 1000 - phone.startedAt : 0
}

// What the bar says: [label, detail].
function words(tr, phone, conversation, now, title, notice) {
    const which = mode(phone, conversation)
    const time = clock(elapsed(phone, now))
    const running = tasks(phone, conversation).filter(t => ACTIVE.indexOf(t.status) >= 0 && t.status !== "waiting_input").length
    const withTasks = !running ? time : time + " · " + (running === 1 ? tr("@info:status tasks of the call", "1 task running")
        : tr("@info:status tasks of the call; %1 is a count above 1", "%1 tasks running", running))
    switch (which) {
    case "connecting": return [tr("@info:status the call with the Agent", "Connecting…"), notice || ""]
    case "answer": {
        const waiting = tasks(phone, conversation).find(t => t.status === "waiting_input")
        return [tr("@info:status the call with the Agent", "Waiting for your answer"), question(waiting) + (question(waiting) ? " · " : "") + time]
    }
    case "agent": return [tr("@info:status the call with the Agent", "Answering"), withTasks]
    case "you": return [tr("@info:status the call with the Agent", "Listening"), time]
    case "thinking": return [tr("@info:status the call with the Agent", "Working"), time]
    case "muted": return [tr("@info:status the call with the Agent", "Microphone off"), time + " · " + tr("@info:status", "The Agent can't hear you")]
    case "idle": return [tr("@info:status the call with the Agent", "On a call"), withTasks]
    case "elsewhere": return [tr("@info:status", "Call in another conversation"), (title ? title + " · " : "") + time]
    }
    return ["", ""]
}

// The question a waiting task asks, in a few words.
function question(task) {
    if (!task || !task.question) return ""
    const asked = (task.question.questions || [])[0]
    return asked ? asked.question || "" : ""
}

// The call panel's rows: {taskId, text, status: running|queued|answer|done|failed, statusText}.
function panelTasks(tr, phone, conversation) {
    return tasks(phone, conversation).map(t => {
        const status = t.status === "waiting_input" ? "answer"
            : t.status === "queued" ? "queued"
            : t.status === "completed" ? "done"
            : ["failed", "stopped", "interrupted"].indexOf(t.status) >= 0 ? "failed" : "running"
        const statusText = {
            answer: tr("@info:status a task of the call", "Waiting for your answer"),
            queued: tr("@info:status a task of the call", "Queued"),
            done: tr("@info:status a task of the call", "Done"),
            failed: t.status === "stopped" ? tr("@info:status a task of the call", "Stopped") : t.status === "interrupted"
                ? tr("@info:status a task of the call", "Interrupted") : tr("@info:status a task of the call", "Failed"),
            running: t.status === "stopping" ? tr("@info:status a task of the call", "Stopping…") : tr("@info:status a task of the call", "Working")
        }[status]
        return {taskId: t.taskId, text: t.text, status: status, statusText: statusText}
    })
}

// Why a call can't start now, or "" (the top bar's call button says it when tapped).
function blocked(tr, phone, conversation, chat, holding) {
    if (chat && (chat.inCall || chat.callPhase === "user")) return tr("@info", "The Agent is on a call for you: call it when that ends")
    if (phone && phone.sessionId && phone.conversation !== conversation) return tr("@info", "A call is going on in another conversation")
    if (holding) return tr("@info", "Let go of Hold to talk first")
    return ""
}
