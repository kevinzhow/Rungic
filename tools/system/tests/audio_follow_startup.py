# SPDX-License-Identifier: MIT
"""Audio-follow's startup gate and actual KWin script on a fresh private session bus."""
import os
import subprocess
import time
from pathlib import Path
import tempfile
import harness

CLIENT = '''
import runpy, subprocess
from gi.repository import Gio, GLib
subprocess.run(['/usr/libexec/rungic-wait-dbus', 'org.kde.KWin', '20'], check=True)
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


def test_unit():
    # An isolated container booted with systemd, never the host or phone manager.
    control = {**os.environ, 'XDG_RUNTIME_DIR': f'/run/user/{os.getuid()}',
               'DBUS_SESSION_BUS_ADDRESS': f'unix:path=/run/user/{os.getuid()}/bus'}
    unit = 'rungic-audio-follow-readiness-test.service'
    directory = Path(f'/run/user/{os.getuid()}/systemd/user')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / unit
    runtime = tempfile.TemporaryDirectory(prefix='audio-follow-unit-')
    log = Path(runtime.name) / 'stdout'
    original = Path('/src/desktop/rungic-plasma-audio-follow.service').read_text()
    # Isolate dependencies; ExecStart, gate and recovery policy remain the product's.
    isolated = '\n'.join(line for line in original.splitlines()
                         if not line.startswith(('After=', 'Wants=', 'PartOf=', 'WantedBy=')))
    isolated = isolated.replace('[Service]', '[Service]\nEnvironment=DBUS_SESSION_BUS_ADDRESS=' +
                               os.environ['DBUS_SESSION_BUS_ADDRESS'] +
                               '\nEnvironment=XDG_RUNTIME_DIR=' + runtime.name +
                               '\nEnvironment=PULSE_SERVER=unix:/run/user/' + str(os.getuid()) + '/pulse/native' +
                               '\nStandardOutput=file:' + str(log))
    def ctl(*args, check=True):
        return subprocess.run(['systemctl', '--user', *args], env=control,
                              capture_output=True, text=True, check=check, timeout=20)
    def properties():
        return dict(line.split('=', 1) for line in ctl('show', unit,
            '-p', 'NRestarts', '-p', 'Result', '-p', 'ActiveState', '-p', 'SubState',
            '-p', 'ExecMainPID', '-p', 'ExecStartPre').stdout.splitlines())
    steps = []
    try:
        path.write_text(isolated)
        ctl('daemon-reload')
        starting = subprocess.Popen(['systemctl', '--user', 'start', unit], env=control,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(.3)
        assert starting.poll() is None and (not log.exists() or not log.read_text()), 'client ran before KWin name'
        with harness.Session(720, 1280, name=f'audio-follow-unit-{os.getpid()}') as session:
            out, err = starting.communicate(timeout=15)
            assert starting.returncode == 0, 'unit first start: ' + err
            session.wait_for(lambda: log.exists() and 'following: windows from KWin' in log.read_text(),
                             10, 'original audio-follow passed loadScript')
            time.sleep(.3)
            state = properties()
            session.check(state['ActiveState'] == 'active' and state['NRestarts'] == '0',
                          'original audio-follow active with NRestarts=0: ' + str(state))
            steps.extend(session.steps)
            ctl('stop', unit)
            session.kwin.kill(); session.kwin.wait(timeout=3)
        # Keep the actual Restart=on-failure. Inspect the first failed start within RestartSec=5,
        # then stop before policy recovery; a later active state cannot turn this into success.
        log.unlink(missing_ok=True)
        path.write_text(isolated.replace('org.kde.KWin', 'com.rungic.AbsentReadiness 1'))
        ctl('daemon-reload'); ctl('reset-failed', unit, check=False)
        failed = ctl('start', unit, check=False)
        state = properties()
        assert failed.returncode != 0 and state['Result'] == 'exit-code', state
        assert state['NRestarts'] == '0' and state['ActiveState'] != 'active', state
        assert state['ExecMainPID'] == '0' and (not log.exists() or 'following:' not in log.read_text()), state
        assert 'com.rungic.AbsentReadiness not on the session bus after 1 s' in log.read_text()
        steps.append('missing name: first unit start fails; main never ran; NRestarts=0: ' + str(state))
        return steps
    finally:
        ctl('stop', unit, check=False)
        path.unlink(missing_ok=True); ctl('daemon-reload'); ctl('reset-failed', unit, check=False)
        if log.exists():
            Path('/tmp/audio-follow-unit-last.log').write_text(log.read_text())
        runtime.cleanup()


def test():
    if os.environ.get('RUNGIC_TEST_SYSTEMD') == '1':
        return test_unit()
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
