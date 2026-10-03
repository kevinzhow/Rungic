#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The floating window of desktop mode and of the assistant's screens (agent/screen/qml/Main.qml,
docs/65, docs/research/97 §17-§21), as it is, with FullTouch, TeamBoard and FloatingKeyboard: what
the window shows and does in each of its states, and what it asks of its screen.

Its C++ objects are stand-ins with their properties and methods (agentscreen.h: AgentScreen,
Director; floater.h: Floater), which record what the window asks; the real ones, with the real
window in a headless KWin, are tools/system/tests/desktop_mode_window.py. KDE's QML modules that
PySide6 lacks are stand-ins too: Kirigami's Icon (an icon name) and KPipeWire's PipeWireSourceItem
(a picture with nodeId and ready, set by the test as KPipeWire would).

Requires PySide6; QT_QPA_PLATFORM=offscreen allows running without a desktop.
"""
from pathlib import Path
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
CONFIG = tempfile.TemporaryDirectory(prefix='rungic-window-config-')
from PySide6.QtCore import QEvent, QMetaObject, QObject, QPoint, QPointF, QRect, QSize, Property, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication, QInputMethodEvent, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
import shiboken6

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'agent/computer-use'))
from rungic_cua.team import apply_to_board  # noqa: E402  (the board as the team's posts make it)
QML = ROOT / 'agent/screen/qml'
APP = QGuiApplication.instance() or QGuiApplication([])
DEVICE = QTest.createTouchDevice()
PORTRAIT = QRect(0, 0, 400, 880)       # a phone held upright, in logical pixels
LANDSCAPE = QRect(0, 0, 880, 400)
KEPT = []                              # the engines of the windows tested, with their stand-ins

STUBS = {
    'org/kde/kirigami/qmldir': 'module org.kde.kirigami\nIcon 1.0 Icon.qml\n',
    'org/kde/kirigami/Icon.qml': 'import QtQuick\nItem { property var source; property color color; property bool isMask }\n',
    'org/kde/pipewire/qmldir': 'module org.kde.pipewire\nPipeWireSourceItem 1.0 PipeWireSourceItem.qml\n',
    # KPipeWire's item: receives while visible with a node; `ready` once it has a frame (the test says).
    'org/kde/pipewire/PipeWireSourceItem.qml':
        'import QtQuick\nRectangle { readonly property string stub: "pipewire"; property int nodeId; property bool ready: false; color: "green" }\n',
}


class Localized(QObject):
    """KLocalizedContext's i18nc, as the window's context object (main.cpp): the English text."""

    @Slot(str, str, result=str)
    @Slot(str, str, 'QVariant', result=str)
    def i18nc(self, context, text, arg=None):
        return text if arg is None else text.replace('%1', str(arg))


class Screen(QObject):
    """AgentScreen (agentscreen.h): its properties, set by the test, and what the window asks of it."""
    statusChanged = Signal()
    nodeIdChanged = Signal()
    pointerNodeIdChanged = Signal()
    tvNodeIdChanged = Signal()
    promptingChanged = Signal()
    activityChanged = Signal()

    def __init__(self, workspace=0, node=40):
        super().__init__()
        self.calls = []
        self._workspace = workspace
        self.values = {'status': 'running', 'nodeId': node, 'pointerNodeId': 0, 'tvNodeId': 0, 'onTv': False,
                       'prompting': False, 'activityState': '', 'activityText': '', 'teamRole': '', 'teamKind': ''}

    def set(self, **values):
        signals = {'status': self.statusChanged, 'onTv': self.statusChanged, 'nodeId': self.nodeIdChanged,
                   'pointerNodeId': self.pointerNodeIdChanged, 'tvNodeId': self.tvNodeIdChanged,
                   'prompting': self.promptingChanged}
        self.values.update(values)
        for name in values:
            signals.get(name, self.activityChanged).emit()

    def of(self, kind):
        return [args for name, *args in self.calls if name == kind]

    status = Property(str, lambda s: s.values['status'], notify=statusChanged)
    nodeId = Property(int, lambda s: s.values['nodeId'], notify=nodeIdChanged)
    pointerNodeId = Property(int, lambda s: s.values['pointerNodeId'], notify=pointerNodeIdChanged)
    tvNodeId = Property(int, lambda s: s.values['tvNodeId'], notify=tvNodeIdChanged)
    onTv = Property(bool, lambda s: s.values['onTv'], notify=statusChanged)
    prompting = Property(bool, lambda s: s.values['prompting'], notify=promptingChanged)
    workspace = Property(int, lambda s: s._workspace, constant=True)
    activityState = Property(str, lambda s: s.values['activityState'], notify=activityChanged)
    activityText = Property(str, lambda s: s.values['activityText'], notify=activityChanged)
    teamRole = Property(str, lambda s: s.values['teamRole'], notify=activityChanged)
    teamKind = Property(str, lambda s: s.values['teamKind'], notify=activityChanged)

    @Slot(float, float)
    def pointerMove(self, fx, fy):
        self.calls.append(('pointerMove', fx, fy))

    @Slot(int, bool)
    def pointerButton(self, button, pressed):
        self.calls.append(('pointerButton', button, pressed))

    @Slot(float, float)
    def scroll(self, dx, dy):
        self.calls.append(('scroll', dx, dy))

    @Slot()
    def castToTv(self):
        self.calls.append(('castToTv',))

    @Slot(result=str)
    def backgroundFile(self):
        return ''

    @Slot(result=QSize)
    def outputSize(self):
        return QSize(1920, 1080)

    @Slot(bool)
    def setPointerShown(self, shown):
        self.calls.append(('setPointerShown', shown))

    @Slot(bool)
    def setTvShown(self, shown):
        self.calls.append(('setTvShown', shown))

    @Slot(bool)
    def setFullscreen(self, full):
        self.calls.append(('setFullscreen', full))

    @Slot(str)
    def typeText(self, text):
        self.calls.append(('typeText', text))

    @Slot(int, bool)
    def key(self, code, pressed):
        self.calls.append(('key', code, pressed))

    @Slot()
    def close(self):
        self.calls.append(('close',))


class Board(QObject):
    """BoardTile: the team's board in the director (workspace 100, no picture)."""
    boardChanged = Signal()

    def __init__(self, board=None):
        super().__init__()
        self._board = board or {'phase': 'working', 'title': 'Ship it', 'members': []}
    workspace = Property(int, lambda s: 100, constant=True)
    nodeId = Property(int, lambda s: 0, constant=True)
    activityState = Property(str, lambda s: '', constant=True)
    activityText = Property(str, lambda s: '', constant=True)
    teamRole = Property(str, lambda s: '', constant=True)
    teamKind = Property(str, lambda s: '', constant=True)
    board = Property('QVariantMap', lambda s: s._board, notify=boardChanged)


