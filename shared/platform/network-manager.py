#!/usr/bin/python3
"""NetworkManager D-Bus subset backed by Android, not a network daemon.

Android alone owns the interfaces, routes, resolver and credentials. All data is
live and volatile. Unsupported mutations fail explicitly. Requires PyGObject.
"""
import concurrent.futures
import hashlib
import ipaddress
import json
import logging
import os
import re
import signal
import socket
import struct
import tempfile
import threading
import time
import uuid
from xml.sax.saxutils import escape

from gi.repository import Gio, GLib

import rungic_host_watch

NM = 'org.freedesktop.NetworkManager'
BASE = '/org/freedesktop/NetworkManager'
PROPS = 'org.freedesktop.DBus.Properties'
OM = 'org.freedesktop.DBus.ObjectManager'
OM_PATH = '/org/freedesktop'
HOST = 'com.rungic.Android.Network'
SOCKET = os.environ.get('RUNGIC_PLATFORM_SOCKET', '/mnt/android-wayland/platform.sock')
LOG = logging.getLogger('android-network')
V = GLib.Variant
WIFI_REFRESH = 10
# The container shares Android's network namespace (lxc.net.0.type = none), so the Linux resolver can
# use the DNS servers of Android's default network directly, the VPN's when one is up (issue #7).
RESOLV_CONF = '/etc/resolv.conf'
RESOLV_MARKER = '# Managed by Rungic from the Android default network'
# What the rootfs image ships (tools/ci/build_rootfs_image.py): also ours to replace.
RESOLV_PLACEHOLDER = '# Set from Android network on first boot'
RESOLV_MAX_SERVERS = 3  # glibc reads at most three nameservers


def host_request(timeout=3.5, **request):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(timeout)
        client.connect(SOCKET)
        client.sendall(json.dumps(request).encode() + b'\n')
        data = bytearray()
        while b'\n' not in data:
            part = client.recv(8192)
            if not part or len(data) + len(part) > 524288:
                raise OSError('Invalid Android response')
            data.extend(part)
        result = json.loads(data.split(b'\n', 1)[0])
        if 'error' in result:
            raise OSError(result['error'])
        return result


def resolv_conf_text(snapshot):
    """/etc/resolv.conf for the default network of an Android snapshot: its DNS servers (at most
    three, IPv6 link-local ones with their interface), or none when Android has no default network,
    so that a stale VPN server is never kept."""
    servers = []
    for row in snapshot.get('networks', []):
        if not row.get('default'):
            continue
        for value in row.get('dns', []):
            address, _, scope = str(value).partition('%')
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                continue
            if scope and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', scope):
                continue
            entry = f'{ip}%{scope}' if scope and ip.version == 6 and ip.is_link_local else str(ip)
            if entry not in servers:
                servers.append(entry)
        break
    lines = [RESOLV_MARKER] + [f'nameserver {server}' for server in servers[:RESOLV_MAX_SERVERS]]
    if not servers:
        lines.append('# No default network on Android now')
    return '\n'.join(lines) + '\n'


def resolv_conf_is_ours(path):
    """Ours to rewrite: missing, empty, the image's placeholder or written by us. A file someone
    edited by hand (without the marker) is left alone."""
    try:
        with open(path, encoding='utf-8', errors='replace') as stream:
            first = stream.readline().strip()
            rest = stream.read().strip()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if first == RESOLV_MARKER:
        return True
    return first in ('', RESOLV_PLACEHOLDER) and not rest


def sync_resolv_conf(snapshot, path=None):
    """Write the default network's resolver configuration, atomically and only when it changes.
    -> 'written', 'unchanged' or 'kept' (a hand-made file), or 'failed'."""
    path = path or RESOLV_CONF
    if os.path.islink(path) or not resolv_conf_is_ours(path):
        return 'kept'
    text = resolv_conf_text(snapshot)
    try:
        with open(path, encoding='utf-8') as stream:
            if stream.read() == text:
                return 'unchanged'
    except OSError:
        pass
    folder = os.path.dirname(path) or '.'
    temporary = None
    try:
        # A new, exclusively created file of a random name beside the target: a concurrent start or
        # a leftover file or link of a fixed name cannot be written through.
        descriptor, temporary = tempfile.mkstemp(prefix='.resolv.conf.', dir=folder)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        return 'failed'
    return 'written'


def props(**kwargs):
    return {key: V(sig, value) for key, (sig, value) in kwargs.items()}


def ident(value):
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def profile(row, wifi):
    name = row.get('ssid') or 'Android ' + row['kind']
    kind = '802-11-wireless' if wifi else '802-3-ethernet' if row['kind'] == 'ethernet' else 'generic'
    uid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'android-network:' + row['kind'] + ':' + name + ':' + row['interface']))
    settings = {'connection': props(id=('s', name), uuid=('s', uid), type=('s', kind),
                                   **{'interface-name': ('s', row['interface']), 'read-only': ('b', True), 'autoconnect': ('b', False)}),
                'ipv4': props(method=('s', 'auto')), 'ipv6': props(method=('s', 'auto'))}
    if wifi:
        settings[kind] = props(ssid=('ay', list(row.get('ssid', '').encode())), mode=('s', 'infrastructure'))
    elif kind == '802-3-ethernet':
        settings[kind] = {}
    else:
        settings['generic'] = {}
    return name, uid, kind, settings


