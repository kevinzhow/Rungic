# SPDX-License-Identifier: MIT
"""rungic-a11y drives a Qt Quick application by the names of its controls (docs/55), on a headless KWin
with the session's AT-SPI bus: accessibility starts off; turned on, the running application registers
within seconds without a restart; buttons found by name are pressed (C 7 + 8 = gives 15) and a text
field is filled, with no coordinates; then accessibility is turned off again.

The application stands in for Kalk: a small calculator of Qt Quick Controls buttons. (Kalk's own
keypad draws its keys without AT-SPI actions; those are tapped through Android input, which the phone
checks: acceptance app.launch.)"""
import json
import os
import subprocess
import time
from pathlib import Path

import harness

A11Y = ['python3', '/src/system/diagnostics/rungic-a11y']
APP = 'rungic-calc-standin'
QML = r'''
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
ApplicationWindow {
    width: 360; height: 640; visible: true; title: "calculator stand-in"
    property string shown: "0"
    property real acc: 0
    property string op: ""
    property bool fresh: true
    function digit(d) { shown = fresh ? d : shown + d; fresh = false }
    function apply() { var v = Number(shown); acc = op === "+" ? acc + v : v; shown = String(acc); fresh = true }
    ColumnLayout {
        anchors.fill: parent
        Label { objectName: "display"; text: shown; font.pixelSize: 40 }
        GridLayout {
            columns: 3
            Repeater {
                model: ["7", "8", "+", "=", "C"]
                Button {
                    text: modelData
                    onClicked: {
                        if (text === "C") { shown = "0"; acc = 0; op = ""; fresh = true }
                        else if (text === "+") { apply(); op = "+" }
                        else if (text === "=") { apply(); op = "" }
                        else digit(text)
                    }
                }
            }
        }
    }
}
'''
MAIN = r'''
#include <QApplication>
#include <QLineEdit>
#include <QQmlApplicationEngine>
#include <QUrl>
int main(int argc, char **argv) {
    QApplication app(argc, argv);
    QQmlApplicationEngine engine;
    engine.load(QUrl::fromLocalFile(QString::fromLocal8Bit(argv[1])));
    // Qt Quick's text fields offer no AT-SPI EditableText (docs/55: on the phone such fields are
    // focused and typed into through Android input); a widget line edit does.
    QLineEdit note;
    note.setAccessibleName("note");
    note.setWindowTitle("note");
    note.show();
    return engine.rootObjects().isEmpty() ? 1 : app.exec();
}
'''


def a11y(*args):
    done = subprocess.run([*A11Y, *args], capture_output=True, text=True, timeout=60)
    if done.returncode:
        raise harness.Failed(f'rungic-a11y {" ".join(args)}: {done.stderr[-1500:]}')
    return json.loads(done.stdout)


def build(work):
    work.mkdir(exist_ok=True)
    (work / 'main.qml').write_text(QML)
    (work / 'main.cpp').write_text(MAIN)
    flags = subprocess.run(['pkg-config', '--cflags', '--libs', 'Qt6Quick', 'Qt6Qml', 'Qt6Gui', 'Qt6Widgets'], capture_output=True,
                           text=True, check=True).stdout.split()
    subprocess.run(['g++', '-std=c++20', '-fPIC', '-O1', '-o', str(work / APP), str(work / 'main.cpp'), *flags],
                   check=True, capture_output=True)
    return work / APP