class Director(QObject):
    """Director (agentscreen.h): its members' screens, the focus and the level; the window's
    setFocus and nextLevel are recorded and, as the app answers, applied."""
    changed = Signal()

    def __init__(self, screens):
        super().__init__()
        self._screens = screens
        self._focus = screens[0].workspace
        self._level = 0
        self.calls = []

    def tile(self, workspace):
        return next((s for s in self._screens if s.workspace == workspace), None)

    screens = Property('QVariantList', lambda s: s._screens, notify=changed)
    focusTile = Property(QObject, lambda s: s.tile(s._focus), notify=changed)
    focusScreen = Property(QObject, lambda s: s.tile(s._focus) if s._focus != 100 else s._screens[0], notify=changed)
    focus = Property(int, lambda s: s._focus, notify=changed)
    level = Property(int, lambda s: s._level, notify=changed)

    @Slot(int)
    def setFocus(self, workspace):
        self.calls.append(('setFocus', workspace))
        self._focus = workspace
        self.changed.emit()

    @Slot()
    def nextLevel(self):
        self.calls.append(('nextLevel',))
        self._level = (self._level + 1) % 3
        self.changed.emit()


class Floater(QObject):
    """Floater (floater.h): the phone's screen and the window's surfaces."""
    areaChanged = Signal()

    def __init__(self, area, cast=False):
        super().__init__()
        self._area = area
        self._cast = cast
        self.rects = []
        self.opaque = []
        self.on_cast = []

    def set_area(self, area):
        self._area = area
        self.areaChanged.emit()

    area = Property(QRect, lambda s: s._area, notify=areaChanged)
    castPresent = Property(bool, lambda s: s._cast, notify=areaChanged)

    @Slot('QVariantList')
    def setInputRects(self, rects):
        self.rects = [r.toRect() if hasattr(r, 'toRect') else r for r in rects]

    @Slot(QWindow)
    def showFullscreen(self, window):
        # An ordinary fullscreen window: the whole phone screen from its first configure.
        window.setGeometry(self._area)
        window.show()

    @Slot(QWindow, bool)
    def setOpaque(self, window, opaque):
        self.opaque.append(opaque)

    @Slot(QWindow, result=bool)
    def placeOnCast(self, window):
        self.on_cast.append(window)
        if self._cast:
            window.resize(1920, 1080)
        return self._cast


def items(item):
    yield item
    for child in item.childItems():
        yield from items(child)


