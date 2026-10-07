#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The Agent app's own QML (agent/assistant/app/qml: Main, ChatPage, ChatModel, ChatEntry, Composer,
ConversationDrawer, SettingsPage), run offline with the design system (assistant_qml.py): what the
user sees of a conversation as the voice service's replies and events arrive. Only the D-Bus client
(AgentClient) is a stand-in: the test plays the service. Requires PySide6 (and Pillow for pictures).
"""
from pathlib import Path
import sys
import time
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import assistant_qml as q  # noqa: E402
from PIL import Image  # noqa: E402

NOW = time.time()


def message(i, role='user', text=None):
    return {'type': 'message', 'role': role, 'id': f'm{i}', 'text': text or f'第 {i} 条消息 ' + '内容' * 20, 'time': NOW - 600 + i}


def finished_turn(t0, answer='做完了', steps=2):
    events = [{'type': 'agent-started', 'time': t0}]
    for i in range(steps - 1):
        events.append({'type': 'command', 'id': f'c{t0}{i}', 'command': f'ls {i}', 'status': 'done', 'exitCode': 0, 'time': t0 + 1})
    events += [{'type': 'agent-message', 'id': f'a{t0}', 'text': answer, 'final': True, 'time': t0 + 5},
               {'type': 'agent-finished', 'time': t0 + 7}]
    return events


class AppTest(unittest.TestCase):
    def setUp(self):
        self.stage = q.Stage()
        self.engine = self.stage.engine()
        self.root = self.stage.load(self.engine, 'Main')
        self.window = self.root
        self.content = q.content(self.root)
        self.page = q.of_type(self.content, 'ChatPage')[0]
        q.spin(0.3)

    def tearDown(self):
        self.window.close()
        self.engine.deleteLater()
        q.spin(0.05)
        self.stage.close()

    def texts(self):
        return q.texts(self.content)

    def open(self, cid, history, title='一段对话'):
        """The user opens a conversation; the service answers with its stored history."""
        q.invoke(self.root, 'openConversation', cid)
        q.send(self.engine, 'conversationOpened', {'conversation': cid, 'title': title, 'untitled': False, 'history': history})
        q.spin(0.3)

    def event(self, **e):
        e.setdefault('conversation', self.page.property('conversationId'))
        e.setdefault('time', time.time())
        q.send(self.engine, 'event', e)

    @staticmethod
    def at_end(view):
        # ListView's estimated delegate heights can move its content origin below zero.
        return view.property('contentY') + view.height() >= view.property('originY') + view.property('contentHeight') - 1

    def list_view(self):
        return max(q.of_type(self.page, 'QQuickListView'), key=lambda v: v.height())

    # covers: agent.chat-app/E1
    def test_opening_shows_the_stored_history_and_says_opening_only_when_slow(self):
        q.invoke(self.root, 'openConversation', 'c1')
        self.assertIn(['openConversation', 'c1'], q.calls(self.engine))
        q.spin(0.05)
        texts = self.texts()
        self.assertNotIn('Opening…', texts, 'a quick load shows nothing in between')
        self.assertNotIn('How can I help?', texts, 'not empty: its history is on its way')
        q.spin(0.25)
        self.assertIn('Opening…', self.texts(), 'past 150 ms it says it is opening')
        self.assertNotIn('How can I help?', self.texts())
        # The service answers from its own store (it does not wait for Codex to resume the thread).
        q.send(self.engine, 'conversationOpened', {'conversation': 'c1', 'title': '天气', 'untitled': False,
                                                   'history': [message(1, text='明天会下雨吗'), message(2, 'assistant', '不会')]})
        q.spin(0.3)
        texts = self.texts()
        self.assertIn('明天会下雨吗', ' '.join(texts))
        self.assertNotIn('Opening…', texts)
        self.assertNotIn('How can I help?', texts)
        # A conversation that really has nothing: the empty state, and only then.
        self.open('c2', [])
        self.assertIn('How can I help?', self.texts())

    # covers: agent.chat-app/E2
    def test_reading_back_stays_where_it_was_dragged(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        self.open('long', [message(i, 'user' if i % 2 else 'assistant') for i in range(60)])
        view = self.list_view()
        self.assertTrue(self.at_end(view), 'opened at the latest')
        self.assertNotIn('Jump to latest', self.texts())
        # A finger drags the thread down: back into the history.
        x = int(view.width() / 2)
        y = int(view.mapToScene(view.boundingRect().center()).y())
        QTest.mousePress(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(x, y - 150))
        for step in range(1, 16):
            QTest.mouseMove(self.window, QPoint(x, y - 150 + step * 20), 16)
        QTest.mouseRelease(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(x, y + 150), 16)
        q.spin(1.5)
        held = view.property('contentY')
        self.assertFalse(self.at_end(view), 'scrolled back')
        self.assertIn('Jump to latest', self.texts())
        # New content meanwhile (a reply, agent work): the view stays where the reader is.
        self.event(type='message', role='assistant', id='late', text='新的回答')
        for event in finished_turn(time.time()):
            self.event(**event)
        q.spin(0.5)
        self.assertAlmostEqual(view.property('contentY'), held, delta=1, msg='no jump back to the end')
        self.assertGreaterEqual(view.property('contentY'), view.property('originY') - view.property('topMargin') - 1,
                                'not pulled past the top into blank space')
        q.click([b for b in q.of_type(self.page, 'PillButton') if b.property('text') == 'Jump to latest'][0])
        q.spin(0.5)
        self.assertTrue(self.at_end(view), '"Jump to latest" goes back to the end')

    # covers: agent.chat-app/E3
    def test_an_idle_app_draws_nothing(self):
        history = [message(1), message(2, 'assistant')] + finished_turn(NOW - 300) + [message(3)]
        self.open('idle', history)
        # Sanity: the counter sees frames while something moves (a running turn shines).
        self.event(type='agent-started')
        self.assertGreater(q.frames(self.window, 1.0), 0, 'a running turn animates')
        self.event(type='agent-finished')
        q.spin(1.0)                 # the last transitions settle
        self.assertEqual(q.frames(self.window, 2.0), 0, 'nothing on screen moves: no frame drawn')
        # Animations that are not on screen (the side panel closed, the loading ring hidden) do not keep it busy.
        self.assertFalse(q.of_type(self.page, 'BusyRing')[0].isVisible() if q.of_type(self.page, 'BusyRing') else False)

    # covers: agent.chat-app/E4
    def test_a_turn_folds_into_one_card(self):
        self.open('work', [message(1, text='整理下载文件夹')])
        start = time.time() - 3
        self.event(type='agent-started', time=start)
        self.event(type='command', id='x1', command="/bin/bash -lc 'ls ~/Downloads'", status='running', exitCode=None)
        q.spin(1.2)
        running = [t for t in self.texts() if t.startswith('Working · ')]
        self.assertTrue(running, self.texts())
        self.assertRegex(running[0], r'^Working · \d+s')
        counted = self.list_view().property('count')
        self.event(type='command', id='x1', command="/bin/bash -lc 'ls ~/Downloads'", status='done', exitCode=0, output='a.txt')
        self.event(type='agent-message', id='n1', text='看过了', final=False)
        self.event(type='agent-message', id='f1', text='已经整理好了。', final=True)
        self.event(type='agent-finished', time=start + 9)
        q.spin(0.3)
        texts = self.texts()
        self.assertIn('Worked through 3 steps · 9s', texts, 'done: steps and time, folded')
        self.assertFalse([t for t in texts if t.startswith('Working · ')])
        self.assertGreaterEqual(self.list_view().property('count'), counted, 'what was shown stays')
        self.assertIn('已经整理好了。', ' '.join(texts), 'the answer under the card')
        # Copy and Read aloud under the answer; Read aloud asks the voice to read it.
        read = [b for b in q.of_type(self.page, 'IconButton') if b.property('text') == 'Read aloud' and q.shown(b)]
        self.assertTrue([b for b in q.of_type(self.page, 'IconButton') if b.property('text') == 'Copy' and q.shown(b)])
        q.click(read[-1])
        self.assertIn(['readAloud', '已经整理好了。'], q.calls(self.engine))
        # Opening the card shows the steps.
        meta = [b for b in q.of_type(self.page, 'MetaButton') if q.shown(b)][-1]
        q.click(meta)
        q.spin(0.3)
        self.assertIn('Command · Done', self.texts())

    # covers: agent.phone-mode/E17
    def test_a_phone_task_shows_the_same_card_as_a_turn(self):
        # A task given in a call has the plan and the current activity of a push-to-talk turn's
        # card: the same task state (task_state.py) and the same card (TaskProgress), 2026-10-05.
        self.open('call', [message(1, text='做个茶园游戏')])
        task = {'taskId': 'task_1', 'conversation': 'call', 'text': '做个茶园游戏', 'status': 'running', 'created': time.time()}
        self.event(type='phone-task', task=task)
        self.event(type='task', taskId='task_1', plan=[{'step': '写游戏简报', 'status': 'completed'},
                                                       {'step': '画像素素材', 'status': 'inProgress'}],
                   current={'kind': 'command', 'text': 'Run Krita', 'detail': '', 'progress': None, 'seconds': 3},
                   recent=[], files=[], preview=None)
        q.spin(0.4)
        plan = sorted((p for p in q.of_type(self.page, 'PlanStep') if q.shown(p)), key=lambda p: p.property('y'))
        self.assertEqual([p.property('text') for p in plan], ['写游戏简报', '画像素素材'])
        self.assertTrue([a for a in q.of_type(self.page, 'ActivityCard') if q.shown(a) and a.property('text') == 'Run Krita'])
        # A later update of the task itself keeps its card.
        self.event(type='phone-task', task={**task, 'status': 'waiting_input'})
        q.spin(0.3)
        self.assertEqual(len([p for p in q.of_type(self.page, 'PlanStep') if q.shown(p)]), 2)

    # covers: agent.chat-app/E5
    def test_after_a_service_restart_the_conversation_reopens_and_the_open_turn_ends(self):
        self.open('r1', [message(1, text='渲染甜甜圈')])
        start = time.time() - 34
        self.event(type='agent-started', time=start)
        self.event(type='command', id='b1', command='blender -b', status='running', exitCode=None)
        q.spin(0.2)
        q.clear(self.engine)
        # The service restarted under the turn: it says so, the app opens the same conversation again.
        q.send(self.engine, 'event', {'type': 'agent-restarted', 'conversation': 'r1', 'time': time.time()})
        self.assertIn(['openConversation', 'r1'], q.calls(self.engine))
        history = [message(1, text='渲染甜甜圈'), {'type': 'agent-started', 'time': start},
                   {'type': 'command', 'id': 'b1', 'command': 'blender -b', 'status': 'done', 'exitCode': 0, 'time': start + 34}]
        q.send(self.engine, 'conversationOpened', {'conversation': 'r1', 'title': '渲染', 'untitled': False, 'history': history})
        q.spin(1.3)
        texts = self.texts()
        self.assertFalse([t for t in texts if t.startswith('Working · ')], 'the turn the history left open is not running')
        self.assertIn('Worked through 1 step · 34s', texts, 'it ended where its last event was')
        # Live: a turn still open when the next one starts ended then (stopped), it does not count on.
        self.event(type='agent-started', time=start + 40)
        self.event(type='agent-started', time=start + 50)
        q.spin(0.2)
        self.assertTrue([t for t in self.texts() if t.startswith('Stopped · ')], self.texts())

    # covers: agent.chat-app/E6
    def test_a_second_start_hands_its_conversation_to_the_running_window(self):
        # main.cpp's AppInstance.Open/OpenActivated call this on the running window (the second
        # process exits); here the window's side: on top of a settings page, the conversation opens.
        q.js(self.engine, self.root, 'openSettings()')
        q.spin(0.5)
        stack = q.of_type(self.content, 'PageStack')[0]
        self.assertEqual(stack.property('depth'), 2)
        q.clear(self.engine)
        q.invoke(self.root, 'openConversation', 'from-card')
        q.spin(0.5)
        self.assertEqual(stack.property('depth'), 1, 'back on the conversation')
        self.assertEqual(self.page.property('conversationId'), 'from-card')
        self.assertIn(['openConversation', 'from-card'], q.calls(self.engine))
        self.assertNotIn('startTalking', [c[0] for c in q.calls(self.engine)], 'no voice session is started for it')

    # covers: agent.chat-app/E7
    def test_the_theme_changes_at_once_and_stays_after_a_restart(self):
        theme = self.engine.singletonInstance('com.rungic.design', 'Theme')
        self.assertFalse(theme.property('dark'), 'following the system (light here)')
        light = self.root.property('color').name()
        q.js(self.engine, self.root, "settings.theme = 'dark'")
        q.spin(0.2)
        self.assertTrue(theme.property('dark'))
        self.assertEqual(self.root.property('color').name(), '#141618', 'the window takes the dark background')
        self.assertNotEqual(light, '#141618')
        q.spin(1.0)                 # QML Settings write on their own
        self.window.close()
        self.engine.deleteLater()
        q.spin(0.1)
        self.engine = self.stage.engine()
        self.root = self.window = self.stage.load(self.engine, 'Main')
        self.content = q.content(self.root)
        q.spin(0.2)
        self.assertTrue(self.engine.singletonInstance('com.rungic.design', 'Theme').property('dark'), 'kept after a restart')
        q.js(self.engine, self.root, "settings.theme = 'system'")
        q.spin(0.1)
        self.assertFalse(self.engine.singletonInstance('com.rungic.design', 'Theme').property('dark'), 'back to the system')

    def composer(self):
        return q.of_type(self.page, 'Composer')[0]

    # covers: agent.chat-app/E8 agent.attachments/E5
    def test_the_attach_panel_lays_out_any_number_of_photos_and_rests(self):
        self.open('photos', [message(1)])
        composer = self.composer()
        # Typing first, then +: the panel takes the keyboard's place.
        q.click([b for b in q.of_type(self.page, 'IconButton') if b.property('text') == 'Use keyboard' and q.shown(b)][0])
        self.assertEqual(composer.property('phase'), 'keyboard')
        field = [t for t in q.items(composer) if t.inherits('QQuickTextEdit') and q.shown(t)][0]
        q.click([b for b in q.of_type(self.page, 'IconButton') if b.property('text') == 'Add photos or files' and q.shown(b)][0])
        q.spin(0.3)
        self.assertEqual(composer.property('phase'), 'attach')
        self.assertFalse(field.hasActiveFocus(), 'the text field let go: the keyboard goes down')
        self.assertTrue(q.shown(field), 'the text stays above the panel')
        self.assertIn('Photos', self.texts())
        self.assertIn('Files', self.texts())
        grid = q.of_type(composer, 'QQuickGrid')[0]
        self.assertEqual(grid.property('columns'), 4)
        for count in (0, 1, 3, 4, 9):
            for i in range(len(list(self.stage.pictures.glob('*.png'))), count):
                Image.new('RGB', (40 + i, 30), (i * 20 % 255, 90, 160)).save(self.stage.pictures / f'p{i}.png')
            q.spin(1.0)
            cells = sorted((b for b in q.items(grid) if b.inherits('QQuickAbstractButton') and b.isVisible()),
                           key=lambda c: (c.y(), c.x()))
            self.assertEqual(len(cells), min(count, 8), f'{count} photos: the 8 most recent from Pictures')
            self.assertEqual('Recent photos' in self.texts(), count > 0)
            if cells:
                frame = grid.parentItem()
                size = (frame.width() - 3 * 4) // 4
                self.assertEqual({(c.width(), c.height()) for c in cells}, {(size, size)}, 'four across, square')
                self.assertEqual(len({c.y() for c in cells[:4]}), 1)
                if len(cells) > 4:
                    self.assertGreater(cells[4].y(), cells[0].y(), 'the fifth starts the next row')
            q.spin(0.5)
            self.assertEqual(q.frames(self.window, 1.0), 0, f'{count} photos: the layout is done, nothing redraws')

    # covers: agent.chat-app/E9
    def test_the_side_panel_searches_groups_by_day_and_deletes(self):
        import datetime
        from PySide6.QtCore import QObject
        drawer = [o for o in self.page.findChildren(QObject) if o.metaObject().className().startswith('ConversationDrawer')][0]
        today = datetime.datetime.now()

        def day(n):
            return (today - datetime.timedelta(days=n)).timestamp()
        listing = [{'id': 'main', 'title': 'Main conversation', 'main': True, 'updated': day(0)},
                   {'id': 't1', 'title': '天气预报', 'updated': day(0), 'preview': '明天晴'},
                   {'id': 'y1', 'title': '买菜清单', 'updated': day(1)},
                   {'id': 'd5', 'title': '整理下载', 'updated': day(5), 'preview': '已经删掉 3 个文件'},
                   {'id': 'old', 'title': '旧事', 'updated': day(60)}]
        q.js(self.engine, drawer, 'open()')
        self.assertIn(['listConversations'], q.calls(self.engine))
        q.send(self.engine, 'conversationsListed', listing)
        q.spin(0.5)
        texts = self.texts()
        for title in ('天气预报', '买菜清单', '整理下载', '旧事'):
            self.assertIn(title, texts)
        self.assertIn('Main conversation', texts, 'the main conversation is one of the panel\'s own entries')
        five = (today - datetime.timedelta(days=5))
        for label in ('Today', 'Yesterday', f"{five:%b} {five.day}", 'Earlier'):
            self.assertIn(label, texts)
        # Search: by title and by the latest message; the main conversation stays.
        search = q.of_type(self.content, 'SearchField')[0]
        search.setProperty('text', '删掉')
        q.spin(0.2)
        texts = self.texts()
        self.assertIn('整理下载', texts)
        self.assertNotIn('天气预报', texts)
        self.assertIn('Main conversation', texts)
        search.setProperty('text', '')
        q.spin(0.2)
        # New conversation.
        q.click([b for b in q.of_type(self.content, 'NavItem') if b.property('text') == 'New conversation' and q.shown(b)][0])
        self.assertEqual(self.page.property('conversationId'), '')
        # Hold one to delete it: the service deletes it (and archives its Codex thread).
        q.js(self.engine, drawer, 'open()')
        q.send(self.engine, 'conversationsListed', listing)
        q.spin(0.5)
        row = [b for b in q.of_type(self.content, 'NavItem') if b.property('text') == '买菜清单' and q.shown(b)][0]
        from PySide6.QtCore import QMetaObject
        QMetaObject.invokeMethod(row, 'pressAndHold')
        q.spin(0.2)
        q.click([b for b in q.of_type(self.content, 'PillButton') if b.property('text') == 'Delete' and q.shown(b)][0])
        self.assertIn(['deleteConversation', 'y1'], q.calls(self.engine))
        q.spin(0.3)
        self.assertNotIn('买菜清单', self.texts())

    # ---- pictures and files (docs/88) -----------------------------------------------------
    def picture_file(self, name, size=(300, 200), frames=1):
        path = self.stage.config.parent / name
        images = [Image.new('RGB', size, (40 * i % 255, 120, 200)) for i in range(frames)]
        if frames > 1:
            images[0].save(path, save_all=True, append_images=images[1:], duration=60, loop=0)
        else:
            images[0].save(path)
        return path

    def answer_turn(self, text, spoken=None):
        start = time.time() - 10
        self.event(type='agent-started', time=start)
        self.event(type='agent-message', id='ans', text=text, final=True, time=start + 8)
        self.event(type='agent-finished', time=start + 9)
        if spoken:
            self.event(type='message', role='assistant', id='spoken', text=spoken)
        q.spin(1.0)

    def shown_of(self, kind):
        return [i for i in q.of_type(self.page, kind) if q.shown(i)]

    # covers: agent.attachments/E1
    def test_pictures_of_an_answer_show_under_the_turn_and_files_as_cards(self):
        rocket = self.picture_file('rocket.png')
        model = self.stage.config.parent / 'donut.blend'
        model.write_bytes(b'BLENDER')
        answer = f'渲染好了：\n\n![小火箭](<{rocket}>)\n\n模型在 [donut.blend]({model})。'
        for spoken in (None, '渲染好了，图在下面。'):
            with self.subTest(spoken=bool(spoken)):
                self.open('pics' + str(bool(spoken)), [message(1, text='渲染一个火箭')])
                self.answer_turn(answer, spoken)
                thumbs = self.shown_of('Thumbnail')
                self.assertEqual(len(thumbs), 1, 'the picture under the turn, also when the voice spoke the answer')
                self.assertEqual(thumbs[0].property('source').toString(), 'file://' + str(rocket))
                self.assertEqual(thumbs[0].property('loadState'), 'ready', 'it loads (a bare path would not)')
                chips = self.shown_of('FileChip')
                self.assertEqual([c.property('name') for c in chips], ['donut.blend'], 'the other file as a card')
                body = ' '.join(self.texts())
                self.assertNotIn('![', body, 'no picture left in the text to fail loading')
                self.assertNotIn(str(rocket), body)
                q.click(thumbs[0])
                q.spin(0.5)
                viewer = [v for v in self.page.findChildren(q.QObject) if v.metaObject().className().startswith('ImageViewer')][0]
                self.assertTrue(viewer.property('opened') or viewer.property('visible'), 'a tap shows it large')
                self.assertEqual(viewer.property('source').toString(), 'file://' + str(rocket))
                q.js(self.engine, viewer, 'close()')
                q.spin(0.3)

    # covers: agent.attachments/E2
    def test_moving_pictures_play_in_the_chat_large_and_in_previews(self):
        gif = self.picture_file('moving.gif', frames=4)
        self.open('gif', [message(1, text='做个动图')])
        self.answer_turn(f'好了：![动图](<{gif}>)')
        q.spin(1.0)
        pictures = [p for p in self.shown_of('Picture') if str(p.property('source').toString()).endswith('moving.gif')]
        self.assertTrue(pictures and all(p.property('moving') for p in pictures), 'it moves in the chat')
        q.click(self.shown_of('Thumbnail')[0])
        q.spin(1.0)
        large = [p for p in q.of_type(self.content, 'Picture') if p.property('moving')
                 and str(p.property('source').toString()).endswith('moving.gif') and p not in pictures]
        self.assertTrue(large, 'and large')
        viewer = [v for v in self.page.findChildren(q.QObject) if v.metaObject().className().startswith('ImageViewer')][0]
        q.js(self.engine, viewer, 'close()')
        q.spin(0.3)
        # The user's own: in the bubble above their message, and in the tray before sending.
        self.event(type='message', role='user', id='u2', text='看这个', attachments=[{'path': str(gif), 'name': 'moving.gif', 'kind': 'image'}])
        composer = self.composer()
        q.invoke(composer, 'addAttachment', 'file://' + str(gif))
        composer.setProperty('keyboard', True)
        q.spin(1.0)
        moving = [p for p in self.shown_of('Picture') if p.property('moving') and str(p.property('source').toString()).endswith('moving.gif')]
        self.assertGreaterEqual(len(moving), 3, 'answer, bubble and tray all play')
        # Not shown, not playing: the window hidden pauses them.
        players = [i for i in q.items(self.content) if i.inherits('QQuickAnimatedImage')]
        self.assertTrue(players and not any(p.property('paused') for p in players))
        self.window.hide()
        q.spin(0.3)
        self.assertTrue(all(p.property('paused') for p in players), 'paused while the window is hidden')

    # covers: agent.attachments/E3
    def test_photos_and_files_go_with_the_message_and_show_above_it(self):
        photo = self.picture_file('cat.jpg')
        doc = self.stage.config.parent / 'report.pdf'
        doc.write_bytes(b'%PDF-1.4')
        self.open('send', [message(1)])
        composer = self.composer()
        q.invoke(composer, 'addAttachment', 'file://' + str(photo))
        q.invoke(composer, 'addAttachment', 'file://' + str(doc))
        q.invoke(composer, 'addAttachment', 'file://' + str(photo))      # twice: once
        composer.setProperty('keyboard', True)
        q.spin(0.3)
        self.assertEqual([a['kind'] for a in composer.property('attachments').toVariant()], ['image', 'file'])
        q.clear(self.engine)
        q.invoke(composer, 'submit')
        sent = [c for c in q.calls(self.engine) if c[0] == 'sendText']
        self.assertEqual(len(sent), 1)
        import json
        self.assertEqual(json.loads(sent[0][2]), [{'path': str(photo), 'name': 'cat.jpg', 'kind': 'image'},
                                                 {'path': str(doc), 'name': 'report.pdf', 'kind': 'file'}])
        # The service echoes the message with them: the photo's thumbnail and the file's name above the bubble.
        self.event(type='message', role='user', id='typed-1', text='', typed=True, attachments=json.loads(sent[0][2]))
        q.spin(0.5)
        pictures = [p for p in self.shown_of('Picture') if str(p.property('source').toString()) == 'file://' + str(photo)]
        self.assertEqual(len(pictures), 1)
        self.assertEqual(q.js(self.engine, pictures[0], 'status === Image.Ready'), True, 'the thumbnail loads')
        self.assertIn('report.pdf', self.texts())

    # ---- settings ---------------------------------------------------------------------------------
    # covers: agent.sign-in/E1
    def test_settings_name_codex_sign_in_and_the_api_key_apart(self):
        q.js(self.engine, self.root, 'openSettings()')
        q.spin(0.5)
        self.assertIn(['request', 'Setup'], q.calls(self.engine))
        base = {'codex': {'installed': True, 'version': '0.159.2', 'update': {}}, 'preferences': {}}
        cases = [({'account': {'type': 'chatgpt', 'planType': 'team', 'email': 'a@b.c'}, 'key': {'set': False}},
                  'ChatGPT plan (Team)', 'Not set'),
                 ({'account': {'type': 'apiKey'}, 'key': {'set': True, 'masked': 'sk-…abcd'}}, 'API key', 'sk-…abcd'),
                 ({'account': None, 'key': {'set': False}}, 'Not signed in', 'Not set')]
        for setup, sign_in, key in cases:
            with self.subTest(sign_in):
                q.send(self.engine, 'replied', 'Setup', {**base, **setup, 'accountStatus': 'ready' if setup.get('account') else 'signed-out'})
                q.spin(0.2)
                texts = self.texts()
                self.assertIn('Sign-in', texts)
                self.assertIn(sign_in, texts, 'how Codex signs in')
                self.assertIn('OpenAI API Key', texts)
                self.assertIn('Voice, calls and optional Luna desktop operation', texts, 'what the key is for, apart from Codex')
                self.assertIn('Desktop operation', texts, 'desktop execution has its own setting')
                self.assertIn(key, texts)
        # Who pays: on the sign-in page, by kind; and the key's own page says its use is billed apart.
        bills = {'ChatGPT plan (Team)': "The Agent's tasks count against your ChatGPT plan.",
                 'API key': "The Agent's tasks are billed to the OpenAI API by use, not to a ChatGPT plan."}
        q.js(self.engine, self.root, 'openSettings("AccountPage.qml")')
        q.spin(0.5)
        for setup, sign_in, _key in cases[:2]:
            q.send(self.engine, 'replied', 'Setup', {**base, **setup, 'accountStatus': 'ready' if setup.get('account') else 'signed-out'})
            q.spin(0.2)
            self.assertIn(bills[sign_in], self.texts())
        q.js(self.engine, self.root, 'openSettings("KeyPage.qml")')
        q.spin(0.5)
        self.assertTrue([t for t in self.texts() if 'Billed to the OpenAI API by use, apart from a ChatGPT plan.' in t])


    # covers: agent.computer-use/E8 agent.computer-use/E9
    def test_desktop_mode_choice_uses_the_service_and_keeps_a_rejected_choice(self):
        q.js(self.engine, self.root, 'openSettings("DesktopPage.qml")')
        q.spin(0.5)
        page = q.of_type(self.content, 'DesktopPage')[0]
        setup = {'codex': {'installed': True}, 'desktop': {'mode': 'codex'}, 'key': {'set': False}}
        q.send(self.engine, 'replied', 'Setup', setup)
        q.spin(0.1)
        choices = {c.property('text'): c for c in q.of_type(page, 'ChoiceRow')}
        codex, api = choices['Codex (default)'], choices['Luna via OpenAI API']
        self.assertTrue(codex.property('checked'))
        self.assertFalse(api.property('enabled'), 'API needs a separately configured key')
        q.send(self.engine, 'replied', 'Setup', {**setup, 'key': {'set': True}})
        q.spin(0.1)
        q.click(api)
        self.assertIn(['request', 'SetDesktopMode', ['luna']], q.calls(self.engine))
        self.assertFalse(api.property('enabled'), 'another selection cannot race the pending request')
        q.send(self.engine, 'replied', 'SetDesktopMode', {'error': 'Finish the current task first'})
        q.spin(0.1)
        self.assertTrue(codex.property('checked'), 'rejected selection leaves the actual mode')
        self.assertIn('Finish the current task first', self.texts())
        q.click(api)
        q.send(self.engine, 'replied', 'SetDesktopMode', {'mode': 'luna'})
        q.spin(0.1)
        self.assertTrue(api.property('checked'))
        self.assertFalse(codex.property('checked'))

    # covers: agent.sign-in/E1 agent.usage-widget/E5
    def test_sign_in_entry_and_unknown_account_do_not_claim_signed_out(self):
        q.js(self.engine, self.root, 'openAgentPage("sign-in")')
        q.spin(0.3)
        page = q.of_type(self.content, 'AccountPage')[0]
        q.send(self.engine, 'replied', 'Setup', {'codex': {'installed': True}, 'accountStatus': 'offline', 'account': None})
        q.spin(0.2)
        self.assertEqual(page.property('visualState'), 'unreachable')
        self.assertFalse(page.property('choicesShown'))
        self.assertIn('Cannot confirm the sign-in right now', self.texts())
        button = next(b for b in q.of_type(page, 'PillButton') if b.property('text') == 'Check again')
        q.click(button)
        self.assertIn(['request', 'Setup'], q.calls(self.engine))
        q.send(self.engine, 'replied', 'Setup', {'codex': {'installed': True}, 'accountStatus': 'signed-out', 'account': None})
        q.spin(0.2)
        self.assertEqual(page.property('visualState'), 'signedOut')
        self.assertTrue(page.property('choicesShown'))

    def test_missing_codex_opens_existing_installer_and_unknown_only_rechecks(self):
        q.js(self.engine, self.root, 'openAgentPage("sign-in")')
        q.spin(0.2)
        page = q.of_type(self.content, 'AccountPage')[0]
        q.send(self.engine, 'replied', 'Setup', {'codex': {'installed': False}, 'accountStatus': 'not-installed'})
        q.spin(0.2)
        self.assertEqual(page.property('visualState'), 'notInstalled')
        self.assertFalse(page.property('choicesShown'))
        q.click(next(b for b in q.of_type(page, 'PillButton') if b.property('text') == 'Install Codex'))
        q.spin(0.2)
        installer = q.of_type(self.content, 'CodexPage')[0]
        q.send(self.engine, 'replied', 'Setup', {'codex': {'installed': False}, 'accountStatus': 'not-installed'})
        q.spin(0.2)
        self.assertIn("Codex isn't installed yet", self.texts())
        self.assertFalse(any(c[:2] == ['request', 'InstallCodex'] for c in q.calls(self.engine)), 'opening never auto-installs')
        q.send(self.engine, 'replied', 'Setup', {'codex': {'installed': None}, 'accountStatus': 'offline'})
        q.spin(0.2)
        self.assertIn('Codex installation not confirmed', self.texts())
        buttons = [b for b in q.of_type(installer, 'PillButton') if b.isVisible()]
        self.assertNotIn('Install Codex', [b.property('text') for b in buttons])
        q.click(next(b for b in buttons if b.property('text') == 'Check again'))
        self.assertEqual(q.calls(self.engine)[-1], ['request', 'Setup'])


if __name__ == '__main__':
    unittest.main()
