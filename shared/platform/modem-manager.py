#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""ModemManager D-Bus subset backed by Android's telephony, not a modem daemon (docs/73).

Android's RIL owns the modem, the SIM and the data connection. This service publishes Android's data
subscription as one ModemManager modem, so ModemManagerQt users (the signal indicator and mobile data
quick setting of Plasma Mobile, the cellular network settings) read the real state:
  Modem       state (SIM missing/locked, searching, registered, connected), signal quality, access
              technology, manufacturer and model
  Modem3gpp   registration state, operator name and code
  Sim         operator of the SIM; present only with a SIM
The mobile data switch is NetworkManager's (shared/platform/network-manager.py: the modem device and
its "mobile data" connection). SIM PIN entry, network selection and bearers stay with Android; those
calls fail with org.freedesktop.ModemManager1.Error.Core.Unsupported. The property set follows
ModemManager 1.24 and Droidian's ofono2mm (BSD-3-Clause), which bridges oFono the same way.
Requests go to the APK's platform socket ("telephony"). Requires PyGObject.
"""
import os
import concurrent.futures
import json
import logging
import signal
import socket
import time

from gi.repository import Gio, GLib

import rungic_host_watch
import rungic_platform_transport

MM = 'org.freedesktop.ModemManager1'
BASE = '/org/freedesktop/ModemManager1'
MODEM = BASE + '/Modem/0'
SIM = BASE + '/SIM/0'
OM = 'org.freedesktop.DBus.ObjectManager'
PROPS = 'org.freedesktop.DBus.Properties'
SOCKET = os.environ.get('RUNGIC_PLATFORM_SOCKET', '/mnt/android-wayland/platform.sock')
LOG = logging.getLogger('android-modem')
V = GLib.Variant
POLL = 5
UNSUPPORTED = MM + '.Error.Core.Unsupported'

# MMModemState, MMModemStateFailedReason, MMModemLock, MMModem3gppRegistrationState.
FAILED, INITIALIZING, LOCKED, DISABLED, SEARCHING, REGISTERED, CONNECTED = -1, 1, 2, 3, 7, 8, 11
SIM_MISSING, SIM_ERROR = 2, 3
LOCKS = {'pin': 2, 'puk': 4, 'network-locked': 8}
# Android TelephonyManager.NETWORK_TYPE_* -> MMModemAccessTechnology.
ACCESS = {1: 1 << 3, 2: 1 << 4, 3: 1 << 5, 4: 1 << 10, 5: 1 << 11, 6: 1 << 12, 7: 1 << 10, 8: 1 << 6, 9: 1 << 7,
          10: 1 << 8, 12: 1 << 13, 13: 1 << 14, 14: 1 << 12, 15: 1 << 9, 16: 1 << 1, 17: 1 << 5, 19: 1 << 14, 20: 1 << 15}
# MMModemCapability: GSM/UMTS, LTE and 5GNR.
CAPABILITIES = 0x4 | 0x8 | 0x40

INTROSPECTION = {
    MM: '''<method name="ScanDevices"/><method name="SetLogging"><arg name="level" type="s" direction="in"/></method>
        <method name="ReportKernelEvent"><arg name="properties" type="a{sv}" direction="in"/></method>
        <method name="InhibitDevice"><arg name="uid" type="s" direction="in"/><arg name="inhibit" type="b" direction="in"/></method>''',
    MM + '.Modem': '''<method name="Enable"><arg name="enable" type="b" direction="in"/></method>
        <method name="ListBearers"><arg name="bearers" type="ao" direction="out"/></method>
        <method name="CreateBearer"><arg name="properties" type="a{sv}" direction="in"/><arg name="path" type="o" direction="out"/></method>
        <method name="DeleteBearer"><arg name="bearer" type="o" direction="in"/></method>
        <method name="Reset"/><method name="FactoryReset"><arg name="code" type="s" direction="in"/></method>
        <method name="SetPowerState"><arg name="state" type="u" direction="in"/></method>
        <method name="SetCurrentCapabilities"><arg name="capabilities" type="u" direction="in"/></method>
        <method name="SetCurrentModes"><arg name="modes" type="(uu)" direction="in"/></method>
        <method name="SetCurrentBands"><arg name="bands" type="au" direction="in"/></method>
        <method name="SetPrimarySimSlot"><arg name="sim_slot" type="u" direction="in"/></method>
        <method name="Command"><arg name="cmd" type="s" direction="in"/><arg name="timeout" type="u" direction="in"/><arg name="response" type="s" direction="out"/></method>
        <signal name="StateChanged"><arg name="old" type="i"/><arg name="new" type="i"/><arg name="reason" type="u"/></signal>''',
    MM + '.Modem.Modem3gpp': '''<method name="Register"><arg name="operator_id" type="s" direction="in"/></method>
        <method name="Scan"><arg name="results" type="aa{sv}" direction="out"/></method>''',
    MM + '.Sim': '''<method name="SendPin"><arg name="pin" type="s" direction="in"/></method>
        <method name="SendPuk"><arg name="puk" type="s" direction="in"/><arg name="pin" type="s" direction="in"/></method>
        <method name="EnablePin"><arg name="pin" type="s" direction="in"/><arg name="enabled" type="b" direction="in"/></method>
        <method name="ChangePin"><arg name="old_pin" type="s" direction="in"/><arg name="new_pin" type="s" direction="in"/></method>''',
    OM: '''<method name="GetManagedObjects"><arg name="objects" type="a{oa{sa{sv}}}" direction="out"/></method>
        <signal name="InterfacesAdded"><arg name="object" type="o"/><arg name="interfaces" type="a{sa{sv}}"/></signal>
        <signal name="InterfacesRemoved"><arg name="object" type="o"/><arg name="interfaces" type="as"/></signal>''',
}


def host_request(timeout=10, **request):
    result = rungic_platform_transport.request(dict(op='telephony', **request), timeout, ui_socket=SOCKET, limit=65536)
    if 'error' in result:
        raise OSError(result['error'])
    return result


def props(**kwargs):
    return {key: V(sig, value) for key, (sig, value) in kwargs.items()}


def modem_state(state):
    """(MMModemState, failed reason, lock, 3GPP registration state) of an Android snapshot."""
    sim = state.get('sim', 'unknown')
    service = state.get('service', 1)             # ServiceState: 0 in service, 1 out of service, 2 emergency only, 3 radio off
    registration = {0: 5 if state.get('roaming') else 1, 1: 2, 2: 3}.get(service, 0)
    if sim == 'absent':
        return FAILED, SIM_MISSING, 1, 0
    if sim in ('error', 'disabled'):
        return FAILED, SIM_ERROR, 1, 0
    if sim in LOCKS:
        return LOCKED, 0, LOCKS[sim], 0
    if sim != 'ready':
        return INITIALIZING, 0, 0, 0
    if service == 3:
        return DISABLED, 0, 1, 0
    if service != 0:
        return SEARCHING, 0, 1, registration
    return (CONNECTED if state.get('dataConnected') else REGISTERED), 0, 1, registration


def make_graph(state):
    """ModemManager objects of an Android snapshot; no modem when Android has none."""
    graph = {BASE: {MM: props(Version=('s', '1.24.0-android-bridge.1'))}}
    if not state or not state.get('modem'):
        return graph
    mm_state, reason, lock, registration = modem_state(state)
    has_sim = state.get('sim') not in ('absent', 'unknown', None)
    level = max(0, min(4, int(state.get('level', 0))))
    registered = mm_state >= REGISTERED
    model = state.get('model') or 'Android'
    graph[MODEM] = {
        MM + '.Modem': props(
            Sim=('o', SIM if has_sim else '/'), SimSlots=('ao', []), PrimarySimSlot=('u', 0), Bearers=('ao', []),
            SupportedCapabilities=('au', [CAPABILITIES]), CurrentCapabilities=('u', CAPABILITIES),
            MaxBearers=('u', 1), MaxActiveBearers=('u', 1), MaxActiveMultiplexedBearers=('u', 0),
            Manufacturer=('s', state.get('manufacturer') or 'Android'), Model=('s', model), Revision=('s', ''),
            HardwareRevision=('s', ''), CarrierConfiguration=('s', ''), CarrierConfigurationRevision=('s', ''),
            DeviceIdentifier=('s', 'android-telephony'), Device=('s', 'android'), Physdev=('s', 'android'),
            Drivers=('as', ['android']), Plugin=('s', 'android'), PrimaryPort=('s', 'android'),
            Ports=('a(su)', [('android', 0)]), EquipmentIdentifier=('s', ''),
            UnlockRequired=('u', lock), UnlockRetries=('a{uu}', {}),
            State=('i', mm_state), StateFailedReason=('u', reason),
            AccessTechnologies=('u', ACCESS.get(state.get('dataType', 0), 0) if registered else 0),
            SignalQuality=('(ub)', (level * 25 if registered else 0, True)), OwnNumbers=('as', []),
            PowerState=('u', 2 if mm_state == DISABLED else 3),
            SupportedModes=('a(uu)', [(0xFFFFFFFF, 0)]), CurrentModes=('(uu)', (0xFFFFFFFF, 0)),
            SupportedBands=('au', []), CurrentBands=('au', []), SupportedIpFamilies=('u', 7)),
        MM + '.Modem.Modem3gpp': props(
            Imei=('s', ''), RegistrationState=('u', registration),
            OperatorCode=('s', state.get('operator', '') if registered else ''),
            OperatorName=('s', state.get('operatorName', '') if registered else ''),
            EnabledFacilityLocks=('u', 0), SubscriptionState=('u', 0), EpsUeModeOperation=('u', 0),
            Pco=('a(ubay)', []), InitialEpsBearer=('o', '/'), InitialEpsBearerSettings=('a{sv}', {}),
            PacketServiceState=('u', 2 if registered else 0))}
    if has_sim:
        graph[SIM] = {MM + '.Sim': props(
            Active=('b', True), SimIdentifier=('s', ''), Imsi=('s', ''), Eid=('s', ''),
            OperatorIdentifier=('s', state.get('simOperator', '')), OperatorName=('s', state.get('simOperatorName', '')),
            EmergencyNumbers=('as', []), PreferredNetworks=('a(su)', []), SimType=('u', 1), EsimStatus=('u', 0),
            Removability=('u', 1))}
    return graph


def introspection(interface, values):
    body = INTROSPECTION.get(interface, '')
    for key, value in values.items():
        body += f'<property name="{key}" type="{value.get_type_string()}" access="read"/>'
    return Gio.DBusNodeInfo.new_for_xml(f'<node><interface name="{interface}">{body}</interface></node>').interfaces[0]


class Bridge:
    def __init__(self, bus):
        self.bus = bus
        self.graph = {}
        self.registrations = {}
        self.pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix='android-modem')
        self.polling = False
        self.alive = True
        self.due = 0.0
        bus.register_object(BASE, introspection(OM, {}), self.call, None, None)
        self.publish(None)

    def emit(self, path, interface, name, sig, values):
        self.bus.emit_signal(None, path, interface, name, V('(' + sig + ')', values))

    def publish(self, state):
        graph, old = make_graph(state), self.graph
        self.graph = graph
        for path, interfaces in graph.items():
            added = {}
            for iface, values in interfaces.items():
                if (path, iface) not in self.registrations:
                    self.registrations[path, iface] = self.bus.register_object(
                        path, introspection(iface, values), self.call, self.get, None)
                    added[iface] = values
                elif values != old.get(path, {}).get(iface, {}):
                    delta = {k: v for k, v in values.items() if v != old[path][iface].get(k)}
                    self.emit(path, PROPS, 'PropertiesChanged', 'sa{sv}as', (iface, delta, []))
                    if iface == MM + '.Modem' and 'State' in delta:
                        self.emit(path, iface, 'StateChanged', 'iiu', (old[path][iface]['State'].unpack(), delta['State'].unpack(), 0))
            if added and path.startswith(BASE + '/Modem/'):  # as ModemManager: modems only, SIMs by path
                self.emit(BASE, OM, 'InterfacesAdded', 'oa{sa{sv}}', (path, added))
        for path, interfaces in old.items():
            removed = [iface for iface in interfaces if iface not in graph.get(path, {})]
            if removed:
                if path.startswith(BASE + '/Modem/'):
                    self.emit(BASE, OM, 'InterfacesRemoved', 'oas', (path, removed))
                for iface in removed:
                    self.bus.unregister_object(self.registrations.pop((path, iface)))
        return False

    def poll(self):
        if not self.polling and time.monotonic() >= self.due:
            self.polling = True
            def work():
                try:
                    state = host_request(action='state')
                except (OSError, ValueError):
                    state = None
                def done():
                    self.polling = False
                    self.due = time.monotonic() + POLL
                    if self.alive and state is not None:  # Android unreachable: keep the last state
                        self.publish(state)
                    return False
                GLib.idle_add(done)
            self.pool.submit(work)
        return GLib.SOURCE_CONTINUE

    def get(self, bus, sender, path, interface, name):
        return self.graph.get(path, {}).get(interface, {}).get(name)

    def call(self, bus, sender, path, interface, method, args, invocation):
        if interface == OM and method == 'GetManagedObjects':
            objects = {p: i for p, i in self.graph.items() if p.startswith(BASE + '/Modem/')}
            return invocation.return_value(V('(a{oa{sa{sv}}})', (objects,)))
        if interface == MM and method in ('ScanDevices', 'SetLogging', 'ReportKernelEvent'):
            self.due = 0.0
            return invocation.return_value(None)
        if interface == MM + '.Modem' and method == 'ListBearers':
            return invocation.return_value(V('(ao)', ([],)))
        if interface == MM + '.Modem' and method == 'Enable' and args.unpack()[0]:
            return invocation.return_value(None)       # Android keeps the radio on; airplane mode is Android's
        if interface == MM + '.Sim':
            return invocation.return_dbus_error(UNSUPPORTED, 'The SIM PIN is entered on Android')
        invocation.return_dbus_error(UNSUPPORTED, 'Managed by Android')


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    # The modem before the name: ModemManagerQt clients started before this service enumerate once when
    # the name appears and ignore later InterfacesAdded (modemmanager-qt 6.23, manager.cpp).
    bridge = Bridge(bus)
    while True:
        try:
            bridge.publish(host_request(action='state'))
            break
        except (OSError, ValueError):
            time.sleep(2)
    # Never replace a real ModemManager or another bridge instance.
    result = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'RequestName',
                           V('(su)', (MM, 4)), V('(u)', (0,)).get_type(), Gio.DBusCallFlags.NONE, 3000, None)
    if result.unpack()[0] != 1:
        raise SystemExit(MM + ' is already owned')
    loop = GLib.MainLoop()

    def stop():
        bridge.alive = False
        loop.quit()
        return False
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, stop)
    bridge.poll()
    # Asked again when Android reports a change, not on a timer (docs/49).
    def changed():
        bridge.due = 0
        bridge.poll()
        return False
    rungic_host_watch.watch(('telephony',), lambda: GLib.idle_add(changed), legacy=POLL, name='modem-watch')
    try:
        loop.run()
    finally:
        bridge.alive = False
        bridge.pool.shutdown(wait=True, cancel_futures=True)


if __name__ == '__main__':
    main()