def ip_config(row, version):
    address_data, route_data, dns = [], [], []
    gateway = ''
    for value in row.get('addresses', []):
        try:
            a = ipaddress.ip_interface(value)
            if a.version == version:
                address_data.append(props(address=('s', str(a.ip)), prefix=('u', a.network.prefixlen)))
        except ValueError:
            continue
    for value in row.get('dns', []):
        try:
            if ipaddress.ip_address(value).version == version:
                dns.append(value.split('%')[0])
        except ValueError:
            continue
    for value in row.get('routes', []):
        try:
            r = ipaddress.ip_network(value['destination'], strict=False)
            if r.version != version:
                continue
            entry = props(dest=('s', str(r.network_address)), prefix=('u', r.prefixlen))
            if value.get('gateway'):
                hop = str(ipaddress.ip_address(value['gateway'])).split('%')[0]
                entry['next-hop'] = V('s', hop)
                if value.get('default'):
                    gateway = hop
            route_data.append(entry)
        except (ValueError, KeyError):
            continue
    result = props(AddressData=('aa{sv}', address_data), Gateway=('s', gateway), RouteData=('aa{sv}', route_data),
                   Domains=('as', []), Searches=('as', []), DnsOptions=('as', []), DnsPriority=('i', 0))
    if version == 4:
        result.update(props(NameserverData=('aa{sv}', [props(address=('s', a)) for a in dns]),
                            Nameservers=('au', [struct.unpack('=I', socket.inet_aton(a))[0] for a in dns]),
                            WinsServers=('au', []), WinsServerData=('as', [])))
    else:
        result['Nameservers'] = V('aay', [list(ipaddress.ip_address(a).packed) for a in dns])
    return result


def security_flags(security):
    # Android WifiInfo SECURITY_TYPE_* -> NM AP security flags. Unknown is
    # deliberately not labelled open: no AP is published without known security.
    return {0: (0, 0), 1: (1, 0), 2: (1, 0x100 | 0x88), 3: (1, 0x200 | 0x88),
            4: (1, 0x400 | 0x88), 5: (1, 0x2000 | 0x88), 6: (1, 0x800 | 0x88),
            9: (1, 0x200 | 0x88)}.get(security)


SCAN_LINE = re.compile(r'^\s*([0-9a-fA-F:]{17})\s+(\d+)\s+(-?\d+)\S*\s+([\d.]+)\s+(.*?)\s*((?:\[[^\]]*\])+)\s*$')
SAVED_LINE = re.compile(r'^(\d+)\s+(.*\S)\s{2,}(\S+)\s*$')


def parse_scan(text):
    """`cmd wifi list-scan-results`: BSSID, frequency, RSSI, age, SSID (may hold spaces), flags."""
    rows = {}
    for line in (text or '').splitlines():
        m = SCAN_LINE.match(line)
        if not m or not m[5]:
            continue                                  # hidden networks are not listed
        bssid = m[1].lower()
        rows[bssid] = {'bssid': bssid, 'frequency': int(m[2]), 'rssi': int(m[3]), 'age': float(m[4]),
                       'ssid': m[5], 'flags': m[6]}
    return list(rows.values())


def parse_saved(text):
    """`cmd wifi list-networks`: saved networks by Android id; one id may list several security types."""
    saved = {}
    for line in (text or '').splitlines():
        m = SAVED_LINE.match(line.strip())
        if m:
            entry = saved.setdefault(int(m[1]), {'id': int(m[1]), 'ssid': m[2], 'security': []})
            entry['security'].append(m[3].rstrip('^'))
    return list(saved.values())


def scan_security(flags):
    """Flags of a scan result -> (Android security for connect-network, NM Flags, WpaFlags, RsnFlags).
    Key management: PSK 0x100, 802.1X 0x200, SAE 0x400, OWE 0x800; ciphers CCMP 0x8 (pair), 0x80 (group)."""
    wpa = rsn = 0
    for entry in re.findall(r'\[([^\]]*)\]', flags):
        mgmt = (0x100 if 'PSK' in entry else 0) | (0x200 if 'EAP' in entry else 0) | \
               (0x400 if 'SAE' in entry else 0) | (0x800 if 'OWE' in entry else 0)
        if not mgmt:
            continue
        if entry.startswith('WPA-'):
            wpa |= mgmt | 0x88
        else:
            rsn |= mgmt | 0x88
    keys = wpa | rsn
    if keys & 0x200 and not keys & 0x500:
        security = 'eap'                               # enterprise: joined in Android's settings
    elif keys & 0x100:
        security = 'wpa2'
    elif keys & 0x400:
        security = 'wpa3'
    elif keys & 0x800:
        security = 'owe'
    else:
        security = 'open'
    return security, (1 if keys and security != 'owe' else 0), wpa, rsn


def rssi_strength(rssi):
    return max(0, min(100, round((rssi + 100) * 100 / 45)))


def ap_path(bssid_or_ssid):
    return BASE + '/AccessPoint/' + ident(bssid_or_ssid)


def saved_path(entry):
    return BASE + '/Settings/saved' + str(entry['id'])


def saved_profile(entry):
    security = entry['security'][0] if entry['security'] else 'open'
    key_mgmt = {'wpa2-psk': 'wpa-psk', 'wpa-psk': 'wpa-psk', 'wpa3-sae': 'sae', 'sae': 'sae', 'owe': 'owe',
                'wpa2-enterprise': 'wpa-eap', 'wpa3-enterprise': 'wpa-eap'}.get(security, 'none')
    uid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'android-wifi-saved:%d:%s' % (entry['id'], entry['ssid'])))
    settings = {'connection': props(id=('s', entry['ssid']), uuid=('s', uid), type=('s', '802-11-wireless'),
                                   autoconnect=('b', True)),
                '802-11-wireless': props(ssid=('ay', list(entry['ssid'].encode())), mode=('s', 'infrastructure')),
                'ipv4': props(method=('s', 'auto')), 'ipv6': props(method=('s', 'auto'))}
    if key_mgmt != 'none':
        settings['802-11-wireless']['security'] = V('s', '802-11-wireless-security')
        settings['802-11-wireless-security'] = props(**{'key-mgmt': ('s', key_mgmt)})
    return settings


MODEM_PATH = '/org/freedesktop/ModemManager1/Modem/0'   # shared/platform/modem-manager.py
MOBILE = BASE + '/Settings/modem'
STARTED = int(time.time())


