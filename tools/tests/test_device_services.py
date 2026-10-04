# SPDX-License-Identifier: MIT
"""A device backend remains reachable with the UI absent; uncertain submissions never replay."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'shared/platform'))
import rungic_platform_transport as transport


class Backend:
    def __init__(self, path, reply):
        self.path, self.reply, self.requests = str(path), reply, []
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(self.path)
        self.sock.listen(4)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        conn, _ = self.sock.accept()
        with conn:
            raw = conn.makefile('rb').readline()
            if raw:
                self.requests.append(json.loads(raw))
                if self.reply is not None:
                    conn.sendall(self.reply)

    def close(self):
        self.sock.close()
        self.thread.join(timeout=2)


# covers: desktop.host-bridges/E4 desktop.network/E8 desktop.sms/E4
# covers[consumer]: iface:network iface:telephony iface:device-backend
@pytest.mark.parametrize('op', ['network-get', 'wifi', 'network-wifi', 'telephony', 'sms', 'bluetooth', 'container-memory'])
def test_device_request_succeeds_without_any_ui_socket(tmp_path, monkeypatch, op):
    monkeypatch.delenv('RUNGIC_PLATFORM_SOCKET', raising=False)
    backend = Backend(tmp_path / 'device', b'{"ok":true}\n')
    monkeypatch.setenv('RUNGIC_DEVICE_SOCKET', backend.path)
    try:
        assert transport.request({'op': op}) == {'ok': True}
        assert backend.requests == [{'op': op}]
    finally:
        backend.close()


# covers: desktop.host-bridges/E4 desktop.sms/E2 desktop.sms/E4
# covers[consumer]: iface:telephony
@pytest.mark.parametrize('reply,exception', [(None, ConnectionError), (b'{"status":"sent"}', ConnectionError),
                                            (b'[]\n', ValueError), (b'x' * 129 + b'\n', ValueError)])
def test_ambiguous_submission_never_falls_back_to_a_live_ui(tmp_path, monkeypatch, reply, exception):
    backend = Backend(tmp_path / 'device', reply)
    ui = socket.socket(socket.AF_UNIX)
    ui.bind(str(tmp_path / 'ui')); ui.listen(4); ui.settimeout(0.05)
    monkeypatch.setenv('RUNGIC_DEVICE_SOCKET', backend.path)
    try:
        with pytest.raises(exception):
            transport.request({'op': 'sms', 'action': 'send', 'to': '10000', 'text': 'test'},
                              ui_socket=str(tmp_path / 'ui'), limit=128)
        assert len(backend.requests) == 1
        with pytest.raises(TimeoutError):
            ui.accept()
    finally:
        backend.close(); ui.close()


# covers: desktop.host-bridges/E4
# covers[consumer]: iface:platform-bridge
def test_presentation_and_mixed_watches_stay_on_the_ui_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv('RUNGIC_DEVICE_SOCKET', 'device')
    assert transport.endpoint({'op': 'display-get'}, 'ui') == 'ui'
    assert transport.endpoint({'op': 'watch', 'topics': ['network', 'capture']}, 'ui') == 'ui'
    assert transport.endpoint({'op': 'watch', 'topics': ['network', 'telephony']}, 'ui') == 'device'
    monkeypatch.delenv('RUNGIC_DEVICE_SOCKET')
    assert transport.endpoint({'op': 'sms'}, 'explicit-contract-endpoint') == 'explicit-contract-endpoint'


# covers: desktop.host-bridges/E4 desktop.sms/E4
def test_unexpected_default_backend_identity_is_rejected_before_sending(monkeypatch):
    path = '\0rungic-test-' + uuid.uuid4().hex
    monkeypatch.setattr(transport, 'DEVICE_DEFAULT', path)
    monkeypatch.delenv('RUNGIC_DEVICE_SOCKET', raising=False)
    monkeypatch.delenv('RUNGIC_PLATFORM_SOCKET', raising=False)
    backend = Backend(path, None)
    try:
        if os.getuid() == 0:
            pytest.skip('This check needs an unprivileged provider')
        with pytest.raises(PermissionError):
            transport.request({'op': 'sms'})
        assert backend.requests == []
    finally:
        backend.close()


# covers: agent.voice/E9
def test_execution_is_owned_by_the_user_manager_and_preserves_the_login_environment(tmp_path):
    spec = importlib.util.spec_from_file_location('workspace_unit', ROOT / 'tools/tests/test_workspace_unit.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    sections = module.unit(ROOT / 'agent/assistant/rungic-voice-agent.service')
    for entries in sections.values():
        for key, value in entries:
            if key in module.STOPPED_WITH:
                assert not any(name in value for name in module.SESSION)
    assert dict(sections['Install'])['WantedBy'] == 'default.target'
    # No screen, Android mount or preexisting session environment is needed to invoke execution.
    script = ROOT / 'agent/assistant/rungic-agent-start'
    result = subprocess.run(['sh', str(script), sys.executable, '-c', 'import os; print(os.getuid())'],
                            env={'PATH': os.environ['PATH'], 'HOME': str(tmp_path)},
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == str(os.getuid())
    keepalive = module.unit(ROOT / 'agent/assistant/rungic-agent-user.service')
    assert dict(keepalive['Service'])['ExecCondition'] == '/usr/bin/getent passwd 1000'
    assert 'enable-linger' in dict(keepalive['Service'])['ExecStart']


def supervisor(tmp_path, group='0::/\n', rc=1):
    """Run the production watcher with every Android filesystem/command redirected to a sandbox."""
    base, state, proc = tmp_path / 'base', tmp_path / 'state', tmp_path / 'proc'
    for path in (base, state, proc / 'self', tmp_path / 'bin'):
        path.mkdir(parents=True)
    (base / 'device.enabled').touch()
    (proc / 'self/cgroup').write_text(group)
    text = (ROOT / 'system/android-device').read_text()
    text = text.replace('/data/adb/rungic-plasma', str(base)).replace(
        '/data/adb/rungic-lxc/runtime/var/lib/lxc/plasma/state/host/device', str(state))
    text = text.replace('/proc/', str(proc) + '/')
    for mount in ('/sys/fs/cgroup', '/dev/memcg', '/dev/cpuctl', '/dev/stune'):
        path = tmp_path / mount.lstrip('/'); path.mkdir(parents=True)
        (path / 'cgroup.procs').touch()
        text = text.replace(mount, str(path))
    script = tmp_path / 'watcher'; script.write_text(text)
    calls = tmp_path / 'calls'
    commands = {'id': 'echo 0', 'stat': 'echo 10000', 'pm': 'echo package:/tmp/candidate.apk',
                'app_process': f'echo launch >> "{calls}"; echo PRIVATE-CONTENT; exit {rc}',
                'sleep': f'echo delay-$1 >> "{calls}"'}
    for name, body in commands.items():
        path = tmp_path / 'bin' / name; path.write_text('#!/bin/sh\n' + body + '\n'); path.chmod(0o755)
    result = subprocess.run(['sh', str(script), 'watch'], env={**os.environ, 'PATH': str(tmp_path / 'bin') + ':' + os.environ['PATH']},
                            capture_output=True, text=True, timeout=10)
    return result, calls, state


# covers: desktop.host-bridges/E5
def test_supervisor_has_finite_recovery_without_container_restarts_or_payload_logs(tmp_path):
    result, calls, state = supervisor(tmp_path)
    assert result.returncode == 1
    assert calls.read_text().splitlines() == ['launch', 'delay-3', 'launch', 'delay-12', 'launch', 'delay-27',
                                             'launch', 'delay-30', 'launch']
    log = (state / 'lifecycle.log').read_text()
    assert 'restart-limit' in log and log.count('backend-exit code=1') == 5
    assert 'PRIVATE-CONTENT' not in log + result.stdout + result.stderr
    assert 'lxc-stop' not in (ROOT / 'system/android-device').read_text()


# covers: desktop.host-bridges/E5
def test_supervisor_refuses_to_run_inside_an_app_freezer_group(tmp_path):
    result, calls, state = supervisor(tmp_path, '0::/apps/uid_10123/pid_1234\n')
    assert result.returncode != 0 and not calls.exists()
    assert 'inherited-app-cgroup' in (state / 'lifecycle.log').read_text()