class WindowTest(unittest.TestCase):
    """One window per test, as main.cpp makes it: Main.qml with its screen, director and floater."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory(prefix='rungic-window-')
        for name in ('Main.qml', 'FullTouch.qml', 'TeamBoard.qml', 'FloatingKeyboard.qml'):
            shutil.copy(QML / name, Path(cls.dir.name, name))
        for name, text in STUBS.items():
            path = Path(cls.dir.name, 'imports', name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def setUp(self):
        # Fullscreen's touch mode and the keyboard's place are remembered (rungic-agent-screenrc): each
        # test from none.
        os.environ['XDG_CONFIG_HOME'] = tempfile.mkdtemp(dir=CONFIG.name)

    def open(self, workspace=0, area=PORTRAIT, director=None, cast=False, node=40, status='running'):
        self.screen = director.focusScreen if director else Screen(workspace, node)
        self.screen.set(status=status)
        self.director = director
        self.floater = Floater(area, cast)
        self.localized = Localized()
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(Path(self.dir.name, 'imports')))
        engine.rootContext().setContextObject(self.localized)
        engine.rootContext().setContextProperty('agent', None if director else self.screen)
        engine.rootContext().setContextProperty('director', director)
        engine.rootContext().setContextProperty('floater', self.floater)
        warnings = []
        engine.warnings.connect(lambda w: warnings.extend(w))
        engine.load(QUrl.fromLocalFile(str(Path(self.dir.name, 'Main.qml'))))
        self.assertTrue(engine.rootObjects(), f'Main.qml does not load: {[w.toString() for w in warnings]}')
        self.engine = engine
        win = engine.rootObjects()[0]
        win.setGeometry(area)                          # Floater::attach: the whole phone screen
        win.setProperty('ready', True)                 # main.cpp, once it is a layer surface
        if status != 'off':
            QTest.qWaitForWindowExposed(win)
        self.win = win
        self.full_window = next(w for w in win.findChildren(QQuickWindow) if w is not win)
        self.addCleanup(self.close, engine, win, self.full_window)
        QTest.qWait(400)                               # its first layout's animations
        return win

    # covers: desktop-mode.floating-window/E7
    def test_a_disabled_screen_is_hidden_from_its_first_layout(self):
        win = self.open(workspace=1, node=0, status='off')
        self.assertFalse(win.isVisible(), 'no black placeholder while the native backend exits')
        self.assertFalse(self.full_window.isVisible(), 'no fullscreen replacement either')

    def close(self, engine=None, win=None, full=None):
        if engine is None:
            engine, win, full = self.engine, self.win, self.full_window
        if getattr(engine, 'closed', False):
            return
        engine.closed = True
        full.hide()
        win.close()
        # Kept, not deleted: PySide6 handed a later window the wrapper of a deleted one at its address.
        # With what its context names: a binding of a window still alive reading a stand-in Python had
        # collected crashed a later test (QV4::QObjectWrapper::wrap).
        KEPT.append((engine, self.screen, self.director, self.floater, self.localized))
        QTest.qWait(20)

    # ---- what is where ---------------------------------------------------------------------------
    def stage(self):
        return next(i for w in (self.win, self.full_window) for i in w.contentItem().childItems()
                    if i.property('turned') is not None)

    def find(self, predicate, window=None):
        roots = [window.contentItem()] if window else [self.win.contentItem(), self.full_window.contentItem()]
        return [i for r in roots for i in items(r) if predicate(i)]

    def toolbar(self):
        return self.find(lambda i: i.property('actions') is not None and i.property('slide') is not None)[0]

    def icons(self):
        actions = self.toolbar().property('actions')         # a JS array
        return [actions.property(i).property('icon').toString() for i in range(actions.property('length').toInt())]

    def pictures(self, window=None):
        return self.find(lambda i: i.property('stub') == 'pipewire', window)

    def picture(self):
        """The screen's picture (the rounded rectangle holding the streams)."""
        return self.find(lambda i: i.property('radius') is not None and any(c.property('stub') == 'pipewire'
                                                                           for c in i.childItems()))[0]

    def full_touch(self):
        return self.find(lambda i: i.property('padState') is not None)[0]

    def caption(self):
        return self.find(lambda i: i.property('dot') is not None and i.property('label') is not None)[0]

    def label(self, text):
        return [i for i in self.find(lambda i: i.property('text') == text) if i.isVisible() and i.opacity() > 0]

    def window_of(self, item):
        """The window `item` is in (not QQuickItem.window(): PySide6 then deleted that window at the end
        of the test)."""
        while item.parentItem() is not None:
            item = item.parentItem()
        top = shiboken6.getCppPointer(item)
        return next((w for w in (self.win, self.full_window) + tuple(self.floater.on_cast)
                     if shiboken6.getCppPointer(w.contentItem()) == top), None)

    def scene(self, item, x, y):
        p = item.mapToScene(QPointF(x, y))
        return QPoint(round(p.x()), round(p.y()))

    # ---- doing ----------------------------------------------------------------------------------
    def tap(self, window, point, hold=40):
        QTest.touchEvent(window, DEVICE).press(0, point, window).commit()
        QTest.qWait(hold)
        QTest.touchEvent(window, DEVICE).release(0, point, window).commit()
        QTest.qWait(60)

    def tap_action(self, icon):
        bar = self.toolbar()
        index = self.icons().index(icon)
        window = self.window_of(bar)
        self.tap(window, self.scene(bar, 8 + 40 * index + 4 * index + 20, 20))

    def drag(self, window, points, dt=16):
        QTest.touchEvent(window, DEVICE).press(0, points[0], window).commit()
        for p in points[1:]:
            QTest.qWait(dt)
            QTest.touchEvent(window, DEVICE).move(0, p, window).commit()
        QTest.qWait(dt)
        QTest.touchEvent(window, DEVICE).release(0, points[-1], window).commit()
        QTest.qWait(60)

    def call(self, name):
        QMetaObject.invokeMethod(self.win, name)

    def enter_fullscreen(self):
        self.call('setFullscreen')
        self.wait(lambda: self.win.property('full') and not self.win.property('morphing'), 'fullscreen')

    def leave_fullscreen(self):
        self.call('leaveFullscreen')
        self.wait(lambda: not self.full_window.isVisible() and not self.win.property('morphing'), 'back in the window')

    def wait(self, condition, what, timeout=5):
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                self.fail(f'timed out waiting for {what}')
            QTest.qWait(20)


