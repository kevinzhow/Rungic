// SPDX-License-Identifier: GPL-2.0-or-later
// Voice assistant: chat list with push-to-talk for the Codex voice agent (docs/59).
//   --overlay            the resident overlay the Home button brings up (docs/67)
//   --conversation ID    open that conversation (in the running app, if there is one)
#include <KColorScheme>
#include <KLocalizedQmlContext>
#include <KLocalizedString>
#include <KSharedConfig>
#include <KWindowSystem>

#include <QDBusConnection>
#include <QDBusMessage>
#include <QGuiApplication>
#include <QIcon>
#include <QQmlApplicationEngine>
#include <QQuickStyle>
#include <QQuickWindow>
#include <QStandardPaths>

#include <functional>

#include "overlay.h"

// One app: a second start hands its conversation to the first (com.rungic.VoiceAssistantApp).
class AppInstance : public QObject
{
    Q_OBJECT
    Q_CLASSINFO("D-Bus Interface", "com.rungic.VoiceAssistantApp")
public:
    explicit AppInstance(QQmlApplicationEngine *engine)
        : QObject(engine)
        , m_engine(engine)
    {
    }

public Q_SLOTS:
    Q_SCRIPTABLE void OpenPage(const QString &page, const QString &token) {
        KWindowSystem::setCurrentXdgActivationToken(token);
        const auto roots = m_engine->rootObjects();
        auto *window = roots.isEmpty() ? nullptr : qobject_cast<QQuickWindow *>(roots.constFirst());
        if (!window) return;
        QMetaObject::invokeMethod(window, "openAgentPage", Q_ARG(QVariant, page));
        window->showNormal(); window->raise(); KWindowSystem::activateWindow(window);
    }
    Q_SCRIPTABLE void OpenActivated(bool suggestions, const QString &id, const QString &token)
    {
        KWindowSystem::setCurrentXdgActivationToken(token);
        if (suggestions) OpenSuggestions(id);
        else Open(id);
    }
    Q_SCRIPTABLE void OpenSuggestions(const QString &id)
    {
        const auto roots = m_engine->rootObjects();
        auto *window = roots.isEmpty() ? nullptr : qobject_cast<QQuickWindow *>(roots.constFirst());
        if (!window) return;
        QMetaObject::invokeMethod(window, "openSuggestions", Q_ARG(QVariant, id));
        window->showNormal(); window->raise(); KWindowSystem::activateWindow(window);
    }
    Q_SCRIPTABLE void Open(const QString &conversation)
    {
        const auto roots = m_engine->rootObjects();
        auto *window = roots.isEmpty() ? nullptr : qobject_cast<QQuickWindow *>(roots.constFirst());
        if (!window) {
            return;
        }
        if (!conversation.isEmpty()) {
            QMetaObject::invokeMethod(window, "openConversation", Q_ARG(QVariant, conversation));
        }
        window->showNormal();
        window->raise();
        KWindowSystem::activateWindow(window);
    }

private:
    QQmlApplicationEngine *m_engine;
};

// Runs a function on a signal of a QML object (connected by name: the design system is a
// QML module this app does not link).
class SchemeRelay : public QObject
{
    Q_OBJECT
public:
    SchemeRelay(std::function<void()> apply, QObject *parent)
        : QObject(parent)
        , m_apply(std::move(apply))
    {
    }
public Q_SLOTS:
    void apply() { m_apply(); }

private:
    std::function<void()> m_apply;
};

