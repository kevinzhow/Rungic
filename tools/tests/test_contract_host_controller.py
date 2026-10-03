# SPDX-License-Identifier: MIT
"""The host controller's contract from the consumer's side (quality/contracts/host-controller.json):
the app's FirstBootState (plain Java, compiled here) reads the install state files as the contract
gives them, and opens the account gate only when it should. The provider's side is the acceptance
scenario contract.host-controller, which reads the same files and actions on the phone."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import contracts  # noqa: E402

STATE = next(q for q in contracts.load('host-controller')['queries'] if q['name'] == 'install-state')


@pytest.fixture(scope='module')
def app(tmp_path_factory):
    javac, java = shutil.which('javac'), shutil.which('java')
    if not (javac and java):
        pytest.skip('no JDK')
    out = tmp_path_factory.mktemp('firstboot')
    subprocess.run([javac, '-encoding', 'UTF-8', '-d', str(out),
                    str(ROOT / 'android/app/src/com/rungic/plasma/FirstBootState.java'),
                    str(ROOT / 'tools/tests/contract_firstboot_driver.java')], check=True)
    return [java, '-cp', str(out), 'com.rungic.plasma.ContractFirstBootDriver']


def read(app, tmp_path, state):
    """What the app makes of the install state as the provider check reads it (install-state)."""
    source, seed, status = tmp_path / 'rungic-install-source.properties', tmp_path / 'seed.env', tmp_path / 'status'
    for path in (source, seed, status):
        path.unlink(missing_ok=True)
    if 'RELEASE_ID' in state:
        source.write_text(f'RELEASE_ID={state["RELEASE_ID"]}\n')
        status.write_text(''.join(f'{k}={v}\n' for k, v in state.items() if k != 'RELEASE_ID'))
    done = subprocess.run([*app, str(source), str(seed), str(status)], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


# covers[consumer]: iface:host-controller
def test_the_app_opens_only_a_finished_install_of_its_release(app, tmp_path):
    ready, unmanaged = STATE['replies'][:2]
    installing, waiting, failed, other = STATE['examples']
    assert all(contracts.check_reply(STATE, example) == [] for example in STATE['replies'] + STATE['examples'])
    assert read(app, tmp_path, ready)['ready'] is True
    assert read(app, tmp_path, unmanaged)['ready'] is True, 'no managed install: no gate'
    state = read(app, tmp_path, installing)
    assert (state['ready'], state['failed'], state['message'], state['phase']) == (False, False, 'ROOTFS', 'rootfs')
    state = read(app, tmp_path, waiting)
    assert (state['ready'], state['attention'], state['message']) == (False, True, 'STORAGE_WAIT')
    state = read(app, tmp_path, failed)
    assert (state['ready'], state['failed'], state['message'], state['code']) == (False, True, 'FAILED_CHECKSUM', 'checksum')
    state = read(app, tmp_path, other)
    assert (state['ready'], state['reason']) == (False, 'RELEASE_MISMATCH'), 'another release is not this one finished'


# covers[consumer]: iface:host-controller
def test_a_newer_install_state_cannot_open_the_gate(app, tmp_path):
    ready = STATE['replies'][0]
    assert read(app, tmp_path, {**ready, 'schema': '3'})['reason'] == 'SCHEMA_UNSUPPORTED'
    assert read(app, tmp_path, {**ready, 'phase': 'somewhere'})['ready'] is True, 'ready needs no known phase'
    assert read(app, tmp_path, {**ready, 'state': 'installing', 'phase': 'somewhere'})['reason'] == 'PHASE_UNKNOWN'


# covers[consumer]: iface:host-controller
def test_legacy_ready_state_agrees_with_the_provider_check_and_keeps_release_matching(app, tmp_path):
    # A pre-schema G100 installation omits schema/error and quotes its seed release.
    # FirstBootState explicitly defaults this format to v1; the provider check must
    # accept it too, while the app still rejects a mismatched or future release state.
    legacy = {'RELEASE_ID': "'portov-20260928.5'", 'release': 'portov-20260928.5',
              'state': 'ready', 'phase': 'complete'}
    assert contracts.check_reply(STATE, legacy) == []
    assert read(app, tmp_path, legacy)['ready'] is True
    assert read(app, tmp_path, {**legacy, 'release': 'other'})['reason'] == 'RELEASE_MISMATCH'
    assert read(app, tmp_path, {**legacy, 'schema': '3'})['reason'] == 'SCHEMA_UNSUPPORTED'


def test_the_controller_has_the_actions_the_contract_runs():
    """Every action of the contract is one the controller (system/rungic-plasma) knows."""
    controller = (ROOT / 'system/rungic-plasma').read_text()
    for q in contracts.load('host-controller')['queries']:
        if q['command'].startswith('/data/adb/rungic-plasma/rungic-plasma '):
            action = q['command'].split()[1]
            assert f'\n  {action})' in controller or f'|{action})' in controller or f'\n  {action}|' in controller, action