class FloatingWindow(WindowTest):
    # covers: desktop-mode.floating-window/E3
    def test_a_tap_shows_the_toolbar_under_the_picture_which_hides_again(self):
        win = self.open(workspace=0)
        picture = self.picture()
        self.assertFalse(win.property('toolbarShown'))
        self.tap(win, self.scene(picture, picture.width() / 2, picture.height() / 2))
        self.assertTrue(win.property('toolbarShown'))
        bar = self.toolbar()
        QTest.qWait(250)
        self.assertGreater(bar.opacity(), 0.9)
        top = picture.mapToScene(QPointF(0, picture.height())).y()
        self.assertAlmostEqual(bar.y(), top + 10, delta=1, msg='the toolbar sits a small gap below the picture')
        self.assertEqual(self.icons(), ['view-fullscreen', 'video-television', 'go-previous', 'window-close'])
        self.assertTrue(self.label('Desktop'), 'it says which screen this is')
        QTest.qWait(3400)
        self.assertFalse(win.property('toolbarShown'), 'it hides a few seconds after the last touch')

    # covers: desktop-mode.floating-window/E3
    def test_near_the_bottom_the_toolbar_goes_above_and_a_drag_shows_it(self):
        win = self.open(workspace=1)
        win.setProperty('py', PORTRAIT.height() - 170)
        QTest.qWait(300)
        picture = self.picture()
        start = self.scene(picture, picture.width() / 2, picture.height() / 2)
        self.drag(win, [start + QPoint(0, -6 * k) for k in range(8)])
        self.assertTrue(win.property('toolbarShown'), 'a drag shows the toolbar')
        QTest.qWait(300)
        self.assertTrue(win.property('barAbove'))
        self.assertLess(self.toolbar().y() + self.toolbar().height(), picture.mapToScene(QPointF(0, 0)).y())
        self.assertTrue(self.label('Assistant Screen'), "the assistant's screen says so")
        self.assertEqual(self.icons()[-1], 'window-close')

    # covers: desktop-mode.floating-window/E4
    def test_only_the_picture_and_its_toolbar_take_touches_and_none_reach_the_screen(self):
        win = self.open(workspace=0)
        picture = self.picture()
        origin = picture.mapToScene(QPointF(0, 0))
        self.assertEqual(self.floater.rects, [QRect(round(origin.x()), round(origin.y()), round(picture.width()),
                                                    round(picture.height()))])
        self.tap(win, self.scene(picture, picture.width() / 2, picture.height() / 2))
        QTest.qWait(300)
        self.assertEqual(len(self.floater.rects), 2, 'the toolbar takes touches while it shows')
        self.tap(win, self.scene(picture, 30, 30))
        self.drag(win, [self.scene(picture, 60 + 5 * k, 40) for k in range(6)])
        self.assertFalse(self.screen.of('pointerMove') + self.screen.of('pointerButton') + self.screen.of('scroll'),
                         'the floating window is looked at, not worked: no touch reaches the screen')

    # covers: desktop-mode.floating-window/E4
    def test_black_only_until_the_picture_comes(self):
        self.open(workspace=0, node=0)
        picture = self.picture()
        stream = self.pictures()[0]
        self.assertEqual(picture.property('color').name(), '#000000', 'black while there is no picture')
        self.screen.set(nodeId=40)
        QTest.qWait(50)
        self.assertTrue(stream.isVisible())
        self.assertEqual(picture.property('color').name(), '#000000', 'black until its first frame')
        stream.setProperty('ready', True)
        QTest.qWait(50)
        self.assertEqual(picture.property('color').alpha(), 0, 'no black behind the picture (its edge showed a line)')

    # covers: desktop-mode.floating-window/E5
    def test_no_picture_received_while_the_tv_or_fullscreen_shows_the_screen(self):
        win = self.open(workspace=0)
        stream = self.pictures()[0]
        stream.setProperty('ready', True)
        self.assertTrue(stream.isVisible())
        self.screen.set(status='tv')
        QTest.qWait(50)
        self.assertFalse(win.isVisible(), 'the floating window goes while a TV shows the screen')
        self.assertFalse(stream.isVisible(), 'and its picture is not received (KPipeWire pauses a hidden item)')
        self.screen.set(status='running')
        QTest.qWait(50)
        self.assertTrue(stream.isVisible())
        self.enter_fullscreen()
        streams = [s for s in self.pictures() if s.isVisible() and s.property('nodeId')]
        self.assertEqual(streams, [stream], 'fullscreen shows the one picture: none is left in the floating window')
        self.assertIs(self.window_of(stream), self.full_window)

    # covers: desktop-mode.floating-window/E5
    def test_the_dot_breathes_ten_steps_a_second_and_not_at_all_when_idle(self):
        win = self.open(workspace=1)
        breaths = []
        win.breathChanged.connect(lambda: breaths.append(win.property('breath')))
        QTest.qWait(1000)
        self.assertEqual(breaths, [], 'nothing redrawn while the assistant does nothing')
        self.screen.set(activityState='working', activityText='Reading the mail')
        QTest.qWait(50)
        breaths.clear()
        QTest.qWait(1000)
        self.assertTrue(8 <= len(breaths) <= 11, f'{len(breaths)} steps a second, not every frame')

    # covers: desktop-mode.floating-window/E6
    def test_turned_and_back_the_window_stays_on_the_screen_at_a_fitting_size(self):
        win = self.open(workspace=0)
        self.floater.set_area(LANDSCAPE)
        QTest.qWait(50)
        win.setProperty('panelWidth', 600)
        QTest.qWait(50)
        self.floater.set_area(PORTRAIT)
        QTest.qWait(400)
        width, px, py = (win.property(k) for k in ('panelWidth', 'px', 'py'))
        self.assertLessEqual(width, PORTRAIT.width(), 'not half the landscape width (it was, 2026-10-02)')
        self.assertGreaterEqual(width, PORTRAIT.width() / 2)
        self.assertTrue(0 <= px and px + width <= PORTRAIT.width() and 0 <= py
                        and py + win.property('panelHeight') <= PORTRAIT.height(), 'on the screen')

    # covers: desktop-mode.auth-prompts/E3
    def test_a_prompt_in_the_screen_says_so_and_its_caption_opens_fullscreen(self):
        win = self.open(workspace=0)
        caption = self.caption()
        self.assertEqual(caption.property('state'), 'hidden')
        self.screen.set(prompting=True)
        QTest.qWait(250)
        self.assertEqual(caption.property('state'), 'prompt')
        self.assertTrue(caption.isVisible())
        self.assertIn('Authentication needed', caption.property('label'))
        self.tap(win, self.scene(caption, caption.width() / 2, caption.height() / 2))
        self.wait(lambda: win.property('full') and not win.property('morphing'), 'fullscreen from the caption')
        self.assertFalse(win.property('toolbarShown') and self.toolbar().property('shown') and False)
        self.assertEqual(caption.property('state'), 'hidden', 'answered in fullscreen: no caption there')
        self.leave_fullscreen()
        self.assertEqual(caption.property('state'), 'prompt', 'still waiting: said again in the window')
        self.screen.set(prompting=False)
        QTest.qWait(50)
        self.assertEqual(caption.property('state'), 'hidden', 'answered: the caption goes')


