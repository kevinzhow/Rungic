# SPDX-License-Identifier: MIT
"""The Android bridges of NetworkManager, BlueZ and ModemManager (shared/platform/*.py) and the
watch they follow Android's state with (host_watch.py), from the Linux side: each service runs as
installed (its script, with rungic_host_watch importable) on a private D-Bus bus standing in for
the system bus, against a stand-in of the Rungic app's platform socket that answers as the app
does (AndroidNetworkBridge, AndroidBluetoothBridge, AndroidTelephonyBridge and HostEvents.java:
the watch returns when a topic's version moves or the epoch differs, else after its timeout).
The test reads what NetworkManager, BlueZ and ModemManager clients read over D-Bus. No Android.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest

gi = pytest.importorskip('gi')
from gi.repository import Gio, GLib  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PLATFORM = ROOT / 'shared/platform'
V = GLib.Variant
NM = 'org.freedesktop.NetworkManager'
NM_PATH = '/org/freedesktop/NetworkManager'
MM = 'org.freedesktop.ModemManager1'
PROPS = 'org.freedesktop.DBus.Properties'

if not shutil.which('dbus-daemon'):
    pytest.skip('dbus-daemon is needed for a private bus', allow_module_level=True)


class Host:
    """The Rungic app's platform socket: replies from `handle(request)`, the op "watch" as HostEvents."""

    def __init__(self, handle):
        self.handle = handle
        self.requests = []
        self.lock = threading.Condition()
        self.versions = {}
        self.epoch = str(uuid.uuid4())
        self.watch_supported = True
        self.dir = tempfile.mkdtemp(prefix='rungic-host-')
        self.path = os.path.join(self.dir, 'platform.sock')
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(self.path)
        self.server.listen(16)
        threading.Thread(target=self.serve, daemon=True).start()

    def bump(self, topic):
        with self.lock:
            self.versions[topic] = self.versions.get(topic, 0) + 1
            self.lock.notify_all()

    def restart(self):
        """The app starts again: a new epoch, versions from zero."""
        with self.lock:
            self.epoch = str(uuid.uuid4())
            self.versions = {}
            self.lock.notify_all()

    def watch(self, request):
        if not self.watch_supported:
            return {'error': 'unknown op watch'}
        seen = request.get('seen')
        deadline = time.monotonic() + request['timeout'] / 1000
        with self.lock:
            same = request.get('epoch') == self.epoch
            while True:
                now = {t: self.versions.get(t, 0) for t in request['topics']}
                changed = not same or seen is None or any(seen.get(t, -1) != v for t, v in now.items())
                left = deadline - time.monotonic()
                if changed or left <= 0:
                    return {'epoch': self.epoch, 'versions': now, 'changed': changed}
                self.lock.wait(left)
                same = request.get('epoch') == self.epoch

    def serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.answer, args=(conn,), daemon=True).start()

    def answer(self, conn):
        with conn:
            request = json.loads(conn.makefile('rb').readline())
            with self.lock:
                self.requests.append(request)
            reply = self.watch(request) if request.get('op') == 'watch' else self.handle(request)
            if reply is not None:
                try:
                    conn.sendall((json.dumps(reply) + '\n').encode())
                except OSError:
                    pass

    def ops(self, **match):
        with self.lock:
            return [r for r in self.requests if all(r.get(k) == v for k, v in match.items())]

    def close(self):
        self.server.close()
        shutil.rmtree(self.dir, ignore_errors=True)


@pytest.fixture(scope='module')
def bus():
    test_bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
    test_bus.up()
    yield test_bus.get_bus_address()
    test_bus.down()