def mobile_profile(telephony):
    """Android's mobile data as one gsm connection: autoconnect is Android's data switch, home-only its
    data roaming switch. The APN stays Android's."""
    operator = telephony.get('simOperator', '')
    return {'connection': props(id=('s', telephony.get('simOperatorName') or 'Mobile data'), type=('s', 'gsm'),
                                uuid=('s', str(uuid.uuid5(uuid.NAMESPACE_URL, 'android-mobile:' + operator))),
                                autoconnect=('b', bool(telephony.get('dataEnabled'))), timestamp=('t', STARTED)),
            'gsm': props(apn=('s', ''), **{'home-only': ('b', not telephony.get('dataRoaming'))}),
            'ipv4': props(method=('s', 'auto')), 'ipv6': props(method=('s', 'auto'))}


def make_graph(snapshot):
    """Build a complete snapshot before publishing any changes on the bus."""
    available = snapshot is not None
    snapshot = snapshot or {}
    rows = snapshot.get('networks', [])
    wifi = next((r for r in rows if r['kind'] == 'wifi'), None)
    default = next((r for r in rows if r.get('default')), None)
    enabled = bool(snapshot.get('wifiEnabled', False))
    connectivity = (4 if default.get('validated') else 2 if default.get('captive') else 3) if default else (1 if available else 0)
    state = (70 if default.get('validated') else 50) if default else (20 if available else 0)
    graph, settings = {}, {}
    devices, connections, active = [], [], []
    # Wi-Fi scan results and Android's saved networks (docs/73), fetched next to the snapshot.
    scan = snapshot.get('scan', []) if enabled else []
    saved = snapshot.get('saved', [])
    saved_by_ssid = {entry['ssid']: entry for entry in saved}
    for entry in saved:
        path = saved_path(entry)
        settings[path] = saved_profile(entry)
        graph[path] = {NM + '.Settings.Connection': props(Unsaved=('b', False), Flags=('u', 0), Filename=('s', ''))}
        connections.append(path)
    aps = {}
    for r in scan:
        security, flags, wpa, rsn = scan_security(r['flags'])
        aps[ap_path(r['bssid'])] = {NM + '.AccessPoint': props(Flags=('u', flags), WpaFlags=('u', wpa), RsnFlags=('u', rsn),
            Ssid=('ay', list(r['ssid'].encode())), Frequency=('u', r['frequency']), HwAddress=('s', r['bssid'].upper()),
            Mode=('u', 2), MaxBitrate=('u', 0), Strength=('y', rssi_strength(r['rssi'])), LastSeen=('i', -1),
            Bandwidth=('u', 0))}
    graph.update(aps)
    scanned_ssids = {r['ssid'] for r in scan}
    # Android's data subscription as a modem device (docs/73) while Android reports a modem.
    telephony = snapshot.get('telephony') or {}
    modem = bool(telephony.get('modem'))
    sim_ready = modem and telephony.get('sim') == 'ready'
    registered = sim_ready and telephony.get('service') == 0
    if sim_ready:
        settings[MOBILE] = mobile_profile(telephony)
        graph[MOBILE] = {NM + '.Settings.Connection': props(Unsaved=('b', False), Flags=('u', 0), Filename=('s', ''))}
        connections.append(MOBILE)
    primary = '/'
    primary_type = ''
    # Always retain the real Wi-Fi device when disabled; other objects exist
    # only while Android reports those actual networks. No synthetic Ethernet.
    inputs = [('wifi', wifi)]
    cellular = next((r for r in rows if r['kind'] == 'cellular'), None) if modem else None
    if modem:
        inputs.append(('modem', cellular))
    inputs += [(ident(r['interface']), r) for r in rows if r['kind'] != 'wifi' and r is not cellular]
    for token, row in inputs:
        is_wifi = token == 'wifi'
        is_modem = token == 'modem'
        device = BASE + '/Devices/' + token
        devices.append(device)
        iface = row['interface'] if row else 'wlan0'
        dev_state = 100 if row else (30 if enabled and available else 20)
        if is_modem:
            iface = 'android-modem'
            dev_state = 100 if row else 30 if registered else 20
        device_type = 2 if is_wifi else 8 if is_modem else 1 if row['kind'] == 'ethernet' else 14
        ac = BASE + '/ActiveConnection/' + token if row else '/'
        conn = BASE + '/Settings/' + token if row else '/'
        ip4, ip6, ap = '/', '/', '/'
        hw = row.get('mac', '') if row else ''
        dev_connectivity = (4 if row.get('validated') else 2 if row.get('captive') else 3) if row else 1
        known = saved_by_ssid.get(row.get('ssid')) if (row and is_wifi) else None
        if known:
            conn = saved_path(known)                  # the connection is Android's saved network
        if is_modem and sim_ready:
            conn = MOBILE                             # the connection is Android's mobile data
        if row:
            name, uid, kind, profile_data = profile(row, is_wifi)
            if is_modem and sim_ready:
                profile_data = settings[conn]
                name, uid, kind = profile_data['connection']['id'].unpack(), profile_data['connection']['uuid'].unpack(), 'gsm'
            elif known:
                profile_data = settings[conn]
                uid = profile_data['connection']['uuid'].unpack()
            else:
                settings[conn] = profile_data
                connections.append(conn)
            active.append(ac)
            for version in (4, 6):
                config = ip_config(row, version)
                if config['AddressData'].n_children():
                    path = BASE + '/IP%dConfig/' % version + token
                    graph[path] = {NM + '.IP%dConfig' % version: config}
                    if version == 4:
                        ip4 = path
                    else:
                        ip6 = path
            sec = security_flags(row.get('security', -1))
            if is_wifi and row.get('ssid') and sec is not None:
                ap = ap_path((row.get('bssid') or '').lower() or row['ssid'])
                rssi = row.get('rssi', -100)
                strength = max(0, min(100, round((rssi + 100) * 100 / 45)))
                graph[ap] = {NM + '.AccessPoint': props(Flags=('u', sec[0]), WpaFlags=('u', 0), RsnFlags=('u', sec[1]),
                    Ssid=('ay', list(row['ssid'].encode())), Frequency=('u', max(0, row.get('frequency', 0))),
                    HwAddress=('s', row.get('bssid', '')), Mode=('u', 2), MaxBitrate=('u', max(0, row.get('linkMbps', 0)) * 1000),
                    Strength=('y', strength), LastSeen=('i', int(time.monotonic())))}
            if not known and conn not in graph:
                graph[conn] = {NM + '.Settings.Connection': props(Unsaved=('b', True), Flags=('u', 3), Filename=('s', ''))}
            graph[ac] = {NM + '.Connection.Active': props(Connection=('o', conn), SpecificObject=('o', ap), Id=('s', name),
                Uuid=('s', uid), Type=('s', kind), Devices=('ao', [device]), State=('u', 2),
                StateFlags=('u', 128 | 4 | (8 if ip4 != '/' else 0) | (16 if ip6 != '/' else 0)),
                Default=('b', bool(row.get('default') and ip4 != '/' and graph[ip4][NM + '.IP4Config']['Gateway'].unpack())),
                Default6=('b', bool(row.get('default') and ip6 != '/' and graph[ip6][NM + '.IP6Config']['Gateway'].unpack())),
                Ip4Config=('o', ip4), Ip6Config=('o', ip6), Dhcp4Config=('o', '/'), Dhcp6Config=('o', '/'),
                Vpn=('b', False), Controller=('o', '/'), Master=('o', '/'))}
            if row.get('default'):
                primary, primary_type = ac, kind
        graph[device] = {NM + '.Device': props(Udi=('s', 'android:' + iface), Interface=('s', iface), IpInterface=('s', iface if row else ''),
            Driver=('s', 'android'), DriverVersion=('s', ''), FirmwareVersion=('s', ''), Capabilities=('u', 1),
            State=('u', dev_state), StateReason=('(uu)', (dev_state, 0)), DeviceType=('u', device_type),
            Managed=('b', available), Autoconnect=('b', False), Real=('b', True), HwAddress=('s', hw),
            Ip4Config=('o', ip4), Ip6Config=('o', ip6), Dhcp4Config=('o', '/'), Dhcp6Config=('o', '/'),
            ActiveConnection=('o', ac),
            AvailableConnections=('ao', ([conn] if row else []) + ([saved_path(e) for e in saved if e['ssid'] in scanned_ssids
                                                                     and saved_path(e) != conn] if is_wifi else [])),
            FirmwareMissing=('b', False), NmPluginMissing=('b', False), Mtu=('u', max(0, row.get('mtu', 0)) if row else 0),
            Metered=('u', (1 if row.get('metered') else 2) if row else 0),
            Ip4Connectivity=('u', dev_connectivity if ip4 != '/' else 1), Ip6Connectivity=('u', dev_connectivity if ip6 != '/' else 1))}
        if is_modem:
            # NMDeviceStateReason: 29 SIM not inserted, 30 PIN required, 31 PUK required.
            reason = {'absent': 29, 'pin': 30, 'puk': 31}.get(telephony.get('sim'), 0)
            graph[device][NM + '.Device'].update(props(Udi=('s', MODEM_PATH), IpInterface=('s', row['interface'] if row else ''),
                StateReason=('(uu)', (dev_state, reason)), Autoconnect=('b', bool(telephony.get('dataEnabled'))),
                AvailableConnections=('ao', [MOBILE] if sim_ready else [])))
            # NMDeviceModemCapabilities: GSM/UMTS, LTE, 5GNR.
            graph[device][NM + '.Device.Modem'] = props(ModemCapabilities=('u', 0x4 | 0x8 | 0x40),
                CurrentCapabilities=('u', 0x4 | 0x8 | 0x40), DeviceId=('s', ''), Apn=('s', ''),
                OperatorCode=('s', telephony.get('operator', '') if registered else ''))
        elif is_wifi:
            # Encryption and known frequency bands; deliberately no AP/hotspot capability.
            caps = 0x28 | 0x100 | 0x200
            if snapshot.get('wifi5GHz'):
                caps |= 0x400
            if snapshot.get('wifi6GHz'):
                caps |= 0x800
            graph[device][NM + '.Device.Wireless'] = props(HwAddress=('s', hw), PermHwAddress=('s', ''), Mode=('u', 2 if row else 0),
                Bitrate=('u', max(0, row.get('linkMbps', 0)) * 1000 if row else 0), WirelessCapabilities=('u', caps),
                AccessPoints=('ao', list(aps) + ([ap] if ap != '/' and ap not in aps else [])), ActiveAccessPoint=('o', ap),
                LastScan=('x', snapshot.get('lastScan', -1)))
        elif device_type == 1:
            graph[device][NM + '.Device.Wired'] = props(HwAddress=('s', hw), PermHwAddress=('s', ''), Speed=('u', 0),
                S390Subchannels=('as', []), Carrier=('b', True))
        else:
            graph[device][NM + '.Device.Generic'] = props(TypeDescription=('s', 'Android ' + row['kind']))
    graph[BASE] = {NM: props(Devices=('ao', devices), AllDevices=('ao', devices), ActiveConnections=('ao', active),
        PrimaryConnection=('o', primary), PrimaryConnectionType=('s', primary_type), ActivatingConnection=('o', '/'),
        State=('u', state), Startup=('b', False), Version=('s', '1.54.0-android-bridge.1'), Capabilities=('au', []),
        NetworkingEnabled=('b', available), WirelessEnabled=('b', enabled), WirelessHardwareEnabled=('b', available),
        WwanEnabled=('b', modem and telephony.get('service') != 3), WwanHardwareEnabled=('b', modem),
        Connectivity=('u', connectivity),
        ConnectivityCheckAvailable=('b', False), ConnectivityCheckEnabled=('b', False), ConnectivityCheckUri=('s', ''),
        Metered=('u', (1 if default.get('metered') else 2) if default else 0)),
        HOST: props(BackendAvailable=('b', available), Manager=('s', 'Android'), Version=('s', '1'),
                    DefaultTransport=('s', default['kind'] if default else ''), ConfigurationReadOnly=('b', False))}
    graph[BASE + '/Settings'] = {NM + '.Settings': props(Connections=('ao', connections), Hostname=('s', 'android'), CanModify=('b', True))}
    graph[BASE + '/AgentManager'] = {NM + '.AgentManager': {}}
    return graph, settings


