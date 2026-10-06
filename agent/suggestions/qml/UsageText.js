.pragma library
// Shared by AgentWidget and AgentUsageDetails; `l10n` is the caller's KI18nContext (rungic-suggestions).
// `p` is one provider of AgentUsage schema 2 (docs/research/95), {} while none is known.
function number(l10n, value) { return value === undefined || value === null ? l10n.i18n("no records yet") : Number(value).toLocaleString(Qt.locale(), "f", 0) }
function mode(l10n, p) {
    const a = p.account || {}
    if (p.status === "offline" || p.status === "error") return status(l10n, p)
    if (!p.id || p.status === "connecting") return l10n.i18n("Connecting")
    return ({"api-key": a.label || "API Key", subscription: a.label || l10n.i18n("Subscription"), none: l10n.i18n("Not signed in")})[a.kind] || l10n.i18n("Not signed in")
}
function status(l10n, p) {
    return ({working: l10n.i18n("Working"), ready: l10n.i18n("Ready"), offline: l10n.i18n("Not connected"), "signed-out": l10n.i18n("Not signed in"), error: l10n.i18n("Unavailable")})[p.status] || l10n.i18n("Connecting")
}
function windowName(l10n, w) {
    const m = w.windowMinutes
    return !m ? l10n.i18n("Usage window") : m >= 1440 ? l10n.i18np("%1-day limit", "%1-day limit", m / 1440) : m >= 60 ? l10n.i18np("%1-hour limit", "%1-hour limit", m / 60) : l10n.i18np("%1-minute limit", "%1-minute limit", m)
}
function reset(l10n, w, now) {
    if (!w.resetsAt) return l10n.i18n("No reset time given")
    const mins = Math.ceil((w.resetsAt - now) / 60)
    if (mins <= 0) return l10n.i18n("Reset time reached, waiting for an update")
    return mins >= 60 ? l10n.i18nc("@info %1 hours and %2 minutes", "Resets in %1 h %2 min", Math.floor(mins / 60), mins % 60) : l10n.i18nc("@info", "Resets in %1 min", mins)
}
function token(l10n, p) { const t = (p.tokens || {}).device; return t === undefined || t === null ? l10n.i18n("No usage recorded yet") : l10n.i18n("%1 tokens recorded on this device", number(l10n, t)) }

// The widget's compact forms (docs/research/95): every string fits a 146 px meter column.
// Numbers: 67,421 → 67.4K (English) or 6.7万 (Chinese).
function compact(l10n, value) {
    if (value === undefined || value === null) return "–"
    const n = Number(value), zh = Qt.locale().name.startsWith("zh")
    const scaled = (v, unit) => l10n.i18nc("@info a compact number: %1 the value, %2 its unit", "%1%2",
                                            Number(v).toLocaleString(Qt.locale(), "f", v < 100 ? 1 : 0).replace(/[.,]0$/, ""), unit)
    if (zh) return n < 10000 ? Number(n).toLocaleString(Qt.locale(), "f", 0) : n < 1e8 ? scaled(n / 1e4, "万") : scaled(n / 1e8, "亿")
    if (n < 1000) return Number(n).toLocaleString(Qt.locale(), "f", 0)
    return n < 1e6 ? scaled(n / 1e3, "K") : n < 1e9 ? scaled(n / 1e6, "M") : scaled(n / 1e9, "B")
}
// A limit window's short name: 5-hour, Weekly, Daily, 30-day …
function shortWindow(l10n, w) {
    const m = w.windowMinutes
    if (!m) return w.label || l10n.i18nc("@info a usage limit without a known length", "Limit")
    if (m === 10080) return l10n.i18nc("@info usage limit window", "Weekly")
    if (m === 1440) return l10n.i18nc("@info usage limit window", "Daily")
    return m >= 1440 ? l10n.i18ncp("@info usage limit window", "%1-day", "%1-day", m / 1440)
         : m >= 60 ? l10n.i18ncp("@info usage limit window", "%1-hour", "%1-hour", m / 60)
         : l10n.i18ncp("@info usage limit window", "%1-minute", "%1-minute", m)
}
// Under a day: a countdown (2h 35m); later: weekday and time (Thu 09:00).
function when(l10n, at, now) {
    const mins = Math.ceil((at - now) / 60)
    if (mins < 60) return l10n.i18ncp("@info a duration", "%1 min", "%1 min", Math.max(1, mins))
    if (mins < 1440) return l10n.i18nc("@info a duration: %1 hours, %2 minutes", "%1h %2m", Math.floor(mins / 60), mins % 60)
    return new Date(at * 1000).toLocaleString(Qt.locale(), l10n.i18nc("@info Qt date format: weekday and time", "ddd HH:mm"))
}
function resetNote(l10n, w, now) {
    if (!w.resetsAt) return l10n.i18nc("@info usage limit", "No reset time given")
    if (w.resetsAt <= now) return l10n.i18nc("@info usage limit", "Resetting now")
    const t = when(l10n, w.resetsAt, now), countdown = w.resetsAt - now < 86400
    if (w.usedPercent >= 100) return countdown ? l10n.i18nc("@info usage limit: %1 a duration", "Back in %1", t) : l10n.i18nc("@info usage limit: %1 a weekday and time", "Back %1", t)
    return countdown ? l10n.i18nc("@info usage limit: %1 a duration", "Resets in %1", t) : l10n.i18nc("@info usage limit: %1 a weekday and time", "Resets %1", t)
}

// A previous ready reply cannot enable actions while the latest read is unknown.
function canAsk(p) { return p && p.stale !== true && ["ready", "working"].includes(p.status) }