class Service:
    """A bridge script running as its unit does, on `address` as its system bus."""

    def __init__(self, script, name, address, host):
        self.lib = tempfile.mkdtemp(prefix='rungic-lib-')
        shutil.copy(PLATFORM / 'host_watch.py', os.path.join(self.lib, 'rungic_host_watch.py'))
        shutil.copy(PLATFORM / 'rungic_platform_transport.py', os.path.join(self.lib, 'rungic_platform_transport.py'))
        env = dict(os.environ, DBUS_SYSTEM_BUS_ADDRESS=address, RUNGIC_PLATFORM_SOCKET=host.path,
                   PYTHONPATH=self.lib, PYTHONDONTWRITEBYTECODE='1')
        self.process = subprocess.Popen([sys.executable, str(PLATFORM / script)], env=env,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.bus = Gio.DBusConnection.new_for_address_sync(
            address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        self.name = name
        wait(lambda: self.bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                                        'NameHasOwner', V('(s)', (name,)), None, 0, 2000, None).unpack()[0]
             or self.process.poll() is not None, 10, f'{name} on the bus')
        assert self.process.poll() is None, self.process.stderr.read().decode()

    def call(self, path, interface, method, args=None, bus=None):
        return (bus or self.bus).call_sync(self.name, path, interface, method, args, None, 0, 10000, None).unpack()

    def error(self, path, interface, method, args=None):
        with pytest.raises(GLib.Error) as raised:
            self.call(path, interface, method, args)
        return Gio.DBusError.get_remote_error(raised.value)

    def prop(self, path, interface, name):
        return self.call(path, PROPS, 'Get', V('(ss)', (interface, name)))[0]

    def objects(self, path):
        return self.call(path, 'org.freedesktop.DBus.ObjectManager', 'GetManagedObjects')[0]

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        shutil.rmtree(self.lib, ignore_errors=True)


def wait(condition, timeout=5, what='condition'):
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f'timed out waiting for {what}')
        time.sleep(0.05)


# ---------------------------------------------------------------------------------- NetworkManager
WIFI_ROW = {'handle': 101, 'interface': 'wlan0', 'kind': 'wifi', 'default': True, 'validated': True, 'captive': False,
            'metered': False, 'mtu': 1500, 'ssid': 'HomeNet', 'bssid': 'aa:bb:cc:dd:ee:01', 'mac': '02:00:00:00:00:01',
            'addresses': ['192.168.5.20/24', 'fe80::1234/64'], 'dns': ['192.168.5.1', '2001:db8::53'],
            'routes': [{'destination': '0.0.0.0/0', 'default': True, 'gateway': '192.168.5.1'},
                       {'destination': '192.168.5.0/24', 'default': False}],
            'rssi': -50, 'frequency': 5180, 'linkMbps': 866, 'security': 2}
SCAN = '''    BSSID              Frequency      RSSI           Age(sec)     SSID                                 Flags
  aa:bb:cc:dd:ee:01     5180    -50(0:-52/1:-51)   0.512    HomeNet                              [RSN-PSK-CCMP][ESS]
  aa:bb:cc:dd:ee:02     2437    -70(0:-71/1:-72)   1.204    Cafe Guest                           [RSN-PSK-CCMP][ESS]
  aa:bb:cc:dd:ee:03     2412    -80(0:-80/1:-81)   2.000    Library                              [ESS]
'''
SAVED = '''Network Id      SSID                         Security type
0            HomeNet                       wpa2-psk^
1            Cafe Guest                    wpa2-psk
'''


