// rungic-workspace-input: the agent's pointer and keyboard in its own workspace (docs/research/91).
//
// An agent workspace is a KWin of its own; nobody is there to allow a RemoteDesktop portal
// session, so the agent's input goes straight to that KWin through its fake-input protocol
// (org_kde_kwin_fake_input, granted to this executable by its desktop file, as to the
// assistant screen's window). Connects to $WAYLAND_DISPLAY and reads one command a line on
// stdin, answering "ok" (or "error ...") when the compositor has it:
//   move X Y          pointer to X Y (global logical coordinates)
//   rel DX DY         pointer by DX DY
//   button CODE 0|1   Linux button code (BTN_LEFT 0x110 = 272), released or pressed
//   axis 0|1 VALUE    vertical or horizontal scroll, in pointer axis units (15 a notch)
//   key KEYSYM 0|1    a keysym through KWin (chord modifiers are explicit)
//   text UTF8_HEX     printable text through KWin; validate the whole request first
#include <QCoreApplication>
#include <QGuiApplication>
#include <QHash>
#include <QVector>
#include <QSocketNotifier>
#include <QWaylandClientExtensionTemplate>
#include <cstdio>
#include <iostream>
#include <string>
#include <unistd.h>
#include <xkbcommon/xkbcommon.h>

#include "qwayland-fake-input.h"
#include "qwayland-keystate.h"

class FakeInput : public QWaylandClientExtensionTemplate<FakeInput>, public QtWayland::org_kde_kwin_fake_input
{
public:
    FakeInput()
        : QWaylandClientExtensionTemplate<FakeInput>(6)
    {
        initialize();
    }
};

// Reject text while modifiers or Caps Lock are active. wl_keyboard modifiers
// are focus-local; KWin's keystate protocol provides the global state.
class KeyState : public QWaylandClientExtensionTemplate<KeyState>, public QtWayland::org_kde_kwin_keystate
{
public:
    KeyState() : QWaylandClientExtensionTemplate<KeyState>(5) { initialize(); }
    QHash<uint32_t, uint32_t> states;
    bool idle(wl_display *display)
    {
        if (!isActive() || QWaylandClientExtension::version() < 5) return false;
        states.clear();
        fetchStates();
        if (wl_display_roundtrip(display) < 0) return false;
        for (uint32_t key : {key_capslock, key_alt, key_control, key_shift, key_meta, key_altgr}) {
            if (states.value(key, UINT32_MAX) != state_unlocked) return false;
        }
        return true;
    }
protected:
    void org_kde_kwin_keystate_stateChanged(uint32_t key, uint32_t state) override { states[key] = state; }
};

int main(int argc, char *argv[])
{
    qputenv("QT_QPA_PLATFORM", "wayland");
    QGuiApplication app(argc, argv);
    QGuiApplication::setDesktopFileName(QStringLiteral("com.rungic.WorkspaceInput"));
    FakeInput input;
    auto *display = qApp->nativeInterface<QNativeInterface::QWaylandApplication>()->display();
    KeyState state;
    wl_display_roundtrip(display);
    bool authenticated = false;

    auto reply = [](const std::string &text) {
        std::cout << text << std::endl;
    };
    QSocketNotifier notifier(STDIN_FILENO, QSocketNotifier::Read);
    QObject::connect(&notifier, &QSocketNotifier::activated, &app, [&]() {
        std::string line;
        if (!std::getline(std::cin, line)) {
            QCoreApplication::quit();
            return;
        }
        if (!input.isActive()) {
            reply("error the compositor offers no fake input");
            return;
        }
        if (!authenticated) {
            input.authenticate(QStringLiteral("Rungic workspace"), QStringLiteral("The agent's pointer and keyboard"));
            authenticated = true;
        }
        wl_display_roundtrip(display);
        char command[16] = {};
        double a = 0, b = 0;
        const int fields = std::sscanf(line.c_str(), "%15s %lf %lf", command, &a, &b);
        const std::string name = fields > 0 ? command : "";
        if (name == "move" && fields == 3) {
            input.pointer_motion_absolute(wl_fixed_from_double(a), wl_fixed_from_double(b));
        } else if (name == "rel" && fields == 3) {
            input.pointer_motion(wl_fixed_from_double(a), wl_fixed_from_double(b));
        } else if (name == "button" && fields == 3) {
            input.button(uint32_t(a), uint32_t(b));
        } else if (name == "axis" && fields == 3) {
            input.axis(uint32_t(a), wl_fixed_from_double(b));
        } else if (name == "key" && fields == 3) {
            if (input.QWaylandClientExtension::version() < 6) {
                reply("error keyboard input requires fake-input version 6");
                return;
            }
            input.keyboard_keysym(uint32_t(a), uint32_t(b));
        } else if (name == "text" && line.size() >= 5 && line[4] == ' ') {
            const QByteArray hex = QByteArray::fromStdString(line.substr(5));
            const QByteArray bytes = QByteArray::fromHex(hex);
            const QString text = QString::fromUtf8(bytes);
            if (bytes.toHex() != hex || text.toUtf8() != bytes) {
                reply("error text must be valid UTF-8 encoded as lowercase hex");
                return;
            }
            if (!state.idle(display)) {
                reply("error text requires known inactive modifiers and Caps Lock");
                return;
            }
            if (input.QWaylandClientExtension::version() < 6) {
                reply("error text input requires fake-input version 6");
                return;
            }
            QVector<xkb_keysym_t> symbols;
            for (auto character : text.toUcs4()) {
                const auto symbol = xkb_utf32_to_keysym(character);
                if (!QChar::isPrint(character) || symbol == XKB_KEY_NoSymbol) {
                    reply("error no text key for Unicode " + std::to_string(character));
                    return;
                }
                symbols.append(symbol);
            }
            // KWin 6.6.6 derives modifiers from its active keymap and restores
            // them after each symbol; it supplies a temporary map for Unicode
            // absent from the layout. Reuse that implementation, as the portal does.
            for (auto symbol : symbols) {
                input.keyboard_keysym(symbol, 1);
                input.keyboard_keysym(symbol, 0);
                if (wl_display_roundtrip(display) < 0) {
                    reply("error compositor disconnected during text input");
                    return;
                }
            }
        } else {
            reply("error unknown command: " + line);
            return;
        }
        wl_display_roundtrip(display);
        reply("ok");
    });
    return app.exec();
}
