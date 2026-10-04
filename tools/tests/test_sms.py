# SPDX-License-Identifier: MIT
"""Real SMS consumer on a contract socket, and production Java state without Android."""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import contracts


def load(socket_path):
    spec = importlib.util.spec_from_file_location('rungic_sms_test', ROOT / 'shared/platform/sms.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SOCKET = socket_path
    return module


# covers[consumer]: iface:telephony
# covers: desktop.sms/E1
def test_send_uses_the_requested_text_and_sim_without_changes(capsys):
    with contracts.StandIn(['network', 'telephony']) as bridge:
        sms = load(bridge.path)
        assert sms.main(['send', '10000', '查询话费', '--subscription', '1']) == 0
    reply = json.loads(capsys.readouterr().out)
    assert reply['status'] == 'sent' and reply['delivered'] is False
    assert bridge.requests == [{'op': 'sms', 'action': 'send', 'to': '10000', 'text': '查询话费', 'subscription': 1}]
    assert bridge.problems == []


# covers[consumer]: iface:telephony
# covers: desktop.sms/E2
@pytest.mark.parametrize('status', ['failed', 'pending'])
def test_partial_failure_or_unknown_outcome_never_retries(status, capsys):
    reply = {'status': status, 'parts': 2, 'sentParts': 1, 'submittedAt': 1234, 'subscription': 1, 'error': 'radio error'}
    with contracts.StandIn('telephony', {'sms-send': reply}) as bridge:
        assert load(bridge.path).main(['send', '10000', 'test']) == 1
    assert json.loads(capsys.readouterr().out) == reply
    assert len(bridge.requests) == 1
    assert bridge.problems == []


# covers[consumer]: iface:telephony
# covers: desktop.sms/E3
def test_early_reply_is_not_lost_when_wait_starts_later(capsys):
    reply = {'messages': [{'id': 1, 'address': '10000', 'text': 'response', 'date': 1235, 'box': 'inbox', 'read': False}], 'truncated': False}
    with contracts.StandIn('telephony', {'sms-list': reply}) as bridge:
        assert load(bridge.path).main(['list', '--from', '10000', '--after', '1234', '--wait', '60']) == 0
    assert json.loads(capsys.readouterr().out) == reply
    assert bridge.requests == [{'op': 'sms', 'action': 'list', 'box': 'inbox', 'limit': 20, 'from': '10000', 'since': 1234}]
    assert bridge.problems == []


# covers[consumer]: iface:telephony
# covers: desktop.sms/E3
def test_empty_wait_is_a_timeout_not_a_success(capsys):
    with contracts.StandIn('telephony', {'sms-list': {'messages': [], 'truncated': False}}) as bridge:
        sms = load(bridge.path)
        assert sms.main(['list', '--from', '10000', '--wait', '0']) == 1
        assert sms.wait_for('10000', 'inbox', 1234, 20, 0)['timedOut'] is True
        sms.wait_for = lambda *a: {'messages': [], 'truncated': False, 'timedOut': True}
        assert sms.main(['list', '--from', '10000', '--wait', '1']) == 1
    assert bridge.problems == []


# covers[consumer]: iface:telephony
# covers: desktop.sms/E2
def test_permission_denial_is_explicit(capsys):
    with contracts.StandIn('telephony', {'sms-send': {'error': 'Android did not grant SEND_SMS'}}) as bridge:
        assert load(bridge.path).main(['send', '10000', 'test']) == 2
    assert 'SEND_SMS' in json.loads(capsys.readouterr().out)['error']
    assert len(bridge.requests) == 1


# covers: desktop.sms/E1 desktop.sms/E3
@pytest.mark.parametrize('args', [
    ['send', '10', 'text'], ['send', '10000;id', 'text'], ['send', '10000', 'x' * 1001],
    ['list', '--wait', '-1'], ['list', '--wait', 'inf'], ['list', '--since', 'nan'],
    ['list', '--after', '-1'], ['list', '--from', 'x'],
])
def test_invalid_arguments_do_not_reach_android(args, capsys):
    with contracts.StandIn('telephony') as bridge:
        assert load(bridge.path).main(args) == 2
    assert bridge.requests == []
    assert 'error' in json.loads(capsys.readouterr().out)


# covers: desktop.sms/E1 desktop.sms/E2 desktop.sms/E3
def test_real_java_validation_and_multipart_state(tmp_path):
    javac, java = shutil.which('javac'), shutil.which('java')
    if not (javac and java):
        pytest.skip('JDK required')
    subprocess.run([javac, '-encoding', 'UTF-8', '-d', str(tmp_path),
                    str(ROOT / 'android/app/src/com/rungic/plasma/SmsState.java'),
                    str(ROOT / 'tools/tests/SmsStateDriver.java')], check=True)
    result = subprocess.run([java, '-cp', str(tmp_path), 'com.rungic.plasma.SmsStateDriver'],
                            text=True, capture_output=True, check=True)
    assert 'passed' in result.stdout
