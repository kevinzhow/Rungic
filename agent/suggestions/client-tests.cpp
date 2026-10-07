// SPDX-License-Identifier: GPL-2.0-or-later
// covers: agent.usage-widget/E5
#include "client.h"
#include <QDBusConnection>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTest>

class UsageFixture : public QObject {
    Q_OBJECT
    Q_CLASSINFO("D-Bus Interface", "com.rungic.Suggestions")
public:
    bool installed = false;
public Q_SLOTS:
    QString AgentUsage() {
        return QString::fromUtf8(QJsonDocument(QJsonObject{
            {"primary", "codex"}, {"providers", QJsonArray{QJsonObject{
                {"id", "codex"}, {"installed", installed},
                {"status", installed ? "ready" : "not-installed"}, {"stale", false}}}}}).toJson());
    }
};

class ClientTests : public QObject {
    Q_OBJECT
private Q_SLOTS:
    void missingThenDisconnectedThenRecovered() {
        auto bus = QDBusConnection::sessionBus();
        QVERIFY(bus.isConnected());
        UsageFixture fixture;
        QVERIFY(bus.registerObject("/com/rungic/Suggestions", &fixture, QDBusConnection::ExportAllSlots));
        QVERIFY(bus.registerService("com.rungic.Suggestions"));
        UsageClient client;
        QTRY_COMPARE(client.providers().size(), 1);
        QCOMPARE(client.primary()["status"].toString(), "not-installed");
        QCOMPARE(client.primary()["installed"].toBool(), false);

        QVERIFY(bus.unregisterService("com.rungic.Suggestions"));
        client.refresh();
        QTRY_COMPARE(client.primary()["status"].toString(), "offline");
        QVERIFY(client.primary()["installed"].isNull());
        QVERIFY(client.primary()["stale"].toBool());

        fixture.installed = true;
        QVERIFY(bus.registerService("com.rungic.Suggestions"));
        client.refresh();
        QTRY_COMPARE(client.primary()["status"].toString(), "ready");
        QVERIFY(client.primary()["installed"].toBool());
        QVERIFY(!client.primary()["stale"].toBool());
        bus.unregisterService("com.rungic.Suggestions");
        bus.unregisterObject("/com/rungic/Suggestions");
    }
};
QTEST_GUILESS_MAIN(ClientTests)
#include "client-tests.moc"