class Fullscreen(WindowTest):
    def frames(self):
        """After every frame either window draws, whether the picture is on the screen: each window
        shows what it drew last (nothing once hidden), the stage with the picture or the still that
        stands for it, and one of them must show it. A window drawing its frame without the picture
        while the other has not drawn it yet is the blank frame."""
        seen = []
        last = {}

        def picture_in(window):
            stage = self.stage()
            if self.window_of(stage) is window and stage.isVisible():
                return True
            stills = self.find(lambda i: i.property('cache') is False and i.property('source') is not None
                               and i.isVisible() and bool(i.property('source').toString()), window)
            return bool(stills)

        def drawn(window):
            for w in (self.win, self.full_window):
                if not w.isVisible():
                    last[id(w)] = False
            last[id(window)] = window.isVisible() and picture_in(window)
            seen.append(('full' if window is self.full_window else 'floater', round(self.win.property('morphT'), 3),
                         any(last.values())))

        self.win.frameSwapped.connect(lambda: drawn(self.win))
        self.full_window.frameSwapped.connect(lambda: drawn(self.full_window))
        return seen

    # covers: desktop-mode.fullscreen/E1
    def test_in_and_out_morph_with_the_picture_in_every_frame_and_back_in_place(self):
        win = self.open(workspace=0)
        self.pictures()[0].setProperty('ready', True)
        win.setProperty('px', 40)
        win.setProperty('py', 300)
        win.setProperty('panelWidth', 260)
        QTest.qWait(400)
        place = [win.property(k) for k in ('px', 'py', 'panelWidth')]
        seen = self.frames()
        self.enter_fullscreen()
        steps = sorted({t for _, t, _ in seen})
        self.assertGreater(len([t for t in steps if 0 < t < 1]), 3, f'a morph, not a jump: {steps}')
        self.assertTrue(all(has for _, _, has in seen), f'a frame without the picture: {seen}')
        seen.clear()
        self.leave_fullscreen()
        self.assertGreater(len({t for _, t, _ in seen if 0 < t < 1}), 3, 'the way back is a morph too')
        self.assertTrue(all(has for _, _, has in seen), f'a frame without the picture: {seen}')
        self.assertEqual([win.property(k) for k in ('px', 'py', 'panelWidth')], place, 'back where it was, as large')
        self.assertEqual(win.property('mode'), 'window')

    # covers: desktop-mode.fullscreen/E4
    def test_on_a_portrait_phone_the_picture_turns_and_on_a_landscape_screen_not(self):
        self.open(workspace=0, area=PORTRAIT)
        self.enter_fullscreen()
        stage = self.stage()
        self.assertEqual((stage.rotation(), stage.width(), stage.height()), (90, 880, 400),
                         'landscape in the window, turned a quarter clockwise (the phone held with its top to the left)')
        self.assertEqual((self.full_window.width(), self.full_window.height()), (400, 880),
                         'the window itself is the phone upright: nothing asks the phone to turn')
        self.close()
        self.open(workspace=0, area=LANDSCAPE)
        self.enter_fullscreen()
        self.assertEqual(self.stage().rotation(), 0, "a PC's landscape screen: not turned")

    # covers: desktop-mode.fullscreen/E7
    def test_desktop_mode_fills_the_screen_edge_to_edge_with_black_bars(self):
        win = self.open(workspace=0)
        self.pictures()[0].setProperty('ready', True)
        self.call('setFullscreen')
        self.wait(lambda: win.property('toolbarShown'), 'the toolbar showing on arrival', 3)
        self.wait(lambda: not win.property('toolbarShown'), 'the toolbar sliding away', 2.5)
        picture = self.picture()
        stage = self.stage()
        self.assertEqual((win.property('fullMargin'), picture.property('radius')), (0, 0), 'no margin, no corners')
        self.assertEqual(round(picture.width()), 711, '16:9 as large as the 880x400 stage allows')
        self.assertEqual(round(picture.height()), 400)
        backdrop = next(i for i in stage.childItems() if i.property('color') is not None and i.property('radius') == 0
                        and i.width() == stage.width())
        self.assertEqual(backdrop.property('color').name(), '#000000', 'black around it, no wallpaper')
        self.assertFalse(any(c.isVisible() for c in backdrop.childItems()), 'no wallpaper image')
        shadows = self.find(lambda i: i.property('blur') is not None and i.property('offset') is not None)
        self.assertFalse(any(s.isVisible() for s in shadows), 'no shadow')
        bar = self.full_touch()
        self.tap(self.full_window, self.scene(bar, 40, 200))         # the black bar left of the picture
        self.assertTrue(win.property('toolbarShown'), 'a tap on the black bar shows the toolbar')
        self.assertFalse(self.screen.of('pointerButton'), 'and clicks nothing')

    # covers: desktop-mode.fullscreen-touch/E3
    def test_touchpad_mode_shows_the_pointer_picture_without_a_gap(self):
        win = self.open(workspace=0)
        base = self.pictures()[0]
        base.setProperty('ready', True)
        self.enter_fullscreen()
        self.assertFalse(self.screen.of('setPointerShown'), 'touchscreen mode: the picture without a pointer')
        seen = []
        self.full_window.frameSwapped.connect(lambda: seen.append(
            any(p.isVisible() and p.property('ready') and p.opacity() > 0 for p in self.pictures())))
        self.tap_action('input-touchpad') if win.property('toolbarShown') else None
        if not self.screen.of('setPointerShown'):
            self.tap(self.full_window, self.scene(self.full_touch(), 40, 200))
            self.tap_action('input-touchpad')
        self.assertEqual(self.screen.of('setPointerShown'), [[True]], 'the picture with the pointer asked for')
        self.screen.set(pointerNodeId=41)
        QTest.qWait(100)
        pointer = next(p for p in self.pictures() if p.property('nodeId') == 41)
        self.assertTrue(pointer.isVisible(), 'received while it starts')
        self.assertEqual(pointer.opacity(), 0, 'not seen before its first frame')
        self.assertTrue(base.isVisible(), 'the picture without it stays meanwhile')
        pointer.setProperty('ready', True)
        QTest.qWait(100)
        self.assertEqual(pointer.opacity(), 1)
        self.assertFalse(base.isVisible(), 'the one without the pointer is not received under it')
        self.tap_action('input-touchpad') if win.property('toolbarShown') else (
            self.tap(self.full_window, self.scene(self.full_touch(), 40, 200)), self.tap_action('input-touchpad'))
        QTest.qWait(100)
        self.assertTrue(base.isVisible(), 'back to touchscreen mode: the picture without the pointer at once')
        self.assertEqual(self.screen.of('setPointerShown'), [[True]], 'the pointer picture kept while that one comes back')
        QTest.qWait(500)
        self.assertEqual(self.screen.of('setPointerShown'), [[True], [False]], 'the pointer picture let go after it')
        self.assertTrue(seen and all(seen), 'a picture in every frame, no black between the two')


