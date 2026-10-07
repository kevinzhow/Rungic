// SPDX-License-Identifier: GPL-3.0-or-later
// Public Qt Virtual Keyboard input-method API; no KWin or Qt private ABI.
#include <QGuiApplication>
#include <QInputMethod>
#include <QQmlExtensionPlugin>
#include <QTimer>
#include <QVirtualKeyboardAbstractInputMethod>
#include <QVirtualKeyboardInputContext>
#include <QVirtualKeyboardInputEngine>
#include <QVirtualKeyboardSelectionListModel>
#include "echo.h"
#include "runtime.h"
// rime_api.h defines these as macros; they would break moc output (QMetaType::Bool).
#undef Bool
#undef True
#undef False

// A librime session, and with it the shared user dictionary (runtime.h), exists only while the
// keyboard is in use: opened by the first key Rime has to see, closed once nothing is being
// composed and the panel is hidden or no key came for idleInterval ms (docs/41, 2026-10-03).
class RimeInputMethod : public QVirtualKeyboardAbstractInputMethod {
    Q_OBJECT
    // For tests and diagnostics: whether a session is open, and whether it is a shared one.
    Q_PROPERTY(bool sessionOpen READ sessionOpen NOTIFY sessionChanged)
    Q_PROPERTY(bool sessionShared READ sessionShared NOTIFY sessionChanged)
    Q_PROPERTY(int idleInterval READ idleInterval WRITE setIdleInterval NOTIFY idleIntervalChanged)
    using Engine = QVirtualKeyboardInputEngine;
    using Model = QVirtualKeyboardSelectionListModel;
    RimeRuntime &rime = RimeRuntime::instance();
    RimeApi *api = rime.api;
    RimeRuntime::Session session;
    QTimer idle;
    Engine::InputMode mode = Engine::InputMode::Pinyin;
    QStringList candidates;
    bool composing = false;
    QString baseText;      // the text around the cursor and the cursor when the composition started
    int baseCursor = -1;
    QStringList preedits;  // the preedits shown during this composition (echo.h)

    bool ensureSession(bool sensitive) {
        // A field whose text must not be remembered gets a guest session, which does not learn:
        // librime has no option to stop a session with the user dictionary from learning.
        if (session.id && sensitive && session.shared && !composing) { rime.close(session); emit sessionChanged(); }
        // A guest session moves to the user dictionary between words once it is free again.
        if (session.id && !sensitive && !session.shared && !composing) {
            RimeRuntime::Session shared;
            if (rime.open(shared, true)) { rime.close(session); session = shared; emit sessionChanged(); }
        }
        if (!session.id && (sensitive ? rime.openGuest(session) : rime.open(session))) emit sessionChanged();
        return session.id;
    }
    void release() {
        if (!session.id || composing) return;   // finishing or resetting the composition restarts the timer
        rime.close(session);
        emit sessionChanged();
    }
    void used() { if (session.id) idle.start(); }

    void clearCandidates() {
        composing = false;
        candidates.clear();
        emit selectionListChanged(Model::Type::WordCandidateList);
        emit selectionListActiveItemChanged(Model::Type::WordCandidateList, -1);
    }

    void refresh() {
        auto context = inputContext();
        if (!context || !session.id) return;
        // Read commits before notifying Qt: commit() can synchronously reset us.
        RIME_STRUCT(RimeCommit, commit);
        QString text;
        if (api->get_commit(session.id, &commit)) {
            text = QString::fromUtf8(commit.text);
            api->free_commit(&commit);
        }
        if (!text.isEmpty()) context->commit(text);

        QString preedit;
        RIME_STRUCT(RimeContext, state);
        if (api->get_context(session.id, &state)) {
            preedit = QString::fromUtf8(state.composition.preedit);
            api->free_context(&state);
        }
        candidates.clear();
        RimeCandidateListIterator iterator{};
        if (api->candidate_list_begin(session.id, &iterator)) {
            while (candidates.size() < 200 && api->candidate_list_next(&iterator))
                candidates.append(QString::fromUtf8(iterator.candidate.text));
            api->candidate_list_end(&iterator);
        }
        composing = !preedit.isEmpty();
        if (!composing) preedits.clear();
        else if (preedits.isEmpty() || preedits.last() != preedit) preedits.append(preedit);
        context->setPreeditText(preedit);
        emit selectionListChanged(Model::Type::WordCandidateList);
        emit selectionListActiveItemChanged(Model::Type::WordCandidateList, candidates.isEmpty() ? -1 : 0);
    }

public:
    explicit RimeInputMethod(QObject *parent = nullptr) : QVirtualKeyboardAbstractInputMethod(parent) {
        idle.setSingleShot(true);
        idle.setInterval(5000);
        connect(&idle, &QTimer::timeout, this, &RimeInputMethod::release);
        // Hiding covers focus loss too: Qt hides the panel when no focused item accepts input,
        // and plasma-keyboard hides it when KWin deactivates the text input.
        if (auto im = QGuiApplication::inputMethod())
            connect(im, &QInputMethod::visibleChanged, this, [this] {
                if (!QGuiApplication::inputMethod()->isVisible() && session.id) idle.start(0);
            });
    }
    ~RimeInputMethod() override { rime.close(session); }
    bool sessionOpen() const { return session.id; }
    bool sessionShared() const { return session.shared; }
    int idleInterval() const { return idle.interval(); }
    void setIdleInterval(int ms) { if (ms != idle.interval()) { idle.setInterval(ms); emit idleIntervalChanged(); } }