# Signatures are a deliberately bounded part of the public NM protocol.
METHODS = {
    NM: {'GetDevices': ('', 'ao'), 'GetAllDevices': ('', 'ao'), 'GetDeviceByIpIface': ('s', 'o'),
         'GetPermissions': ('', 'a{ss}'), 'CheckConnectivity': ('', 'u'), 'state': ('', 'u'),
         'ActivateConnection': ('ooo', 'o'), 'AddAndActivateConnection': ('a{sa{sv}}oo', 'oo'),
         'AddAndActivateConnection2': ('a{sa{sv}}ooa{sv}', 'ooa{sv}'), 'DeactivateConnection': ('o', ''),
         'Enable': ('b', ''), 'Sleep': ('b', ''), 'Reload': ('u', '')},
    NM + '.Device': {'Disconnect': ('', ''), 'Delete': ('', '')},
    NM + '.Device.Wireless': {'GetAccessPoints': ('', 'ao'), 'GetAllAccessPoints': ('', 'ao'), 'RequestScan': ('a{sv}', '')},
    NM + '.Settings': {'ListConnections': ('', 'ao'), 'GetConnectionByUuid': ('s', 'o'),
                      'AddConnection': ('a{sa{sv}}', 'o'), 'AddConnectionUnsaved': ('a{sa{sv}}', 'o'),
                      'SaveHostname': ('s', ''), 'ReloadConnections': ('', 'b')},
    NM + '.Settings.Connection': {'GetSettings': ('', 'a{sa{sv}}'), 'GetSecrets': ('s', 'a{sa{sv}}'),
                                  'Update': ('a{sa{sv}}', ''), 'UpdateUnsaved': ('a{sa{sv}}', ''),
                                  'Update2': ('a{sa{sv}}ua{sv}', 'a{sv}'), 'Delete': ('', ''), 'Save': ('', '')},
    NM + '.AgentManager': {'Register': ('s', ''), 'RegisterWithCapabilities': ('su', ''), 'Unregister': ('', '')},
    HOST: {'OpenSettings': ('', '')},
    OM: {'GetManagedObjects': ('', 'a{oa{sa{sv}}}')},
}
SIGNALS = {NM: {'StateChanged': 'u', 'DeviceAdded': 'o', 'DeviceRemoved': 'o', 'CheckPermissions': ''},
    NM + '.Device': {'StateChanged': 'uuu'}, NM + '.Device.Wireless': {'AccessPointAdded': 'o', 'AccessPointRemoved': 'o'},
    NM + '.Settings': {'NewConnection': 'o', 'ConnectionRemoved': 'o'}, NM + '.Settings.Connection': {'Updated': '', 'Removed': ''},
    OM: {'InterfacesAdded': 'oa{sa{sv}}', 'InterfacesRemoved': 'oas'}}