# covers[system]: delivery.ui-automation/E1, delivery.ui-automation/E2
def test():
    app = build(Path('/tmp/calc'))
    with harness.Session(360, 720, 'a11y') as s:
        s.check(a11y('state') == {'enabled': False}, 'accessibility starts off')
        s.start([str(app), '/tmp/calc/main.qml'], env={**os.environ, 'QT_QUICK_CONTROLS_STYLE': 'Basic'})
        s.wait_for(lambda: s.find(caption='calculator stand-in'), 20, 'the application window')
        names = lambda: [a['name'] for a in a11y('apps')]
        s.check(APP not in names(), 'while it is off the application is not on the AT-SPI bus')

        s.check(a11y('enable') == {'enabled': True}, 'enable turns it on')
        started = time.monotonic()
        s.wait_for(lambda: APP in names(), 10, 'the running application to register')
        took = time.monotonic() - started
        s.check(took < 5, f'the running application registered without a restart, in {took:.1f} s')

        def find(**query):
            args = [APP] + [x for k, v in query.items() for x in (f'--{k}', v)]
            found = a11y('find', *args)
            if len(found) != 1:
                raise harness.Failed(f'find {query}: {found}; tree {json.dumps(a11y("tree", APP))[:3000]}')
            return found[0]

        for key in ('C', '7', '+', '8', '='):
            button = find(role='button', name=f'^{re_escape(key)}$')
            pressed = a11y('act', APP, button['path'])
            s.check(pressed['ok'], f'button {key} found by name and pressed ({pressed["action"]})')
        s.wait_for(lambda: a11y('find', APP, '--role', 'label', '--name', '^15$'), 5, 'the display to show 15')
        s.check(True, 'C 7 + 8 = shows 15')

        field = find(role='text', name='^note$')
        s.check(a11y('text', APP, field['path'], 'hello 你好')['ok'], 'a text field found by name is filled')
        held = []
        try:
            s.wait_for(lambda: held.append(find(role='text', name='^note$').get('text')) or held[-1] == 'hello 你好',
                       5, 'the field to hold the text')
        except harness.Failed:
            raise harness.Failed(f'the field holds {held[-1]!r}, not the text')
        s.check(True, 'the field holds the text')

        s.check(a11y('disable') == {'enabled': False} and a11y('state') == {'enabled': False},
                'disable turns accessibility off again')
        return s.steps


def re_escape(text):
    return ''.join('\\' + c if c in '+=.*?()[]{}|^$\\' else c for c in text)



