# SPDX-License-Identifier: MIT
"""The Agent app and its Home overlay as built (agent/assistant/app with the design system), on a
headless KWin with two outputs, the phone's and a cast screen's (docs/67, docs/87). Their session bus
is a private one with stand-ins of what the app talks to there: the voice service (com.rungic.VoiceAgent,
its interface from rungic_voice_agent.py) and KWin's effects (org.kde.KWin /Effects); the Wayland
side is the real KWin. Plasma Mobile's panel settings (a QML module of plasma-mobile) are a stand-in.

- Hold(true, screen), as the navigation panel calls it, maps the overlay on that output and starts
  talking; Hold(false) sends;
- the overlay loads KWin's blur and contrast effects while it is shown and unloads them when hidden,
  leaving alone one the user had loaded;
- the app is one instance: a second start (a notification, the overlay's "open in app") hands its
  conversation to the running window and exits; no second window.
"""
import ast
import json
import os
import subprocess
import time
from pathlib import Path

import harness

from gi.repository import Gio, GLib

SRC = Path('/src')
BUILD = Path('/tmp/assistant-build')
STUBS = Path('/tmp/assistant-stubs')
CALLS = Path('/tmp/assistant-calls.jsonl')
USER_EFFECTS = Path('/tmp/assistant-user-effects.json')
EFFECTS_XML = '''<node><interface name="org.kde.kwin.Effects">
<method name="isEffectLoaded"><arg type="s" direction="in"/><arg type="b" direction="out"/></method>
<method name="loadEffect"><arg type="s" direction="in"/><arg type="b" direction="out"/></method>
<method name="unloadEffect"><arg type="s" direction="in"/></method>
</interface></node>'''
# Both stand-ins in one process on the private bus; every call is a line in CALLS.
STAND_INS = '''
import json, sys
from gi.repository import Gio, GLib
agent_xml, effects_xml, calls, user_effects = sys.argv[1:5]
loaded = set()

def note(*call):
    with open(calls, "a") as f:
        f.write(json.dumps(call) + "\\n")

def agent(conn, sender, path, iface, method, params, inv):
    args = params.unpack()
    note("agent", method, *args)
    replies = {"OpenConversation": {"conversation": args[0] if args else "", "title": "", "untitled": True, "history": []},
               "OpenAssistant": {"conversation": "main", "title": "Main conversation", "main": True, "history": []},
               "ListConversations": [], "Setup": {"preferences": {"homeHold": True}}}
    outs = [a for a in node_agent.interfaces[0].lookup_method(method).out_args]
    inv.return_value(GLib.Variant("(s)", (json.dumps(replies.get(method, {})),)) if outs else None)

def effects(conn, sender, path, iface, method, params, inv):
    name = params.unpack()[0]
    note("kwin", method, name)
    users = set(json.load(open(user_effects))) if __import__("os").path.exists(user_effects) else set()
    if method == "isEffectLoaded":
        inv.return_value(GLib.Variant("(b)", (name in loaded or name in users,)))
    elif method == "loadEffect":
        loaded.add(name); inv.return_value(GLib.Variant("(b)", (True,)))
    else:
        loaded.discard(name); inv.return_value(None)

node_agent = Gio.DBusNodeInfo.new_for_xml(agent_xml)
node_effects = Gio.DBusNodeInfo.new_for_xml(effects_xml)
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
bus.register_object("/com/rungic/VoiceAgent", node_agent.interfaces[0], agent, None, None)
bus.register_object("/Effects", node_effects.interfaces[0], effects, None, None)
Gio.bus_own_name_on_connection(bus, "com.rungic.VoiceAgent", 0, None, None)
Gio.bus_own_name_on_connection(bus, "org.kde.KWin", 0, None, None)
GLib.MainLoop().run()
'''
PANELS = 'import QtQml\nQtObject { property string screenName; property real navigationPanelHeight: 0; property real statusBarHeight: 0 }\n'


def build():
    """The design system's QML module and the app, built from the working tree."""
    for name, source, target in (('design', 'desktop/design', 'rungicdesign'),
                                 ('app', 'agent/assistant/app', 'rungic-voice-assistant')):
        out = BUILD / name
        log = open(f'/tmp/assistant-build-{name}.log', 'w')
        subprocess.run(['cmake', '-S', str(SRC / source), '-B', str(out), '-DCMAKE_BUILD_TYPE=RelWithDebInfo'],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
        subprocess.run(['cmake', '--build', str(out), '-j', str(os.cpu_count()), '--target', target],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    state = STUBS / 'org/kde/plasma/private/mobileshell/state'
    state.mkdir(parents=True, exist_ok=True)
    (state / 'PanelSettingsDBusClient.qml').write_text(PANELS)
    (state / 'qmldir').write_text('module org.kde.plasma.private.mobileshell.state\nPanelSettingsDBusClient 1.0 PanelSettingsDBusClient.qml\n')
    return BUILD / 'app/rungic-voice-assistant'


def agent_interface():
    tree = ast.parse((SRC / 'agent/assistant/rungic_voice_agent.py').read_text())
    return next(n.value.value for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], 'id', '') == 'INTERFACE')


def calls(kind=None):
    if not CALLS.exists():
        return []
    lines = [json.loads(line) for line in CALLS.read_text().splitlines()]
    return [line[1:] for line in lines if kind is None or line[0] == kind]