def signature_parts(signature):
    def end(pos):
        if signature[pos] == 'a':
            return end(pos + 1)
        if signature[pos] in '({':
            closing = ')' if signature[pos] == '(' else '}'
            pos += 1
            while signature[pos] != closing:
                pos = end(pos)
            return pos + 1
        return pos + 1
    pos = 0
    while pos < len(signature):
        next_pos = end(pos)
        yield signature[pos:next_pos]
        pos = next_pos


def introspection(interface, properties):
    xml = '<node><interface name="' + interface + '">'
    for key, value in properties.items():
        writable = (interface == NM and key in ('WirelessEnabled', 'WwanEnabled')) or (interface == NM + '.Device' and key == 'Autoconnect')
        access = 'readwrite' if writable else 'read'
        xml += '<property name="%s" type="%s" access="%s"/>' % (key, escape(value.get_type_string()), access)
    for method, (ins, outs) in METHODS.get(interface, {}).items():
        xml += '<method name="' + method + '">'
        for direction, sig in [('in', ins), ('out', outs)]:
            for t in signature_parts(sig):
                xml += '<arg type="%s" direction="%s"/>' % (escape(t), direction)
        xml += '</method>'
    for name, sig in SIGNALS.get(interface, {}).items():
        xml += '<signal name="' + name + '">'
        for t in signature_parts(sig):
            xml += '<arg type="%s"/>' % escape(t)
        xml += '</signal>'
    return Gio.DBusNodeInfo.new_for_xml(xml + '</interface></node>').interfaces[0]


