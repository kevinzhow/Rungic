# SPDX-License-Identifier: MIT
"""The Linux resolver follows Android's default network (shared/platform/network-manager.py, issue #7):
the service writes /etc/resolv.conf from the DNS servers of the default network in each snapshot of
the platform bridge, here a stand-in (tools/contracts.py, quality/contracts/network.json), and leaves
a file someone wrote by hand alone."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import contracts  # noqa: E402

pytest.importorskip('gi.repository.GLib')
from test_contract_network import SNAPSHOT, WIFI, Bus, service  # noqa: E402

VPN = {**WIFI, 'handle': 9, 'interface': 'tun0', 'kind': 'vpn', 'default': True,
       'addresses': ['172.19.0.1/30'], 'dns': ['172.19.0.2'],
       'routes': [{'destination': '0.0.0.0/0', 'default': True}]}


def snapshot(*networks):
    return {**SNAPSHOT, 'networks': list(networks)}


def module_with(monkeypatch, tmp_path):
    with contracts.StandIn(['network', 'telephony'], {'network-get': SNAPSHOT}) as android:
        module = service(monkeypatch, android.path, 'shared/platform/network-manager.py', 'android_network')
    path = tmp_path / 'resolv.conf'
    monkeypatch.setattr(module, 'RESOLV_CONF', str(path))
    return module, path


# covers: desktop.network/E6
def test_the_default_networks_servers_are_written(monkeypatch, tmp_path):
    module, path = module_with(monkeypatch, tmp_path)
    path.write_text(module.RESOLV_PLACEHOLDER + '\n')               # the image as shipped
    assert module.sync_resolv_conf(snapshot(WIFI)) == 'written'
    assert path.read_text() == f'{module.RESOLV_MARKER}\nnameserver 192.168.5.1\n'
    assert oct(path.stat().st_mode & 0o777) == '0o644'
    assert module.sync_resolv_conf(snapshot(WIFI)) == 'unchanged'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['resolv.conf']   # no temporary file left


# covers: desktop.network/E6
def test_a_leftover_link_beside_the_file_is_never_written_through(monkeypatch, tmp_path):
    module, path = module_with(monkeypatch, tmp_path)
    victim = tmp_path / 'victim'
    victim.write_text('keep\n')
    (tmp_path / '.resolv.conf.rungic').symlink_to(victim)          # the old fixed temporary name
    assert module.sync_resolv_conf(snapshot(WIFI)) == 'written'
    assert victim.read_text() == 'keep\n' and 'nameserver 192.168.5.1' in path.read_text()


# covers: desktop.network/E6
def test_a_vpn_takes_over_and_its_server_goes_with_it(monkeypatch, tmp_path):
    module, path = module_with(monkeypatch, tmp_path)
    wifi_behind = {**WIFI, 'default': False}
    module.sync_resolv_conf(snapshot(wifi_behind, VPN))
    assert 'nameserver 172.19.0.2\n' in path.read_text() and '192.168.5.1' not in path.read_text()
    module.sync_resolv_conf(snapshot(WIFI))                          # the VPN is off again
    assert '172.19.0.2' not in path.read_text() and 'nameserver 192.168.5.1' in path.read_text()


# covers: desktop.network/E6
def test_offline_keeps_no_stale_server(monkeypatch, tmp_path):
    module, path = module_with(monkeypatch, tmp_path)
    module.sync_resolv_conf(snapshot(VPN))
    module.sync_resolv_conf(snapshot({**WIFI, 'default': False}))   # no default network
    text = path.read_text()
    assert text.startswith(module.RESOLV_MARKER) and 'nameserver' not in text


# covers: desktop.network/E6
def test_servers_are_checked_limited_and_link_local_keeps_its_interface(monkeypatch, tmp_path):
    module, path = module_with(monkeypatch, tmp_path)
    row = {**WIFI, 'dns': ['fe80::1%wlan0', 'not-an-address', '2001:db8::53', '192.168.5.1',
                           '192.168.5.1', '9.9.9.9', '1.1.1.1', '8.8.8.8%bad;scope']}
    module.sync_resolv_conf(snapshot(row))
    assert path.read_text().splitlines()[1:] == [
        'nameserver fe80::1%wlan0', 'nameserver 2001:db8::53', 'nameserver 192.168.5.1']


# covers: desktop.network/E7
def test_a_file_edited_by_hand_is_left_alone(monkeypatch, tmp_path):
    module, path = module_with(monkeypatch, tmp_path)
    path.write_text('nameserver 223.5.5.5\nnameserver 1.1.1.1\n')    # the G100 S, 2026-09-23
    assert module.sync_resolv_conf(snapshot(VPN)) == 'kept'
    assert path.read_text() == 'nameserver 223.5.5.5\nnameserver 1.1.1.1\n'
    link = tmp_path / 'link.conf'
    link.symlink_to(path)                                            # e.g. systemd-resolved's stub
    assert module.sync_resolv_conf(snapshot(VPN), str(link)) == 'kept'


# covers: desktop.network/E6
def test_the_service_writes_on_each_snapshot_and_not_without_one(monkeypatch, tmp_path):
    module, path = module_with(monkeypatch, tmp_path)
    bridge = module.Bridge(Bus())                                    # publish(None): Android not reachable
    assert not path.exists()
    bridge.publish(snapshot(VPN))
    assert 'nameserver 172.19.0.2' in path.read_text()
    bridge.publish(None)                                             # unreachable again: keep what is there
    assert 'nameserver 172.19.0.2' in path.read_text()
