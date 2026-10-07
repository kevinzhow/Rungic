// SPDX-License-Identifier: GPL-2.0-or-later
#include "client.h"
#include <KLocalizedString>
#include <QDBusConnection>
#include <QDBusInterface>
#include <QDBusPendingCallWatcher>
#include <QDBusPendingReply>
#include <QDateTime>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QProcessEnvironment>
#include <QGuiApplication>
#include <KWaylandExtras>
#include <KWindowSystem>
#include <QTimer>

static constexpr auto BusName = "com.rungic.Suggestions";
static constexpr auto BusPath = "/com/rungic/Suggestions";
SuggestionsClient::SuggestionsClient(QObject *parent) : QObject(parent),
    m_watcher(BusName, QDBusConnection::sessionBus(), QDBusServiceWatcher::WatchForOwnerChange) {
    QDBusConnection::sessionBus().connect(BusName, BusPath, BusName, "Changed", this, SLOT(onChanged()));
    connect(&m_watcher, &QDBusServiceWatcher::serviceOwnerChanged, this,
        [this](const QString &, const QString &, const QString &owner) {
            if (owner.isEmpty()) { m_error = i18n("Reconnecting to the suggestions service"); Q_EMIT changed(); }
            else { refresh(); watching(m_watching); }
        });
    QTimer::singleShot(0, this, &SuggestionsClient::refresh);
}
void SuggestionsClient::onChanged() { refresh(); }
void SuggestionsClient::call(const QString &method, const QVariantList &args, const QString &id, const QString &action) {
    m_busy = true; Q_EMIT changed();
    auto message = QDBusMessage::createMethodCall(BusName, BusPath, BusName, method);
    message.setArguments(args);
    auto *w = new QDBusPendingCallWatcher(QDBusConnection::sessionBus().asyncCall(message, method == "OpenCard" ? 70000 : -1), this);
    connect(w, &QDBusPendingCallWatcher::finished, this, [this, w, method, id, action] {
        QDBusPendingReply<QString> reply = *w; w->deleteLater(); m_busy = false;
        if (reply.isError()) { m_error = reply.error().message(); Q_EMIT changed(); return; }
        const auto result = QJsonDocument::fromJson(reply.value().toUtf8()).object();
        m_error = result.value("error").toString();
        if (method == "List" && m_error.isEmpty()) {
            m_groups = result.value("groups").toArray().toVariantList();
            m_historyGroups = result.value("historyGroups").toArray().toVariantList();
            m_items = result.value("items").toArray().toVariantList(); m_coverage.clear();
            auto briefing = result.value("briefing").toObject();
            m_cards = briefing.take("cards").toArray().toVariantList();
            m_briefing = briefing.toVariantMap();
            for (const auto &v : result.value("coverage").toArray()) m_coverage.append(v.toString());
        } else {
            if (method == "OpenCard" && result.contains("conversation")) conversation(result.value("conversation").toString());
            Q_EMIT replied(id, action, result.toVariantMap());
        }
        Q_EMIT changed();
    });
}
void SuggestionsClient::refresh() { call("List", {}); }
void SuggestionsClient::scan() {
    auto message = QDBusMessage::createMethodCall(BusName, BusPath, BusName, "Refresh");
    QDBusConnection::sessionBus().asyncCall(message);
}
void SuggestionsClient::act(const QString &id, const QString &action, const QVariantMap &args) {
    call("Act", {id, action, QString::fromUtf8(QJsonDocument(QJsonObject::fromVariantMap(args)).toJson(QJsonDocument::Compact))}, id, action);
}
void SuggestionsClient::openCard(const QString &id) { call("OpenCard", {id}, id, "openCard"); }
void SuggestionsClient::dismissCard(const QString &id) { call("DismissCard", {id}, id, "dismissCard"); }
void SuggestionsClient::curate() { call("Curate", {}, {}, "curate"); }
void SuggestionsClient::presentCard(const QString &id, bool opened) {
    auto message = QDBusMessage::createMethodCall(BusName, BusPath, BusName, "CardPresented");
    message.setArguments({id, opened}); QDBusConnection::sessionBus().asyncCall(message);
}
void SuggestionsClient::launch(const QStringList &arguments) {
    const auto start = [arguments](const QString &token) {
        QProcess process;
        auto environment = QProcessEnvironment::systemEnvironment();
        environment.remove("XDG_ACTIVATION_TOKEN");
        if (!token.isEmpty()) environment.insert("XDG_ACTIVATION_TOKEN", token);
        process.setProcessEnvironment(environment);
        process.setProgram("/usr/bin/rungic-voice-assistant");
        process.setArguments(arguments);
        process.startDetached();
    };
    if (auto *window = QGuiApplication::focusWindow(); window && KWindowSystem::isPlatformWayland())
        KWaylandExtras::xdgActivationToken(window, "com.rungic.VoiceAssistant").then(this, start);
    else start({});
}
void SuggestionsClient::open(const QString &id) { launch({"--suggestion", id}); }
void SuggestionsClient::openAgent(bool usage) { launch({usage ? "--usage" : "--agent"}); }
void SuggestionsClient::signIn() { launch({"--sign-in"}); }
void SuggestionsClient::openCodex() { launch({"--codex"}); }
void SuggestionsClient::conversation(const QString &id) { if (!id.isEmpty()) launch({"--conversation", id}); }
void SuggestionsClient::watching(bool visible) {
    m_watching = visible;
    auto message = QDBusMessage::createMethodCall(BusName, BusPath, BusName, "SetVisible");
    message.setArguments({visible}); QDBusConnection::sessionBus().asyncCall(message);
}
void SuggestionsClient::present(const QVariantList &receipts, bool opened) {
    auto message = QDBusMessage::createMethodCall(BusName, BusPath, BusName, "Presented");
    message.setArguments({QString::fromUtf8(QJsonDocument(QJsonArray::fromVariantList(receipts)).toJson(QJsonDocument::Compact)), opened});
    QDBusConnection::sessionBus().asyncCall(message);
}
double SuggestionsClient::tomorrow(int hour) const {
    return QDateTime(QDate::currentDate().addDays(1), QTime(qBound(0, hour, 23), 0)).toSecsSinceEpoch();
}

UsageClient::UsageClient(QObject *parent) : QObject(parent) {
    QDBusConnection::sessionBus().connect(BusName, BusPath, BusName, "UsageChanged", this, SLOT(onUsageChanged()));
    QTimer::singleShot(0, this, &UsageClient::refresh);
}
void UsageClient::refresh() {
    if (m_pending) return;
    m_pending = true;
    auto request = QDBusMessage::createMethodCall(BusName, BusPath, BusName, "AgentUsage");
    auto *w = new QDBusPendingCallWatcher(QDBusConnection::sessionBus().asyncCall(request, 5000), this);
    connect(w, &QDBusPendingCallWatcher::finished, this, [this, w] {
        QDBusPendingReply<QString> reply = *w; w->deleteLater(); m_pending = false;
        if (reply.isError()) {
            QVariantList stale;
            for (auto provider : providers()) {
                auto p = provider.toMap();
                p["stale"] = true; p["status"] = "offline"; p["installed"] = QVariant();
                stale.append(p);
            }
            m_data["providers"] = stale;
            m_data["error"] = i18n("The usage service isn't connected yet");
        } else m_data = QJsonDocument::fromJson(reply.value().toUtf8()).object().toVariantMap();
        Q_EMIT changed();
    });
}
QVariantMap UsageClient::primary() const {
    const auto id = m_data.value("primary").toString();
    for (const auto &provider : providers()) if (provider.toMap().value("id") == id) return provider.toMap();
    return {};
}
