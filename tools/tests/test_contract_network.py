# SPDX-License-Identifier: MIT
"""The network contract (and the telephony state it reads) from the Linux side: the real NetworkManager
service shared/platform/network-manager.py against tools/contracts.py's stand-in of the platform bridge
(quality/contracts/network.json, telephony.json), on a D-Bus connection that records what it would
publish. The provider's side is the acceptance scenario contract.network on the phone."""
import importlib.machinery
import importlib.util
import queue
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import contracts  # noqa: E402

GLib = pytest.importorskip('gi.repository.GLib')
NM = 'org.freedesktop.NetworkManager'
BASE = '/org/freedesktop/NetworkManager'


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class MainLoop:
    """The service's GLib main loop, as a queue this test runs: GLib's default context is shared with
    the Qt of other tests in this process, and iterating it there runs their sources."""

    def __init__(self):
        self.calls, self.timers = queue.Queue(), []

    def idle_add(self, fn, *args):
        self.calls.put((fn, args))
        return 1

    def timeout_add_seconds(self, seconds, fn, *args):
        self.timers.append((seconds, fn))
        return 1

    def __getattr__(self, name):
        return getattr(GLib, name)

    def settle(self, condition, timeout=5):
        """Run what the service's worker threads hand to the main loop until `condition()`."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                fn, args = self.calls.get(timeout=0.01)
                if fn(*args):
                    self.calls.put((fn, args))
            except queue.Empty:
                pass
            if condition():
                return True
        return condition()


def service(monkeypatch, socket_path, script, name):
    """A platform service script as a module, its platform bridge the stand-in, its main loop ours."""
    monkeypatch.setitem(sys.modules, 'rungic_platform_transport',
                        load('rungic_platform_transport', ROOT / 'shared/platform/rungic_platform_transport.py'))
    monkeypatch.setitem(sys.modules, 'rungic_host_watch', load('rungic_host_watch', ROOT / 'shared/platform/host_watch.py'))
    module = load(name, ROOT / script)
    monkeypatch.setattr(module, 'SOCKET', socket_path)
    if hasattr(module, 'RESOLV_CONF'):                 # never the test machine's own resolver
        monkeypatch.setattr(module, 'RESOLV_CONF', str(Path(socket_path).with_name('resolv.conf')))
    monkeypatch.setattr(module, 'GLib', MainLoop())
    return module


class Bus:
    """The D-Bus connection: objects registered and signals emitted are recorded, not sent."""

    def __init__(self):
        self.objects, self.signals, self.next = {}, [], 1

    def register_object(self, path, info, call, get, set_):
        self.next += 1
        self.objects[self.next] = (path, info.name)
        return self.next

    def unregister_object(self, number):
        self.objects.pop(number)

    def emit_signal(self, destination, path, interface, name, values):
        self.signals.append((path, interface, name, values.unpack()))


class Invocation:
    def __init__(self):
        self.value = self.error = None

    def return_value(self, value):
        self.value = value.unpack() if value is not None else ()

    def return_dbus_error(self, name, message):
        self.error = (name, message)


def prop(bridge, path, interface, name):
    return bridge.graph[path][interface][name].unpack()


def example(contract, name):
    return next(q for q in contracts.load(contract)['queries'] if q['name'] == name)


WIFI = {**example('network', 'network-get')['reply']['networks'][0],
        'ssid': 'Home Wifi', 'bssid': '94:83:c4:12:34:56', 'mac': '02:00:00:aa:bb:cc', 'rssi': -55,
        'frequency': 5180, 'linkMbps': 866, 'security': 2}
CELLULAR = {**example('network', 'network-get')['reply']['networks'][0], 'handle': 7, 'interface': 'rmnet_data1',
            'kind': 'cellular', 'default': False, 'metered': True, 'addresses': ['10.20.30.40/32'], 'dns': [],
            'routes': [{'destination': '0.0.0.0/0', 'default': True, 'gateway': '10.20.30.1'}]}
SNAPSHOT = {**example('network', 'network-get')['reply'], 'networks': [WIFI, CELLULAR]}
STATE = example('telephony', 'state')['replies'][0]


def started(monkeypatch, bridge_socket):
    module = service(monkeypatch, bridge_socket.path, 'shared/platform/network-manager.py', 'android_network')
    bridge = module.Bridge(Bus())
    bridge.poll()
    assert module.GLib.settle(lambda: not bridge.polling and bridge.online is not None)
    return module, bridge


# covers[consumer]: iface:network iface:telephony
def test_android_networks_become_networkmanager_objects(monkeypatch):
    with contracts.StandIn(['network', 'telephony'], {'network-get': SNAPSHOT, 'state': STATE}) as android:
        module, bridge = started(monkeypatch, android)
    assert bridge.online is True
    # The Wi-Fi device, connected through Android's saved network of the same name.
    wifi = BASE + '/Devices/wifi'
    assert prop(bridge, wifi, NM + '.Device', 'Interface') == 'wlan0'
    assert prop(bridge, wifi, NM + '.Device', 'State') == 100
    active = prop(bridge, wifi, NM + '.Device', 'ActiveConnection')
    assert prop(bridge, active, NM + '.Connection.Active', 'Connection') == BASE + '/Settings/saved0'
    assert bridge.settings[BASE + '/Settings/saved0']['connection']['id'].unpack() == 'Home Wifi'
    # Addresses, routes and DNS from the row; the default route's gateway.
    ip4 = prop(bridge, wifi, NM + '.Device', 'Ip4Config')
    assert prop(bridge, ip4, NM + '.IP4Config', 'AddressData') == [{'address': '192.168.5.20', 'prefix': 24}]
    assert prop(bridge, ip4, NM + '.IP4Config', 'Gateway') == '192.168.5.1'
    assert prop(bridge, ip4, NM + '.IP4Config', 'NameserverData') == [{'address': '192.168.5.1'}]
    # Both scan results are access points (parsed from `cmd wifi list-scan-results`), with their security.
    aps = prop(bridge, wifi, NM + '.Device.Wireless', 'AccessPoints')
    ssids = {bytes(prop(bridge, ap, NM + '.AccessPoint', 'Ssid')).decode(): ap for ap in aps}
    assert set(ssids) == {'Home Wifi', 'Cafe Guest'}
    assert prop(bridge, ssids['Home Wifi'], NM + '.AccessPoint', 'RsnFlags') & 0x100     # PSK
    assert prop(bridge, ssids['Cafe Guest'], NM + '.AccessPoint', 'Flags') == 0          # open
    # Android's mobile data as a modem with the operator, its data connection the gsm profile.
    modem = BASE + '/Devices/modem'
    assert prop(bridge, modem, NM + '.Device.Modem', 'OperatorCode') == '46001'
    assert prop(bridge, modem, NM + '.Device', 'IpInterface') == 'rmnet_data1'
    assert bridge.settings[module.MOBILE]['connection']['id'].unpack() == 'China Unicom'
    # Online through validated Wi-Fi.
    assert prop(bridge, BASE, NM, 'State') == 70 and prop(bridge, BASE, NM, 'Connectivity') == 4
    assert prop(bridge, BASE, NM, 'PrimaryConnectionType') == '802-11-wireless'
    assert [r for r in android.requests] == [{'op': 'network-get'}, {'op': 'wifi', 'action': 'saved'},
                                             {'op': 'wifi', 'action': 'scan-results'},
                                             {'op': 'telephony', 'action': 'state'}]
    assert android.problems == []


# covers[consumer]: iface:network
def test_an_unknown_protocol_or_no_android_is_no_network_backend(monkeypatch):
    newer = {**SNAPSHOT, 'version': 2}
    with contracts.StandIn(['network', 'telephony'], {'network-get': newer}) as android:
        _, bridge = started(monkeypatch, android)
    assert bridge.online is False
    assert prop(bridge, BASE, NM, 'NetworkingEnabled') is False
    assert prop(bridge, BASE, 'com.rungic.Android.Network', 'BackendAvailable') is False
    # Nothing else is asked of an app that speaks another protocol.
    assert android.requests == [{'op': 'network-get'}]


# covers[consumer]: iface:network iface:telephony
def test_changes_from_the_desktop_are_android_requests(monkeypatch):
    with contracts.StandIn(['network', 'telephony'], {'network-get': SNAPSHOT, 'state': STATE}) as android:
        module, bridge = started(monkeypatch, android)
        before = len(android.requests)
        V = GLib.Variant
        # The Wi-Fi switch.
        assert bridge.set(None, None, BASE, NM, 'WirelessEnabled', V('b', False)) is True
        # Joining a WPA2 network from the settings: Android saves and joins it.
        join = Invocation()
        settings = {'802-11-wireless': {'ssid': V('ay', list(b'Cafe Guest'))},
                    '802-11-wireless-security': {'key-mgmt': V('s', 'wpa-psk'), 'psk': V('s', 'correct horse')}}
        bridge.call(None, ':1.5', BASE, NM, 'AddAndActivateConnection',
                    V('(a{sa{sv}}oo)', (settings, BASE + '/Devices/wifi', '/')), join)
        assert module.GLib.settle(lambda: join.value is not None or join.error is not None)
        assert join.error is None
        # A scan, leaving Wi-Fi, forgetting the saved network, mobile data off.
        scan, leave, forget, data = Invocation(), Invocation(), Invocation(), Invocation()
        bridge.call(None, ':1.5', BASE + '/Devices/wifi', NM + '.Device.Wireless', 'RequestScan', V('(a{sv})', ({},)), scan)
        bridge.call(None, ':1.5', BASE, NM, 'DeactivateConnection', V('(o)', (BASE + '/ActiveConnection/wifi',)), leave)
        bridge.call(None, ':1.5', BASE + '/Settings/saved0', NM + '.Settings.Connection', 'Delete', V('()', ()), forget)
        bridge.call(None, ':1.5', BASE, NM, 'DeactivateConnection', V('(o)', (BASE + '/ActiveConnection/modem',)), data)
        assert module.GLib.settle(lambda: all(i.value is not None or i.error for i in (scan, leave, forget, data)))
        asked = android.requests[before:]
    changes = [r for r in asked if r not in ({'op': 'network-get'}, {'op': 'wifi', 'action': 'saved'},
                                             {'op': 'wifi', 'action': 'scan-results'}, {'op': 'telephony', 'action': 'state'})]
    assert {'op': 'network-wifi', 'enabled': False} in changes
    assert {'op': 'wifi', 'action': 'connect', 'ssid': 'Cafe Guest', 'security': 'wpa2', 'passphrase': 'correct horse'} in changes
    assert {'op': 'wifi', 'action': 'scan'} in changes
    assert {'op': 'wifi', 'action': 'disconnect'} in changes
    assert {'op': 'wifi', 'action': 'forget', 'id': 0} in changes
    assert {'op': 'telephony', 'action': 'data', 'on': False} in changes
    # Every request is one of the contracts' and has the fields they name.
    assert android.problems == []
    assert all(contracts.query(android.contract, r) for r in asked), asked
