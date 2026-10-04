# rungic-plasma-bridges
install -Dm755 "$SRC/desktop/device-panel.py" "$DESTDIR/usr/bin/rungic-platform"
install -Dm644 "$SRC/desktop/com.rungic.Platform.desktop" "$DESTDIR/usr/share/applications/com.rungic.Platform.desktop"
# Catalogs: the device panel's, and rungic_render's (Blender, shipped by rungic-plasma-config, a
# host build without gettext).
install -d "$DESTDIR/usr/share/locale/zh_CN/LC_MESSAGES"
for domain in rungic-platform rungic-render; do
    msgfmt -c --check-format -o "$DESTDIR/usr/share/locale/zh_CN/LC_MESSAGES/$domain.mo" "$SRC/desktop/po/zh_CN/$domain.po"
done
install -Dm755 "$SRC/shared/platform/clipboard.py" "$DESTDIR/usr/bin/rungic-clipboard"
install -Dm755 "$SRC/shared/platform/sms.py" "$DESTDIR/usr/bin/rungic-sms"
install -Dm644 "$SRC/shared/platform/rungic_platform_transport.py" "$DESTDIR/usr/lib/python3/dist-packages/rungic_platform_transport.py"
install -Dm644 "$SRC/shared/platform/host_watch.py" "$DESTDIR/usr/lib/python3/dist-packages/rungic_host_watch.py"
install -Dm755 "$SRC/shared/platform/network-manager.py" "$DESTDIR/usr/libexec/rungic-android-network"
install -Dm755 "$SRC/shared/platform/bluez.py" "$DESTDIR/usr/libexec/rungic-android-bluetooth"
install -Dm755 "$SRC/shared/platform/modem-manager.py" "$DESTDIR/usr/libexec/rungic-android-modem"
install -Dm755 "$SRC/shared/media/media-bridge.py" "$DESTDIR/usr/bin/rungic-media-bridge"
install -Dm755 "$SRC/shared/media/audio-route.py" "$DESTDIR/usr/bin/rungic-audio-route"
install -Dm755 "$SRC/shared/media/audio-follow.py" "$DESTDIR/usr/bin/rungic-audio-follow"
mkdir -p "$DESTDIR/usr/bin"
g++ -std=gnu++20 -O2 -g1 -o "$DESTDIR/usr/bin/rungic-camera-source" "$SRC/shared/media/camera-source.cpp" \
    $(pkg-config --cflags --libs libpipewire-0.3 json-glib-1.0) -lyuv -pthread
# Translations of shared/'s user-visible texts (gettext domain rungic-shared).
for po in "$SRC"/shared/po/*/rungic-shared.po; do
    lang=$(basename "$(dirname "$po")")
    mkdir -p "$DESTDIR/usr/share/locale/$lang/LC_MESSAGES"
    msgfmt -c --check-format -o "$DESTDIR/usr/share/locale/$lang/LC_MESSAGES/rungic-shared.mo" "$po"
done
for unit in rungic-plasma-network rungic-plasma-bluetooth rungic-plasma-modem; do
    install -Dm644 "$SRC/desktop/$unit.service" "$DESTDIR/usr/lib/systemd/system/$unit.service"
done
for unit in rungic-plasma-media rungic-plasma-clipboard rungic-plasma-audio-follow; do
    install -Dm644 "$SRC/desktop/$unit.service" "$DESTDIR/usr/lib/systemd/user/$unit.service"
done

g++ -std=gnu++20 -O2 -g1 -o "$DESTDIR/usr/bin/rungic-communication-audio" "$SRC/shared/media/communication-audio.cpp" \
    $(pkg-config --cflags --libs Qt6Core Qt6Network gstreamer-1.0 gstreamer-app-1.0)
install -Dm644 "$SRC/desktop/rungic-communication-audio.service" "$DESTDIR/usr/lib/systemd/user/rungic-communication-audio.service"