class Android:
    """The app's network, Wi-Fi and telephony replies, changed by the test."""

    def __init__(self):
        self.network = {'version': 1, 'wifiEnabled': True, 'wifi5GHz': True, 'wifi6GHz': False, 'networks': [dict(WIFI_ROW)]}
        self.telephony = {'modem': True, 'manufacturer': 'motorola', 'model': 'moto g100', 'sim': 'absent', 'slots': 1,
                          'simOperator': '', 'simOperatorName': '', 'operator': '', 'operatorName': '', 'service': 1,
                          'roaming': False, 'level': 0, 'dataType': 0, 'dataEnabled': False, 'dataConnected': False,
                          'dataRoaming': False}
        self.bluetooth = {'state': 12, 'enabled': True, 'name': 'moto g100', 'address': '11:22:33:44:55:66',
                          'discovering': False, 'devices': [{'address': 'AA:00:00:00:00:01', 'name': 'Headphones', 'class': 0x240418,
                                                             'bond': 12, 'connected': False, 'uuids': []}]}
        self.found = []
        self.saved, self.scan = SAVED, SCAN
        self.wifi_switch = {'accepted': True}
        self.network_error = None
        self.stall = threading.Event()

    def __call__(self, request):
        op, action = request.get('op'), request.get('action')
        if op == 'network-get':
            if self.stall.is_set():
                time.sleep(5)          # longer than the service waits (3.5 s): Android does not answer
                return None
            return self.network_error or json.loads(json.dumps(self.network))
        if op == 'network-wifi':
            return self.wifi_switch
        if op == 'wifi':
            if action == 'saved':
                return {'text': self.saved}
            if action == 'scan-results':
                return {'text': self.scan}
            if action in ('scan', 'activate', 'forget', 'disconnect', 'connect'):
                return {'accepted': True}
        if op == 'telephony':
            if action == 'state':
                return dict(self.telephony)
            if action == 'data':
                return {'accepted': True}
        if op == 'bluetooth':
            if action == 'state':
                state = json.loads(json.dumps(self.bluetooth))
                if self.found:
                    state['found'] = self.found
                return state
            if action == 'discover':
                self.bluetooth['discovering'] = request['on'] and self.bluetooth['enabled']
                return {'accepted': True}
            if action in ('power', 'connect', 'disconnect', 'unpair', 'name', 'pair'):
                return {'accepted': True}
        return {'error': f'unknown request {op} {action}'}


@pytest.fixture
def network(bus):
    android = Android()
    host = Host(android)
    service = Service('network-manager.py', NM, bus, host)
    wait(lambda: service.prop(NM_PATH, NM, 'Connectivity') == 4, 5, 'the first reading')
    yield service, host, android
    service.stop()
    host.close()


def device(service, kind):
    for path in service.call(NM_PATH, NM, 'GetDevices')[0]:
        if service.prop(path, NM + '.Device', 'DeviceType') == kind:
            return path
    raise AssertionError(f'no device of type {kind}')


def ssid(service, ap):
    return bytes(service.prop(ap, NM + '.AccessPoint', 'Ssid')).decode()


# covers: desktop.network/E1
def test_network_clients_see_androids_wifi_addresses_and_connectivity(network):
    service, host, android = network
    assert service.prop(NM_PATH, NM, 'Connectivity') == 4            # full: Android validated the network
    assert service.prop(NM_PATH, NM, 'State') == 70                   # NM_STATE_CONNECTED_GLOBAL
    assert service.call(NM_PATH, NM, 'CheckConnectivity') == (4,)
    primary = service.prop(NM_PATH, NM, 'PrimaryConnection')
    assert service.prop(primary, NM + '.Connection.Active', 'Id') == 'HomeNet'
    assert service.prop(primary, NM + '.Connection.Active', 'Default') is True
    wifi = device(service, 2)
    assert service.prop(wifi, NM + '.Device', 'Interface') == 'wlan0'
    assert service.prop(wifi, NM + '.Device', 'State') == 100         # activated
    assert ssid(service, service.prop(wifi, NM + '.Device.Wireless', 'ActiveAccessPoint')) == 'HomeNet'
    ip4 = service.prop(wifi, NM + '.Device', 'Ip4Config')
    assert [a['address'] for a in service.prop(ip4, NM + '.IP4Config', 'AddressData')] == ['192.168.5.20']
    assert service.prop(ip4, NM + '.IP4Config', 'Gateway') == '192.168.5.1'
    assert [d['address'] for d in service.prop(ip4, NM + '.IP4Config', 'NameserverData')] == ['192.168.5.1']
    ip6 = service.prop(wifi, NM + '.Device', 'Ip6Config')
    assert [a['address'] for a in service.prop(ip6, NM + '.IP6Config', 'AddressData')] == ['fe80::1234']
    # The same through the ObjectManager, as libnm reads it at start.
    objects = service.objects('/org/freedesktop')
    assert objects[NM_PATH][NM]['Connectivity'] == 4


