#!/usr/bin/python3
"""Android-owned device capabilities for the Plasma session (private Unix IPC)."""
import os
import configparser
import gettext
import json
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import rungic_platform_transport

SOCKET = os.environ.get('RUNGIC_PLATFORM_SOCKET', '/mnt/android-wayland/platform.sock')
# Follows the Plasma desktop language (LANGUAGE/LANG of the session).
_translation = gettext.translation('rungic-platform', localedir='/usr/share/locale', fallback=True)
_, pgettext = _translation.gettext, _translation.pgettext

def request(data):
    result = rungic_platform_transport.request(data, 4, ui_socket=SOCKET, limit=65536)
    if 'error' in result:
        raise RuntimeError(result['error'])
    return result

if len(sys.argv) > 1 and sys.argv[1] == '--request':
    print(json.dumps(request(json.loads(sys.argv[2])), ensure_ascii=False, indent=2))
    raise SystemExit()

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, GLib

class DeviceApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.rungic.Platform')
        self.connect('activate', self.activate)
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.rows = {}
        self.window = None
        self.busy = False
        self.changing = False
        self.brightness_pending = 0

    def activate(self, app):
        if self.window:
            self.window.present()
            return
        self.window = Adw.PreferencesWindow(application=self, title=_('Android Device'), default_width=360, default_height=700)
        self.window.connect('close-request', self.closed)
        self.window.set_search_enabled(False)
        self.page = Adw.PreferencesPage(title=_('Device'), icon_name='phone-symbolic')
        self.window.add(self.page)
        g = self.group(_('Phone and Linux'))
        self.row(g, 'model', _('Phone model'))
        self.row(g, 'system', 'Android / Linux')
        self.row(g, 'timezone', _('Time zone'))
        g = self.group(_('Network'), _('Android manages network connections'))
        for key, title in [('network',_('Connection')),('address',_('IP address')),('dns','DNS'),('wifi',_('Wi-Fi link'))]:
            self.row(g, key, title)
        self.button(g, _('Manage networks'), 'network')
        g = self.group(_('Display'), _('Android may limit refresh rate requests to save power or manage heat'))
        self.row(g, 'refresh', _('Supported refresh rates'))
        self.row(g, 'frames', _('Current display and frame rate'))
        self.orientation = Adw.ComboRow(title=_('Screen orientation'), model=Gtk.StringList.new([_('Follow Android'),_('Portrait'),_('Landscape')]))
        self.orientation.connect('notify::selected', self.orientation_changed)
        g.add(self.orientation)
        self.follow = Adw.SwitchRow(title=_('Brightness follows Android'), active=True)
        g.add(self.follow)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.02, 1.0, 0.01)
        self.scale.set_value(0.5)
        self.scale.set_hexpand(True)
        self.scale.set_draw_value(False)
        row = Adw.ActionRow(title=_('Desktop window brightness'))
        row.add_suffix(self.scale); g.add(row)
        self.follow.connect('notify::active', self.follow_changed)
        self.scale.connect('value-changed', self.brightness_changed)
        self.button(g, _('Android display settings'), 'display')
        g = self.group(_('Battery'))
        self.row(g, 'battery', _('Charge and temperature'))
        self.row(g, 'charging', _('Charging status'))
        g = self.group(_('Memory'), _('Limits the memory the Linux desktop can use. Above the limit, Linux reclaims memory or closes programs itself instead of pushing out Android’s VPN and other apps; with a very low limit, large programs may be closed.'))
        self.row(g, 'memory', _('Linux memory in use'))
        self.memory_presets = ['2048', '3072', '4096', '5120', '6144', 'unlimited']
        self.memory = Adw.ComboRow(title=_('Memory limit'), model=Gtk.StringList.new(
            [_('Minimum · 2 GB'), '3 GB', _('4 GB (default)'), '5 GB', '6 GB', _('No limit')]))
        self.memory.connect('notify::selected', self.memory_changed)
        g.add(self.memory)
        g = self.group(_('System settings'))
        for title, target in [(_('Sound and output devices'),'sound'),(_('Bluetooth devices'),'bluetooth'),(_('Date and time zone'),'datetime'),(_('Location settings'),'location')]:
            self.button(g, title, target)
        self.button(g, _('Test vibration'), None, {'op':'vibrate'})
        self.message = Adw.PreferencesGroup(description=_('Connecting to Android…'))
        self.page.add(self.message)
        self.window.present()
        self.timer = GLib.timeout_add_seconds(2, self.refresh)
        self.refresh()

    def closed(self, *_):
        GLib.source_remove(self.timer)
        if self.brightness_pending: GLib.source_remove(self.brightness_pending); self.brightness_pending=0
        self.window = None
        return False

    def group(self, title, description=None):
        g = Adw.PreferencesGroup(title=title)
        if description: g.set_description(description)
        self.page.add(g)
        return g

    def row(self, group, key, title):
        row = Adw.ActionRow(title=title, subtitle=_('Loading…'))
        row.set_subtitle_selectable(True)
        group.add(row); self.rows[key]=row

    def button(self, group, title, target, data=None):
        row = Adw.ActionRow(title=title, activatable=True)
        row.add_suffix(Gtk.Image.new_from_icon_name('go-next-symbolic'))
        row.connect('activated', lambda *_: self.send(data or {'op':'settings','target':target}))
        group.add(row)

    def send(self, data):
        future=self.pool.submit(request, data)
        def done(f):
            try: f.result(); message=''
            except Exception as e: message=str(e)
            GLib.idle_add(self.show_message, message)
        future.add_done_callback(done)

    def show_message(self, message):
        if self.window: self.message.set_description(message)
        return False

    def orientation_changed(self, *_):
        if not self.changing:
            self.send({'op':'orientation','mode':['system','portrait','landscape'][self.orientation.get_selected()]})

    def memory_changed(self, *_):
        if not self.changing:
            self.send({'op':'container-memory','limit':self.memory_presets[self.memory.get_selected()]})

    def follow_changed(self, *_):
        if self.changing:return
        follow=self.follow.get_active()
        self.scale.set_sensitive(not follow)
        self.send({'op':'brightness','value':-1 if follow else self.scale.get_value()})

    def brightness_changed(self, *_):
        if self.changing or self.follow.get_active():return
        if self.brightness_pending:GLib.source_remove(self.brightness_pending)
        def update():
            self.brightness_pending=0
            self.send({'op':'brightness','value':self.scale.get_value()})
            return False
        self.brightness_pending=GLib.timeout_add(120,update)

    def refresh(self):
        if not self.window:return False
        if self.busy:return True
        self.busy=True
        def read():
            data=request({'op':'status'})
            # Same cached Android identity as the NetworkManager bridge.
            detail=request({'op':'network-get'})
            current=data.get('network',{})
            for net in detail.get('networks',[]):
                if net.get('interface') == current.get('interface'):
                    current.update({k:net[k] for k in ('ssid','rssi','frequency','linkMbps') if k in net})
            try: data['memory']=request({'op':'container-memory'})
            except Exception: data['memory']=None
            return data
        future=self.pool.submit(read)
        def done(f):
            try:data,error=f.result(),None
            except OSError:data,error=None,_('Cannot reach the Android side. Open Rungic on the phone.')
            except Exception as e:data,error=None,str(e)
            GLib.idle_add(self.update, data, error)
        future.add_done_callback(done)
        return True

    def update(self, data, error):
        self.busy=False
        if not self.window:return False
        if error:
            # What was read before may no longer be true: show nothing rather than old values.
            self.message.set_description(error)
            for row in self.rows.values():row.set_subtitle(_('Not available'))
            return False
        self.message.set_description('')
        self.changing=True
        self.orientation.set_selected(['system','portrait','landscape'].index(data.get('orientation','portrait')))
        self.changing=False
        r=self.rows
        r['model'].set_subtitle(f"{data['manufacturer']} {data['model']}")
        import platform
        version=platform.freedesktop_os_release().get('PRETTY_NAME', 'Linux')
        r['system'].set_subtitle(f"Android {data['android']} / {version}")
        r['timezone'].set_subtitle(data['timezone'])
        net=data.get('network',{})
        # Android sends fixed transport names (PlatformBridge.java); they are values, labelled here.
        transports={'移动网络':_('Mobile network'),'以太网':_('Ethernet'),'其他':_('Other')}
        transport=transports.get(net.get('transport'),net.get('transport',_('Not connected')))
        r['network'].set_subtitle((transport + (_(' · Online') if net.get('validated') else _(' · Internet not verified'))) if net.get('connected') else _('Not connected'))
        r['address'].set_subtitle('\n'.join(net.get('addresses',[])) or _('None'))
        r['dns'].set_subtitle('\n'.join(net.get('dns',[])) or _('None'))
        if net.get('transport')=='Wi-Fi':
            ssid=net.get('ssid','')
            if ssid in ('<unknown ssid>','"<unknown ssid>"',''):ssid=_('See the network name in Android')
            r['wifi'].set_subtitle(f"{ssid}\n{net.get('rssi','?')} dBm · {net.get('frequency','?')} MHz\n" + _('Link speed {speed} Mbps').format(speed=net.get('linkMbps','?')))
        else:r['wifi'].set_subtitle(_('Not connected to Wi-Fi'))
        battery=data.get('battery',{})
        r['battery'].set_subtitle(f"{battery.get('percent','?')}% · {battery.get('temperature','?')}°C")
        states={1:_('Status unknown'),2:_('Charging'),3:_('Discharging'),4:_('Charging paused'),5:_('Fully charged')}
        r['charging'].set_subtitle(states.get(battery.get('status'),_('Unknown'))+(_(' · Plugged in') if battery.get('plugged') else _(' · Not plugged in')))
        c=configparser.ConfigParser()
        c.read('/mnt/android-wayland/android-refresh.ini')
        if c.has_section('refresh'):
            v=c['refresh'];r['refresh'].set_subtitle(f"{float(v.get('minimum-hz',0)):.0f}–{float(v.get('maximum-hz',0)):.0f} Hz")
            r['frames'].set_subtitle(_('Android reports {reported:.0f} Hz\nDesktop submits {submitted:.1f} fps').format(
                reported=float(v.get('android-reported-hz',0)), submitted=float(v.get('submitted-fps',0))))
        memory=data.get('memory')
        if memory:
            limit=f"{memory['limit_mib']} MB" if memory.get('limit_mib') else pgettext('memory limit', 'none')
            text=_('{usage} MB · limit {limit} · peak {peak} MB (of {total} MB)').format(
                usage=memory['usage_mib'], limit=limit, peak=memory['peak_mib'], total=memory['total_mib'])
            # What the use is made of; a launcher before 2026-10-03 does not say.
            if 'programs_mib' in memory:
                text+='\n'+_('Programs {programs} MB · file cache {cache} MB (in-memory files {shmem} MB) · swap {swap} MB').format(
                    programs=memory['programs_mib'], cache=memory['cache_mib'], shmem=memory['shmem_mib'], swap=memory['swap_mib'])
            r['memory'].set_subtitle(text)
            if memory.get('choice') in self.memory_presets:
                self.changing=True
                self.memory.set_selected(self.memory_presets.index(memory['choice']))
                self.changing=False
        else:r['memory'].set_subtitle(_('Update the Android side (Rungic APK 2.5) to show this'))
        self.changing=True
        value=data.get('windowBrightness',-1)
        self.follow.set_active(value<0);self.scale.set_sensitive(value>=0)
        if value>=0 and not self.brightness_pending:self.scale.set_value(value)
        self.changing=False
        return False

DeviceApp().run(sys.argv)
