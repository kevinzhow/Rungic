# rungic-plasma-config: files under system/config mirror the target paths.
cp -a "$SRC/system/config/etc" "$SRC/system/config/usr" "$DESTDIR/"
for script in "$DESTDIR"/usr/share/kconf_update/*.py "$DESTDIR"/usr/share/kconf_update/*.sh; do
    [ ! -f "$script" ] || chmod 755 "$script"
done
install -Dm644 "$SRC/system/dpkg-locales.conf" "$DESTDIR/etc/dpkg/dpkg.cfg.d/zz-rungic-locales"
install -Dm644 "$SRC/desktop/mozilla.sources" "$DESTDIR/etc/apt/sources.list.d/mozilla.sources"
install -Dm644 "$SRC/desktop/mozilla.pref" "$DESTDIR/etc/apt/preferences.d/mozilla"
install -Dm644 "$SRC/system/wireplumber-android.conf" "$DESTDIR/etc/wireplumber/wireplumber.conf.d/60-rungic-android.conf"
install -Dm644 "$SRC/system/network-manager.conf" "$DESTDIR/etc/dbus-1/system.d/rungic-android-network.conf"
install -Dm644 "$SRC/system/bluetooth.conf" "$DESTDIR/etc/dbus-1/system.d/rungic-android-bluetooth.conf"
install -Dm644 "$SRC/system/modem-manager.conf" "$DESTDIR/etc/dbus-1/system.d/rungic-android-modem.conf"
install -Dm644 "$SRC/desktop/gpu-env" "$DESTDIR/etc/plasma/gpu-env"
install -Dm644 "$SRC/system/pulse.pa" "$DESTDIR/etc/plasma/pulse.pa"
install -Dm755 "$SRC/system/policy-rc.d" "$DESTDIR/usr/sbin/policy-rc.d"
# systemd-coredump (for coredumpctl) must never set Android's global core_pattern.
mkdir -p "$DESTDIR/etc/sysctl.d"
ln -s /dev/null "$DESTDIR/etc/sysctl.d/50-coredump.conf"
# Services kept off by default (docs/83): postinst applies each unit's default once, so the
# package does not own the masks and a change made in Settings survives upgrades.
install -Dm644 "$SRC/desktop/services/policy.json" "$DESTDIR/usr/share/rungic/service-policy.json"
# One-time migrations of the container's system (docs/122): run by postinst, recorded in
# /var/lib/rungic/migrations. Named YYYYMMDD-what.sh under system/migrations/system.
install -Dm755 "$SRC/system/rungic-migrate" "$DESTDIR/usr/libexec/rungic-migrate"
install -d "$DESTDIR/usr/lib/rungic/migrations/system"
for migration in "$SRC/system/migrations/system/"*; do
    case "$migration" in *.sh) install -m755 "$migration" "$DESTDIR/usr/lib/rungic/migrations/system/" ;; esac
done