# covers: desktop.network/E2
def test_wifi_settings_list_scan_join_and_forget_through_android(network):
    service, host, android = network
    wifi = device(service, 2)
    aps = {ssid(service, ap): ap for ap in service.call(wifi, NM + '.Device.Wireless', 'GetAllAccessPoints')[0]}
    assert set(aps) == {'HomeNet', 'Cafe Guest', 'Library'}
    # Secured networks say so, which is what makes the Wi-Fi settings ask for a password.
    assert service.prop(aps['Cafe Guest'], NM + '.AccessPoint', 'RsnFlags') & 0x100     # PSK
    assert service.prop(aps['Library'], NM + '.AccessPoint', 'Flags') == 0              # open
    saved = {service.call(p, NM + '.Settings.Connection', 'GetSettings')[0]['connection']['id']: p
             for p in service.call(NM_PATH + '/Settings', NM + '.Settings', 'ListConnections')[0]}
    assert {'HomeNet', 'Cafe Guest'} <= set(saved)
    assert saved['Cafe Guest'] in service.prop(wifi, NM + '.Device', 'AvailableConnections')

    service.call(wifi, NM + '.Device.Wireless', 'RequestScan', V('(a{sv})', ({},)))
    assert host.ops(op='wifi', action='scan')
    assert service.call(NM_PATH, NM, 'ActivateConnection', V('(ooo)', (saved['Cafe Guest'], wifi, '/'))) == \
        (NM_PATH + '/ActiveConnection/wifi',)
    assert host.ops(op='wifi', action='activate', id=1)
    service.call(saved['Cafe Guest'], NM + '.Settings.Connection', 'Delete')
    assert host.ops(op='wifi', action='forget', id=1)
    # Android's list after forgetting: the connection goes away for the settings too.
    android.saved = SAVED.splitlines()[0] + '\n' + SAVED.splitlines()[1] + '\n'
    host.bump('network')
    wait(lambda: saved['Cafe Guest'] not in service.call(NM_PATH + '/Settings', NM + '.Settings', 'ListConnections')[0],
         15, 'the forgotten network gone')

    # A new secured network: no password, no request to Android; with one, Android joins it.
    new = {'connection': {'type': V('s', '802-11-wireless')}, '802-11-wireless': {'ssid': V('ay', b'Cafe Guest')},
           '802-11-wireless-security': {'key-mgmt': V('s', 'wpa-psk')}}
    assert service.error(NM_PATH, NM, 'AddAndActivateConnection', V('(a{sa{sv}}oo)', (new, wifi, aps['Cafe Guest']))) == \
        NM + '.NoSecrets'
    assert not host.ops(op='wifi', action='connect')
    new['802-11-wireless-security']['psk'] = V('s', 'secret-pass')
    service.call(NM_PATH, NM, 'AddAndActivateConnection', V('(a{sa{sv}}oo)', (new, wifi, aps['Cafe Guest'])))
    assert host.ops(op='wifi', action='connect', ssid='Cafe Guest', security='wpa2', passphrase='secret-pass')


# covers: desktop.network/E3
def test_turning_wifi_off_asks_android_and_shows_what_android_reports(network):
    service, host, android = network
    service.call(NM_PATH, PROPS, 'Set', V('(ssv)', (NM, 'WirelessEnabled', V('b', False))))
    assert host.ops(op='network-wifi', enabled=False)
    # Accepted is not done: until Android reads off, Linux still shows it on.
    time.sleep(0.5)
    assert service.prop(NM_PATH, NM, 'WirelessEnabled') is True
    android.network = dict(android.network, wifiEnabled=False, networks=[])
    host.bump('network')
    wait(lambda: service.prop(NM_PATH, NM, 'WirelessEnabled') is False, 5, 'Wi-Fi off as Android reads it')
    assert service.prop(NM_PATH, NM, 'Connectivity') == 1               # none
    # Android refusing the switch fails the call; nothing changes.
    android.wifi_switch = {'accepted': False}
    with pytest.raises(GLib.Error):
        service.call(NM_PATH, PROPS, 'Set', V('(ssv)', (NM, 'WirelessEnabled', V('b', True))))
    assert service.prop(NM_PATH, NM, 'WirelessEnabled') is False