class Keyboard(WindowTest):
    def keyboard_open(self):
        win = self.open(workspace=0)
        self.pictures()[0].setProperty('ready', True)
        self.enter_fullscreen()
        win.setProperty('typing', True)
        self.wait(lambda: win.property('keyboard') is not None, 'the keyboard')
        QTest.qWait(300)
        return win, win.property('keyboard')

    def bar_key(self, keyboard, label=None, icon=None):
        return next(i for i in items(keyboard) if i.property('tapped') is None and i.isVisible()
                    and i.property('checked') is not None and i.property('icon') is not None
                    and ((label and i.property('label') == label) or (icon and i.property('icon') == icon)))

    def press(self, keyboard, label=None, icon=None):
        key = self.bar_key(keyboard, label, icon)
        self.tap(self.full_window, self.scene(key, key.width() / 2, key.height() / 2))

    def type(self, text):
        for c in text:
            QTest.keyClick(self.full_window, c)

    def keys(self):
        return [(code, pressed) for code, pressed in self.screen.of('key')]

    def pinch(self, keyboard, at, spread, steps=8, apart=60):
        """Two fingers on the keyboard's bar, `apart` apart, then `spread` more (or less, negative)."""
        a, b = at - apart / 2, at + apart / 2
        window = self.full_window
        points = lambda a, b: (self.scene(keyboard, a, 14), self.scene(keyboard, b, 14))
        pa, pb = points(a, b)
        QTest.touchEvent(window, DEVICE).press(0, pa, window).press(1, pb, window).commit()
        for k in range(1, steps + 1):
            QTest.qWait(16)
            pa, pb = points(a - spread / 2 * k / steps, b + spread / 2 * k / steps)
            QTest.touchEvent(window, DEVICE).move(0, pa, window).move(1, pb, window).commit()
        QTest.qWait(16)
        QTest.touchEvent(window, DEVICE).release(0, pa, window).release(1, pb, window).commit()
        QTest.qWait(400)

    def free_bar(self, keyboard):
        """A point of the keyboard's bar between its two rows of keys (it moves the keyboard there)."""
        rows = [i for i in keyboard.findChildren(QQuickItem) if i.property('spacing') is not None
                and i.property('visible') and i.parentItem() is not None and i.parentItem().height() == 40]
        left = max(r.x() + r.width() for r in rows if r.x() < keyboard.width() / 2)
        right = min(r.x() for r in rows if r.x() >= keyboard.width() / 2)
        return (left + right) / 2

    # covers: desktop-mode.floating-keyboard/E1
    def test_the_keyboard_turns_with_the_picture_floats_docks_and_is_remembered_apart(self):
        win, keyboard = self.keyboard_open()
        origin, along = keyboard.mapToScene(QPointF(0, 0)), keyboard.mapToScene(QPointF(100, 0))
        self.assertEqual((round(along.x() - origin.x()), round(along.y() - origin.y())), (0, 100),
                         'turned with the picture: upright for a phone held sideways')
        stage = self.stage()
        x = self.free_bar(keyboard)
        start = self.scene(keyboard, x, 14)
        self.drag(self.full_window, [start] + [self.scene(keyboard, x - 25 * k, 14 - 20 * k) for k in range(1, 9)])
        QTest.qWait(400)
        cx = keyboard.property('cx')
        half = keyboard.width() / 2 / stage.width()
        self.assertAlmostEqual(cx, half + 6 / stage.width(), places=3, msg='let go, it settles in the bottom left corner')
        self.assertEqual(keyboard.property('cy'), 1)
        self.assertAlmostEqual(keyboard.y() + keyboard.height(), stage.height() - 18, delta=1, msg='at the bottom')
        width = keyboard.width()
        self.pinch(keyboard, self.free_bar(keyboard), 60)
        self.assertGreater(keyboard.width(), width + 20, 'two fingers make it larger')
        self.assertFalse(keyboard.property('docked'))
        self.pinch(keyboard, self.free_bar(keyboard), 500)
        self.assertTrue(keyboard.property('docked'), 'past its largest it docks')
        self.assertEqual((keyboard.width(), keyboard.x()), (stage.width(), 0), 'the full width')
        self.pinch(keyboard, keyboard.width() / 2, -300, apart=400)
        self.assertFalse(keyboard.property('docked'), 'pinched in it floats again')
        self.pinch(keyboard, self.free_bar(keyboard), 40)
        remembered = [keyboard.property(k) for k in ('floatWidth', 'cx', 'docked')]
        QTest.qWait(700)                                      # Settings writes a moment later
        self.close()
        win, keyboard = self.keyboard_open()
        self.assertEqual([keyboard.property(k) for k in ('floatWidth', 'cx', 'docked')], remembered,
                         "desktop mode's keyboard as it was left")
        self.close()
        self.open(workspace=1)
        self.enter_fullscreen()
        self.win.setProperty('typing', True)
        self.wait(lambda: self.win.property('keyboard') is not None, "the assistant's keyboard")
        keyboard = self.win.property('keyboard')
        self.assertEqual((keyboard.property('cx'), keyboard.property('floatWidth')), (0.5, keyboard.property('standard')),
                         "the assistant's screens' keyboard is remembered apart: its own default")

    # covers: desktop-mode.floating-keyboard/E3
    def test_the_bar_sends_the_desktop_keys_and_holds_ctrl_and_alt_for_the_next(self):
        win, keyboard = self.keyboard_open()
        for label, code in (('Esc', 1), ('Tab', 15)):
            self.screen.calls.clear()
            self.press(keyboard, label=label)
            self.assertEqual(self.keys(), [(code, True), (code, False)], label)
        for icon, code in (('go-previous', 105), ('go-up', 103), ('go-down', 108), ('go-next', 106)):
            self.screen.calls.clear()
            self.press(keyboard, icon=icon)
            self.assertEqual(self.keys(), [(code, True), (code, False)], icon)
        self.screen.calls.clear()
        self.press(keyboard, label='Ctrl')
        self.assertTrue(keyboard.property('ctrl'))
        self.type('c')
        self.assertEqual(self.keys(), [(29, True), (46, True), (46, False), (29, False)], 'Ctrl+C as keys')
        self.assertFalse(self.screen.of('typeText'), 'not typed as text')
        self.assertFalse(keyboard.property('ctrl'), 'Ctrl lets go after one key')
        self.screen.calls.clear()
        self.type('c')
        self.assertEqual(self.screen.of('typeText'), [['c']], 'the next letter is text again')
        self.screen.calls.clear()
        self.press(keyboard, label='Alt')
        self.type('4')
        self.assertEqual(self.keys(), [(56, True), (5, True), (5, False), (56, False)], 'Alt and a digit as keys')

    # covers: desktop-mode.floating-keyboard/E4
    def test_the_toolbar_goes_above_the_keyboard_and_touches_on_it_stay_there(self):
        win, keyboard = self.keyboard_open()
        win.setProperty('toolbarShown', True)
        QTest.qWait(300)
        bar = self.toolbar()
        self.assertLessEqual(bar.y() + bar.height(), keyboard.y() - 8 + 0.5, 'above the keyboard, its last row free')
        self.screen.calls.clear()
        self.tap(self.full_window, self.scene(keyboard, keyboard.width() / 2, 6))              # its bar, between keys
        self.drag(self.full_window, [self.scene(keyboard, 20 + 4 * k, keyboard.height() - 30) for k in range(6)])
        self.assertFalse(self.screen.of('pointerMove') + self.screen.of('pointerButton'),
                         'nothing reaches the picture under the keyboard')

    # covers: desktop-mode.floating-keyboard/E5
    def test_the_keyboard_goes_when_hidden_or_when_fullscreen_ends(self):
        win, keyboard = self.keyboard_open()
        self.press(keyboard, icon='arrow-down')
        self.wait(lambda: win.property('keyboard') is None, 'the keyboard hidden by its key')
        self.assertFalse(win.property('typing'))
        win.setProperty('typing', True)
        self.wait(lambda: win.property('keyboard') is not None, 'the keyboard again')
        self.leave_fullscreen()
        self.assertFalse(win.property('typing'))
        self.assertIsNone(win.property('keyboard'), 'out of fullscreen, no keyboard')


