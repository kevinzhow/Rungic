// SPDX-License-Identifier: GPL-2.0-or-later
#include "model.h"
#include "briefing.h"
#include "layout.h"
#include "usage.h"
#include "claude_code.h"
#include <KConfig>
#include <KConfigGroup>
#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QTemporaryDir>
#include <QtTest>
#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QSet>
#include <time.h>

class CareTests : public QObject {
    Q_OBJECT
    static QJsonObject item(QString key = "crash:1", QString source = "crashes") {
        auto o = Care::observation(key, "应用退出", "原因待核实", "fault", 1, {{"signature", "abc"}});
        o["source"] = source; return o;
    }
    // ---- Agent usage (docs/research/95) ----
    static Care::UsageProvider provider(const QString &id, int order = 100, bool optional = false) {
        Care::UsageProvider p; p.id = id; p.name = id.toUpper(); p.vendor = "V"; p.order = order; p.optional = optional;
        p.service = "com.example." + id; p.path = "/usage"; p.interface = "com.example.Usage"; p.method = "Usage";
        return p;
    }
    static QJsonObject token(const QString &account, const QString &session, qint64 total, qint64 last) {
        return {{"accountKey", account}, {"session", session}, {"turn", "turn"}, {"total", total}, {"last", last}};
    }
    static QJsonObject shown(const QJsonObject &view, const QString &id) {
        for (const auto &v : view["providers"].toArray()) if (v.toObject()["id"] == id) return v.toObject();
        return {};
    }
    static void write(const QString &path, const QByteArray &bytes) {
        QDir().mkpath(QFileInfo(path).absolutePath());
        QFile f(path); QVERIFY(f.open(QIODevice::WriteOnly)); f.write(bytes);
    }
private Q_SLOTS:
    // covers: agent.care-ledger/E1
    void groupedCrashesKeepIndependentRecordsAndReceipts() {
        Care::Model m(""); auto a = item("a"), b = item("b"), c = item("other");
        const auto first = a["id"].toString(), second = b["id"].toString();
        for (auto *o : {&a, &b}) (*o)["evidence"] = QJsonObject{{"package", "desktop"}, {"version", "1"}, {"reports", 2}};
        c["evidence"] = QJsonObject{{"package", "player"}, {"version", "1"}};
        m.observe(a, 100); m.observe(b, 101); m.observe(c, 102);
        m.updatePlan(second, {{"result", "Missing evidence, cause unconfirmed.\n\nDetails"}});
        const auto groups = m.groups(); QCOMPARE(groups.size(), 2);
        QJsonObject group;
        for (const auto &v : groups) if (v.toObject()["count"].toInt() == 2) group = v.toObject();
        QCOMPARE(group["reports"].toInt(), 4); QCOMPARE(group["summary"].toString(), "Missing evidence, cause unconfirmed.");
        QCOMPARE(group["representativeId"].toString(), second);
        m.present(second, 1, false, 200); QVERIFY(!m.get(first).contains("displayedRevision"));
        m.act(second, "dismiss", {}, 201);
        QCOMPARE(m.groups(true).size(), 1); QCOMPARE(m.groups().size(), 2);
        QCOMPARE(m.list().size(), 3);
    }
    // covers: agent.care-ledger/E9
    void crashCopyPreservesEvidenceWithoutClaimingImpact() {
        Care::Model m(""); auto o = item();
        o["evidence"] = QJsonObject{{"package", "xdg-desktop-portal-kde"}, {"reports", 3}, {"version", "6.6"}};
        o["body"] = "Reports with the same signature kept recently";
        m.observe(o, 100);
        const auto shown = m.get(o["id"].toString());
        QVERIFY(!shown["displayTitle"].toString().contains("xdg-desktop"));
        QVERIFY(!shown["summary"].toString().contains("signature"));
        QCOMPARE(shown["evidence"].toObject()["package"].toString(), QString("xdg-desktop-portal-kde"));
        QCOMPARE(shown["body"].toString(), o["body"].toString());
    }
    // covers: agent.suggestion-tasks/E2
    void structuredSummaryDoesNotMakeAPlanExecutable() {
        Care::Model m(""); const auto o = item(); const auto id = o["id"].toString(); m.observe(o, 100);
        auto r = m.updatePlan(id, {{"conclusion", "Possibly a known bug, not confirmed"}, {"confidence", "suspected"}, {"nextStep", "Observe"}});
        QCOMPARE(r["summary"].toString(), "Possibly a known bug, not confirmed"); QVERIFY(!r["canApply"].toBool());
        QVERIFY(m.updatePlan(id, {{"confidence", "guaranteed"}}).contains("error"));
        r = m.updatePlan(id, {{"result", "New investigation"}}); QCOMPARE(r["summary"].toString(), "New investigation");
    }
    // ---- Agent usage (docs/research/95) ----
    void initTestCase() { qputenv("TZ", "UTC"); tzset(); }
    // covers: agent.usage-widget/E2
    void usageDeduplicatesRequestsRestartsAndAccounts() {
        QTemporaryDir d; const auto path = d.path() + "/usage.json";
        Care::Usage u(path); u.setProviders({provider("codex")});
        const QJsonObject snapshot{{"accountKey", "account-a"}, {"account", QJsonObject{{"kind", "api-key"}}}};
        u.snapshot("codex", snapshot, 100);
        auto event = token("account-a", "thread", 1200, 200);
        u.record("codex", event, 101); u.record("codex", event, 102);
        auto device = [](const QJsonObject &view) { return shown(view, "codex")["tokens"].toObject()["device"]; };
        QCOMPARE(device(u.view(102)).toInteger(), 200); // resumed history is not counted
        event = token("account-a", "thread", 1700, 500);
        u.record("codex", event, 103); QCOMPARE(device(u.view(103)).toInteger(), 700); // same turn, second model request
        Care::Usage restarted(path); restarted.setProviders({provider("codex")});
        QVERIFY(device(restarted.view(104)).isNull()); // not shown before the account is confirmed again
        restarted.snapshot("codex", snapshot, 105); restarted.record("codex", event, 106);
        QCOMPARE(device(restarted.view(106)).toInteger(), 700);
        QCOMPARE(shown(restarted.view(106), "codex")["tokens"].toObject()["today"].toInteger(), 700);
        restarted.snapshot("codex", {{"accountKey", "account-b"}}, 107);
        QVERIFY(device(restarted.view(107)).isNull());
        restarted.record("codex", token("account-b", "thread", 1700, 500), 108);
        QCOMPARE(device(restarted.view(108)).toInteger(), 500);
        restarted.snapshot("codex", snapshot, 109); QCOMPARE(device(restarted.view(109)).toInteger(), 700);
        QCOMPARE(Care::readObject(path)["schema"].toInt(), 2);
    }
    // covers: agent.usage-widget/E2
    void usageLedgerMigratesSchemaOneAndLegacyEvents() {
        QTemporaryDir d; const auto path = d.path() + "/usage.json";
        const QJsonObject account{{"turns", QJsonObject{{Care::fingerprint("thread"), QJsonObject{{"tokens", 1000}}}}}, {"total", 400}, {"days", QJsonObject{}}};
        QVERIFY(Care::writeObject(path, {{"schema", 1}, {"accounts", QJsonObject{{"a", account}}}}));
        Care::Usage u(path); u.setProviders({provider("codex")});
        u.snapshot("codex", {{"accountKey", "a"}}, 100);
        const QJsonObject legacy{{"type", "token-usage"}, {"accountKey", "a"}, {"threadId", "thread"}, {"turnId", "t"},
            {"tokenUsage", QJsonObject{{"total", QJsonObject{{"totalTokens", 1100}}}, {"last", QJsonObject{{"totalTokens", 100}}}}}};
        QVERIFY(u.record("codex", Care::codexTokenEvent(legacy), 101));
        QCOMPARE(shown(u.view(101), "codex")["tokens"].toObject()["device"].toInteger(), 500);
        QVERIFY(!u.record("codex", Care::codexTokenEvent(legacy), 102));
        QVERIFY(!u.record("undeclared", token("a", "s", 10, 0), 103)); // no descriptor, no record
    }
    // covers: agent.usage-widget/E1 agent.usage-widget/E6
    void usageQuotaIsNullableAndResetDoesNotInventFreshData() {
        Care::Usage u(""); u.setProviders({provider("codex")});
        const QJsonArray limits{QJsonObject{{"id", "codex.primary"}, {"label", "codex"}, {"usedPercent", 75}, {"windowMinutes", 300}, {"resetsAt", 200}}};
        u.snapshot("codex", {{"accountKey", "a"}, {"account", QJsonObject{{"kind", "subscription"}, {"label", "ChatGPT"}}}, {"limits", limits}}, 100);
        auto p = shown(u.view(101), "codex"); QCOMPARE(p["limits"].toArray().size(), 1);
        QVERIFY(!p["limits"].toArray()[0].toObject()["expired"].toBool());
        QCOMPARE(p["limits"].toArray()[0].toObject()["windowMinutes"].toInt(), 300);
        QVERIFY(shown(u.view(201), "codex")["limits"].toArray()[0].toObject()["expired"].toBool());
        QVERIFY(shown(u.view(301), "codex")["stale"].toBool());
        // A partial read keeps the last limits and says so.
        u.snapshot("codex", {{"accountKey", "a"}, {"error", "not updated"}}, 302);
        p = shown(u.view(302), "codex");
        QCOMPARE(p["limits"].toArray().size(), 1); QVERIFY(p["stale"].toBool()); QCOMPARE(p["error"].toString(), "not updated");
        u.snapshot("codex", {{"accountKey", "a"}, {"account", QJsonObject{{"kind", "api-key"}}}}, 303);
        QVERIFY(shown(u.view(303), "codex")["limits"].toArray().isEmpty());
        u.snapshot("codex", {{"status", "signed-out"}}, 304);
        p = shown(u.view(304), "codex");
        QVERIFY(p["tokens"].toObject()["device"].isNull()); QCOMPARE(p["status"].toString(), "signed-out");
        QCOMPARE(p["account"].toObject()["kind"].toString(), "none");
    }
    // covers: agent.usage-widget/E3
    void usageDescriptorsLoadFromSystemAndUserDirectories() {
        QTemporaryDir d; const auto system = d.path() + "/system", user = d.path() + "/user";
        write(system + "/codex.json", R"({"schema":1,"id":"codex","name":"Codex","vendor":"OpenAI","order":10,
            "dbus":{"service":"com.rungic.VoiceAgent","path":"/com/rungic/VoiceAgent","interface":"com.rungic.VoiceAgent","method":"Usage"}})");
        write(system + "/reader.json", R"({"schema":1,"id":"reader","name":"Reader","command":["/usr/libexec/reader","--json"],"optional":true})");
        write(system + "/broken.json", "{not json");
        write(system + "/both.json", R"({"schema":1,"id":"both","name":"B","command":["/bin/x"],"dbus":{"service":"a.b","path":"/","interface":"a.b"}})");
        write(system + "/relative.json", R"({"schema":1,"id":"relative","name":"R","command":["reader"]})");
        write(system + "/misnamed.json", R"({"schema":1,"id":"other","name":"M","command":["/bin/x"]})");
        write(system + "/Bad-Id.json", R"({"schema":1,"id":"Bad-Id","name":"M","command":["/bin/x"]})");
        write(system + "/nosource.json", R"({"schema":1,"id":"nosource","name":"N"})");
        write(system + "/badpath.json", R"({"schema":1,"id":"badpath","name":"N","dbus":{"service":"a.b","path":"relative","interface":"a.b"}})");
        write(user + "/reader.json", R"({"schema":1,"id":"reader","name":"My reader","command":["/home/u/bin/reader"],"order":5})");
        write(user + "/mine.json", R"({"schema":1,"id":"mine","name":"Mine","command":["/home/u/bin/mine"],"future":"ignored"})");
        QStringList errors;
        const auto list = Care::usageProviders({system, user, d.path() + "/missing"}, &errors);
        QStringList ids; for (const auto &p : list) ids.append(p.id);
        QCOMPARE(ids, (QStringList{"reader", "codex", "mine"})); // by order, then id
        QCOMPARE(errors.size(), 7);
        QCOMPARE(list[0].name, "My reader"); QVERIFY(list[0].file.startsWith(user)); QVERIFY(!list[0].optional);
        QCOMPARE(list[0].timeout, 10000); QCOMPARE(list[0].command, (QStringList{"/home/u/bin/reader"}));
        QVERIFY(list[1].dbus()); QCOMPARE(list[1].timeout, 35000); QCOMPARE(list[1].service, "com.rungic.VoiceAgent");
        // The descriptors this repository ships load.
        for (const auto *file : {"/../agent-usage/claude-code.json", "/../../voice-agent/agent-usage/codex.json"}) {
            const QString path = QStringLiteral(CARE_TESTDATA) + file;
            if (!QFile::exists(path)) continue; // the voice agent's tree is not part of this package's build
            QCOMPARE(Care::validateUsageProvider(Care::readObject(path), QFileInfo(path).fileName()), QString());
        }
    }
    // covers: agent.usage-widget/E3
    void usageViewOrdersProvidersAndChoosesPrimary() {
        Care::Usage u(""); u.setProviders({provider("a", 10), provider("b", 20), provider("c", 30, true)});
        auto v = u.view(100);
        QCOMPARE(v["schema"].toInt(), 2); QCOMPARE(v["providers"].toArray().size(), 2); // optional c not seen yet
        QCOMPARE(v["primary"].toString(), "a"); // nothing active: the first
        QCOMPARE(shown(v, "a")["status"].toString(), "connecting"); QVERIFY(shown(v, "a")["stale"].toBool());
        QCOMPARE(shown(v, "a")["name"].toString(), "A"); QCOMPARE(shown(v, "a")["vendor"].toString(), "V");
        u.snapshot("a", {{"accountKey", "k"}}, 101); u.snapshot("b", {{"accountKey", "k"}}, 101);
        u.record("b", token("k", "s", 50, 50), 102);
        QCOMPARE(u.view(103)["primary"].toString(), "b"); // most recently active
        u.snapshot("a", {{"accountKey", "k"}, {"status", "working"}}, 90);
        QCOMPARE(u.view(104)["primary"].toString(), "a"); // working wins
        u.snapshot("c", {{"status", "ready"}, {"lastActive", 500}}, 105);
        v = u.view(106);
        QCOMPARE(v["providers"].toArray().size(), 3); QCOMPARE(v["providers"].toArray()[2].toObject()["id"].toString(), "c");
        QCOMPARE(v["primary"].toString(), "a");
        u.snapshot("a", {{"accountKey", "k"}, {"status", "ready"}}, 107);
        QCOMPARE(u.view(108)["primary"].toString(), "c");
        QCOMPARE(u.view(108)["updatedAt"].toInteger(), 107);
        u.snapshot("c", {{"available", false}}, 109);
        QCOMPARE(u.view(110)["providers"].toArray().size(), 2); // its agent isn't used here
        Care::Usage none(""); QCOMPARE(none.view(1)["primary"].toString(), QString());
    }
    // covers: agent.usage-widget/E2
    void usageAccountsAreIsolatedPerProvider() {
        Care::Usage u(""); u.setProviders({provider("a"), provider("b")});
        u.snapshot("a", {{"accountKey", "same"}}, 100); u.snapshot("b", {{"accountKey", "same"}}, 100);
        u.record("a", token("same", "s", 300, 300), 101);
        auto v = u.view(102);
        QCOMPARE(shown(v, "a")["tokens"].toObject()["device"].toInteger(), 300);
        QVERIFY(shown(v, "b")["tokens"].toObject()["device"].isNull()); // the same key under another agent is not shared
        u.record("b", token("same", "s", 300, 300), 103); // the same session id under another agent counts separately
        QCOMPARE(shown(u.view(104), "b")["tokens"].toObject()["device"].toInteger(), 300);
        QCOMPARE(shown(u.view(104), "a")["tokens"].toObject()["device"].toInteger(), 300);
    }
    // covers: agent.usage-widget/E5
    void usageStaleAndErrorArePerProvider() {
        Care::Usage u(""); u.setProviders({provider("a"), provider("b")});
        u.snapshot("a", {{"accountKey", "k"}}, 100); u.snapshot("b", {{"accountKey", "k"}}, 100);
        u.failure("b", "B is unavailable");
        auto v = u.view(101);
        QVERIFY(!shown(v, "a")["stale"].toBool()); QCOMPARE(shown(v, "a")["error"].toString(), QString());
        QVERIFY(shown(v, "b")["stale"].toBool()); QCOMPARE(shown(v, "b")["status"].toString(), "error");
        QCOMPARE(shown(v, "b")["error"].toString(), "B is unavailable");
        u.failure("a", {}, true);
        QCOMPARE(shown(u.view(102), "a")["status"].toString(), "offline"); QVERIFY(shown(u.view(102), "a")["stale"].toBool());
        u.snapshot("b", {{"accountKey", "k"}}, 103);
        QVERIFY(!shown(u.view(104), "b")["stale"].toBool()); QCOMPARE(shown(u.view(104), "b")["status"].toString(), "ready");
    }
    // covers: agent.usage-widget/E5
    void usageInstallationIsIndependentAndUnknownAfterTransportFailure() {
        Care::Usage u(""); u.setProviders({provider("a")});
        QVERIFY(shown(u.view(99), "a")["installed"].isNull());
        u.snapshot("a", {{"installed", false}, {"status", "not-installed"}}, 100);
        auto p = shown(u.view(101), "a");
        QCOMPARE(p["status"].toString(), "not-installed");
        QVERIFY(p["installed"].isBool() && !p["installed"].toBool());
        QVERIFY(p["tokens"].toObject()["device"].isNull());
        u.failure("a", {}, true);
        QVERIFY(shown(u.view(102), "a")["installed"].isNull());
        QCOMPARE(shown(u.view(102), "a")["status"].toString(), "offline");
        u.snapshot("a", {{"installed", true}, {"status", "signed-out"}}, 103);
        QCOMPARE(shown(u.view(104), "a")["status"].toString(), "signed-out");
        QVERIFY(shown(u.view(104), "a")["installed"].toBool());
    }
    // covers: agent.usage-widget/E3
    void usageDropsUnknownFields() {
        Care::Usage u(""); u.setProviders({provider("a")});
        u.snapshot("a", {{"accountKey", "secret-key"}, {"email", "someone@example.com"}, {"name", "Spoofed"}, {"id", "other"},
            {"status", "dancing"}, {"model", "m"}, {"tokenEvents", QJsonArray{}},
            {"account", QJsonObject{{"kind", "subscription"}, {"label", "L"}, {"plan", "p"}, {"token", "sk-secret"}}},
            {"tokens", QJsonObject{{"account", 12}, {"device", -3}, {"cost", 5}}},
            {"limits", QJsonArray{QJsonObject{{"id", "x"}, {"usedPercent", 5}, {"secret", "y"}}, QJsonObject{{"id", "no-percent"}}}}}, 100);
        const auto p = shown(u.view(101), "a");
        const auto text = QJsonDocument(p).toJson();
        for (const auto *leak : {"secret", "someone", "Spoofed", "cost", "dancing", "tokenEvents"}) QVERIFY2(!text.contains(leak), leak);
        const auto keys = p.keys();
        QCOMPARE(QSet<QString>(keys.begin(), keys.end()), (QSet<QString>{"id", "name", "vendor", "icon", "status", "account", "model", "tokens", "limits", "updatedAt", "stale", "error", "installed"}));
        QCOMPARE(p["name"].toString(), "A"); QCOMPARE(p["status"].toString(), "ready");
        QCOMPARE(p["account"].toObject().keys(), (QStringList{"kind", "label", "plan"}));
        QCOMPARE(p["tokens"].toObject()["account"].toInteger(), 12); QVERIFY(p["tokens"].toObject()["device"].isNull());
        QCOMPARE(p["limits"].toArray().size(), 1);
        QCOMPARE(p["limits"].toArray()[0].toObject().keys(), (QStringList{"expired", "id", "label", "resetsAt", "usedPercent", "windowMinutes"}));
    }
    // covers: agent.usage-widget/E3
    void usageIconsArePathsThatExist() {
        QTemporaryDir d;
        write(d.path() + "/mark.svg", "<svg xmlns=\"http://www.w3.org/2000/svg\"/>");
        write(d.path() + "/mark.txt", "not an image");
        const auto descriptor = [&d](const QJsonValue &icon) {
            return QJsonObject{{"schema", 1}, {"id", "a"}, {"name", "A"}, {"command", QJsonArray{"/bin/true"}}, {"icon", icon}};
        };
        Care::UsageProvider p;
        QCOMPARE(Care::validateUsageProvider(descriptor(QJsonObject{{"light", d.path() + "/mark.svg"}, {"dark", d.path() + "/missing.svg"}}), "a.json", &p), QString());
        QCOMPARE(p.iconLight, d.path() + "/mark.svg"); QVERIFY(p.iconDark.isEmpty());
        Care::Usage u(""); u.setProviders({p});
        QCOMPARE(shown(u.view(1), "a")["icon"].toObject(), (QJsonObject{{"light", d.path() + "/mark.svg"}}));
        QCOMPARE(Care::validateUsageProvider(descriptor(QJsonObject{{"light", d.path() + "/mark.svg"}, {"dark", d.path() + "/mark.svg"}}), "a.json", &p), QString());
        u.setProviders({p}); QCOMPARE(shown(u.view(1), "a")["icon"].toObject()["dark"].toString(), d.path() + "/mark.svg");
        // Missing, relative, not an image, or only a dark variant: no icon, the agent itself stays.
        for (const auto &bad : {QJsonValue(QJsonObject{{"light", d.path() + "/missing.svg"}}), QJsonValue(QJsonObject{{"light", "mark.svg"}}),
                                QJsonValue(QJsonObject{{"light", d.path() + "/mark.txt"}}), QJsonValue(QJsonObject{{"dark", d.path() + "/mark.svg"}}),
                                QJsonValue(d.path() + "/mark.svg")}) {
            QCOMPARE(Care::validateUsageProvider(descriptor(bad), "a.json", &p), QString());
            u.setProviders({p}); QVERIFY(shown(u.view(1), "a")["icon"].isNull());
        }
    }
    // covers: agent.usage-widget/E2
    void usageAdapterLedgerTokensPassThrough() {
        Care::Usage u(""); u.setProviders({provider("reader")});
        u.snapshot("reader", {{"tokens", QJsonObject{{"device", 1375}, {"today", 1322}}}}, 100);
        const auto t = shown(u.view(101), "reader")["tokens"].toObject();
        QCOMPARE(t["device"].toInteger(), 1375); QCOMPARE(t["today"].toInteger(), 1322); QVERIFY(t["account"].isNull());
    }
    // covers: agent.usage-widget/E4
    void claudeCodeTranscriptsCountEachMessageOnce() {
        QTemporaryDir d; const auto root = d.path() + "/claude";
        const QString fixture = QStringLiteral(CARE_TESTDATA) + "/claude-code/projects/-home-u-proj";
        for (const auto *file : {"/s1.jsonl", "/s2.jsonl", "/s1/subagents/agent-x.jsonl"}) {
            QFile f(fixture + file); QVERIFY(f.open(QIODevice::ReadOnly));
            write(root + "/projects/-home-u-proj" + file, f.readAll());
        }
        const Care::ClaudeCode::Paths paths{{root}, d.path() + "/ledger.json", d.path() + "/statusline.json"};
        const auto now = QDateTime::fromString("2026-09-30T12:00:00Z", Qt::ISODate).toSecsSinceEpoch();
        auto r = Care::ClaudeCode::read(paths, now);
        // msg_A (1115, in two files) + msg_B (53, yesterday) + msg_C (7) + subagent msg_D (200)
        QCOMPARE(r["tokens"].toObject()["device"].toInteger(), 1375);
        QCOMPARE(r["tokens"].toObject()["today"].toInteger(), 1322);
        QCOMPARE(r["model"].toString(), "claude-sonnet-5"); QCOMPARE(r["status"].toString(), "ready");
        QCOMPARE(r["lastActive"].toInteger(), QDateTime::fromString("2026-09-30T02:00:00Z", Qt::ISODate).toSecsSinceEpoch());
        QVERIFY(!r.contains("limits")); QVERIFY(!r.contains("error")); QVERIFY(!r.contains("account"));
        QCOMPARE(Care::ClaudeCode::read(paths, now)["tokens"].toObject()["device"].toInteger(), 1375); // no recount
        QFile s1(root + "/projects/-home-u-proj/s1.jsonl"); QVERIFY(s1.open(QIODevice::Append));
        s1.write(R"({"type":"assistant","message":{"id":"msg_E","usage":{"input_tokens":400,"output_tokens)"); s1.flush();
        QCOMPARE(Care::ClaudeCode::read(paths, now)["tokens"].toObject()["device"].toInteger(), 1375); // half a line waits
        s1.write(R"(":600}},"requestId":"req_E","timestamp":"2026-09-30T11:00:00.000Z","sessionId":"s1","uuid":"a9"})" "\n"); s1.close();
        QCOMPARE(Care::ClaudeCode::read(paths, now)["tokens"].toObject()["device"].toInteger(), 2375);
        const auto during = QDateTime::fromString("2026-09-30T11:01:00Z", Qt::ISODate).toSecsSinceEpoch();
        QCOMPARE(Care::ClaudeCode::read(paths, during)["status"].toString(), "working"); // a response a minute ago
        QVERIFY(QFile::remove(root + "/projects/-home-u-proj/s2.jsonl")); // Claude Code's cleanup
        r = Care::ClaudeCode::read(paths, now + 86400);
        QCOMPARE(r["tokens"].toObject()["device"].toInteger(), 2375); QCOMPARE(r["tokens"].toObject()["today"].toInteger(), 0);
        QCOMPARE(r["status"].toString(), "ready");
        QVERIFY(!(QFileInfo(paths.ledger).permissions() & (QFile::ReadGroup | QFile::ReadOther)));
        // A first read cut short by its time budget continues next time instead of starting over.
        const Care::ClaudeCode::Paths fresh{{root}, d.path() + "/fresh.json", d.path() + "/statusline.json"};
        r = Care::ClaudeCode::read(fresh, now, 0);
        QVERIFY(!r["error"].toString().isEmpty());
        QCOMPARE(Care::ClaudeCode::read(fresh, now)["tokens"].toObject()["device"].toInteger(), 2368); // s2 (msg_C) is gone
        QCOMPARE(Care::ClaudeCode::lineTokens(QJsonObject{{"message", QJsonObject{{"usage", QJsonObject{{"input_tokens", 1},
            {"cache_creation_input_tokens", 9}, {"cache_creation", QJsonObject{{"ephemeral_5m_input_tokens", 2}, {"ephemeral_1h_input_tokens", 3}}}}}}}}), 6);
        const Care::ClaudeCode::Paths nothing{{d.path() + "/none"}, d.path() + "/n.json", d.path() + "/ns.json"};
        QCOMPARE(Care::ClaudeCode::read(nothing, now), (QJsonObject{{"available", false}}));
    }
    // covers: agent.usage-widget/E4
    void claudeCodeLimitsOnlyFromTheDocumentedStatusline() {
        QTemporaryDir d;
        const Care::ClaudeCode::Paths paths{{d.path() + "/none"}, d.path() + "/ledger.json", d.path() + "/statusline.json"};
        QFile f(QStringLiteral(CARE_TESTDATA) + "/claude-code/statusline.json"); QVERIFY(f.open(QIODevice::ReadOnly));
        const auto input = f.readAll();
        const qint64 now = 1738420000;
        QCOMPARE(Care::ClaudeCode::statusline(input, paths, now), "Opus · 5h 24% · 7d 41%");
        const auto kept = QString::fromUtf8(QJsonDocument(Care::readObject(paths.statusline)).toJson());
        QVERIFY(!kept.contains("transcript_path") && !kept.contains("cost") && !kept.contains("/home/u"));
        auto r = Care::ClaudeCode::read(paths, now + 60);
        QVERIFY(!r.contains("tokens")); QCOMPARE(r["model"].toString(), "claude-opus-5-5");
        QCOMPARE(r["account"].toObject()["kind"].toString(), "subscription");
        const auto limits = r["limits"].toArray(); QCOMPARE(limits.size(), 2);
        QCOMPARE(limits[0].toObject()["windowMinutes"].toInt(), 300); QCOMPARE(limits[0].toObject()["usedPercent"].toDouble(), 23.5);
        QCOMPARE(limits[0].toObject()["resetsAt"].toInteger(), 1738425600); QCOMPARE(limits[1].toObject()["windowMinutes"].toInt(), 10080);
        // A new session's input before its first response has no rate_limits: the last ones stay.
        auto next = QJsonDocument::fromJson(input).object(); next.remove("rate_limits");
        Care::ClaudeCode::statusline(QJsonDocument(next).toJson(), paths, now + 120);
        QCOMPARE(Care::ClaudeCode::read(paths, now + 180)["limits"].toArray().size(), 2);
        QVERIFY(!Care::ClaudeCode::read(paths, now + 3721).contains("limits")); // an hour without a new reading
        // A window Claude Code dropped (reset) goes; a malformed percentage (claude-code#52326) is not kept.
        next["rate_limits"] = QJsonObject{{"seven_day", QJsonObject{{"used_percentage", 1738857600}, {"resets_at", 1738857600}}}};
        Care::ClaudeCode::statusline(QJsonDocument(next).toJson(), paths, now + 4000);
        QVERIFY(Care::ClaudeCode::read(paths, now + 4001)["limits"].toArray().isEmpty());
        QCOMPARE(Care::ClaudeCode::statusline("not json", paths, now), QString());
    }
    // covers: agent.care-ledger/E6
    void staleReferencesNeverCreateRecords() {
        QTemporaryDir d; const auto path = d.path() + "/state.json";
        QVERIFY(Care::writeObject(path, {{"schema", 2}, {"items", QJsonObject{{"stale", QJsonValue::Null}}}}));
        Care::Model m(path); QVERIFY(m.load()); QVERIFY(m.list().isEmpty());
        QVERIFY(!m.present("missing", 1, false, 100));
        QVERIFY(!m.update("missing", {{"result", "late"}}));
        QVERIFY(m.updatePlan("missing", {}).contains("error"));
        QVERIFY(m.act("missing", "dismiss", {}, 100).contains("error"));
        QVERIFY(!m.taskEvent("missing", "old-task", {{"type", "finished"}}, 100));
        m.notifiedReceipts({QJsonObject{{"id", "missing"}, {"revision", 1}}}, 100);
        QVERIFY(m.list().isEmpty()); QVERIFY(m.save());
        Care::Model restarted(path); QVERIFY(restarted.load()); QVERIFY(restarted.list().isEmpty());
    }
    // covers: agent.briefing/E7
    void widgetLayoutPreservesUserChoicesAndRemoval() {
        QTemporaryDir d; const auto path = d.path() + "/layout";
        {
            KConfig c(path, KConfig::SimpleConfig);
            auto desktop = c.group("Containments").group("1");
            desktop.writeEntry("plugin", QStringLiteral("org.kde.plasma.mobile.homescreen.folio"));
            auto folio = desktop.group("Folio");
            folio.writeEntry("favorites", QStringLiteral("[]")); // Explicitly empty is a choice.
            folio.writeEntry("pages", QStringLiteral("[[{\"type\":\"application\",\"storageId\":\"existing.desktop\",\"row\":0,\"column\":0}]]"));
            c.group("Containments").group("2").group("Applets").group("99").writeEntry("plugin", QStringLiteral("other.widget"));
            QVERIFY(c.sync());
        }
        QVERIFY(Care::setupWidget(path, {"new.desktop"}));
        {
            KConfig c(path, KConfig::SimpleConfig); auto desktop = c.group("Containments").group("1");
            auto folio = desktop.group("Folio");
            QCOMPARE(folio.readEntry("favorites", QString()), QStringLiteral("[]"));
            auto pages = QJsonDocument::fromJson(folio.readEntry("pages", QString()).toUtf8()).array();
            auto items = pages[0].toArray(); QCOMPARE(items.size(), 3);
            QCOMPARE(items[0].toObject()["storageId"].toString(), QStringLiteral("existing.desktop"));
            QCOMPARE(items[1].toObject()["row"].toInt(), 1);
            QCOMPARE(items[1].toObject()["id"].toInt(), 100);
            QVERIFY(QFile::exists(path + ".before-rungic-suggestions-widget"));
            desktop.group("Applets").group("100").deleteGroup();
            items.removeAt(1); pages[0] = items;
            folio.writeEntry("pages", QString::fromUtf8(QJsonDocument(pages).toJson(QJsonDocument::Compact)));
            QVERIFY(c.sync());
        }
        QVERIFY(Care::setupWidget(path, {"new.desktop"}));
        KConfig c(path, KConfig::SimpleConfig);
        QVERIFY(!c.group("Containments").group("1").group("Applets").hasGroup("100"));
    }
    // covers: agent.briefing/E7
    void widgetLayoutNeverOverwritesOccupiedPage() {
        QTemporaryDir d; const auto path = d.path() + "/layout";
        {
            KConfig c(path, KConfig::SimpleConfig); auto desktop = c.group("Containments").group("1");
            desktop.writeEntry("plugin", QStringLiteral("org.kde.plasma.mobile.homescreen.folio"));
            desktop.group("Folio").writeEntry("pages", QStringLiteral("[[{\"type\":\"widget\",\"id\":12,\"row\":0,\"column\":0,\"gridWidth\":4,\"gridHeight\":5}]]"));
            QVERIFY(c.sync());
        }
        QVERIFY(Care::setupWidget(path, {"known.desktop"}));
        KConfig c(path, KConfig::SimpleConfig); const auto folio = c.group("Containments").group("1").group("Folio");
        const auto pages = QJsonDocument::fromJson(folio.readEntry("pages", QString()).toUtf8()).array();
        QCOMPARE(pages.size(), 2); QCOMPARE(pages[0].toArray()[0].toObject()["id"].toInt(), 12);
        QVERIFY(folio.readEntry("favorites", QString()).contains("known.desktop"));
    }
    // covers: agent.care-ledger/E1 agent.care-ledger/E2
    void persistentSnoozeAndDeduplication() {
        QTemporaryDir d; const auto path = d.path() + "/state.json";
        Care::Model m(path); auto o = item(); const auto id = o["id"].toString();
        QVERIFY(m.load()); QVERIFY(m.observe(o, 100));
        QVERIFY(!m.observe(o, 101)); QCOMPARE(m.list().size(), 1);
        QVERIFY(!m.act(id, "snooze", {{"at", 300}}, 110).contains("error")); QVERIFY(m.save());
        Care::Model restarted(path); QVERIFY(restarted.load());
        restarted.observe(o, 200); QCOMPARE(restarted.get(id)["state"].toString(), "snoozed");
        QVERIFY(restarted.due(299).isEmpty()); QCOMPARE(restarted.due(300).size(), 1);
        QVERIFY(restarted.due(301).isEmpty()); QVERIFY(!restarted.notification(301, false, false).isEmpty());
        restarted.notified({id}, 301); QVERIFY(restarted.notification(302, true, false).isEmpty());
    }
    // covers: agent.care-ledger/E2 agent.care-ledger/E5
    void resolvedBeforeReminder() {
        Care::Model m(""); const auto o = item(); const auto id = o["id"].toString();
        m.observe(o, 100); m.act(id, "snooze", {{"at", 300}}, 110);
        m.reconcile("crashes", {}, 200);
        QVERIFY(m.due(301).isEmpty()); QCOMPARE(m.get(id)["state"].toString(), "resolved");
        m.observe(o, 400); QCOMPARE(m.get(id)["state"].toString(), "new");
    }
    // covers: agent.care-ledger/E3
    void dismissPersistsThroughRepeatedObservations() {
        Care::Model m(""); auto o = item(); const auto id = o["id"].toString();
        m.observe(o, 100); m.act(id, "dismiss", {}, 101); o["body"] = "次数增加";
        m.observe(o, 200); QCOMPARE(m.get(id)["state"].toString(), "dismissed");
        QVERIFY(m.notification(100000, true, false).isEmpty());
        m.act(id, "restore", {}, 300); QCOMPARE(m.get(id)["state"].toString(), "new");
    }
    // covers: agent.care-ledger/E7
    void closedConditionAndNoImplicitExecution() {
        Care::Model m(""); auto o = item(); const auto id = o["id"].toString();
        m.observe(o, 100); QVERIFY(m.act(id, "closed", {}, 101).contains("error"));
        o["process"] = "player"; m.observe(o, 102);
        QVERIFY(m.act(id, "closed", {}, 103).contains("error"));
        QVERIFY(m.due(105, {}).isEmpty()); // A process name is not an application instance.
        QCOMPARE(m.get(id)["state"].toString(), "new");
    }
    // covers: agent.care-ledger/E4
    void ordinaryHintsQuietOutsideFeedAndRespectInhibition() {
        Care::Model m(""); auto o = item(); m.observe(o, 100000);
        QVERIFY(m.notification(100001, false, false).isEmpty());
        QVERIFY(m.notification(100001, true, true).isEmpty());
        QVERIFY(!m.notification(100001, true, false).isEmpty());
        m.notified({o["id"].toString()}, 100001);
        m.observe(item("crash:2"), 100002);
        QVERIFY(m.notification(100002, true, false).isEmpty());
        QVERIFY(m.notification(200002, true, false).isEmpty()); // stale evidence never delivers
        m.observe(item("crash:2"), 200002);
        QVERIFY(!m.notification(200002, true, false).isEmpty());
    }
    // covers: agent.care-ledger/E5
    void upstreamAndTaskCompletionDoNotResolveFaults() {
        Care::Model m(""); auto o = item(); const auto id = o["id"].toString();
        m.observe(o, 100); m.update(id, {{"upstream", QJsonObject{{"state", "merged"}}}});
        m.beginTask(id, "investigate", {}, 101);
        m.recoverTasks(); QCOMPARE(m.get(id)["state"].toString(), "working");
        QCOMPARE(m.get(id)["upstream"].toObject()["state"].toString(), "merged");
    }
    // covers: agent.suggestion-tasks/E6
    void absenceDoesNotLoseTaskOrLateResult() {
        Care::Model m(""); const auto o = item(); const auto id = o["id"].toString();
        m.observe(o, 100);
        const auto task = m.beginTask(id, "investigate", {}, 101)["task"].toObject()["id"].toString();
        m.reconcile("crashes", {}, 102);
        QCOMPARE(m.get(id)["issueState"].toString(), "absent");
        QCOMPARE(m.get(id)["state"].toString(), "working");
        m.recoverTasks(); QCOMPARE(m.get(id)["task"].toObject()["state"].toString(), "recovering");
        QVERIFY(m.taskEvent(id, task, {{"type", "result"}, {"text", "调查结果"}}, 103));
        QVERIFY(m.taskEvent(id, task, {{"type", "finished"}}, 104));
        QCOMPARE(m.get(id)["result"].toString(), "调查结果");
        QCOMPARE(m.get(id)["state"].toString(), "attention");
        QVERIFY(!m.notification(10000, false, false).isEmpty()); // task result does not need a live fault
        m.present(id, m.get(id)["deliveryRevision"].toInteger(), true, 105);
        QCOMPARE(m.get(id)["state"].toString(), "attention"); // viewing does not make a card disappear
        m.act(id, "reviewed", {}, 106);
        QCOMPARE(m.get(id)["state"].toString(), "resolved");
    }
    // covers: agent.suggestion-tasks/E2
    void confirmationBindsExactPlanAndEvidence() {
        Care::Model m(""); auto o = item(); const auto id = o["id"].toString(); m.observe(o, 100);
        m.updatePlan(id, {{"plan", "证据不足，暂不修改"}, {"verification", "未知"}, {"rollback", "无变更"}});
        QVERIFY(!m.get(id)["canApply"].toBool());
        QVERIFY(m.beginTask(id, "apply", m.get(id)["planRevision"].toString(), 101).contains("error"));
        m.updatePlan(id, {{"planStatus", "ready"}, {"plan", "配置 A"}, {"verification", "功能检查"}, {"rollback", "恢复 A"}});
        const auto a = m.get(id)["planRevision"].toString();
        m.updatePlan(id, {{"planStatus", "ready"}, {"plan", "配置 B"}});
        QVERIFY(m.beginTask(id, "apply", a, 102).contains("error"));
        const auto b = m.get(id)["planRevision"].toString();
        auto begun = m.beginTask(id, "apply", b, 103); QVERIFY(!begun.contains("error"));
        m.updatePlan(id, {{"plan", "配置 C"}, {"planStatus", "ready"}});
        QCOMPARE(m.get(id)["task"].toObject()["approvedPlan"].toObject()["plan"].toString(), "配置 B");
        const auto taskId = begun["task"].toObject()["id"].toString();
        m.taskEvent(id, taskId, {{"type", "finished"}}, 104);
        o["evidence"] = QJsonObject{{"version", "new"}}; m.observe(o, 105);
        QVERIFY(!m.get(id)["canApply"].toBool());
        QVERIFY(m.beginTask(id, "apply", m.get(id)["planRevision"].toString(), 106).contains("error"));
    }
    // covers: agent.care-ledger/E4
    void presentationAndNotificationsArePerRevision() {
        Care::Model m(""); const auto a = item("a"), b = item("b");
        m.observe(a, 100000); m.observe(b, 100000);
        const auto id = a["id"].toString();
        QVERIFY(m.present(id, 1, false, 100001));
        QCOMPARE(m.notification(100002, true, false)["ids"].toArray(), QJsonArray{b["id"]});
        const auto receipt = m.notification(100002, true, false)["receipts"].toArray();
        const auto task = m.beginTask(b["id"].toString(), "investigate", {}, 100003)["task"].toObject()["id"].toString();
        m.taskEvent(b["id"].toString(), task, {{"type", "finished"}}, 100004);
        m.notifiedReceipts(receipt, 100005); // notification reply for old revision must not hide the result
        QVERIFY(!m.notification(100006, false, false).isEmpty());
        QVERIFY(!m.present(b["id"].toString(), 1, true, 100007));
        QVERIFY(m.present(b["id"].toString(), 2, true, 100007));
        QVERIFY(m.notification(100008, false, false).isEmpty());
    }
    // covers: agent.suggestion-tasks/E3
    void restartAndReplacedTasksPreserveCorrelation() {
        QTemporaryDir d; const auto path = d.path() + "/state.json";
        Care::Model m(path); const auto o = item(); const auto id = o["id"].toString(); m.observe(o, 100);
        const auto first = m.beginTask(id, "investigate", {}, 101)["task"].toObject()["id"].toString();
        QVERIFY(m.save()); Care::Model restarted(path); QVERIFY(restarted.load()); restarted.recoverTasks();
        QCOMPARE(restarted.get(id)["state"].toString(), "working");
        QVERIFY(restarted.taskEvent(id, first, {{"type", "finished"}}, 102));
        const auto second = restarted.beginTask(id, "investigate", {}, 103)["task"].toObject()["id"].toString();
        QVERIFY(first != second);
        QVERIFY(!restarted.taskEvent(id, first, {{"type", "failed"}, {"text", "late old error"}}, 104));
        QCOMPARE(restarted.get(id)["state"].toString(), "working");
    }
    // covers: agent.care-ledger/E6
    void oldLedgerMigrationKeepsChoicesAndInvalidatesLegacyPlan() {
        QTemporaryDir d; const auto path = d.path() + "/state.json"; auto o = item(); const auto id = o["id"].toString();
        o["state"] = "snoozed"; o["condition"] = "old-process"; o["plan"] = "旧文字";
        o["notified"] = true; o["upstream"] = QJsonObject{{"state", "prepared"}};
        QVERIFY(Care::writeObject(path, {{"schema", 1}, {"items", QJsonObject{{id, o}}}}));
        Care::Model m(path); QVERIFY(m.load()); QVERIFY(QFile::exists(path + ".schema1-backup"));
        QCOMPARE(m.get(id)["reminderState"].toString(), "snoozed");
        QVERIFY(!m.get(id).contains("condition")); QVERIFY(!m.get(id)["canApply"].toBool());
        QCOMPARE(m.get(id)["upstream"].toObject()["state"].toString(), "prepared"); QVERIFY(m.save());
        QCOMPARE(Care::readObject(path)["schema"].toInt(), 2);
    }
    // covers: agent.care-ledger/E4
    void sourceFailureCannotTriggerScheduledReminder() {
        Care::Model m(""); const auto o = item(); const auto id = o["id"].toString(); m.observe(o, 100);
        m.act(id, "snooze", {{"at", 500}}, 101);
        QVERIFY(m.due(501).isEmpty()); // some other source being fresh does not validate this issue
        m.observe(o, 502); QCOMPARE(m.due(503).size(), 1);
        m.act(id, "dismiss", {}, 504); m.reconcile("crashes", {}, 505); m.observe(o, 506);
        QCOMPARE(m.get(id)["state"].toString(), "dismissed");
    }
    // covers: agent.suggestion-tasks/E6
    void completedResultReminderSurvivesIssueAbsence() {
        Care::Model m(""); const auto o = item(); const auto id = o["id"].toString(); m.observe(o, 100);
        const auto task = m.beginTask(id, "investigate", {}, 101)["task"].toObject()["id"].toString();
        m.taskEvent(id, task, {{"type", "finished"}}, 102);
        m.act(id, "snooze", {{"at", 1000}}, 103);
        m.reconcile("crashes", {}, 104);
        QCOMPARE(m.due(1000).size(), 1);
        QVERIFY(!m.notification(1001, false, false).isEmpty());
    }
    // covers: agent.care-ledger/E6
    void corruptedStateIsNotOverwritten() {
        QTemporaryDir d; QFile f(d.path() + "/state.json"); QVERIFY(f.open(QIODevice::WriteOnly)); f.write("broken"); f.close();
        Care::Model m(f.fileName()); QString error; QVERIFY(!m.load(&error)); QVERIFY(!error.isEmpty());
        QVERIFY(f.open(QIODevice::ReadOnly)); QCOMPARE(f.readAll(), QByteArray("broken"));
    }
    // ---- briefing (docs/research/96) ----
    static QJsonObject card(const QString &ref, const QString &title = "Crashes to go through", const QString &kind = "issues") {
        return {{"title", title}, {"body", "Several apps quit unexpectedly this week."}, {"kind", kind}, {"priority", 50},
                {"refs", QJsonArray{ref}}, {"action", QJsonObject{{"label", "Go through them"}, {"prompt", "List them"}}}, {"notify", false}};
    }
    // covers: agent.briefing/E4
    void curatedOutputIsValidatedStrictly() {
        Care::Model m(""); const auto a = item("a"), b = item("b");
        m.observe(a, 100); m.observe(b, 100);
        const auto ia = a["id"].toString(), ib = b["id"].toString();
        auto extra = card(ia); extra["exec"] = "rm -rf /"; extra["id"] = "chosen-by-model";
        auto multi = card(ia, "Two"); multi["refs"] = QJsonArray{ia, ib, ia};
        QJsonArray cards{extra, multi, card("missing"), card(ib, QString(61, 'x')), card(ib, "Unknown kind", "advert"), card(ia, "Duplicate")};
        auto longBody = card(ib, "Long body"); longBody["body"] = QString(161, 'y'); cards.append(longBody);
        auto noAction = card(ib, "No action"); noAction.remove("action"); cards.append(noAction);
        auto badPriority = card(ib, "Priority"); badPriority["priority"] = 101; cards.append(badPriority);
        auto fraction = card(ib, "Fraction"); fraction["priority"] = 1.5; cards.append(fraction);
        auto mixed = card(ib, "Mixed"); mixed["refs"] = QJsonArray{ib, "missing"}; cards.append(mixed);
        auto control = card(ib, QStringLiteral("Line break‮eht")); cards.append(control);
        QStringList dropped;
        const auto valid = Care::Briefing::validate(QJsonObject{{"cards", cards}}, m.list(), &dropped);
        QCOMPARE(valid.size(), 3);
        QVERIFY(!valid[0].toObject().contains("exec"));
        QVERIFY(valid[0].toObject()["id"].toString().startsWith("agent:"));
        QCOMPARE(valid[1].toObject()["refs"].toArray().size(), 2); // duplicates within refs removed
        QCOMPARE(valid[2].toObject()["title"].toString(), QStringLiteral("Line break eht")); // no bidi/line controls
        QVERIFY(dropped.contains("refs") && dropped.contains("text") && dropped.contains("kind") && dropped.contains("duplicate")
                && dropped.contains("action") && dropped.contains("priority"));
        QJsonArray many;
        const QStringList kinds{"attention", "issues", "improvement", "result", "followup", "issues", "attention"};
        for (int i = 0; i < 7; ++i) many.append(card(i % 2 ? ia : ib, QString("Card %1").arg(i), kinds[i]));
        dropped.clear();
        QCOMPARE(Care::Briefing::validate(QJsonObject{{"cards", many}}, m.list(), &dropped).size(), 5);
        QVERIFY(dropped.contains("count"));
        QVERIFY(Care::Briefing::validate(QJsonArray{card(ia)}, m.list()).isEmpty()); // not an object
        Care::Briefing br("");
        QVERIFY(!br.applyAgent(QJsonObject{{"cards", QJsonArray{card("missing")}}}, m.list(), 200));
        QVERIFY(br.applyAgent(QJsonObject{{"cards", QJsonArray{}}}, m.list(), 200)); // nothing needs the user: allowed
        QCOMPARE(br.source(), "agent");
    }
    // covers: agent.briefing/E3
    void curationTriggersOnlyOnMaterialChanges() {
        using namespace Care::BriefingLimits;
        Care::Model m(""); Care::Briefing br(""); auto a = item("a");
        a["evidence"] = QJsonObject{{"package", "app"}, {"reports", 1}};
        QVERIFY(!br.observe(m.list(), 50)); QCOMPARE(br.nextRun(50), 0); // nothing at all: no curation
        m.observe(a, 100);
        QVERIFY(br.observe(m.list(), 100)); QCOMPARE(br.nextRun(100), 100 + Debounce);
        m.observe(a, 160); QVERIFY(!br.observe(m.list(), 160)); // seen again: not material
        a["body"] = "reworded"; m.observe(a, 170); QVERIFY(!br.observe(m.list(), 170));
        a["evidence"] = QJsonObject{{"package", "app"}, {"reports", 2}}; m.observe(a, 180); QVERIFY(!br.observe(m.list(), 180));
        a["evidence"] = QJsonObject{{"package", "app"}, {"reports", 3}}; m.observe(a, 190); QVERIFY(br.observe(m.list(), 190)); // threshold
        QCOMPARE(br.nextRun(190), 190 + Debounce); // debounce restarts with each change
        br.begin(m.list(), 400); QCOMPARE(br.nextRun(400), 0);
        m.observe(item("b"), 410); QVERIFY(br.observe(m.list(), 410));
        QCOMPARE(br.nextRun(410), 400 + MinInterval);
        // A task result that waits for the user shortens the interval.
        const auto id = a["id"].toString();
        m.observe(a, 415); QVERIFY(!br.observe(m.list(), 415));
        const auto task = m.beginTask(id, "investigate", {}, 420)["task"].toObject()["id"].toString();
        QVERIFY(!task.isEmpty());
        m.taskEvent(id, task, {{"type", "finished"}}, 430);
        QVERIFY(br.observe(m.list(), 430)); QCOMPARE(br.nextRun(430), 400 + MinIntervalUrgent);
        br.begin(m.list(), 1000);
        m.reconcile("crashes", {}, 1100); QVERIFY(br.observe(m.list(), 1100)); // resolved is material
        // Daily cap: the 13th curation waits until the first one of the window is 24 h old.
        Care::Briefing capped(""); qint64 t = 10000;
        for (int i = 0; i < DailyCap; ++i, t += 60) capped.begin(m.list(), t);
        QVERIFY(capped.capped(t));
        m.observe(item("c"), t); QVERIFY(capped.observe(m.list(), t));
        QCOMPARE(capped.nextRun(t), 10000 + 86400);
        QVERIFY(!capped.capped(10000 + 86400));
    }
    // covers: agent.briefing/E4
    void fallbackSummarisesFindingsAndResults() {
        Care::Model m(""); QStringList ids;
        for (const auto key : {"a", "b", "c", "d"}) { const auto o = item(key); m.observe(o, 100); ids.append(o["id"].toString()); }
        const auto task = m.beginTask(ids[3], "investigate", {}, 101)["task"].toObject()["id"].toString();
        m.updatePlan(ids[3], {{"conclusion", "Caused by an outdated driver, not confirmed"}});
        m.taskEvent(ids[3], task, {{"type", "finished"}}, 102);
        Care::Briefing br(""); br.applyFallback(m.list(), 103, "unavailable");
        const auto cards = br.cards(m.list());
        QCOMPARE(cards.size(), 2);
        const auto result = cards[0].toObject(), summary = cards[1].toObject();
        QCOMPARE(result["kind"].toString(), "result"); QCOMPARE(result["refs"].toArray(), QJsonArray{ids[3]});
        QCOMPARE(result["body"].toString(), "Caused by an outdated driver, not confirmed");
        QCOMPARE(summary["kind"].toString(), "issues"); QCOMPARE(summary["count"].toInt(), 3);
        QVERIFY(summary["title"].toString().contains("3"));
        QVERIFY(summary["body"].toString().size() <= Care::BriefingLimits::BodyMax);
        const auto view = br.view(m.list(), true, 104);
        QCOMPARE(view["source"].toString(), "fallback"); QCOMPARE(view["error"].toString(), "unavailable");
        QVERIFY(!view["cards"].toArray()[0].toObject()["action"].toObject().contains("prompt"));
    }
    // covers: agent.briefing/E4
    void agentCardsStayAndUnseenFindingsGetServiceCards() {
        Care::Model m(""); const auto a = item("a"), b = item("b"), c = item("c");
        m.observe(a, 100); m.observe(b, 100);
        Care::Briefing br(""); br.observe(m.list(), 100); br.begin(m.list(), 300);
        QVERIFY(br.applyAgent(QJsonObject{{"cards", QJsonArray{card(a["id"].toString())}}}, m.list(), 310));
        QCOMPARE(br.cards(m.list()).size(), 1); // b was seen and left out by the Agent: it stays out
        m.observe(c, 320); br.observe(m.list(), 320);
        const auto cards = br.cards(m.list());
        QCOMPARE(cards.size(), 2);
        QJsonObject service;
        for (const auto &v : cards) if (v.toObject()["origin"] == "service") service = v.toObject();
        QCOMPARE(service["refs"].toArray(), QJsonArray{c["id"]});
    }
    // covers: agent.briefing/E6
    void dismissedCardStaysHiddenUntilMaterialChange() {
        Care::Model m(""); auto a = item("a"); m.observe(a, 100);
        Care::Briefing br(""); br.observe(m.list(), 100);
        const auto id = br.cards(m.list())[0].toObject()["id"].toString();
        QVERIFY(br.dismiss(id, m.list(), 110));
        QVERIFY(br.cards(m.list()).isEmpty());
        a["body"] = "seen again"; m.observe(a, 200); br.observe(m.list(), 200);
        QVERIFY(br.cards(m.list()).isEmpty());
        m.reconcile("crashes", {}, 300); br.observe(m.list(), 300);
        m.observe(a, 400); br.observe(m.list(), 400); // it came back: a material change
        QCOMPARE(br.cards(m.list()).size(), 1);
        QVERIFY(!br.dismiss("fallback:none", m.list(), 410));
    }
    // covers: agent.briefing/E6
    void feedbackReachesTheNextCurationInput() {
        Care::Model m(""); const auto a = item("a"), b = item("b"); m.observe(a, 100); m.observe(b, 100);
        Care::Briefing br(""); br.begin(m.list(), 100);
        br.applyAgent(QJsonObject{{"cards", QJsonArray{card(a["id"].toString(), "Crash A"), card(b["id"].toString(), "Crash B", "attention")}}}, m.list(), 110);
        const auto cards = br.cards(m.list());
        QVERIFY(br.dismiss(cards[0].toObject()["id"].toString(), m.list(), 120));
        QVERIFY(br.opened(cards[1].toObject()["id"].toString(), m.list(), 130));
        const auto input = br.input(m.list(), 140);
        const auto feedback = input["feedback"].toArray();
        QCOMPARE(feedback.size(), 2);
        QCOMPARE(feedback[0].toObject()["action"].toString(), "dismissed");
        QCOMPARE(feedback[1].toObject()["action"].toString(), "opened");
        QCOMPARE(input["currentCards"].toArray().size(), 2);
        bool flagged = false;
        for (const auto &v : input["items"].toArray()) if (v.toObject()["dismissedByUser"].toBool()) flagged = true;
        QVERIFY(flagged);
        QVERIFY(br.input(m.list(), 140 + 15 * 86400)["feedback"].toArray().isEmpty()); // bounded in time
    }
    // covers: agent.briefing/E5
    void curationInputIsRedacted() {
        Care::Model m(""); const auto home = QDir::homePath();
        auto o = Care::observation("svc", "Failed: " + home + "/Documents/tax-2026.pdf", "raw body with details", "fault", 1,
            {{"unit", "sync@jane.doe@example.com.service"}, {"report", "/var/lib/crash/123"}, {"log", "Sep 30 raw log line secret-token"},
             {"package", "app"}, {"reports", 4}});
        o["source"] = "user-services"; m.observe(o, 100);
        m.updatePlan(o["id"].toString(), {{"result", "full report with raw log line"}, {"conclusion", "Config at /home/other/.config/app broke"}});
        Care::Briefing br("");
        const auto text = QString::fromUtf8(QJsonDocument(br.input(m.list(), 200)).toJson());
        QVERIFY(!text.contains(home + "/")); QVERIFY(!text.contains("tax-2026")); QVERIFY(!text.contains("/home/other"));
        QVERIFY(!text.contains("example.com")); QVERIFY(!text.contains("raw log line")); QVERIFY(!text.contains("secret-token"));
        QVERIFY(!text.contains("/var/lib/crash")); QVERIFY(!text.contains("raw body"));
        QVERIFY(text.contains("\"evidence_reports\": 4")); QVERIFY(text.contains("<private path>"));
    }
    // covers: agent.compat-knowledge/E1
    void knowledgeRequiresEvidenceAndCannotExecute() {
        QJsonObject e{{"schema", 1}, {"id", "test"}, {"title", "test"}, {"kind", "policy"}, {"status", "verified"},
            {"explanation", "intentional"}, {"reviewed", "2026-09-29"}, {"match", QJsonObject{{"package", "app"}, {"versions", QJsonArray{"1"}}}},
            {"evidence", QJsonArray{"docs/test"}}};
        QVERIFY(Care::validateKnowledge(e).isEmpty()); e["exec"] = "false";
        QVERIFY(!Care::validateKnowledge(e).isEmpty()); e.remove("exec"); e["evidence"] = QJsonArray{};
        QVERIFY(!Care::validateKnowledge(e).isEmpty());
    }
};
QTEST_GUILESS_MAIN(CareTests)
#include "tests.moc"