# covers[system]: agent.home-hold/E1 agent.home-hold/E8 agent.chat-app/E6
def test():
    program = build()
    daemon = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--print-address'], stdout=subprocess.PIPE, text=True)
    address = daemon.stdout.readline().strip()
    with harness.Session(1080, 2400, outputs=2) as s:
        s.children.append(daemon)
        env = {**os.environ, 'DBUS_SESSION_BUS_ADDRESS': address, 'QT_QUICK_CONTROLS_STYLE': 'Basic',
               'QML_IMPORT_PATH': f'{BUILD}/design/qml:{STUBS}', 'XDG_CONFIG_HOME': '/tmp/assistant-config'}
        s.start(['python3', '-c', STAND_INS, agent_interface(), EFFECTS_XML, str(CALLS), str(USER_EFFECTS)], env=env)
        bus = Gio.DBusConnection.new_for_address_sync(
            address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)

        def owned(name):
            return bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'NameHasOwner',
                                 GLib.Variant('(s)', (name,)), None, 0, 3000, None).unpack()[0]

        def assistant(method, *args):
            signature = {'Hold': '(bs)', 'Show': '(s)', 'Hide': '()'}[method]
            bus.call_sync('com.rungic.VoiceAssistant', '/Assistant', 'com.rungic.VoiceAssistant.Assistant', method,
                          GLib.Variant(signature, args) if args else None, None, 0, 5000, None)
        s.wait_for(lambda: owned('com.rungic.VoiceAgent') and owned('org.kde.KWin'), 10, 'the stand-ins')

        outputs = s.kwin_api.windows()['outputs']
        s.check(len(outputs) == 2, 'two outputs: the phone and a cast screen')
        cast = outputs[1]
        before = {w['caption'] + w['cls'] for w in s.stack()}
        s.start([str(program), '--overlay'], env=env)
        s.wait_for(lambda: owned('com.rungic.VoiceAssistant'), 30, 'the overlay on the bus')
        s.check(not [w for w in s.stack() if w['caption'] + w['cls'] not in before], 'resident: nothing on screen yet')

        # ---- Hold on the cast screen: there, talking --------------------------------------------
        CALLS.write_text('')
        assistant('Hold', True, cast['name'])
        g = cast['geometry']
        frame = [g['x'], g['y'], g['width'], g['height']] if isinstance(g, dict) else list(g)
        overlay = s.wait_for(lambda: next((w for w in s.stack() if w['caption'] + w['cls'] not in before), None), 10,
                             'the overlay mapped')
        s.check(overlay['frame'] == frame, f'mapped on the output Home was held on ({cast["name"]} {frame}), not the phone\'s')
        s.wait_for(lambda: ['AssistantTalk', cast['name']] in calls('agent'), 5, 'talking')
        s.check(True, 'holding starts talking, the reply for that screen')
        s.check(['loadEffect', 'blur'] in calls('kwin') and ['loadEffect', 'contrast'] in calls('kwin'),
                'shown: blur and contrast loaded (they were not)')
        assistant('Hold', False, '')
        s.wait_for(lambda: ['ReleaseTalking'] in calls('agent'), 5, 'the release')
        s.check(True, 'lifting sends')
        assistant('Hide')
        s.wait_for(lambda: not [w for w in s.stack() if w['caption'] + w['cls'] not in before], 5, 'the overlay hidden')
        s.wait_for(lambda: ['unloadEffect', 'blur'] in calls('kwin') and ['unloadEffect', 'contrast'] in calls('kwin'), 5,
                   'effects unloaded')
        s.check(True, 'hidden: the effects it loaded are unloaded')

        # ---- the user had blur on: left alone -----------------------------------------------------
        USER_EFFECTS.write_text(json.dumps(['blur']))
        CALLS.write_text('')
        assistant('Show', outputs[0]['name'])
        s.wait_for(lambda: [w for w in s.stack() if w['caption'] + w['cls'] not in before], 10, 'the overlay shown again')
        s.wait_for(lambda: ['loadEffect', 'contrast'] in calls('kwin'), 5, 'contrast loaded')
        s.check(['loadEffect', 'blur'] not in calls('kwin'), 'blur, loaded by the user, not loaded again')
        assistant('Hide')
        s.wait_for(lambda: ['unloadEffect', 'contrast'] in calls('kwin'), 5, 'contrast unloaded')
        time.sleep(0.5)
        s.check(['unloadEffect', 'blur'] not in calls('kwin'), 'the user\'s blur stays loaded')

        # ---- one app ---------------------------------------------------------------------------------
        before = {w['caption'] + w['cls'] for w in s.stack()}
        s.start([str(program)], env=env)
        s.wait_for(lambda: owned('com.rungic.VoiceAssistantApp'), 30, 'the app on the bus')
        app = s.wait_for(lambda: [w for w in s.stack() if w['caption'] + w['cls'] not in before], 15, 'the app window')
        s.check(len(app) == 1, 'one app window')
        CALLS.write_text('')
        second = subprocess.run([str(program), '--conversation', 'c42'], env=env, timeout=20, capture_output=True)
        s.check(second.returncode == 0, 'a second start hands over and exits')
        s.wait_for(lambda: ['OpenConversation', 'c42'] in calls('agent'), 10, 'the conversation opened in the running app')
        s.check(True, 'the running window opens the conversation the second start was given')
        time.sleep(1)
        s.check(len([w for w in s.stack() if w['caption'] + w['cls'] not in before]) == 1, 'still one app window')
        # covers[system]: agent.sign-in/E1
        CALLS.write_text('')
        entry = subprocess.run([str(program), '--sign-in'], env=env, timeout=20, capture_output=True)
        s.check(entry.returncode == 0, 'sign-in entry hands over to the existing app and exits')
        s.wait_for(lambda: ['Setup'] in calls('agent'), 10, 'account page requests the actual account state')
        s.check(len([w for w in s.stack() if w['caption'] + w['cls'] not in before]) == 1,
                'sign-in entry uses the existing window')
        return s.steps


if __name__ == '__main__':
    harness.run('assistant_app', test)