class Tv(WindowTest):
    def on_tv(self):
        win = self.open(workspace=0, cast=True)
        self.screen.set(status='tv', onTv=True, tvNodeId=42)
        self.wait(lambda: self.floater.on_cast, 'a window on the cast output')
        tv = self.floater.on_cast[0]
        QTest.qWaitForWindowExposed(tv)
        return win, tv

    # covers: desktop-mode.tv-computer-mode/E1 desktop-mode.tv-computer-mode/E4
    def test_computer_mode_shows_the_desktop_on_the_tv_from_a_fresh_picture(self):
        win, tv = self.on_tv()
        self.assertEqual(self.screen.of('setTvShown'), [[True]], 'a picture of its own for the TV (it starts with a frame)')
        tv_pictures = self.pictures(tv)
        self.assertEqual([p.property('nodeId') for p in tv_pictures], [42])
        self.assertTrue(tv_pictures[0].isVisible())
        self.assertFalse(win.isVisible(), 'the floating window is put away meanwhile')
        self.screen.set(status='running', onTv=False, tvNodeId=0)
        QTest.qWait(100)
        self.assertTrue(win.isVisible(), 'and back when the TV is gone')
        self.assertEqual(self.screen.of('setTvShown'), [[True], [False]])

    # covers: desktop-mode.tv-computer-mode/E2
    def test_the_tv_pointer_buttons_and_wheel_go_into_the_desktop(self):
        win, tv = self.on_tv()
        self.screen.calls.clear()
        QTest.mouseMove(tv, QPoint(960, 270))
        QTest.mousePress(tv, Qt.LeftButton, Qt.NoModifier, QPoint(480, 810))
        QTest.mouseRelease(tv, Qt.LeftButton, Qt.NoModifier, QPoint(480, 810))
        QTest.mouseClick(tv, Qt.RightButton, Qt.NoModifier, QPoint(480, 810))
        moves = self.screen.of('pointerMove')
        self.assertIn([0.25, 0.75], moves, 'where the TV pointer is, as fractions of the desktop')
        self.assertEqual(self.screen.of('pointerButton'), [[0x110, True], [0x110, False], [0x111, True], [0x111, False]])
        from PySide6.QtGui import QWheelEvent
        wheel = QWheelEvent(QPointF(480, 810), QPointF(480, 810), QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                            Qt.NoScrollPhase, False)
        QGuiApplication.sendEvent(tv, wheel)
        self.assertEqual(self.screen.of('scroll'), [[-0.0, 15.0]], 'a notch down is 15 axis units down')

    # covers: desktop-mode.tv-computer-mode/E3
    def test_text_typed_on_the_phone_for_the_tv_goes_into_the_desktop(self):
        win, tv = self.on_tv()
        tv.requestActivate()
        QTest.qWait(50)
        self.screen.calls.clear()
        commit = QInputMethodEvent()
        commit.setCommitString('你好🙂')
        QGuiApplication.sendEvent(tv.focusObject(), commit)
        QTest.keyClick(tv, Qt.Key_Return)
        self.assertEqual(self.screen.of('typeText'), [['你好🙂']])
        self.assertEqual(self.screen.of('key'), [[28, True], [28, False]], 'Enter as a key')

    # covers: desktop-mode.tv-computer-mode/E5
    def test_the_cast_button_casts_this_window_s_screen(self):
        win = self.open(workspace=0)
        self.tap(win, self.scene(self.picture(), 50, 50))
        self.tap_action('video-television')
        self.assertEqual(self.screen.of('castToTv'), [[]], 'the floating window: its own screen')
        self.screen.calls.clear()
        self.enter_fullscreen()
        win.setProperty('toolbarShown', True)
        QTest.qWait(300)
        self.tap_action('video-television')
        self.assertEqual(self.screen.of('castToTv'), [[]], 'fullscreen: the same, after leaving it')
        self.wait(lambda: not win.property('full'), 'out of fullscreen')