# covers: desktop.network/E4 desktop.host-bridges/E1
def test_android_changes_arrive_at_once_and_nothing_polls_meanwhile(network):
    service, host, android = network
    time.sleep(1)
    before = len(host.ops(op='network-get'))
    time.sleep(2.5)            # longer than the old 2 s poll
    assert len(host.ops(op='network-get')) == before, 'no reading without a change'
    watches = host.ops(op='watch')
    assert watches and watches[-1]['topics'] == ['network', 'telephony'] and watches[-1]['timeout'] == 60000
    android.network['networks'][0] = dict(WIFI_ROW, ssid='Office', bssid='aa:bb:cc:dd:ee:09', rssi=-60)
    started = time.monotonic()
    host.bump('network')
    primary = wait(lambda: service.prop(NM_PATH, NM, 'PrimaryConnection') != '/' and
                   service.prop(service.prop(NM_PATH, NM, 'PrimaryConnection'), NM + '.Connection.Active', 'Id') == 'Office'
                   and service.prop(NM_PATH, NM, 'PrimaryConnection'), 3, 'the new network')
    assert time.monotonic() - started < 2
    assert primary
    wifi = device(service, 2)
    ap = service.prop(wifi, NM + '.Device.Wireless', 'ActiveAccessPoint')
    assert ssid(service, ap) == 'Office' and service.prop(ap, NM + '.AccessPoint', 'Strength') == 89


# covers: desktop.network/E5
def test_a_failing_android_turns_the_state_unknown_not_stale(network):
    service, host, android = network
    android.network_error = {'error': 'network unavailable'}
    host.bump('network')
    wait(lambda: service.prop(NM_PATH, NM, 'Connectivity') == 0, 5, 'unknown connectivity')
    assert service.prop(NM_PATH, NM, 'State') == 0                  # NM_STATE_UNKNOWN
    assert service.prop(NM_PATH, NM, 'ActiveConnections') == []
    assert service.prop(NM_PATH, NM, 'PrimaryConnection') == '/'
    android.network_error = None
    host.bump('network')
    wait(lambda: service.prop(NM_PATH, NM, 'Connectivity') == 4, 5, 'back')
    # Android not answering in time is the same as failing.
    android.stall.set()
    host.bump('network')
    wait(lambda: service.prop(NM_PATH, NM, 'Connectivity') == 0, 8, 'unknown after a timeout')
    android.stall.clear()


# covers: desktop.network/E5
def test_what_android_does_not_do_is_refused_not_faked(network):
    service, host, android = network
    settings = NM_PATH + '/Settings'
    profile = {'connection': {'id': V('s', 'vpn'), 'type': V('s', 'vpn')}}
    assert service.error(settings, NM + '.Settings', 'AddConnection', V('(a{sa{sv}})', (profile,))) == NM + '.NotSupported'
    saved = service.call(settings, NM + '.Settings', 'ListConnections')[0][0]
    assert service.error(saved, NM + '.Settings.Connection', 'Update', V('(a{sa{sv}})', (profile,))) == NM + '.NotSupported'
    wifi = device(service, 2)
    enterprise = {'802-11-wireless': {'ssid': V('ay', b'Corp')}, '802-11-wireless-security': {'key-mgmt': V('s', 'wpa-eap')}}
    assert service.error(NM_PATH, NM, 'AddAndActivateConnection', V('(a{sa{sv}}oo)', (enterprise, wifi, '/'))) == \
        NM + '.NotSupported'
    assert not host.ops(op='wifi', action='connect')


# --------------------------------------------------------------------------------- ModemManager
@pytest.fixture
def modem(bus):
    android = Android()
    host = Host(android)
    service = Service('modem-manager.py', MM, bus, host)
    yield service, host, android
    service.stop()
    host.close()


MODEM = '/org/freedesktop/ModemManager1/Modem/0'


