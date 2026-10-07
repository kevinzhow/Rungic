// rungic-agent-screen-window --desktop | --workspace N | --director (docs/65, docs/58, docs/research/91): the floating
// window on the phone of desktop mode (the user's second desktop screen) or of the assistant's
// screen (the agent's workspace N). Started by rungic-desktop-mode or rungic-agent-screen once its
// screen is on; quits when it is turned off. Each has a desktop file of its own (its grants).
#include <KLocalizedQmlContext>
#include <KLocalizedString>
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickWindow>
#include <memory>

#include "agentscreen.h"
#include "floater.h"
#include "phonekeyboard.h"

int main(int argc, char *argv[])
{
    // Fullscreen's keyboard is the window's own (FloatingKeyboard, docs/research/97 §19.9): Qt Virtual
    // Keyboard, in the window and turned with the picture, with the phone keyboard's layouts and Rime
    // (rungic-plasma-input, its dictionary shared with the phone's: one keyboard in use at a time).
    // So this process has no Wayland text-input: text the phone KWin's input method commits to it
    // (the TV's keyboard mode) comes as keys.
    qputenv("QT_IM_MODULE", "qtvirtualkeyboard");
    qputenv("QT_VIRTUALKEYBOARD_DESKTOP_DISABLE", "1");
    qputenv("QT_VIRTUALKEYBOARD_LAYOUT_PATH", "/usr/share/rungic-rime/plasma/keyboard/layouts");
    QGuiApplication app(argc, argv);
    int workspace = 0;
    const QStringList args = app.arguments();
    const int at = args.indexOf(QStringLiteral("--workspace"));
    if (at >= 0 && at + 1 < args.size())
        workspace = qMax(1, args.at(at + 1).toInt());
    // --director: the assistant's screens together (docs/58), its window a workspace's otherwise.
    const bool directing = args.contains(QStringLiteral("--director"));
    if (directing)
        workspace = 1;
    app.setApplicationName(QStringLiteral("rungic-agent-screen"));
    app.setDesktopFileName(workspace > 0 ? QStringLiteral("com.rungic.AgentScreen") : QStringLiteral("com.rungic.DesktopMode"));
    app.setQuitOnLastWindowClosed(false);

    std::unique_ptr<Director> director;
    if (directing) {
        director = std::make_unique<Director>();
        if (director->empty())
            return 0;
    }
    AgentScreen *single = directing ? nullptr : new AgentScreen(workspace, &app);
    Floater floater;
    // Texts follow the Plasma language (catalog rungic-agent-screen, po/).
    KLocalizedString::setApplicationDomain("rungic-agent-screen");
    QQmlApplicationEngine engine;
    engine.addImportPath(QStringLiteral("/usr/lib/rungic-rime/qml"));   // Rungic.Rime, for the Chinese layout
    engine.addImportPath(QStringLiteral("/usr/lib/rungic-agent-screen/qml"));   // the keyboard's style
    KLocalization::setupLocalizedContext(&engine);
    engine.rootContext()->setContextProperty(QStringLiteral("agent"), single);
    engine.rootContext()->setContextProperty(QStringLiteral("director"), director.get());
    engine.rootContext()->setContextProperty(QStringLiteral("floater"), &floater);
    engine.rootContext()->setContextProperty(QStringLiteral("phoneKeyboardLocales"), phoneKeyboardLocales());
    engine.loadFromModule("com.rungic.agentscreen", "Main");
    if (engine.rootObjects().isEmpty())
        return 1;
    auto window = qobject_cast<QQuickWindow *>(engine.rootObjects().first());
    floater.attach(window);
    window->setProperty("ready", true);
    return app.exec();
}