def launch_language(language, editor=False):
    """Real Plasma Mobile, Kalk and KWrite using source modules/native input instead of Android.

    Native field text readback does not replace phone screenshots or Android input proof.
    """
    import importlib.machinery
    import shlex
    import types
    from types import SimpleNamespace

    import ui_launch_check as ui
    import rungic_agent as agent

    label = 'KWrite' if editor else ('计算器' if language == 'zh_CN' else 'Calculator')
    app_id, process = ('org.kde.kwrite', 'kwrite') if editor else ('org.kde.kalk', 'kalk')
    locale = 'zh_CN.UTF-8' if language == 'zh_CN' else 'C.UTF-8'
    # covers[system]: agent.dev-diagnostics/E3
    with harness.Session(540, 960, 'launch-' + language) as session:
        os.environ.update(LANG=locale, LC_ALL=locale, LANGUAGE=language,
                          XDG_MENU_PREFIX='plasma-', PLASMA_PLATFORM='phone',
                          QT_QUICK_CONTROLS_MOBILE='1')
        subprocess.run(['kbuildsycoca6', '--noincremental'], check=True, capture_output=True)
        session.start(['/usr/lib/aarch64-linux-gnu/libexec/kactivitymanagerd'])
        time.sleep(2)
        session.start(['plasmashell', '-p', 'org.kde.plasma.mobileshell'])

        def query_a11y(*args):
            reply = subprocess.run([*A11Y, *map(str, args)], capture_output=True,
                                   text=True, timeout=60)
            if reply.returncode:
                shell_log = Path('/tmp/org.kde.plasma.mobileshell.err').read_text(errors='replace')
                raise harness.Failed(f'a11y {args}: {reply.stderr[-2000:]}\nPlasma: {shell_log[-2000:]}')
            return json.loads(reply.stdout)

        def windows():
            # The diagnostic's exact KWin query, returned over the existing Cua callback:
            # this container has no user journal.
            loader = importlib.machinery.SourceFileLoader('native_diagnostic', A11Y[1])
            module = types.ModuleType(loader.name)
            loader.exec_module(module)
            source = module.KWIN_SCRIPT.replace(
                'print("TOKEN " + JSON.stringify(list));',
                'callDBus("SERVICE", "/com/rungic/Cua", "com.rungic.Cua", "Report", JSON.stringify(list));')
            return session.kwin_api._script(source)

        def run(command, level='user', check=True, **kwargs):
            if level != 'shell':
                # The system runner loads Python modules directly from this source snapshot;
                # the phone installs this same package under /usr/lib/rungic-cua.
                command = command.replace('PYTHONPATH=/usr/lib/rungic-cua ',
                                          'LANG=C.UTF-8 LC_ALL=C.UTF-8 LANGUAGE=en PYTHONPATH=/src/agent/computer-use ')
                reply = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=60)
                if check and reply.returncode:
                    raise harness.Failed(f'{command}: {reply.stderr[-2000:]}')
                return reply
            args = shlex.split(command)
            if args[:2] == ['wm', 'size']:
                return SimpleNamespace(stdout='Physical size: 540x960', returncode=0)
            if args[:2] == ['input', 'swipe']:
                x, y, target_x, target_y = map(int, args[2:6])
                pointer = session.pointer()
                pointer.press(x, y)
                for step in range(1, 21):
                    pointer._send(f'move {x + (target_x - x) * step / 20} {y + (target_y - y) * step / 20}')
                    time.sleep(.02)
                pointer.release()
                time.sleep(.8)
            elif args[:2] == ['input', 'tap']:
                session.tap(*map(int, args[2:4]))
            elif args[:2] == ['input', 'text']:
                text = args[2]
                if not text.isascii() or not all(c.islower() or c.isdigit() or c == ' ' for c in text):
                    raise harness.Failed('native keycode text requires lowercase ASCII/digits/spaces; task104 tracks Shift')
                session.pointer().type_text(text)
                time.sleep(.5)
            else:
                raise AssertionError(command)
            return SimpleNamespace(stdout='', returncode=0)

        agent.a11y = query_a11y
        agent.ui_windows = windows
        agent.run = run
        agent.host_request = lambda *args, **kwargs: {'physicalWidth': 540}
        ui.run = run
        agent.ui_enable(True)
        session.wait_for(lambda: any(app['name'] == 'plasmashell' and app['windows']
                                     for app in query_a11y('apps')), 20, 'mobile shell accessible')
        entry = ui.desktop_entry(app_id)
        session.check(entry['name'] == ('KWrite' if editor else 'Calculator'), 'acceptance subprocess locale is English')
        session.check(label.casefold() in entry['labels'], language + ' desktop file includes actual drawer translation')
        for turn in range(2):
            launch = ui.launch(app_id, process, False)
            session.steps.append('native launch ' + json.dumps(launch, ensure_ascii=False))
            session.check(launch['label'].casefold() in entry['labels'] if editor else launch['label'] == label,
                          language + ' actual drawer label independent of subprocess locale')
            session.check(all(launch[key] for key in ('started', 'registered', 'window')),
                          language + ' actual drawer tap starts same application PID/AT-SPI/KWin window')
            if editor:
                pid = launch['windows'][0]['pid']
                environment = dict(value.split('=', 1) for value in Path(f'/proc/{pid}/environ').read_bytes().decode().split('\0')
                                   if '=' in value and value.split('=', 1)[0] in ('LANG', 'LC_ALL', 'LANGUAGE'))
                menus = [node['name'] for node in query_a11y('find', pid, '--role', 'menu item')]
                session.steps.append('editor locale ' + json.dumps({'environment': environment, 'menus': menus}, ensure_ascii=False))
                expected_menu = '文件' if language == 'zh_CN' else 'File'
                session.check(any(expected_menu in name for name in menus),
                              language + ' actual editor menu language: ' + json.dumps({'environment': environment, 'menus': menus}, ensure_ascii=False))
                editor_round(session, query_a11y, windows, launch['windows'][0], language, turn)
            closed = ui.close(process, app_id)
            session.steps.append('native close ' + json.dumps(closed, ensure_ascii=False))
            session.check(closed['exited'], language + ' verified window and process exit')
        if editor:
            session.check(not Path('/usr/share/applications/org.kde.kate.desktop').exists(),
                          'Kate hard-dependency launcher excluded; KWrite entry retained')
            agent.ui_enable(False)
            return session.steps
        ui.home()
        ui.open_drawer()
        field = ui.drawer_search_fields()[0]
        agent.ui_tap('plasmashell', field['path'])
        run('input text rungic42', 'shell')
        fields = ui.drawer_search_fields()
        session.steps.append('native input ' + json.dumps(fields, ensure_ascii=False))
        session.check(any(field.get('text') == 'rungic42' for field in fields),
                      language + ' actual drawer field reads full ASCII input (native keys; no Android/OCR proof)')
        agent.ui_enable(False)
        return session.steps



