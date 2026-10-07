# SPDX-License-Identifier: MIT
"""Audio-follow's startup gate and actual KWin script on a fresh private session bus."""
import os
import subprocess
import time
import harness

CLIENT = '''
import runpy, subprocess
from gi.repository import Gio, GLib
subprocess.run(['/usr/libexec/rungic-wait-dbus', 'org.kde.KWin', '--timeout', '20'], check=True)
audio = runpy.run_path('/usr/bin/rungic-audio-follow')
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
node = Gio.DBusNodeInfo.new_for_xml(audio['INTERFACE'])
loop = GLib.MainLoop()
seen = []
def windows(connection, sender, path, interface, method, args, invocation):
    seen.append(args.unpack()[0])
    invocation.return_value(None)
    loop.quit()
bus.register_object(audio['SERVICE_PATH'], node.interfaces[0], windows, None, None)
audio['load_script'](bus, 'rungic-audio-follow-startup-test')
GLib.timeout_add_seconds(5, lambda: (loop.quit(), False)[1])
loop.run()
assert seen, 'actual KWin script did not report windows'
print('windows reported', flush=True)
'''


def test():
    # covers[system]: desktop-mode.audio-follow/E1
    runtime = '/tmp/audio-follow-startup-rt'
    os.makedirs(runtime, mode=0o700, exist_ok=True)
    client = subprocess.Popen(['python3', '-c', CLIENT],
                              env={**os.environ, 'XDG_RUNTIME_DIR': runtime},
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(.3)
        if client.poll() is not None:
            raise harness.Failed('audio-follow proceeded before KWin: ' + client.stderr.read())
        with harness.Session(720, 1280, name='audio-follow-startup') as session:
            out, err = client.communicate(timeout=15)
            session.check(client.returncode == 0, 'same startup attempt loaded real KWin script: ' + err[-500:])
            session.check('windows reported' in out, 'script reported windows without a service restart')
            return session.steps
    finally:
        if client.poll() is None:
            client.terminate()
            client.communicate(timeout=3)


if __name__ == '__main__':
    harness.run('audio_follow_startup', test)
