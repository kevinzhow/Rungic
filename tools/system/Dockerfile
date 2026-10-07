# The system test image (tools/system_test.py, quality/README.md 分层): the build image with what
# Rungic's Linux system features run on, and an unprivileged user. A headless KWin (--virtual) is the
# compositor; nothing of Android is here: its interfaces are stand-ins of their contracts. A fresh
# Ubuntu rootfs too: tests marked "system-test: as root" install into it (openssh-server: SSH on first install).
# GStreamer's headers build the communication audio service and its client; /mnt/android-wayland, as in
# the phone's container, holds stand-ins of the app's sockets where programs have their paths fixed.
# The AT-SPI bus and its GObject binding: rungic-a11y drives applications by their controls' names.
ARG BASE
FROM ${BASE}
ARG http_proxy
ARG https_proxy
RUN apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
        kwin-wayland dbus-daemon dbus-user-session qdbus-qt6 python3-gi gir1.2-gtk-3.0 gir1.2-glib-2.0 \
        cmake qt6-base-dev qt6-base-private-dev qt6-declarative-dev qt6-wayland-dev liblayershellqtinterface-dev \
        plasma-wayland-protocols wayland-protocols libwayland-dev libxkbcommon-dev pkgconf libkf6i18n-dev gettext \
        libkpipewire-dev libegl-dev libgles-dev libdrm-dev qt6-websockets-dev \
        qml6-module-qtquick-virtualkeyboard qt6-virtualkeyboard-plugin qml6-module-qtcore qml6-module-qtquick-window \
        qml6-module-qtquick-effects qml6-module-org-kde-kirigami qml6-module-org-kde-pipewire qml6-module-org-kde-layershell \
        fonts-noto-core openssh-server \
        libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
        libkf6windowsystem-dev libkf6colorscheme-dev libkf6config-dev qml6-module-org-kde-ki18n \
        qml6-module-qtquick-dialogs qml6-module-qt-labs-folderlistmodel qml6-module-qtquick-shapes \
        at-spi2-core gir1.2-atspi-2.0 qml6-module-qtquick-controls qml6-module-qtquick-layouts \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m tester \
    && install -d -o tester /mnt/android-wayland
# wl-clipboard 2.3.0, the release packages/wl-clipboard pins and the phone has (docs/61): the clipboard
# bridge's wl-paste --watch needs ext-data-control, the only data-control KWin 6.6 offers; Ubuntu's
# 2.2.1 has wlroots' only. In /usr/local, first on PATH (tools/system/tests/desktop_clipboard.py); all of
# it there, fish's completions too (meson put them over the Ubuntu package's, which integrity_drift saw).
RUN curl -fsSL -o /tmp/wl-clipboard.tar.gz https://github.com/bugaevc/wl-clipboard/archive/refs/tags/v2.3.0.tar.gz \
    && echo 'b4dc560973f0cd74e02f817ffa2fd44ba645a4f1ea94b7b9614dacc9f895f402  /tmp/wl-clipboard.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/wl-clipboard.tar.gz -C /tmp \
    && meson setup /tmp/wl-clipboard-build /tmp/wl-clipboard-2.3.0 --prefix=/usr/local \
        -Dfishcompletiondir=/usr/local/share/fish/vendor_completions.d >/dev/null \
    && ninja -C /tmp/wl-clipboard-build install >/dev/null \
    && rm -rf /tmp/wl-clipboard*
# The media of the apps area (tools/system/build-media.sh): PulseAudio as the container's sound
# server, PipeWire and WirePlumber for the cameras, GStreamer for the codec elements, and what they
# build with.
RUN apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
        pulseaudio pulseaudio-utils pipewire pipewire-bin wireplumber libpipewire-0.3-dev libjson-glib-dev libyuv-dev \
        libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev gstreamer1.0-tools gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-pipewire \
        python3-gst-1.0 gir1.2-gst-plugins-base-1.0 \
    && rm -rf /var/lib/apt/lists/*
# The Rime keyboard's engine and session tests (desktop/rime/tests/run.sh; system test rime_keyboard).
RUN apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
        librime-dev rime-data-luna-pinyin rime-prelude rime-essay qt6-virtualkeyboard-dev \
        qml6-module-qttest qt6-declarative-dev-tools ninja-build \
    && rm -rf /var/lib/apt/lists/*

# Real localized Plasma Mobile/Kalk drawer checks use the base image's applications and CJK fonts.
RUN localedef -i zh_CN -f UTF-8 zh_CN.UTF-8

# Ubuntu's slim Docker image excludes gettext .mo files. Restore the actual Chinese
# catalogs for localized editor/menu checks; this override belongs only to the test image.
RUN printf 'path-include=/usr/share/locale/zh_CN/LC_MESSAGES/*.mo\n' > /etc/dpkg/dpkg.cfg.d/zzzz-rungic-system-locales

# Apply the release's exact launcher policy before unpacking the editor dependency.
COPY rungic-apps /etc/dpkg/dpkg.cfg.d/zz-rungic-apps
# The release's preinstalled text editor is exercised through the actual mobile drawer.
RUN apt-get update && apt-get install -y --no-install-recommends kwrite=4:25.12.3-0ubuntu1 \
    && apt-get install -y --reinstall kate-data libkf6xmlgui-data libkf6texteditor-data \
    && rm -rf /var/lib/apt/lists/*