int main(int argc, char *argv[])
{
    // Qt can consume the environment token during application initialization.
    // Preserve it for forwarding to an already running instance.
    const QString activationToken = qEnvironmentVariable("XDG_ACTIVATION_TOKEN");
    QGuiApplication app(argc, argv);
    QGuiApplication::setApplicationName(QStringLiteral("rungic-voice-assistant"));
    // Words in the Plasma session's language (catalog rungic-voice-assistant, po/); the design
    // system's controls bring their own (rungic-design).
    KLocalizedString::setApplicationDomain(QByteArrayLiteral("rungic-voice-assistant"));
    // QML Settings (the app's choices, docs/87) need an organisation: ~/.config/Rungic/.
    QGuiApplication::setOrganizationName(QStringLiteral("Rungic"));
    QGuiApplication::setOrganizationDomain(QStringLiteral("rungic.com"));
    QGuiApplication::setApplicationDisplayName(i18nc("@title the app's name", "Voice Assistant"));
    QGuiApplication::setDesktopFileName(QStringLiteral("com.rungic.VoiceAssistant"));
    QGuiApplication::setWindowIcon(QIcon::fromTheme(QStringLiteral("audio-input-microphone")));
    if (qEnvironmentVariableIsEmpty("QT_QUICK_CONTROLS_STYLE")) {
        QQuickStyle::setStyle(QStringLiteral("org.kde.desktop"));
    }
    const QStringList args = app.arguments();
    const bool overlay = args.contains(QStringLiteral("--overlay"));
    const qsizetype at = args.indexOf(QStringLiteral("--conversation"));
    const QString conversation = at >= 0 && at + 1 < args.size() ? args.at(at + 1) : QString();
    const qsizetype suggestionAt = args.indexOf(QStringLiteral("--suggestion"));
    const bool suggestions = suggestionAt >= 0;
    const QString page = args.contains("--codex") ? "codex" : args.contains("--sign-in") ? "sign-in" : args.contains("--usage") ? "usage" : args.contains("--agent") ? "agent" : "";
    const QString suggestion = suggestions ? args.value(suggestionAt + 1) : QString();
    auto bus = QDBusConnection::sessionBus();

    QQmlApplicationEngine engine;
    // i18n() and friends in the app's QML (the overlay's too).
    KLocalization::setupLocalizedContext(&engine);
    QObject::connect(&engine, &QQmlApplicationEngine::objectCreationFailed, &app, [] { QCoreApplication::exit(1); },
                     Qt::QueuedConnection);
    if (overlay) {
        Overlay *object = Overlay::instance();
        engine.loadFromModule("com.rungic.voiceassistant", "AssistantOverlay");
        if (engine.rootObjects().isEmpty()) {
            return 1;
        }
        object->setWindow(qobject_cast<QQuickWindow *>(engine.rootObjects().constFirst()));
        if (!bus.registerService(QStringLiteral("com.rungic.VoiceAssistant"))
            || !bus.registerObject(QStringLiteral("/Assistant"), object, QDBusConnection::ExportScriptableSlots)) {
            qWarning("com.rungic.VoiceAssistant: already running or no session bus");
            return 1;
        }
        return app.exec();
    }
    if (!bus.registerService(QStringLiteral("com.rungic.VoiceAssistantApp"))) {
        QDBusMessage open = QDBusMessage::createMethodCall(QStringLiteral("com.rungic.VoiceAssistantApp"), QStringLiteral("/App"),
                                                           QStringLiteral("com.rungic.VoiceAssistantApp"), QStringLiteral("OpenActivated"));
        open.setArguments({suggestions, suggestions ? suggestion : conversation, activationToken});
        if (!page.isEmpty()) {
            open = QDBusMessage::createMethodCall("com.rungic.VoiceAssistantApp", "/App", "com.rungic.VoiceAssistantApp", "OpenPage");
            open.setArguments({page, activationToken});
        }
        bus.call(open);
        return 0;
    }
    // The app's own colours, dark or light as its theme is (docs/59, docs/87): the design
    // system's Theme follows the system unless the app's settings choose. Declared through
    // KDE_COLOR_SCHEME_PATH: the platform theme hands it to KWin (the KDE palette protocol)
    // and Kirigami reads it, so the shell's status bar and navigation panel take the same
    // colours. Applied again when the theme switches.
    QObject *theme = engine.singletonInstance<QObject *>("com.rungic.design", "Theme");
    const auto applyScheme = [&app, theme] {
        const bool dark = theme ? theme->property("dark").toBool() : true;
        const QString name = dark ? QStringLiteral("RungicVoiceAssistant.colors") : QStringLiteral("RungicVoiceAssistantLight.colors");
        const QString scheme = QStandardPaths::locate(QStandardPaths::GenericDataLocation, QStringLiteral("rungic-voice-assistant/") + name);
        if (!scheme.isEmpty()) {
            app.setProperty("KDE_COLOR_SCHEME_PATH", scheme);
            QGuiApplication::setPalette(KColorScheme::createApplicationPalette(KSharedConfig::openConfig(scheme)));
        }
    };
    applyScheme();
    if (theme) {
        QObject::connect(theme, SIGNAL(darkChanged()), new SchemeRelay(applyScheme, &app), SLOT(apply()));
    }
    engine.setInitialProperties({{QStringLiteral("initialConversation"), conversation},
                                 {QStringLiteral("initialPage"), page}, {QStringLiteral("initialSuggestions"), suggestions}, {QStringLiteral("initialSuggestion"), suggestion}});
    engine.loadFromModule("com.rungic.voiceassistant", "Main");
    bus.registerObject(QStringLiteral("/App"), new AppInstance(&engine), QDBusConnection::ExportScriptableSlots);
    return app.exec();
}

#include "main.moc"