class DirectorWindow(WindowTest):
    def directing(self, board=False):
        screens = [Screen(2, 52), Screen(3, 53), Screen(4, 54)] + ([Board()] if board else [])
        director = Director(screens)
        win = self.open(director=director)
        for p in self.pictures():
            p.setProperty('ready', True)
        return win, director

    def tiles(self):
        return [i for i in self.find(lambda i: i.property('focused') is not None and i.property('isBoard') is not None)]

    # covers: desktop-mode.director/E1
    def test_one_member_is_a_plain_window_and_the_board_beside_one_screen_makes_two_tiles(self):
        win = self.open(director=Director([Screen(2, 52)]))
        tiles = self.tiles()
        self.assertEqual(len(tiles), 1)
        self.assertEqual(win.property('columnWidth'), 0, 'no column: it looks like a floating window')
        self.assertEqual(round(tiles[0].width()), round(win.property('panelWidth')), 'its picture the whole window')
        self.close()
        win = self.open(director=Director([Screen(2, 52), Board()]))
        tiles = self.tiles()
        self.assertEqual(len(tiles), 2, 'a screen and the board: two tiles')
        board = next(t for t in tiles if t.property('isBoard'))
        self.assertFalse(board.property('focused'))
        self.assertTrue(board.isVisible() and win.property('columnWidth') > 0, 'the board in the column beside it')

    # covers: desktop-mode.director/E4
    def test_the_board_in_focus_shows_the_team_and_never_covers_a_screen_s_caption(self):
        board = None
        for post in ({'role': 'lead', 'kind': 'brief', 'text': 'A poster for the fair', 'thread': 'L'},
                     {'role': 'art', 'kind': 'review', 'text': 'Needs a palette', 'parent': 'L', 'workspace': 3},
                     {'role': 'copy', 'kind': 'review', 'text': 'Two headlines', 'parent': 'L', 'workspace': 4},
                     {'role': 'lead', 'kind': 'decision', 'text': 'Blue palette, headline one', 'thread': 'L'},
                     {'role': 'art', 'kind': 'blocked', 'text': 'The font is missing', 'parent': 'L', 'workspace': 3}):
            board = apply_to_board(board, {**post, 'time': 1790000000.0})
        lead = Screen(2, 52)
        lead.set(activityState='working', activityText='Writing the brief')
        director = Director([lead, Screen(3, 53), Board({k: v for k, v in board.items() if k != 'posts'})])
        win = self.open(director=director)
        caption = self.caption()
        board_tile = next(t for t in self.tiles() if t.property('isBoard'))
        QTest.qWait(300)
        self.assertEqual(caption.property('state'), 'working', "the lead's screen in focus has its caption")
        top_left = caption.mapToScene(QPointF(0, 0))
        tile_left = board_tile.mapToScene(QPointF(0, 0))
        self.assertTrue(tile_left.x() >= top_left.x() + caption.width() or tile_left.y() >= top_left.y() + caption.height()
                        or tile_left.y() + board_tile.height() <= top_left.y(), 'the board beside it, not over its caption')
        self.tap(win, self.scene(board_tile, board_tile.width() / 2, board_tile.height() / 2))
        QTest.qWait(300)
        self.assertEqual(director.focus, 100)
        self.assertEqual(caption.property('state'), 'hidden', 'no screen caption over the board')
        shown = {i.property('text') for i in self.find(lambda i: i.property('text') is not None and i.isVisible())}
        for text in ('A poster for the fair', 'At work', 'lead', 'art', 'copy', 'blocked', 'The font is missing',
                     'Two headlines', 'Decision', 'Blue palette, headline one'):
            self.assertIn(text, shown, 'the phase, the brief, a row for each member (role, state, words), the decision')

    # covers: desktop-mode.director/E2
    def test_a_tap_on_a_thumbnail_focuses_it_breathing_in_on_its_own_picture(self):
        win, director = self.directing()
        QTest.qWait(300)
        nodes = sorted(p.property('nodeId') for p in self.pictures() if p.property('nodeId'))
        self.assertEqual(nodes, [52, 53, 54], 'a picture each')
        thumb = next(t for t in self.tiles() if not t.property('focused') and t.property('modelData').workspace == 3)
        scales = []
        thumb.scaleChanged.connect(lambda: scales.append(thumb.scale()))
        self.tap(win, self.scene(thumb, thumb.width() / 2, thumb.height() / 2))
        self.assertEqual(director.calls, [('setFocus', 3)])
        QTest.qWait(400)
        self.assertTrue(thumb.property('focused'))
        self.assertTrue(scales and abs(scales[0] - 0.95) < 0.02 and scales[-1] == 1, f'0.95 to its size: {scales[:3]}...')
        self.assertTrue(all(b >= a for a, b in zip(scales, scales[1:])) and max(scales) <= 1, 'no overshoot')
        self.assertEqual(sorted(p.property('nodeId') for p in self.pictures() if p.property('nodeId')), nodes,
                         'no picture switched (switching one showed black while it connected)')

    # covers: desktop-mode.director/E3
    def test_zoom_cycles_three_levels_and_fullscreen_lays_out_as_the_tv(self):
        win, director = self.directing()
        self.enter_fullscreen()
        win.setProperty('toolbarShown', True)
        QTest.qWait(300)
        self.assertIn('zoom-in', self.icons())
        widths, columns = [], []
        for level in range(4):
            QTest.qWait(400)                                # the panel's size glides to the level's
            focus = next(t for t in self.tiles() if t.property('focused'))
            others = [t for t in self.tiles() if not t.property('focused')]
            self.assertAlmostEqual(focus.height(), round(focus.width() * 9 / 16), delta=1, msg='the focus 16:9')
            stage = self.stage()
            self.assertTrue(focus.height() <= stage.height() + 0.5 and win.property('shownWidth') <= stage.width(),
                            'as large as fits')
            self.assertTrue(focus.width() >= stage.width() * 0.7 or focus.height() >= stage.height() - 0.5,
                            'as large as fits: the width or the height filled')
            if director.level < 2:
                self.assertTrue(all(o.isVisible() and o.x() >= focus.width() for o in others), 'the others on the right')
                self.assertEqual(len({round(o.x()) for o in others}), 1, 'in one column')
                columns.append(round(others[0].width()))
            else:
                self.assertFalse(any(o.isVisible() for o in others), 'solo: the focus alone')
                columns.append(0)
            widths.append(round(focus.width()))
            win.setProperty('toolbarShown', True)
            QTest.qWait(250)
            self.tap_action('zoom-in')
        self.assertEqual(director.calls, [('nextLevel',)] * 4)
        self.assertTrue(columns[0] > columns[1] > columns[2] == 0 and columns[3] == columns[0],
                        f'standard, enlarged (a thinner column), solo, standard: {columns}')
        self.assertTrue(widths[0] <= widths[1] <= widths[2] and widths[3] == widths[0], f'the focus grows: {widths}')


def tearDownModule():
    """The windows' engines go before their stand-ins, and before the tests after these: left to the
    end of the run, an engine's bindings read stand-ins already gone (a crash at exit)."""
    for engine, *_ in KEPT:
        for window in engine.rootObjects():
            window.close()
        engine.deleteLater()
    APP.sendPostedEvents(None, QEvent.DeferredDelete)
    APP.processEvents()
    KEPT.clear()


if __name__ == '__main__':
    unittest.main()