class Bridge:
    def __init__(self, bus):
        self.bus = bus
        self.graph, self.settings = {}, {}
        self.registrations = {}
        self.pool = concurrent.futures.ThreadPoolExecutor(2, thread_name_prefix='android-network')
        self.polling = False
        self.alive = True
        self.online = None
        self.resolv_result = None
        self.agents = set()
        # Wi-Fi scan results and saved networks: refreshed every WIFI_REFRESH seconds, and at once after a
        # scan, connect or forget (refresh_wifi).
        self.wifi = {'scan': [], 'saved': [], 'lastScan': -1}
        self.wifi_due = 0.0
        bus.register_object(OM_PATH, introspection(OM, {}), self.call, None, None)
        self.publish(None)

    def emit(self, path, interface, signal_name, sig, values):
        self.bus.emit_signal(None, path, interface, signal_name, V('(' + sig + ')', values))

    def publish(self, snapshot):
        try:
            graph, settings = make_graph(snapshot)
        except (KeyError, TypeError, ValueError):
            LOG.warning('Invalid Android network snapshot')
            graph, settings = make_graph(None)
            snapshot = None
        # Only a snapshot says what Android's network is: an unreachable Android side changes nothing.
        if snapshot is not None:
            result = sync_resolv_conf(snapshot)
            if result == 'written':
                LOG.info('%s set from the Android default network', RESOLV_CONF)
            elif result != self.resolv_result and result == 'kept':
                LOG.info('%s was edited by hand (no "%s" line): not managing it', RESOLV_CONF, RESOLV_MARKER)
            elif result != self.resolv_result and result == 'failed':
                LOG.warning('Cannot write %s', RESOLV_CONF)
            self.resolv_result = result
        old, old_settings = self.graph, self.settings
        self.graph, self.settings = graph, settings
        online = snapshot is not None
        if online != self.online:
            LOG.info('Android backend %s', 'available' if online else 'unavailable')
            self.online = online
        for path, interfaces in graph.items():
            added = {}
            for iface, values in interfaces.items():
                if (path, iface) not in self.registrations:
                    self.registrations[path, iface] = self.bus.register_object(path, introspection(iface, values), self.call, self.get, self.set)
                    added[iface] = values
                elif values != old.get(path, {}).get(iface, {}):
                    delta = {key: val for key, val in values.items() if val != old[path][iface].get(key)}
                    self.emit(path, PROPS, 'PropertiesChanged', 'sa{sv}as', (iface, delta, []))
            if added:
                self.emit(OM_PATH, OM, 'InterfacesAdded', 'oa{sa{sv}}', (path, added))
        for path, interfaces in old.items():
            removed = [iface for iface in interfaces if iface not in graph.get(path, {})]
            if removed:
                self.emit(OM_PATH, OM, 'InterfacesRemoved', 'oas', (path, removed))
                for iface in removed:
                    self.bus.unregister_object(self.registrations.pop((path, iface)))
        if old:
            before, after = old[BASE][NM]['State'].unpack(), graph[BASE][NM]['State'].unpack()
            if before != after:
                self.emit(BASE, NM, 'StateChanged', 'u', (after,))
            for prop, iface, added, removed in [('Devices', NM, 'DeviceAdded', 'DeviceRemoved')]:
                a, b = set(old[BASE][NM][prop].unpack()), set(graph[BASE][NM][prop].unpack())
                for path in b - a:
                    self.emit(BASE, iface, added, 'o', (path,))
                for path in a - b:
                    self.emit(BASE, iface, removed, 'o', (path,))
            for path, interfaces in graph.items():
                if NM + '.Device' in interfaces and path in old:
                    before = old[path][NM + '.Device']['State'].unpack()
                    after = interfaces[NM + '.Device']['State'].unpack()
                    if before != after:
                        self.emit(path, NM + '.Device', 'StateChanged', 'uuu', (after, before, 0))
                wifi = NM + '.Device.Wireless'
                if wifi in interfaces and wifi in old.get(path, {}):
                    a, b = set(old[path][wifi]['AccessPoints'].unpack()), set(interfaces[wifi]['AccessPoints'].unpack())
                    for ap in b - a:
                        self.emit(path, wifi, 'AccessPointAdded', 'o', (ap,))
                    for ap in a - b:
                        self.emit(path, wifi, 'AccessPointRemoved', 'o', (ap,))
            for path in settings.keys() - old_settings.keys():
                self.emit(BASE + '/Settings', NM + '.Settings', 'NewConnection', 'o', (path,))
            for path in old_settings.keys() - settings.keys():
                self.emit(path, NM + '.Settings.Connection', 'Removed', '', ())
                self.emit(BASE + '/Settings', NM + '.Settings', 'ConnectionRemoved', 'o', (path,))
            for path in settings.keys() & old_settings.keys():
                if settings[path] != old_settings[path]:
                    self.emit(path, NM + '.Settings.Connection', 'Updated', '', ())
        return False

    def poll(self):
        if not self.polling:
            self.polling = True
            def work():
                try:
                    data = host_request(op='network-get')
                    if data.get('version') != 1:
                        raise ValueError('Unsupported host protocol')
                except (OSError, ValueError):
                    data = None
                if data is not None and time.monotonic() >= self.wifi_due:
                    self.wifi_due = time.monotonic() + WIFI_REFRESH
                    try:
                        self.wifi['saved'] = parse_saved(host_request(op='wifi', action='saved')['text'])
                        if data.get('wifiEnabled'):
                            self.wifi['scan'] = parse_scan(host_request(op='wifi', action='scan-results')['text'])
                            self.wifi['lastScan'] = int(time.clock_gettime(time.CLOCK_BOOTTIME) * 1000)
                    except (OSError, ValueError, KeyError):
                        LOG.warning('Android Wi-Fi lists unavailable')
                if data is not None:
                    data.update(self.wifi)
                    try:
                        data['telephony'] = host_request(op='telephony', action='state')
                    except (OSError, ValueError):
                        data['telephony'] = None
                def done():
                    self.polling = False
                    if self.alive:
                        self.publish(data)
                    return False
                GLib.idle_add(done)
            self.pool.submit(work)
        return GLib.SOURCE_CONTINUE

    def saved_entry(self, path):
        return next((e for e in self.wifi['saved'] if saved_path(e) == path), None)

    def refresh_wifi(self):
        self.wifi_due = 0.0
        self.poll()
        return False

    def wifi_action(self, invocation, request, result, timeout=10, later=(2, 6)):
        """An Android Wi-Fi request off the main loop; the reply (result() -> (signature, *values)) and
        the refreshed lists follow on it."""
        def work():
            try:
                host_request(timeout=timeout, op='wifi', **request)
                error = None
            except (OSError, ValueError) as e:
                error = str(e)
            def done():
                if error:
                    invocation.return_dbus_error(NM + '.Failed', error)
                else:
                    sig, *values = result()
                    invocation.return_value(V('(' + sig + ')', tuple(values)))
                for seconds in later:
                    GLib.timeout_add_seconds(seconds, self.refresh_wifi)
                self.refresh_wifi()
                return False
            GLib.idle_add(done)
        self.pool.submit(work)

    def add_and_activate(self, invocation, method, args):
        """A new network from the Wi-Fi settings: SSID from the settings or the access point, the key
        management and passphrase from 802-11-wireless-security. Android saves it (connect-network)."""
        settings, device, specific = args[0], args[1], args[2]
        wireless = settings.get('802-11-wireless', {})
        ssid = bytes(wireless.get('ssid', b'')).decode(errors='replace')
        ap = self.graph.get(specific, {}).get(NM + '.AccessPoint')
        if not ssid and ap:
            ssid = bytes(ap['Ssid'].unpack()).decode(errors='replace')
        security_settings = settings.get('802-11-wireless-security', {})
        key_mgmt = security_settings.get('key-mgmt', 'none')
        passphrase = security_settings.get('psk', '')
        if key_mgmt == 'wpa-psk':
            # An access point offering only SAE is WPA3.
            only_sae = ap and not (ap['RsnFlags'].unpack() | ap['WpaFlags'].unpack()) & 0x100 and ap['RsnFlags'].unpack() & 0x400
            security = 'wpa3' if only_sae else 'wpa2'
        else:
            security = {'sae': 'wpa3', 'owe': 'owe', 'none': 'open'}.get(key_mgmt)
        if not ssid or security is None:
            invocation.return_dbus_error(NM + '.NotSupported', 'Only personal (PSK/SAE), OWE and open Wi-Fi networks '
                                         'are joined here; use Android settings for others.')
            return
        if security in ('wpa2', 'wpa3') and not passphrase:
            invocation.return_dbus_error(NM + '.NoSecrets', 'A password is required')
            return

        def result():
            entry = next((e for e in self.wifi['saved'] if e['ssid'] == ssid), None)
            conn = saved_path(entry) if entry else BASE + '/Settings/wifi'
            values = (conn, BASE + '/ActiveConnection/wifi')
            return ('ooa{sv}', *values, {}) if method == 'AddAndActivateConnection2' else ('oo', *values)

        def work():
            try:
                host_request(timeout=25, op='wifi', action='connect', ssid=ssid, security=security, passphrase=passphrase)
                self.wifi['saved'] = parse_saved(host_request(op='wifi', action='saved')['text'])
                error = None
            except (OSError, ValueError, KeyError) as e:
                error = str(e)
            def done():
                if error:
                    invocation.return_dbus_error(NM + '.Failed', error)
                else:
                    sig, *values = result()
                    invocation.return_value(V('(' + sig + ')', tuple(values)))
                for seconds in (2, 6):
                    GLib.timeout_add_seconds(seconds, self.refresh_wifi)
                self.refresh_wifi()
                return False
            GLib.idle_add(done)
        self.pool.submit(work)

    def mobile_data(self, invocation, on, result=lambda: ('',)):
        """Android's mobile data switch (svc data) off the main loop; invocation may be None."""
        def work():
            try:
                host_request(timeout=15, op='telephony', action='data', on=on)
                error = None
            except (OSError, ValueError) as e:
                error = str(e)
            def done():
                if invocation and error:
                    invocation.return_dbus_error(NM + '.Failed', error)
                elif invocation:
                    sig, *values = result()
                    invocation.return_value(V('(' + sig + ')', tuple(values)))
                for seconds in (2, 6):
                    GLib.timeout_add_seconds(seconds, lambda: (self.poll(), False)[1])
                self.poll()
                return False
            GLib.idle_add(done)
        self.pool.submit(work)

    def get(self, bus, sender, path, interface, name):
        return self.graph.get(path, {}).get(interface, {}).get(name)

    def set(self, bus, sender, path, interface, name, value):
        if path == BASE + '/Devices/modem' and interface == NM + '.Device' and name == 'Autoconnect':
            # Plasma Mobile's mobile data switch: autoconnect off turns Android's mobile data off; on is
            # followed by activating the mobile data connection.
            if not value.unpack():
                self.mobile_data(None, False)
            return True
        if path == BASE and interface == NM and name == 'WwanEnabled':
            return bool(value.unpack())               # the radio stays on; airplane mode is Android's
        if path != BASE or interface != NM or name != 'WirelessEnabled':
            return False
        try:
            result = host_request(op='network-wifi', enabled=value.unpack())
            if not result.get('accepted'):
                return False
            self.poll()
            return True  # accepted only; published state always comes from Android
        except (OSError, ValueError):
            return False

    def call(self, bus, sender, path, interface, method, args, invocation):
        def reply(sig='', *values):
            invocation.return_value(V('(' + sig + ')', values))
        def unsupported():
            invocation.return_dbus_error(NM + '.NotSupported', 'This connection is managed by Android. Use Android network settings; Linux profiles are read-only.')
        try:
            if interface == OM and method == 'GetManagedObjects':
                return reply('a{oa{sa{sv}}}', self.graph)
            if interface == HOST and method == 'OpenSettings':
                def work():
                    try:
                        host_request(op='settings', target='network')
                        GLib.idle_add(lambda: (reply(), False)[1])
                    except (OSError, ValueError):
                        GLib.idle_add(lambda: (invocation.return_dbus_error(HOST + '.Unavailable', 'Return to the Linux desktop and try again.'), False)[1])
                self.pool.submit(work)
                return
            if interface == NM:
                if method in ('GetDevices', 'GetAllDevices'):
                    return reply('ao', self.graph[BASE][NM]['Devices'].unpack())
                if method == 'GetDeviceByIpIface':
                    for p, interfaces in self.graph.items():
                        if NM + '.Device' in interfaces and interfaces[NM + '.Device']['Interface'].unpack() == args.unpack()[0]:
                            return reply('o', p)
                    invocation.return_dbus_error(NM + '.UnknownDevice', 'No such Android interface')
                    return
                if method == 'GetPermissions':
                    permissions = {NM + '.' + s: 'no' for s in ('enable-disable-network', 'enable-disable-wifi', 'enable-disable-wwan',
                        'sleep-wake', 'network-control', 'wifi.share.protected', 'wifi.share.open', 'settings.modify.system',
                        'settings.modify.own', 'settings.modify.hostname', 'settings.modify.global-dns', 'reload', 'checkpoint-rollback',
                        'enable-disable-statistics', 'enable-disable-connectivity-check', 'wifi.scan')}
                    for name in ('enable-disable-wifi', 'enable-disable-wwan', 'network-control', 'wifi.scan', 'settings.modify.own',
                                 'settings.modify.system'):
                        permissions[NM + '.' + name] = 'yes'
                    return reply('a{ss}', permissions)
                if method in ('state', 'CheckConnectivity'):
                    return reply('u', self.graph[BASE][NM]['State' if method == 'state' else 'Connectivity'].unpack())
                if method == 'ActivateConnection' and args.unpack()[0] in self.settings:
                    # Returning an already active connection performs no mutation.
                    for p, ifaces in self.graph.items():
                        ac = ifaces.get(NM + '.Connection.Active', {})
                        if ac.get('Connection') == V('o', args.unpack()[0]):
                            return reply('o', p)
            if interface == NM + '.Device.Wireless':
                if method in ('GetAccessPoints', 'GetAllAccessPoints'):
                    return reply('ao', self.graph[path][interface]['AccessPoints'].unpack())
                if method == 'RequestScan':
                    return self.wifi_action(invocation, {'action': 'scan'}, lambda: ('',), later=(3, 8))
            if interface == NM and method in ('AddAndActivateConnection', 'AddAndActivateConnection2'):
                return self.add_and_activate(invocation, method, args.unpack())
            if interface == NM and method == 'ActivateConnection':
                conn = args.unpack()[0]
                entry = self.saved_entry(conn)
                if entry:
                    return self.wifi_action(invocation, {'action': 'activate', 'id': entry['id']},
                                            lambda: ('o', BASE + '/ActiveConnection/wifi'), timeout=25)
            if interface == NM and method == 'ActivateConnection' and args.unpack()[0] == MOBILE and MOBILE in self.settings:
                return self.mobile_data(invocation, True, lambda: ('o', BASE + '/ActiveConnection/modem'))
            if (interface == NM and method == 'DeactivateConnection' and args.unpack()[0] == BASE + '/ActiveConnection/modem') or \
               (interface == NM + '.Device' and method == 'Disconnect' and path == BASE + '/Devices/modem'):
                return self.mobile_data(invocation, False)
            if interface == NM + '.Settings.Connection' and path == MOBILE and MOBILE in self.settings and \
               method in ('Update', 'UpdateUnsaved', 'Update2'):
                new = args.unpack()[0]
                current = self.settings[MOBILE]
                # Update replaces the settings: absent keys take NetworkManager's defaults.
                home_only = new.get('gsm', {}).get('home-only', False)
                if home_only != current['gsm']['home-only'].unpack():
                    return invocation.return_dbus_error(NM + '.Settings.Connection.NotSupported', 'Data roaming is set in Android settings')
                if new.get('gsm', {}).get('apn', '') != current['gsm']['apn'].unpack():
                    return invocation.return_dbus_error(NM + '.Settings.Connection.NotSupported', 'The APN is set in Android settings')
                autoconnect = new.get('connection', {}).get('autoconnect', True)
                done = (lambda: ('a{sv}', {})) if method == 'Update2' else (lambda: ('',))
                if autoconnect == current['connection']['autoconnect'].unpack():
                    sig, *values = done()
                    return invocation.return_value(V('(' + sig + ')', tuple(values)))
                return self.mobile_data(invocation, autoconnect, done)
            if (interface == NM and method == 'DeactivateConnection' and args.unpack()[0] == BASE + '/ActiveConnection/wifi') or \
               (interface == NM + '.Device' and method == 'Disconnect' and path == BASE + '/Devices/wifi'):
                return self.wifi_action(invocation, {'action': 'disconnect'}, lambda: ('',), timeout=25)
            if interface == NM + '.Settings.Connection' and method == 'Delete' and self.saved_entry(path):
                return self.wifi_action(invocation, {'action': 'forget', 'id': self.saved_entry(path)['id']}, lambda: ('',))
            if interface == NM + '.Settings':
                if method == 'ListConnections':
                    return reply('ao', list(self.settings))
                if method == 'GetConnectionByUuid':
                    for p, s in self.settings.items():
                        if s['connection']['uuid'].unpack() == args.unpack()[0]:
                            return reply('o', p)
                    invocation.return_dbus_error(NM + '.Settings.InvalidConnection', 'No such active Android connection')
                    return
            if interface == NM + '.Settings.Connection':
                if method == 'GetSettings':
                    return reply('a{sa{sv}}', self.settings[path])
                if method == 'GetSecrets':
                    return reply('a{sa{sv}}', {})  # credentials are never read or cached
            if interface == NM + '.AgentManager':
                if method in ('Register', 'RegisterWithCapabilities'):
                    self.agents.add(sender)
                    return reply()
                if method == 'Unregister':
                    self.agents.discard(sender)
                    return reply()
            unsupported()
        except Exception:
            LOG.exception('D-Bus method failed: %s', method)
            invocation.return_dbus_error(HOST + '.Failed', 'Android network bridge request failed')


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    # Objects first: a client enumerating as soon as the name appears finds them.
    bridge = Bridge(bus)
    # Never replace a real NetworkManager or another bridge instance.
    result = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'RequestName',
                          V('(su)', (NM, 4)), V('(u)', (0,)).get_type(), Gio.DBusCallFlags.NONE, 3000, None)
    if result.unpack()[0] != 1:
        raise SystemExit('NetworkManager bus name is already owned')
    loop = GLib.MainLoop()
    def stop():
        bridge.alive = False
        loop.quit()
        return False
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, stop)
    bridge.poll()
    # Asked again when Android reports a network or mobile change, not every 2 s (docs/49).
    rungic_host_watch.watch(('network', 'telephony'), lambda: GLib.idle_add(lambda: bridge.poll() and False),
                            legacy=2, name='network-watch')
    try:
        loop.run()
    finally:
        bridge.alive = False
        bridge.pool.shutdown(wait=True, cancel_futures=True)


if __name__ == '__main__':
    main()
