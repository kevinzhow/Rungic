// SPDX-License-Identifier: GPL-2.0-or-later
#include "usage.h"
#include <QDateTime>
#include <QDir>
#include <QFileInfo>
#include <QRegularExpression>
#include <KLocalizedString>
#include <algorithm>
#include <cmath>
#include <utility>
namespace Care {
namespace {
const QStringList Statuses{"working", "ready", "offline", "connecting", "signed-out", "not-installed", "error"};
const QStringList Kinds{"subscription", "api-key", "none"};
// A non-negative whole number, or null: providers report counts they don't know as null, never 0.
QJsonValue count(const QJsonValue &v) {
    if (!v.isDouble()) return QJsonValue::Null;
    const double d = v.toDouble();
    if (!(d >= 0) || d > 9e15 || d != std::floor(d)) return QJsonValue::Null;
    return QJsonValue(qint64(d));
}
QString text(const QJsonValue &v, int max) { return v.toString().left(max); }
bool matches(const char *pattern, const QString &s) {
    return QRegularExpression(QRegularExpression::anchoredPattern(QString::fromLatin1(pattern))).match(s).hasMatch();
}
// The provider-shaped part of a source's reply (docs/research/95). Everything else is dropped:
// names come from the descriptor, and times, staleness and the device ledger from this service.
QJsonObject sanitized(const QJsonObject &in) {
    QJsonObject out;
    const auto status = in["status"].toString();
    out["status"] = Statuses.contains(status) ? status : QStringLiteral("ready");
    out["installed"] = in["installed"].isBool() ? in["installed"] : QJsonValue(QJsonValue::Null);
    const auto a = in["account"].toObject();
    const auto kind = a["kind"].toString();
    out["account"] = QJsonObject{{"kind", Kinds.contains(kind) ? kind : QStringLiteral("none")},
                                 {"label", text(a["label"], 64)}, {"plan", text(a["plan"], 32)}};
    out["model"] = text(in["model"], 128);
    const auto t = in["tokens"].toObject();
    QJsonObject tokens{{"account", count(t["account"])}};
    // A source that keeps its own per-message ledger (Claude Code transcripts) reports these itself.
    for (const auto *key : {"device", "today"}) if (!count(t[key]).isNull()) tokens[key] = count(t[key]);
    out["tokens"] = tokens;
    if (in["limits"].isArray()) {
        QJsonArray limits;
        for (const auto &v : in["limits"].toArray()) {
            const auto w = v.toObject();
            if (!w["usedPercent"].isDouble()) continue;
            const auto label = text(w["label"], 64);
            auto id = text(w["id"], 64);
            if (id.isEmpty()) id = label.isEmpty() ? QString::number(limits.size()) : label;
            limits.append(QJsonObject{{"id", id}, {"label", label}, {"windowMinutes", count(w["windowMinutes"])},
                                      {"usedPercent", qBound(0.0, w["usedPercent"].toDouble(), 1000.0)}, {"resetsAt", count(w["resetsAt"])}});
            if (limits.size() == 16) break;
        }
        out["limits"] = limits;
    }
    out["error"] = text(in["error"], 500);
    return out;
}
QString day(qint64 now) { return QDateTime::fromSecsSinceEpoch(now).date().toString(Qt::ISODate); }
// An icon file the widget can load as it is: absolute, present, small, SVG or PNG. Anything else is
// dropped (the widget then draws a letter tile), never a reason to drop the agent.
QString icon(const QJsonValue &v) {
    const QFileInfo info(v.toString());
    if (!v.isString() || !QDir::isAbsolutePath(v.toString()) || !info.isFile() || !info.isReadable() || info.size() > 1024 * 1024) return {};
    return QStringList{"svg", "png"}.contains(info.suffix().toLower()) ? info.absoluteFilePath() : QString();
}
}

QString validateUsageProvider(const QJsonObject &d, const QString &fileName, UsageProvider *out) {
    UsageProvider p;
    if (d["schema"].toInt() != 1) return "schema";
    p.id = d["id"].toString();
    if (!matches("[a-z0-9][a-z0-9._-]{0,63}", p.id)) return "id";
    if (!fileName.isEmpty() && fileName != p.id + ".json") return "file name must be <id>.json";
    p.name = d["name"].toString().trimmed();
    if (p.name.isEmpty() || p.name.size() > 64) return "name";
    if (d.contains("vendor") && (!d["vendor"].isString() || d["vendor"].toString().size() > 64)) return "vendor";
    p.vendor = d["vendor"].toString();
    if (d.contains("dbus") == d.contains("command")) return "exactly one source: dbus or command";
    if (d.contains("dbus")) {
        const auto s = d["dbus"].toObject();
        p.service = s["service"].toString(); p.path = s["path"].toString();
        p.interface = s["interface"].toString(); p.method = s["method"].toString("Usage");
        if (!matches("[A-Za-z_-][A-Za-z0-9_-]*(\\.[A-Za-z_-][A-Za-z0-9_-]*)+", p.service) || p.service.size() > 255) return "dbus.service";
        if (!matches("/|(/[A-Za-z0-9_]+)+", p.path)) return "dbus.path";
        if (!matches("[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*)+", p.interface) || p.interface.size() > 255) return "dbus.interface";
        if (!matches("[A-Za-z_][A-Za-z0-9_]{0,254}", p.method)) return "dbus.method";
    } else {
        const auto c = d["command"].toArray();
        if (c.isEmpty() || c.size() > 16) return "command";
        for (const auto &v : c) {
            if (!v.isString() || v.toString().isEmpty() || v.toString().contains(QChar(0))) return "command";
            p.command.append(v.toString());
        }
        if (!QDir::isAbsolutePath(p.command.first())) return "command must start with an absolute path";
    }
    const auto seconds = d["timeoutSeconds"].toInt(p.dbus() ? 35 : 10);
    if (seconds < 1 || seconds > 60) return "timeoutSeconds";
    p.timeout = seconds * 1000;
    p.order = d["order"].toInt(100);
    if (p.order < 0 || p.order > 1000) return "order";
    if (d.contains("optional") && !d["optional"].isBool()) return "optional";
    p.optional = d["optional"].toBool();
    const auto mark = d["icon"].toObject();
    p.iconLight = icon(mark["light"]);
    if (!p.iconLight.isEmpty()) p.iconDark = icon(mark["dark"]);
    if (out) *out = p;
    return {};
}

QList<UsageProvider> usageProviders(const QStringList &directories, QStringList *errors) {
    QHash<QString, UsageProvider> found;
    for (const auto &directory : directories) {
        for (const auto &info : QDir(directory).entryInfoList({"*.json"}, QDir::Files | QDir::Readable, QDir::Name)) {
            UsageProvider p;
            const auto problem = info.size() > 64 * 1024 ? QStringLiteral("too large")
                : validateUsageProvider(readObject(info.filePath()), info.fileName(), &p);
            if (!problem.isEmpty()) { if (errors) errors->append(info.filePath() + ": " + problem); continue; }
            p.file = info.filePath();
            found[p.id] = p; // A later (the user's) directory replaces the same id.
        }
    }
    auto list = found.values();
    std::sort(list.begin(), list.end(), [](const auto &a, const auto &b) { return a.order != b.order ? a.order < b.order : a.id < b.id; });
    return list;
}

QJsonObject codexTokenEvent(const QJsonObject &e) {
    const auto usage = e["tokenUsage"].toObject();
    return {{"accountKey", e["accountKey"]}, {"session", e["threadId"]}, {"turn", e["turnId"]},
            {"total", usage["total"].toObject()["totalTokens"]}, {"last", usage["last"].toObject()["totalTokens"]}};
}

Usage::Usage(QString p) : path(std::move(p)) {
    const auto stored = path.isEmpty() ? QJsonObject{} : readObject(path);
    // Schema 1 held Codex's accounts only.
    ledger = stored["schema"].toInt() == 1 ? QJsonObject{{"codex", QJsonObject{{"accounts", stored["accounts"]}}}} : stored["providers"].toObject();
    // An account must be verified this session before showing any cached values.
}
void Usage::setProviders(const QList<UsageProvider> &providers) { declared = providers; }
bool Usage::declares(const QString &id) const {
    return std::any_of(declared.begin(), declared.end(), [&id](const auto &p) { return p.id == id; });
}
void Usage::save() {
    if (path.isEmpty()) return;
    QString error;
    saveProblem = writeObject(path, {{"schema", 2}, {"providers", ledger}}, &error) ? QString() : i18n("The usage record wasn't saved: %1", error);
}
void Usage::identity(const QString &provider, const QString &key) {
    auto &s = states[provider];
    if (key == s.accountKey) return;
    s.accountKey = key; s.current = {}; s.refreshed = 0; s.problem.clear(); s.failed = false;
}
void Usage::snapshot(const QString &provider, const QJsonObject &data, qint64 now) {
    identity(provider, data["accountKey"].toString());
    auto &s = states[provider];
    s.seen = true;
    s.available = data["available"].toBool(true);
    auto next = sanitized(data);
    if (!next["error"].toString().isEmpty()) {
        // A partial read keeps what the last complete one said; it is shown as stale.
        if (!next.contains("limits")) next["limits"] = s.current["limits"];
        auto tokens = next["tokens"].toObject();
        if (tokens["account"].isNull()) tokens["account"] = s.current["tokens"].toObject()["account"];
        next["tokens"] = tokens;
    }
    s.current = next; s.status = next["status"].toString(); s.problem = next["error"].toString();
    s.failed = !s.problem.isEmpty();
    if (!s.failed) s.refreshed = now;
    if (s.status == "working") s.active = now;
    s.active = qMax(s.active, count(data["lastActive"]).toInteger());
    for (const auto &v : data["tokenEvents"].toArray()) {
        auto event = v.toObject();
        if (!event.contains("accountKey")) event["accountKey"] = s.accountKey;
        record(provider, event, now);
    }
}
void Usage::failure(const QString &provider, const QString &message, bool offline) {
    auto &s = states[provider];
    s.status = offline ? QStringLiteral("offline") : QStringLiteral("error");
    s.problem = message; s.failed = true;
    s.current["installed"] = QJsonValue::Null; // transport failure cannot recheck the binary
}
bool Usage::record(const QString &provider, const QJsonObject &event, qint64 now) {
    if (!declares(provider)) return false;
    const auto key = event["accountKey"].toString(), session = event["session"].toString();
    const auto total = count(event["total"]);
    if (key.isEmpty() || session.isEmpty() || total.isNull()) return false;
    auto own = ledger[provider].toObject();
    auto accounts = own["accounts"].toObject();
    auto account = accounts.value(key).toObject();
    auto sessions = account["turns"].toObject(); // named "turns" since schema 1; one record per session
    const auto id = fingerprint(session);
    auto record = sessions.value(id).toObject();
    // First sight of a resumed session: only its latest request is new here, not its history.
    const auto previous = record.isEmpty() ? qMax<qint64>(0, total.toInteger() - count(event["last"]).toInteger()) : record["tokens"].toInteger();
    if (total.toInteger() <= previous) return false; // repeated/sparse/out-of-order cumulative updates
    const auto added = total.toInteger() - previous;
    auto days = account["days"].toObject();
    days[day(now)] = days.value(day(now)).toInteger() + added;
    record["tokens"] = total; sessions[id] = record;
    account["turns"] = sessions; account["days"] = days;
    account["total"] = account["total"].toInteger() + added;
    account["updated"] = now;
    accounts[key] = account; own["accounts"] = accounts; ledger[provider] = own;
    save();
    return true;
}
qint64 Usage::activeAt(const QString &provider, const State &s) const {
    if (s.accountKey.isEmpty()) return s.active;
    return qMax(s.active, ledger[provider].toObject()["accounts"].toObject()[s.accountKey].toObject()["updated"].toInteger());
}
QJsonObject Usage::provider(const UsageProvider &p, const State &s, qint64 now) const {
    const auto own = s.current["tokens"].toObject();
    const auto local = s.accountKey.isEmpty() ? QJsonObject{} : ledger[p.id].toObject()["accounts"].toObject()[s.accountKey].toObject();
    QJsonObject tokens{{"device", QJsonValue::Null}, {"today", QJsonValue::Null}, {"account", own.value("account").isUndefined() ? QJsonValue(QJsonValue::Null) : own["account"]}};
    if (own.contains("device")) tokens["device"] = own["device"];
    else if (!local.isEmpty()) tokens["device"] = local["total"].toInteger();
    if (own.contains("today")) tokens["today"] = own["today"];
    else if (!local.isEmpty()) tokens["today"] = local["days"].toObject()[day(now)].toInteger();
    QJsonArray limits;
    for (const auto &v : s.current["limits"].toArray()) {
        auto w = v.toObject();
        w["expired"] = !w["resetsAt"].isNull() && w["resetsAt"].toInteger() <= now; // waits for a fresh read, never zeroed here
        limits.append(w);
    }
    QJsonValue mark = QJsonValue::Null;
    if (!p.iconLight.isEmpty()) {
        QJsonObject paths{{"light", p.iconLight}};
        if (!p.iconDark.isEmpty()) paths["dark"] = p.iconDark;
        mark = paths;
    }
    return {{"id", p.id}, {"name", p.name}, {"vendor", p.vendor}, {"icon", mark}, {"status", s.status},
            {"installed", !s.current["installed"].isBool() ? QJsonValue(QJsonValue::Null) : s.current["installed"]},
            {"account", s.current.contains("account") ? s.current["account"] : QJsonObject{{"kind", "none"}, {"label", ""}, {"plan", ""}}},
            {"model", s.current["model"].toString()}, {"tokens", tokens}, {"limits", limits}, {"updatedAt", s.refreshed},
            {"stale", !s.refreshed || now - s.refreshed > 180 || s.failed}, {"error", s.problem.isEmpty() ? saveProblem : s.problem}};
}
QJsonObject Usage::view(qint64 now) const {
    QJsonArray list;
    QString primary;
    qint64 best = -1, updated = 0;
    bool working = false;
    for (const auto &p : declared) {
        const auto s = states.value(p.id);
        if (!s.available || (p.optional && !s.seen)) continue;
        list.append(provider(p, s, now));
        updated = qMax(updated, s.refreshed);
        // The one working now, else the one most recently active, else the first.
        const auto active = activeAt(p.id, s);
        const bool busy = s.status == "working";
        if (primary.isEmpty() || (busy && !working) || (busy == working && active > best)) { primary = p.id; best = active; working = busy; }
    }
    return {{"schema", 2}, {"primary", primary}, {"updatedAt", updated}, {"providers", list}};
}
}