    QList<Engine::InputMode> inputModes(const QString &) override {
        return rime.ready ? QList<Engine::InputMode>{Engine::InputMode::Pinyin, Engine::InputMode::Latin}
                          : QList<Engine::InputMode>{Engine::InputMode::Latin};
    }
    bool setInputMode(const QString &, Engine::InputMode next) override {
        if (next != mode) reset();
        mode = next;
        return mode == Engine::InputMode::Latin || rime.ready;
    }
    bool setTextCase(Engine::TextCase) override { return true; }
    bool keyEvent(Qt::Key key, const QString &text, Qt::KeyboardModifiers modifiers) override {
        if (!rime.ready || !inputContext() || mode != Engine::InputMode::Pinyin) return false;
        const auto hints = inputContext()->inputMethodHints();
        if (hints.testFlag(Qt::ImhHiddenText)) { reset(); return false; }
        if (modifiers & (Qt::ControlModifier | Qt::AltModifier | Qt::MetaModifier)) return false;

        int symbol = 0;
        if (key == Qt::Key_Backspace) symbol = 0xff08;
        else if (key == Qt::Key_Return || key == Qt::Key_Enter) symbol = 0xff0d;
        else if (key == Qt::Key_Escape) symbol = 0xff1b;
        else if (key == Qt::Key_Space) symbol = 0x20;
        else if (text.size() == 1 && text.front().unicode() < 128) symbol = text.front().unicode();

        // Non-ASCII punctuation belongs to the layout. Finish composing first.
        if (!symbol) {
            if (!text.isEmpty() && composing) { api->commit_composition(session.id); refresh(); used(); }
            return false;
        }
        // Editing keys reach Rime only during a composition; they need no session otherwise.
        if (!composing && symbol > 0xff00) return false;
        if (!ensureSession(hints.testFlag(Qt::ImhSensitiveData))) return false;
        if (!composing) {
            baseText = inputContext()->surroundingText();
            baseCursor = inputContext()->cursorPosition();
            preedits.clear();
        }
        const bool handled = api->process_key(session.id, symbol, 0);
        refresh();
        used();
        return handled;
    }
    QList<Model::Type> selectionLists() override { return {Model::Type::WordCandidateList}; }
    int selectionListItemCount(Model::Type type) override {
        return type == Model::Type::WordCandidateList ? candidates.size() : 0;
    }
    QVariant selectionListData(Model::Type type, int index, Model::Role role) override {
        if (type != Model::Type::WordCandidateList || index < 0 || index >= candidates.size()) return {};
        if (role == Model::Role::Display) return candidates.at(index);
        if (role == Model::Role::WordCompletionLength) return 0;
        return {};
    }
    void selectionListItemSelected(Model::Type type, int index) override {
        if (type == Model::Type::WordCandidateList && session.id && index >= 0 && index < candidates.size()) {
            api->select_candidate(session.id, index);
            refresh();
            used();
        }
    }
    // Qt contract: reset must not write to the input context.
    void reset() override {
        if (session.id) api->clear_composition(session.id);
        clearCandidates();
        used();
    }
    // An outside change of the text or cursor finishes the composition; our own preedit coming back
    // from the application does not (echo.h).
    void update() override {
        const auto c = inputContext();
        if (session.id && composing && !(c && preeditEcho(baseText, baseCursor, preedits, c->surroundingText(), c->cursorPosition()))) {
            api->commit_composition(session.id);
            refresh();
        }
        used();
    }

signals:
    void sessionChanged();
    void idleIntervalChanged();
};

class RungicRimePlugin : public QQmlExtensionPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QQmlExtensionInterface_iid)
public:
    void registerTypes(const char *uri) override {
        qmlRegisterType<RimeInputMethod>(uri, 1, 0, "RimeInputMethod");
    }
};
#include "plugin.moc"
