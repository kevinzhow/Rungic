#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""BlueZ D-Bus subset backed by Android's Bluetooth, not a Bluetooth daemon (docs/73).

Android owns the controller, the bonds and the audio routing; the container has no HCI. This service
publishes Android's adapter and devices under org.bluez as bluetoothd does, so BluezQt users (the
Bluetooth settings, the quick setting, bluedevil) work unchanged:
  Adapter1   Powered (Android's switch), Alias/Name, Discovering, StartDiscovery/StopDiscovery,
             RemoveDevice (unpair)
  Device1    bonded devices and those found while discovering; Pair (Android asks the user to
             confirm), Connect/Disconnect (all enabled profiles), Paired/Connected
  AgentManager1, ProfileManager1: registrations are accepted; Android's own dialogs do the pairing.
Requests go to the APK's platform socket ("bluetooth"); unsupported calls fail with
org.bluez.Error.NotSupported. Requires PyGObject.
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

BLUEZ = 'org.bluez'
ADAPTER = '/org/bluez/hci0'
OM = 'org.freedesktop.DBus.ObjectManager'
PROPS = 'org.freedesktop.DBus.Properties'
SOCKET = os.environ.get('RUNGIC_PLATFORM_SOCKET', '/mnt/android-wayland/platform.sock')
LOG = logging.getLogger('android-bluetooth')
V = GLib.Variant
POLL, POLL_DISCOVERING = 5, 2

INTROSPECTION = {
    'org.bluez.Adapter1': '''<method name="StartDiscovery"/><method name="StopDiscovery"/>
        <method name="RemoveDevice"><arg name="device" type="o" direction="in"/></method>
        <method name="SetDiscoveryFilter"><arg name="properties" type="a{sv}" direction="in"/></method>
        <method name="GetDiscoveryFilters"><arg name="filters" type="as" direction="out"/></method>''',
    'org.bluez.Device1': '''<method name="Connect"/><method name="Disconnect"/><method name="Pair"/><method name="CancelPairing"/>
        <method name="ConnectProfile"><arg name="UUID" type="s" direction="in"/></method>
        <method name="DisconnectProfile"><arg name="UUID" type="s" direction="in"/></method>''',
    'org.bluez.AgentManager1': '''<method name="RegisterAgent"><arg name="agent" type="o" direction="in"/><arg name="capability" type="s" direction="in"/></method>
        <method name="UnregisterAgent"><arg name="agent" type="o" direction="in"/></method>
        <method name="RequestDefaultAgent"><arg name="agent" type="o" direction="in"/></method>''',
    'org.bluez.ProfileManager1': '''<method name="RegisterProfile"><arg name="profile" type="o" direction="in"/><arg name="UUID" type="s" direction="in"/><arg name="options" type="a{sv}" direction="in"/></method>
        <method name="UnregisterProfile"><arg name="profile" type="o" direction="in"/></method>''',
    OM: '''<method name="GetManagedObjects"><arg name="objects" type="a{oa{sa{sv}}}" direction="out"/></method>
        <signal name="InterfacesAdded"><arg name="object" type="o"/><arg name="interfaces" type="a{sa{sv}}"/></signal>
        <signal name="InterfacesRemoved"><arg name="object" type="o"/><arg name="interfaces" type="as"/></signal>''',
}
WRITABLE = {'org.bluez.Adapter1': {'Powered', 'Alias', 'Discoverable', 'Pairable', 'DiscoverableTimeout', 'PairableTimeout'},
            'org.bluez.Device1': {'Alias', 'Trusted', 'Blocked'}}


def host_request(timeout=20, **request):
    result = rungic_platform_transport.request(dict(op='bluetooth', **request), timeout, ui_socket=SOCKET, limit=1048576)
    if 'error' in result:
        raise OSError(result['error'])
    return result


def props(**kwargs):
    return {key: V(sig, value) for key, (sig, value) in kwargs.items()}


def device_path(address):
    return ADAPTER + '/dev_' + address.upper().replace(':', '_')


def icon(cls):
    """The freedesktop icon BlueZ derives from the Class of Device."""
    major, minor = (cls >> 8) & 0x1f, (cls >> 2) & 0x3f
    if major == 1:
        return 'computer'
    if major == 2:
        return 'phone'
    if major == 3:
        return 'network-wireless'
    if major == 4:
        return {1: 'audio-headset', 2: 'audio-headset', 6: 'audio-headphones', 11: 'camera-video',
                12: 'camera-video', 15: 'video-display'}.get(minor, 'audio-card')
    if major == 5:
        kind = (cls >> 6) & 0x03
        return {1: 'input-keyboard', 2: 'input-mouse'}.get(kind, 'input-gaming' if minor & 0x0f in (1, 2) else 'input-tablet')
    if major == 6:
        return 'camera-photo' if cls & 0x80 else 'printer' if cls & 0x100 else 'video-display'
    return ''


def make_graph(state):
    """org.bluez objects of an Android snapshot (None: Android unreachable, the adapter shows off)."""
    state = state or {}
    enabled = bool(state.get('enabled'))
    name = state.get('name') or 'Android'
    graph = {'/org/bluez': {'org.bluez.AgentManager1': {}, 'org.bluez.ProfileManager1': {}}}
    graph[ADAPTER] = {'org.bluez.Adapter1': props(
        Address=('s', state.get('address') or '00:00:00:00:00:00'), AddressType=('s', 'public'), Name=('s', name),
        Alias=('s', name), Class=('u', 0x5a020c), Powered=('b', enabled), PowerState=('s', 'on' if enabled else 'off'),
        Discoverable=('b', False), DiscoverableTimeout=('u', 180), Pairable=('b', enabled), PairableTimeout=('u', 0),
        Discovering=('b', bool(state.get('discovering'))), UUIDs=('as', []), Modalias=('s', 'android'),
        Roles=('as', ['central', 'peripheral']))}
    rows = {}
    for row in state.get('found', []):
        rows[row['address'].upper()] = dict(row, bond=10, connected=False)
    for row in state.get('devices', []):                  # bonded devices win over discovery results
        rows[row['address'].upper()] = row
    for address, row in rows.items():
        cls = int(row.get('class', 0))
        paired = row.get('bond') == 12
        name = row.get('alias') or row.get('name') or ''
        values = props(Address=('s', address), AddressType=('s', 'public'), Name=('s', row.get('name') or address),
            Alias=('s', name or address.replace(':', '-')), Class=('u', cls), Appearance=('q', 0), Icon=('s', icon(cls)),
            Paired=('b', paired), Bonded=('b', paired), Trusted=('b', paired), Blocked=('b', False), LegacyPairing=('b', False),
            Connected=('b', bool(row.get('connected'))), UUIDs=('as', [u.lower() for u in row.get('uuids', [])]),
            Modalias=('s', ''), Adapter=('o', ADAPTER), ServicesResolved=('b', bool(row.get('connected'))), WakeAllowed=('b', False))
        if 'rssi' in row:
            values['RSSI'] = V('n', int(row['rssi']))
        graph[device_path(address)] = {'org.bluez.Device1': values}
    return graph


def introspection(interface, values):
    body = INTROSPECTION.get(interface, '')
    for key, value in values.items():
        access = 'readwrite' if key in WRITABLE.get(interface, ()) else 'read'
        body += f'<property name="{key}" type="{value.get_type_string()}" access="{access}"/>'
    return Gio.DBusNodeInfo.new_for_xml(f'<node><interface name="{interface}">{body}</interface></node>').interfaces[0]


class Bridge:
    def __init__(self, bus):
        self.bus = bus
        self.graph = {}
        self.registrations = {}
        self.pool = concurrent.futures.ThreadPoolExecutor(2, thread_name_prefix='android-bluetooth')
        self.polling = False
        self.alive = True
        self.due = 0.0
        self.discovery = {}                               # client bus name -> name watch, as bluetoothd
        bus.register_object('/', introspection(OM, {}), self.call, None, None)
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
                        path, introspection(iface, values), self.call, self.get, self.set)
                    added[iface] = values
                elif values != old.get(path, {}).get(iface, {}):
                    delta = {k: v for k, v in values.items() if v != old[path][iface].get(k)}
                    self.emit(path, PROPS, 'PropertiesChanged', 'sa{sv}as', (iface, delta, []))
            if added:
                self.emit('/', OM, 'InterfacesAdded', 'oa{sa{sv}}', (path, added))
        for path, interfaces in old.items():
            removed = [iface for iface in interfaces if iface not in graph.get(path, {})]
            if removed:
                self.emit('/', OM, 'InterfacesRemoved', 'oas', (path, removed))
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
                    if state and state.get('enabled') and self.discovery and not state.get('discovering'):
                        # Android ends an inquiry after about 12 s; a BlueZ session lasts until stopped.
                        self.act(None, {'action': 'discover', 'on': True}, later=())
                        state['discovering'] = True
                    discovering = bool(state and state.get('discovering'))
                    self.due = time.monotonic() + (POLL_DISCOVERING if discovering else POLL)
                    if self.alive:
                        self.publish(state)
                    return False
                GLib.idle_add(done)
            self.pool.submit(work)
        return GLib.SOURCE_CONTINUE

    def refresh(self):
        self.due = 0.0
        self.poll()
        return False

    def act(self, invocation, request, timeout=20, later=(2, 5)):
        """An Android Bluetooth request off the main loop; an empty reply follows it."""
        def work():
            try:
                host_request(timeout=timeout, **request)
                error = None
            except (OSError, ValueError) as e:
                error = str(e)
            def done():
                if invocation:
                    if error:
                        invocation.return_dbus_error('org.bluez.Error.Failed', error)
                    else:
                        invocation.return_value(None)
                for seconds in later:
                    GLib.timeout_add_seconds(seconds, self.refresh)
                self.refresh()
                return False
            GLib.idle_add(done)
        self.pool.submit(work)

    def start_discovery(self, invocation, sender):
        """Discovery is per client, as in bluetoothd: on while any client wants it; a client leaving
        the bus ends its session."""
        if sender in self.discovery:
            return invocation.return_dbus_error('org.bluez.Error.InProgress', 'Operation already in progress')
        self.discovery[sender] = Gio.bus_watch_name_on_connection(
            self.bus, sender, Gio.BusNameWatcherFlags.NONE, None, lambda bus, name: self.end_discovery(None, name))
        if len(self.discovery) > 1:
            return invocation.return_value(None)
        self.act(invocation, {'action': 'discover', 'on': True})

    def end_discovery(self, invocation, sender):
        watch = self.discovery.pop(sender, None)
        if watch is None:
            if invocation:
                invocation.return_dbus_error('org.bluez.Error.Failed', 'No discovery started')
            return
        Gio.bus_unwatch_name(watch)
        if self.discovery:
            if invocation:
                invocation.return_value(None)
            return
        self.act(invocation, {'action': 'discover', 'on': False})

    def pair(self, invocation, address):
        """Start bonding and answer once Android reports the device bonded (the user confirms on
        Android's dialog) or gives up."""
        def work():
            error = None
            try:
                host_request(action='pair', address=address)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    state = host_request(action='state')
                    bond = next((d.get('bond') for d in state.get('devices', []) if d['address'].upper() == address), None)
                    if bond == 12:
                        break
                    time.sleep(1.5)
                else:
                    error = 'Pairing was not confirmed'
            except (OSError, ValueError) as e:
                error = str(e)
            def done():
                if error:
                    invocation.return_dbus_error('org.bluez.Error.AuthenticationFailed', error)
                else:
                    invocation.return_value(None)
                self.refresh()
                return False
            GLib.idle_add(done)
        self.pool.submit(work)

    def get(self, bus, sender, path, interface, name):
        return self.graph.get(path, {}).get(interface, {}).get(name)

    def set(self, bus, sender, path, interface, name, value):
        if interface == 'org.bluez.Adapter1' and name == 'Powered':
            self.act(None, {'action': 'power', 'on': value.unpack()}, later=(2, 5, 10))
            return True
        if interface == 'org.bluez.Adapter1' and name == 'Alias':
            self.act(None, {'action': 'name', 'name': value.unpack()})
            return True
        # Discoverability, trust and blocking are Android's; accepted and left as Android has them.
        return name in WRITABLE.get(interface, ())

    def call(self, bus, sender, path, interface, method, args, invocation):
        try:
            if interface == OM and method == 'GetManagedObjects':
                return invocation.return_value(V('(a{oa{sa{sv}}})', (self.graph,)))
            if interface in ('org.bluez.AgentManager1', 'org.bluez.ProfileManager1'):
                return invocation.return_value(None)
            if interface == 'org.bluez.Adapter1':
                if method == 'StartDiscovery':
                    return self.start_discovery(invocation, sender)
                if method == 'StopDiscovery':
                    return self.end_discovery(invocation, sender)
                if method == 'SetDiscoveryFilter':
                    return invocation.return_value(None)
                if method == 'GetDiscoveryFilters':
                    return invocation.return_value(V('(as)', ([],)))
                if method == 'RemoveDevice':
                    device = args.unpack()[0]
                    address = self.graph.get(device, {}).get('org.bluez.Device1', {}).get('Address')
                    if address is None:
                        return invocation.return_dbus_error('org.bluez.Error.DoesNotExist', 'No such device')
                    return self.act(invocation, {'action': 'unpair', 'address': address.unpack()})
            if interface == 'org.bluez.Device1' and path in self.graph:
                address = self.graph[path]['org.bluez.Device1']['Address'].unpack()
                if method == 'Pair':
                    return self.pair(invocation, address)
                if method in ('Connect', 'ConnectProfile'):
                    return self.act(invocation, {'action': 'connect', 'address': address}, later=(2, 5, 10))
                if method in ('Disconnect', 'DisconnectProfile'):
                    return self.act(invocation, {'action': 'disconnect', 'address': address}, later=(2, 5))
            invocation.return_dbus_error('org.bluez.Error.NotSupported', 'Not supported by the Android Bluetooth bridge')
        except Exception:
            LOG.exception('D-Bus method failed: %s', method)
            invocation.return_dbus_error('org.bluez.Error.Failed', 'Android Bluetooth bridge request failed')


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    # Objects first: a client enumerating as soon as the name appears finds them.
    bridge = Bridge(bus)
    # Never replace a real bluetoothd or another bridge instance.
    result = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'RequestName',
                           V('(su)', (BLUEZ, 4)), V('(u)', (0,)).get_type(), Gio.DBusCallFlags.NONE, 3000, None)
    if result.unpack()[0] != 1:
        raise SystemExit('org.bluez is already owned')
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
    rungic_host_watch.watch(('bluetooth',), lambda: GLib.idle_add(changed), legacy=POLL, name='bluetooth-watch')
    try:
        loop.run()
    finally:
        bridge.alive = False
        bridge.pool.shutdown(wait=True, cancel_futures=True)


if __name__ == '__main__':
    main()
