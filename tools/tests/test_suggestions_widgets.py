#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The home screen's Agent widgets (agent/suggestions/qml) as they run, offscreen: the real QML of the
suggestion stack and the usage widget, the real design system, and stand-ins only for what is native:
the D-Bus clients (SuggestionsClient, UsageClient: they record what the widget asks for), KI18n and the
design system's C++ types. The finger is a real mouse press, drag and release sent to the window;
Folio's swipe area is stood in for by a Flickable around the widget (both take a drag from their
children unless the child keeps its grab). Requires PySide6."""
from pathlib import Path
import os
import tempfile
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from PySide6.QtCore import QPoint, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlComponent, QQmlEngine  # noqa: E402
from PySide6.QtQuick import QQuickWindow  # noqa: E402,F401  (the window type for Python)
from PySide6.QtTest import QTest  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
APP = QGuiApplication.instance() or QGuiApplication([])

KI18N = '''import QtQml
QtObject {
    property string translationDomain
    function fill(text, args) { return text.replace(/%(\\d+)/g, (m, n) => n <= args.length ? String(args[n - 1]) : m) }
    function i18n(text) { return fill(text, Array.prototype.slice.call(arguments, 1)) }
    function i18nc(context, text) { return fill(text, Array.prototype.slice.call(arguments, 2)) }
    function i18np(one, many, n) { return fill(n === 1 ? one : many, Array.prototype.slice.call(arguments, 2)) }
    function i18ncp(context, one, many, n) { return fill(n === 1 ? one : many, Array.prototype.slice.call(arguments, 3)) }
}
'''
DESIGN_I18N = '''pragma Singleton
import QtQml
QtObject {
    function fill(text, args) { return text.replace(/%(\\d+)/g, (m, n) => n <= args.length ? String(args[n - 1]) : m) }
    function i18n(text) { return fill(text, Array.prototype.slice.call(arguments, 1)) }
    function i18nc(context, text) { return fill(text, Array.prototype.slice.call(arguments, 2)) }
}
'''
# What the widgets were given and what they asked the services for.
RECORDER = '''pragma Singleton
import QtQml
QtObject {
    property var cards: []
    property var providers: []
    property var calls: []
    function add(call) { calls = calls.concat([call]) }
}
'''
SUGGESTIONS_CLIENT = '''import QtQml
QtObject {
    readonly property var cards: Recorder.cards
    readonly property var briefing: ({ generatedAt: 1000, source: "agent" })
    readonly property var items: []
    readonly property string error: ""
    signal replied(string id, string action, var result)
    function openCard(id) { Recorder.add("openCard " + id) }
    function dismissCard(id) { Recorder.add("dismissCard " + id) }
    function presentCard(id, opened) { if (opened) Recorder.add("opened " + id) }
    function watching(visible) {}
    function open(id) { Recorder.add("open") }
    function openAgent(usage) { Recorder.add(usage ? "app: usage page" : "app: agent conversation") }
    function signIn() { Recorder.add("app: sign-in page") }
    function openCodex() { Recorder.add("app: Codex install page") }
    function conversation(id) { Recorder.add("conversation " + id) }
}
'''
USAGE_CLIENT = '''import QtQml
QtObject {
    readonly property var providers: Recorder.providers
    readonly property var primary: Recorder.providers.length ? Recorder.providers[0] : ({})
    readonly property var data: ({})
    function refresh() {}
}
'''


def imports(base):
    """A temporary import path: the design system and the suggestions module from the sources."""
    def module(uri, sources, singletons, stubs):
        target = base / uri.replace('.', '/')
        target.mkdir(parents=True)
        lines = [f'module {uri}']
        for path in sorted(sources.iterdir()) if sources else []:
            if path.suffix not in ('.qml', '.js') or path.stem in stubs:
                continue
            (target / path.name).symlink_to(path)
            if path.suffix == '.qml':
                lines.append(f"{'singleton ' if path.stem in singletons else ''}{path.stem} 1.0 {path.name}")
        for name, (text, singleton) in stubs.items():
            (target / f'{name}.qml').write_text(text)
            lines.append(f"{'singleton ' if singleton else ''}{name} 1.0 {name}.qml")
        (target / 'qmldir').write_text('\n'.join(lines) + '\n')
    module('com.rungic.design', ROOT / 'desktop/design/qml', {'Theme'},
           {'DesignI18n': (DESIGN_I18N, True),
            'SystemTheme': ('pragma Singleton\nimport QtQml\nQtObject { property bool dark: false }\n', True),
            'PageSwipe': ('import QtQuick\nItem {}\n', False)})
    module('com.rungic.suggestions', ROOT / 'agent/suggestions/qml', set(),
           {'Recorder': (RECORDER, True), 'SuggestionsClient': (SUGGESTIONS_CLIENT, False),
            'UsageClient': (USAGE_CLIENT, False)})
    module('org.kde.ki18n', None, set(), {'KI18nContext': (KI18N, False)})


class WidgetTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='rungic-widgets-')
        base = Path(self.directory.name)
        imports(base)
        self.engine = QQmlEngine()
        self.engine.addImportPath(str(base))
        self.warnings = []
        self.engine.warnings.connect(lambda ws: self.warnings.extend(w.toString() for w in ws))
        self.base = base

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.engine.deleteLater()
        APP.processEvents()
        self.directory.cleanup()

    def show(self, qml, cards=(), providers=()):
        self.component = component = QQmlComponent(self.engine)
        component.setData(('import QtQuick\nimport com.rungic.suggestions\n' + qml).encode(),
                          QUrl.fromLocalFile(str(self.base / 'Home.qml')))
        self.window = component.create()
        self.assertIsNotNone(self.window, '\n'.join(e.toString() for e in component.errors()))
        QQmlEngine.setObjectOwnership(self.window, QQmlEngine.CppOwnership)
        self.recorder = self.engine.singletonInstance('com.rungic.suggestions', 'Recorder')
        self.recorder.setProperty('cards', list(cards))
        self.recorder.setProperty('providers', list(providers))
        self.window.show()
        self.wait(0.1)
        self.assertEqual([w for w in self.warnings if 'TypeError' in w or 'ReferenceError' in w], [])

    def item(self, name):
        root = self.window.contentItem()
        pending = list(root.childItems())
        while pending:
            child = pending.pop()
            if child.objectName() == name:
                return child
            pending.extend(child.childItems())
        raise AssertionError(name)

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            APP.processEvents()
            time.sleep(0.01)

    def drag(self, x, y0, y1, steps=12, seconds=0.25):
        QTest.mousePress(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(x, y0))
        for i in range(1, steps + 1):
            self.wait(seconds / steps)
            QTest.mouseMove(self.window, QPoint(x, round(y0 + (y1 - y0) * i / steps)))
        QTest.mouseRelease(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(x, y1))
        self.wait(0.6)                                     # the pager settles

    def tap(self, x, y, hold=0.05):
        QTest.mousePress(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(round(x), round(y)))
        self.wait(hold)
        QTest.mouseRelease(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(round(x), round(y)))
        self.wait(0.2)

    def calls(self):
        calls = self.recorder.property('calls')
        return calls.toVariant() if hasattr(calls, 'toVariant') else list(calls)


CARDS = [{'id': f'card{n}', 'kind': 'attention', 'title': f'Card {n}', 'body': f'Body {n}',
          'action': {'label': 'Look into it'}, 'refs': []} for n in range(1, 6)]
HOME = '''Window {
    width: 360; height: 800
    Flickable {
        objectName: "folio"
        anchors.fill: parent
        contentHeight: 2400                     // Folio's area: an upward swipe opens the drawer
        Rectangle { width: 360; height: 2400; color: "white" }
        SuggestionsWidget { objectName: "widget"; x: 10; y: 100; width: 340; height: 330 }
    }
}
'''


class SuggestionsWidgetTest(WidgetTest):
    def current(self):
        card = self.item('widget').property('currentCard')
        return (card.toVariant() if hasattr(card, 'toVariant') else card)['id']

    # covers: agent.briefing/E1
    def test_one_card_at_a_time_paged_by_swiping(self):
        self.show(HOME, CARDS)
        widget = self.item('widget')
        self.assertEqual(self.current(), 'card1')
        self.assertFalse(widget.property('moving'))
        self.drag(180, 380, 150)                           # up, past a quarter of the card: the next one
        self.assertEqual(self.current(), 'card2')
        self.drag(180, 380, 350)                           # a little: springs back
        self.assertEqual(self.current(), 'card2')
        self.assertEqual(widget.property('dragOffset'), 0)
        self.drag(180, 150, 380)                           # down: the previous one
        self.assertEqual(self.current(), 'card1')
        for _ in range(6):                                 # past the last card it only gives a little
            self.drag(180, 380, 120)
        self.assertEqual(self.current(), 'card5')
        self.assertFalse(widget.property('moving'), 'at rest only the top card shows')

    # covers: agent.briefing/E8
    def test_swipes_on_the_card_stay_with_the_card_and_elsewhere_go_to_folio(self):
        self.show(HOME, CARDS)
        folio = self.item('folio')
        self.drag(180, 380, 150)
        self.assertEqual(folio.property('contentY'), 0, 'a swipe on the card never reaches the drawer')
        self.assertEqual(self.current(), 'card2')
        self.drag(180, 760, 500)                           # on the wallpaper below the widget
        self.assertGreater(folio.property('contentY'), 0, 'the swipe area still gets the wallpaper\'s swipe')

    # covers: agent.briefing/E8
    def test_long_press_is_left_to_folio_and_a_tap_opens_the_card(self):
        self.show(HOME, CARDS, providers=[CODEX])
        self.tap(180, 200, hold=1.2)                       # press and hold: Folio's own editing
        self.assertEqual(self.calls(), [])
        self.tap(180, 200)
        self.assertEqual(self.calls(), ['opened card1', 'openCard card1'])

    # covers: agent.briefing/E9
    def test_unavailable_agent_hides_investigation_but_preserves_records(self):
        self.show(HOME, CARDS, providers=[{**CODEX, 'status': 'signed-out'}])
        widget = self.item('widget')
        self.assertFalse(widget.property('canAskAgent'))
        self.tap(180, 200)
        self.assertNotIn('openCard card1', self.calls())
        self.assertIn('open', self.calls(), 'card tap opens records without starting an Agent task')
        for status, stale in [('offline', False), ('error', False), ('ready', True)]:
            self.recorder.setProperty('providers', [{**CODEX, 'status': status, 'stale': stale}])
            self.wait(0.05)
            self.assertFalse(widget.property('canAskAgent'))
        self.recorder.setProperty('providers', [CODEX])
        self.wait(0.05)
        self.assertTrue(widget.property('canAskAgent'))
        self.tap(180, 200)
        self.assertIn('openCard card1', self.calls(), 'recovery restores the normal task entry')


USAGE_HOME = '''Window {
    width: 360; height: 200
    AgentWidget { objectName: "usage"; x: 10; y: 50; width: 340; height: 100 }
}
'''
CODEX = {'id': 'codex', 'name': 'Codex', 'status': 'ready', 'updatedAt': time.time(),
         'limits': [{'label': '5 h', 'windowMinutes': 300, 'usedPercent': 23, 'resetsAt': time.time() + 5400}],
         'tokens': {'today': 15771}}


class UsageWidgetTest(WidgetTest):
    def mark(self):
        widget = self.item('usage')
        pending = list(widget.childItems())
        while pending:
            child = pending.pop()
            if child.metaObject().className().startswith('AgentMark'):
                return child
            pending.extend(child.childItems())
        raise AssertionError('no agent mark')

    # covers: agent.usage-widget/E7
    def test_usage_opens_the_usage_page_and_the_mark_the_conversation(self):
        self.show(USAGE_HOME, providers=[CODEX])
        self.assertEqual(self.item('usage').property('body'), 'meters')
        self.tap(260, 120)                                 # the meters
        self.assertEqual(self.calls(), ['app: usage page'])
        mark = self.mark()
        centre = mark.mapToScene(QPointF(mark.width() / 2, mark.height() / 2))
        self.tap(centre.x(), centre.y())
        self.assertEqual(self.calls(), ['app: usage page', 'app: agent conversation'])

    # covers: agent.usage-widget/E7
    def test_without_an_agent_it_leads_to_setting_one_up(self):
        self.show(USAGE_HOME, providers=[])
        self.assertEqual(self.item('usage').property('body'), 'none')
        self.tap(260, 120)
        self.assertEqual(self.calls(), ['app: agent conversation'])

    # covers: agent.usage-widget/E5 agent.usage-widget/E7
    def test_signed_out_goes_to_login_and_first_connection_failure_is_visible(self):
        self.show(USAGE_HOME, providers=[{'id': 'codex', 'name': 'Codex', 'status': 'signed-out'}])
        self.assertEqual(self.item('usage').property('body'), 'signin')
        self.tap(260, 120)
        self.assertEqual(self.calls(), ['app: sign-in page'])
        self.recorder.setProperty('providers', [{'id': 'codex', 'name': 'Codex', 'status': 'offline', 'updatedAt': 0}])
        self.wait(0.05)
        self.assertEqual(self.item('usage').property('body'), 'unreachable')
        self.assertEqual(self.item('usage').property('statusText'), 'Connection failed')


if __name__ == '__main__':
    unittest.main()

class InvestigationCardTest(WidgetTest):
    # covers: agent.briefing/E9
    def test_connection_controls_new_investigation_but_keeps_later_and_details(self):
        self.show('''Window { width: 360; height: 500
            SuggestionCard { objectName: "card"; width: 340; x: 10; y: 10;
                item: ({state: "attention", displayTitle: "A system component quit unexpectedly", evidence: {reports: 3, package: "xdg-desktop-portal"}})
            }
        }''')
        card = self.item('card')
        def buttons():
            pending = [card]; result = {}
            while pending:
                item = pending.pop()
                if item.metaObject().className().startswith('PillButton'):
                    result[item.property('text')] = item.property('visible')
                pending.extend(item.childItems())
            return result
        self.assertFalse(buttons()['Ask Agent to check'])
        self.assertTrue(buttons()['Later'])
        card.setProperty('canAskAgent', True); self.wait(0.1)
        self.assertTrue(buttons()['Ask Agent to check'])
        card.setProperty('canAskAgent', False); self.wait(0.1)
        self.assertFalse(buttons()['Ask Agent to check'])
        self.assertTrue(buttons()['Later'])


class AgentAvailabilityCopyTest(WidgetTest):
    # covers: agent.usage-widget/E5 agent.usage-widget/E7
    def test_first_read_distinguishes_signed_out_and_connection_failure_in_visible_text(self):
        self.show(USAGE_HOME, providers=[{**CODEX, 'status': 'signed-out', 'limits': []}])
        def texts():
            pending = [self.window.contentItem()]; values=[]
            while pending:
                item=pending.pop()
                if item.property('visible') and item.property('text'): values.append(item.property('text'))
                pending.extend(item.childItems())
            return values
        self.assertIn('Not signed in to Codex', texts())
        self.recorder.setProperty('providers', [{**CODEX, 'status': 'offline', 'updatedAt': 0, 'limits': []}]); self.wait(.1)
        self.assertIn('Cannot reach Codex right now', texts())
        self.assertNotIn('Not signed in to Codex', texts())
        self.assertNotIn('Last read', ' '.join(texts()))


class MissingCodexTest(WidgetTest):
    # covers: agent.usage-widget/E5 agent.usage-widget/E7
    def test_missing_codex_opens_existing_install_page_without_retry_claim(self):
        self.show(USAGE_HOME, providers=[{**CODEX, 'installed': False, 'status': 'not-installed', 'limits': []}])
        self.assertEqual(self.item('usage').property('body'), 'install')
        self.tap(260, 120)
        self.assertEqual(self.calls(), ['app: Codex install page'])

    # covers: agent.briefing/E9
    def test_first_desktop_intro_is_conditional_until_agent_is_usable(self):
        self.show('Window { width: 360; height: 500; SuggestionsWidget { objectName: "suggestions"; width: 340; height: 330; forcedState: "firstrun" } }',
                  providers=[{**CODEX, 'installed': False, 'status': 'not-installed'}])
        def face():
            pending = [self.item('suggestions')]
            while pending:
                item = pending.pop()
                if item.property('titleText') is not None and item.property('visible'):
                    return item
                pending.extend(item.childItems())
            raise AssertionError('no visible briefing face')
        self.assertEqual(face().property('titleText'), 'Get started with Agent')
        self.assertIn('isn’t installed yet', face().property('bodyText'))
        self.recorder.setProperty('providers', [{**CODEX, 'installed': True, 'status': 'signed-out'}]); self.wait(.05)
        self.assertIn('Sign in to Codex', face().property('bodyText'))
        self.recorder.setProperty('providers', [{**CODEX, 'installed': None, 'status': 'offline'}]); self.wait(.05)
        self.assertIn('Once Codex is installed, signed in and connected', face().property('bodyText'))
        self.recorder.setProperty('providers', [{**CODEX, 'installed': True, 'status': 'ready', 'stale': False}]); self.wait(.05)
        self.assertEqual(face().property('titleText'), 'Codex is getting to know this phone')
