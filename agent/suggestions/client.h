// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <QObject>
#include <QVariantList>
#include <QDBusServiceWatcher>
#include <QtQml/qqmlregistration.h>

class SuggestionsClient : public QObject {
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(QVariantList items READ items NOTIFY changed)
    Q_PROPERTY(QVariantList groups READ groups NOTIFY changed)
    Q_PROPERTY(QVariantList historyGroups READ historyGroups NOTIFY changed)
    Q_PROPERTY(QStringList coverage READ coverage NOTIFY changed)
    Q_PROPERTY(QString error READ error NOTIFY changed)
    Q_PROPERTY(bool busy READ busy NOTIFY changed)
    // The curated briefing (docs/research/96): at most 5 cards, in order. A card: id, title, body,
    // kind (attention|issues|improvement|result|followup), priority, refs (ledger ids), count,
    // action {label}, notify, origin (agent|service). Plain text: show with Text.PlainText.
    Q_PROPERTY(QVariantList cards READ cards NOTIFY changed)
    // revision, generatedAt, source (agent|fallback), basisRevision, curating, error,
    // backgroundCuration, pending, nextCuration.
    Q_PROPERTY(QVariantMap briefing READ briefing NOTIFY changed)
public:
    explicit SuggestionsClient(QObject *parent = nullptr);
    QVariantList cards() const { return m_cards; }
    QVariantMap briefing() const { return m_briefing; }
    // Opens a new Agent conversation about the card (its findings presented in the chat).
    Q_INVOKABLE void openCard(const QString &id);
    // "Not now": hidden until one of its records changes materially.
    Q_INVOKABLE void dismissCard(const QString &id);
    // Asks the Agent to sort the suggestions again now (also when background curation is off).
    Q_INVOKABLE void curate();
    // The card was on screen (opened: tapped), so its records aren't notified again.
    Q_INVOKABLE void presentCard(const QString &id, bool opened = false);
    QVariantList items() const { return m_items; }
    QVariantList groups() const { return m_groups; }
    QVariantList historyGroups() const { return m_historyGroups; }
    QStringList coverage() const { return m_coverage; }
    QString error() const { return m_error; }
    bool busy() const { return m_busy; }
    Q_INVOKABLE void act(const QString &id, const QString &action, const QVariantMap &args = {});
    Q_INVOKABLE void refresh();
    Q_INVOKABLE void scan();
    Q_INVOKABLE void open(const QString &id = {});
    Q_INVOKABLE void openAgent(bool usage = false);
    Q_INVOKABLE void signIn();
    Q_INVOKABLE void openCodex();
    Q_INVOKABLE void conversation(const QString &id);
    Q_INVOKABLE void watching(bool visible);
    Q_INVOKABLE void present(const QVariantList &receipts, bool opened);
    Q_INVOKABLE double tomorrow(int hour = 10) const;
Q_SIGNALS:
    void changed();
    void replied(const QString &id, const QString &action, const QVariantMap &result);
private:
    Q_SLOT void onChanged();
    void call(const QString &method, const QVariantList &args, const QString &id = {}, const QString &action = {});
    void launch(const QStringList &arguments);
    QVariantList m_items, m_groups, m_historyGroups, m_cards;
    QVariantMap m_briefing;
    QStringList m_coverage;
    QString m_error;
    bool m_busy = false;
    bool m_watching = false;
    QDBusServiceWatcher m_watcher;
};

// AgentUsage (schema 2, docs/research/95): `data` is the whole reply, `providers` its agents in order
// and `primary` the one to show first ({} when there is none). A lost service keeps the last values,
// marks them stale and sets data.error.
class UsageClient : public QObject {
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(QVariantMap data READ data NOTIFY changed)
    Q_PROPERTY(QVariantList providers READ providers NOTIFY changed)
    Q_PROPERTY(QVariantMap primary READ primary NOTIFY changed)
public:
    explicit UsageClient(QObject *parent = nullptr);
    QVariantMap data() const { return m_data; }
    QVariantList providers() const { return m_data.value("providers").toList(); }
    QVariantMap primary() const;
    Q_INVOKABLE void refresh();
Q_SIGNALS:
    void changed();
private Q_SLOTS:
    void onUsageChanged() { refresh(); }
private:
    QVariantMap m_data;
    bool m_pending = false;
};