# covers: desktop.cellular/E1
def test_no_sim_is_a_modem_failed_for_a_missing_sim_with_its_model(modem):
    service, host, android = modem
    objects = service.objects('/org/freedesktop/ModemManager1')
    m = objects[MODEM][MM + '.Modem']
    assert m['State'] == -1 and m['StateFailedReason'] == 2           # MM_MODEM_STATE_FAILED, SIM_MISSING
    assert m['Sim'] == '/' and m['Manufacturer'] == 'motorola' and m['Model'] == 'moto g100'
    assert m['SignalQuality'] == (0, True)
    # A SIM in service: registered, the operator and signal shown.
    android.telephony = dict(android.telephony, sim='ready', service=0, level=3, dataType=13, operator='46001',
                             operatorName='CHN-UNICOM', simOperator='46001', simOperatorName='China Unicom')
    host.bump('telephony')
    wait(lambda: service.prop(MODEM, MM + '.Modem', 'State') == 8, 5, 'registered')
    assert service.prop(MODEM, MM + '.Modem', 'SignalQuality') == (75, True)
    assert service.prop(MODEM, MM + '.Modem.Modem3gpp', 'OperatorName') == 'CHN-UNICOM'
    assert service.prop(MODEM, MM + '.Modem', 'AccessTechnologies') == 1 << 14      # LTE
    assert service.prop('/org/freedesktop/ModemManager1/SIM/0', MM + '.Sim', 'OperatorName') == 'China Unicom'


# covers: desktop.cellular/E1
def test_network_manager_shows_the_modem_device_with_the_sim_reason(network):
    service, host, android = network
    modem_device = NM_PATH + '/Devices/modem'
    wait(lambda: modem_device in service.call(NM_PATH, NM, 'GetDevices')[0], 5, 'the modem device')
    assert service.prop(modem_device, NM + '.Device', 'DeviceType') == 8
    assert service.prop(modem_device, NM + '.Device', 'StateReason') == (20, 29)   # unavailable: SIM not inserted


# covers: desktop.cellular/E3
def test_sim_pin_network_selection_and_bearers_are_left_to_android(modem):
    service, host, android = modem
    android.telephony = dict(android.telephony, sim='pin')
    host.bump('telephony')
    wait(lambda: service.prop(MODEM, MM + '.Modem', 'State') == 2, 5, 'locked')
    assert service.prop(MODEM, MM + '.Modem', 'UnlockRequired') == 2                 # SIM PIN
    unsupported = MM + '.Error.Core.Unsupported'
    sim = '/org/freedesktop/ModemManager1/SIM/0'
    assert service.error(sim, MM + '.Sim', 'SendPin', V('(s)', ('1234',))) == unsupported
    assert service.error(sim, MM + '.Sim', 'ChangePin', V('(ss)', ('1234', '4321'))) == unsupported
    assert service.error(MODEM, MM + '.Modem.Modem3gpp', 'Register', V('(s)', ('46001',))) == unsupported
    assert service.error(MODEM, MM + '.Modem.Modem3gpp', 'Scan') == unsupported
    assert service.error(MODEM, MM + '.Modem', 'CreateBearer', V('(a{sv})', ({'apn': V('s', 'internet')},))) == unsupported
    assert not host.ops(op='telephony', action='pin')


# covers: desktop.cellular/E3
def test_data_roaming_is_refused_by_the_mobile_connection(network):
    service, host, android = network
    android.telephony = dict(android.telephony, sim='ready', service=0, dataEnabled=True, simOperator='46001',
                             simOperatorName='China Unicom')
    host.bump('telephony')
    mobile = NM_PATH + '/Settings/modem'
    wait(lambda: mobile in service.call(NM_PATH + '/Settings', NM + '.Settings', 'ListConnections')[0], 5, 'mobile data')
    current = service.call(mobile, NM + '.Settings.Connection', 'GetSettings')[0]
    assert current['gsm']['home-only'] is True
    roaming = {'connection': {'autoconnect': V('b', True)}, 'gsm': {'home-only': V('b', False)}}
    assert service.error(mobile, NM + '.Settings.Connection', 'Update', V('(a{sa{sv}})', (roaming,))) == \
        NM + '.Settings.Connection.NotSupported'
    assert not host.ops(op='telephony', action='data')