# covers[system]: install.rungicos-image/E2
def editor_round(session, query_a11y, windows, window, language, turn):
    """Edit in the actual KWrite view, save/open with real dialogs, and read the user's Shared file.

    Accessibility text edits prove Unicode editing, not a phone keyboard or Android Shared FUSE.
    """
    import hashlib
    pid = str(window['pid'])
    shared = Path.home() / 'Shared'
    shared.mkdir(exist_ok=True)
    path = shared / ('editor-' + language + '.txt')
    original = 'Rungic English text. 中文编辑与保存。\n'
    expected = original if turn == 0 else original.rstrip('\n') + ' Reopened. 再次保存。\n'

    def fields():
        return [node for node in query_a11y('find', pid, '--role', 'text')
                if 'editable' in node.get('states', []) and 'showing' in node.get('states', [])]

    def field():
        nodes = fields()
        if len(nodes) != 1:
            raise harness.Failed('expected one visible editor field: ' + json.dumps(nodes, ensure_ascii=False))
        return nodes[0]

    def dialog(chord):
        known = {item['id'] for item in windows()}
        session.pointer().chord(chord)
        session.wait_for(lambda: any(str(item['pid']) == pid and item['id'] not in known for item in windows()),
                         10, 'KWrite file dialog opens')
        # The native fake-input helper maps keycodes without adding Shift (so 'Shared'
        # becomes 'shared'). Use the existing accessible filename field; this is no
        # Android keyboard proof. Require the active dialog's focused editable text.
        def filename_fields():
            return [node for node in query_a11y('find', pid, '--role', 'text')
                    if all(state in node.get('states', []) for state in ('editable', 'showing', 'focused'))]
        nodes = session.wait_for(filename_fields, 10, 'file dialog filename field focused')
        session.check(len(nodes) == 1, 'one focused filename field')
        session.check(query_a11y('text', pid, nodes[0]['path'], str(path))['ok'], 'file dialog accepts Shared path')
        session.wait_for(lambda: filename_fields()[0].get('text') == str(path), 5, 'filename path readback')
        session.key('ENTER')

    if turn:
        dialog(['CTRL', 'o'])
        session.wait_for(lambda: fields() and field().get('text') == original, 10,
                         'reopened document reads saved Chinese/English text')
    else:
        # KWrite starts at its welcome page, which is not a text document.
        session.pointer().chord(['CTRL', 'n'])
        session.wait_for(fields, 10, 'new KWrite document exposes accessible text')
    node = field()
    session.steps.append('editor field ' + json.dumps(node, ensure_ascii=False))
    session.check(query_a11y('text', pid, node['path'], expected)['ok'], 'actual KWrite editor accepts Chinese/English text')
    session.wait_for(lambda: field().get('text') == expected, 10, 'actual editor text readback matches')
    if turn == 0:
        dialog(['CTRL', 's'])
    else:
        session.pointer().chord(['CTRL', 's'])
    try:
        session.wait_for(lambda: path.is_file() and path.read_text() == expected, 10,
                         'Shared saved file contains actual editor text')
    except harness.Failed as error:
        actual = path.read_text() if path.is_file() else None
        raise harness.Failed(f'{error}: {path} holds {actual!r}; windows {windows()}; tree {query_a11y("tree", pid)}') from error
    session.steps.append('Shared saved ' + json.dumps({'path': str(path), 'uid': path.stat().st_uid,
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'text': path.read_text()}, ensure_ascii=False))
    session.check(path.stat().st_uid == os.getuid(), 'ordinary desktop user owns and reads/writes Shared document')


def launch_languages(editor=False):
    import tempfile

    steps = []
    for language in ('en', 'zh_CN'):
        # A separate session bus/process also avoids sharing Gio's exported Cua object.
        # Activated desktop daemons inherit stderr; a pipe would wait for those unrelated
        # processes after the test exits. Keep the evidence in a file owned by this run.
        with tempfile.TemporaryFile(mode='w+') as log:
            reply = subprocess.run(['dbus-run-session', '--', 'python3', __file__,
                                    '--editor-language' if editor else '--launch-language', language],
                                   stdout=log, stderr=log, text=True, timeout=180,
                                   env={**os.environ, 'LANG': 'zh_CN.UTF-8' if language == 'zh_CN' else 'C.UTF-8',
                                        'LC_ALL': 'zh_CN.UTF-8' if language == 'zh_CN' else 'C.UTF-8',
                                        'LANGUAGE': language})
            log.seek(0)
            output = log.read()
        rows = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
        if reply.returncode or not rows or not rows[-1].get('passed'):
            raise harness.Failed(output[-4000:])
        steps.extend(rows[-1]['steps'])
    return steps


if __name__ == '__main__':
    import sys
    if len(sys.argv) == 3 and sys.argv[1] in ('--launch-language', '--editor-language'):
        editor = sys.argv[1] == '--editor-language'
        harness.run(('editor_' if editor else 'ui_launch_') + sys.argv[2],
                    lambda: launch_language(sys.argv[2], editor))
    else:
        harness.run('ui_automation_atspi', lambda: test() + launch_languages() + launch_languages(editor=True))
