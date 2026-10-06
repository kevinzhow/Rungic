# SPDX-License-Identifier: MIT
"""Real private workspace KWin and upstream KDE portal, three fresh workspace starts (no phone)."""
import os
from pathlib import Path
import subprocess
from gi.repository import Gio, GLib
import contracts
import harness

SLOT = '8'
BACKEND = 'org.freedesktop.impl.portal.desktop.kde'


def test():
    # covers[system]: agent.workspaces/E9
    # covers[consumer]: iface:platform-bridge
    with contracts.StandIn('platform-bridge') as bridge, harness.Session(720, 1280) as session:
        Gio.bus_get_sync(Gio.BusType.SESSION).call_sync(
            'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'UpdateActivationEnvironment',
            GLib.Variant('(a{ss})', ({key: os.environ[key] for key in ('XDG_RUNTIME_DIR', 'WAYLAND_DISPLAY')},)),
            None, 0, 5000)
        for cycle in range(1, 4):
            log_path = Path(f'/tmp/workspace-portal-{cycle}.log')
            with log_path.open('w') as log:
                proc = subprocess.Popen(['rungic-workspace', SLOT], stdout=log, stderr=subprocess.STDOUT,
                    env={**os.environ, 'RUNGIC_PLATFORM_SOCKET': bridge.path, 'KWIN_WAYLAND_NO_PERMISSION_CHECKS': '1'})
                try:
                    bus_file = Path.home() / f'.local/state/rungic-workspaces/{SLOT}/bus'
                    session.wait_for(lambda: bus_file.exists() and (session.runtime / f'wayland-ws-{SLOT}').exists()
                                     or proc.poll() is not None, 40, 'workspace KWin started')
                    session.check(proc.poll() is None, f'cycle {cycle}: workspace is alive')
                    address = bus_file.read_text().strip()
                    def call(method, *arguments):
                        result = subprocess.run(['busctl', '--address=' + address, 'call', 'org.freedesktop.DBus',
                            '/org/freedesktop/DBus', 'org.freedesktop.DBus', method, *arguments],
                            capture_output=True, text=True, timeout=25)
                        if result.returncode:
                            raise harness.Failed(result.stderr[-500:] + '\n' + log_path.read_text()[-1800:])
                        return result.stdout.strip()
                    session.check(call('StartServiceByName', 'su', BACKEND, '0') in ('u 1', 'u 2'),
                                  f'cycle {cycle}: upstream portal owns the private bus name')
                    pid = int(call('GetConnectionUnixProcessID', 's', BACKEND).split()[1])
                    executable = os.readlink(f'/proc/{pid}/exe')
                    session.check(executable.endswith('/xdg-desktop-portal-kde'),
                                  f'cycle {cycle}: activation replaced gate with upstream binary')
                    text = log_path.read_text()
                    session.check('Failed to create wl_display' not in text and 'Wayland did not respond' not in text,
                                  f'cycle {cycle}: no early Wayland failure signature')
                finally:
                    if proc.poll() is None: proc.terminate()
                    proc.wait(timeout=30)
        return session.steps


if __name__ == '__main__':
    harness.run('workspace_portal', test)