# covers: desktop.cellular/E3
def test_an_apn_change_is_refused_not_dropped(network):
    """The APN stays Android's: an Update that changes it fails with NotSupported (until 2026-10-03 it
    returned success and nothing changed)."""
    service, host, android = network
    android.telephony = dict(android.telephony, sim='ready', service=0, dataEnabled=True, simOperator='46001',
                             simOperatorName='China Unicom')
    host.bump('telephony')
    mobile = NM_PATH + '/Settings/modem'
    wait(lambda: mobile in service.call(NM_PATH + '/Settings', NM + '.Settings', 'ListConnections')[0], 5, 'mobile data')
    apn = {'connection': {'autoconnect': V('b', True)}, 'gsm': {'home-only': V('b', True), 'apn': V('s', 'internet')}}
    for method, args in (('Update', V('(a{sa{sv}})', (apn,))), ('Update2', V('(a{sa{sv}}ua{sv})', (apn, 0, {})))):
        assert service.error(mobile, NM + '.Settings.Connection', method, args) == NM + '.Settings.Connection.NotSupported'
    assert service.call(mobile, NM + '.Settings.Connection', 'GetSettings')[0]['gsm']['apn'] == ''
    same = {'connection': {'autoconnect': V('b', True)}, 'gsm': {'home-only': V('b', True), 'apn': V('s', '')}}
    service.call(mobile, NM + '.Settings.Connection', 'Update', V('(a{sa{sv}})', (same,)))   # no change: fine


# ----------------------------------------------------------------------------------------- BlueZ
@pytest.fixture
def bluetooth(bus):
    android = Android()
    host = Host(android)
    service = Service('bluez.py', 'org.bluez', bus, host)
    wait(lambda: service.prop('/org/bluez/hci0', 'org.bluez.Adapter1', 'Powered'), 5, 'the adapter on')
    yield service, host, android
    service.stop()
    host.close()


ADAPTER = '/org/bluez/hci0'


# covers: desktop.bluetooth/E1
def test_the_adapter_and_devices_found_while_discovering_are_bluez_objects(bluetooth):
    service, host, android = bluetooth
    objects = service.objects('/')
    adapter = objects[ADAPTER]['org.bluez.Adapter1']
    assert adapter['Powered'] and adapter['Name'] == 'moto g100' and adapter['Address'] == '11:22:33:44:55:66'
    bonded = objects[ADAPTER + '/dev_AA_00_00_00_00_01']['org.bluez.Device1']
    assert bonded['Paired'] and bonded['Alias'] == 'Headphones' and bonded['Icon'] == 'audio-headphones'
    service.call(ADAPTER, 'org.bluez.Adapter1', 'StartDiscovery')
    assert host.ops(op='bluetooth', action='discover', on=True)
    android.found = [{'address': 'BB:00:00:00:00:02', 'name': 'Speaker', 'class': 0x240414, 'rssi': -61}]
    host.bump('bluetooth')
    found = ADAPTER + '/dev_BB_00_00_00_00_02'
    wait(lambda: found in service.objects('/'), 5, 'the found device')
    device = service.objects('/')[found]['org.bluez.Device1']
    assert device['Alias'] == 'Speaker' and not device['Paired'] and device['RSSI'] == -61
    assert service.prop(ADAPTER, 'org.bluez.Adapter1', 'Discovering')
    service.call(ADAPTER, 'org.bluez.Adapter1', 'StopDiscovery')


# covers: desktop.bluetooth/E2
def test_discovery_goes_on_after_android_ends_an_inquiry_and_stops_with_its_client(bluetooth, bus):
    service, host, android = bluetooth
    client = Gio.DBusConnection.new_for_address_sync(
        bus, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    service.call(ADAPTER, 'org.bluez.Adapter1', 'StartDiscovery', bus=client)
    wait(lambda: service.prop(ADAPTER, 'org.bluez.Adapter1', 'Discovering'), 5, 'discovering')
    starts = len(host.ops(op='bluetooth', action='discover', on=True))
    # Android ends its inquiry after about 12 s and reports it.
    android.bluetooth['discovering'] = False
    host.bump('bluetooth')
    wait(lambda: len(host.ops(op='bluetooth', action='discover', on=True)) > starts, 5, 'discovery started again')
    assert service.prop(ADAPTER, 'org.bluez.Adapter1', 'Discovering')
    assert not host.ops(op='bluetooth', action='discover', on=False)
    client.close_sync(None)        # the settings page quits without StopDiscovery
    wait(lambda: host.ops(op='bluetooth', action='discover', on=False), 5, 'discovery stopped with its client')


# covers: desktop.bluetooth/E3
def test_bluetooth_switched_on_android_follows_in_bluez(bluetooth):
    service, host, android = bluetooth
    android.bluetooth = dict(android.bluetooth, enabled=False, state=10, discovering=False)
    started = time.monotonic()
    host.bump('bluetooth')
    wait(lambda: not service.prop(ADAPTER, 'org.bluez.Adapter1', 'Powered'), 3, 'off')
    assert time.monotonic() - started < 2
    assert service.prop(ADAPTER, 'org.bluez.Adapter1', 'PowerState') == 'off'
    # And the switch in Linux is Android's switch.
    service.call(ADAPTER, PROPS, 'Set', V('(ssv)', ('org.bluez.Adapter1', 'Powered', V('b', True))))
    wait(lambda: host.ops(op='bluetooth', action='power', on=True), 3, 'Android asked to switch on')


# ------------------------------------------------------------------------------------ host watch
sys.path.insert(0, str(PLATFORM))
import host_watch  # noqa: E402


@pytest.fixture
def watcher(monkeypatch):
    host = Host(lambda request: {'error': 'not here'})
    monkeypatch.setattr(host_watch, 'SOCKET', host.path)
    calls = []
    changed = threading.Event()

    def on_change():
        calls.append(time.monotonic())
        changed.set()
    yield host, calls, on_change
    host.close()


# covers: desktop.host-bridges/E1
def test_watch_returns_at_once_on_a_change_and_else_after_the_fallback(watcher):
    host, calls, on_change = watcher
    host_watch.watch(('network',), on_change, fallback=1.5, legacy=0.2)
    wait(lambda: len(calls) == 1, 3, 'the first reading')          # no versions seen yet
    first = host.ops(op='watch')[0]
    assert first['epoch'] is None and first['seen'] is None and first['timeout'] == 1500
    time.sleep(0.3)
    started = time.monotonic()
    host.bump('network')
    wait(lambda: len(calls) == 2, 1, 'the change')
    assert calls[1] - started < 0.5
    later = host.ops(op='watch')[-1]
    assert later['epoch'] == host.epoch and later['seen'] == {'network': 1}
    # Nothing changes: the watch ends after its fallback, and the service reads once.
    wait(lambda: len(calls) == 3, 3, 'the fallback')
    assert calls[2] - calls[1] >= 1.3


# covers: desktop.host-bridges/E2
def test_an_app_without_watch_is_polled_at_the_old_interval(watcher):
    host, calls, on_change = watcher
    host.watch_supported = False
    host_watch.watch(('bluetooth',), on_change, fallback=60, legacy=0.3)
    wait(lambda: len(calls) >= 4, 3, 'polling')
    gaps = [b - a for a, b in zip(calls, calls[1:])]
    assert all(0.25 <= g < 1 for g in gaps), gaps


# covers: desktop.host-bridges/E3
def test_a_restarted_app_is_read_again(watcher):
    host, calls, on_change = watcher
    host_watch.watch(('telephony',), on_change, fallback=60, legacy=0.2)
    wait(lambda: len(calls) == 1, 3, 'the first reading')
    old = host.epoch
    time.sleep(0.3)
    host.restart()                  # same versions (none), another epoch
    wait(lambda: len(calls) == 2, 2, 'the new epoch')
    wait(lambda: host.ops(op='watch')[-1]['epoch'] == host.epoch != old, 2, 'watching the new epoch')
