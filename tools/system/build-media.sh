#!/bin/sh
# In the system test container (run-in-container.sh, as root): the Linux sides of the camera, audio
# and codec interfaces, built from the working tree in /src and installed where their packages put
# them (packaging/rungic-plasma-bridges, packaging/rungic-codec, packaging/rungic-plasma-config).
# The private FFmpeg is not built here.
set -eu
SRC=/src
M=$SRC/shared/media
install -Dm755 "$M/media-bridge.py" /usr/bin/rungic-media-bridge
install -Dm755 "$M/audio-route.py" /usr/bin/rungic-audio-route
install -Dm644 "$SRC/shared/platform/host_watch.py" /usr/lib/python3/dist-packages/rungic_host_watch.py
g++ -std=gnu++20 -O2 -g1 -o /usr/bin/rungic-camera-source "$M/camera-source.cpp" \
    $(pkg-config --cflags --libs libpipewire-0.3 json-glib-1.0) -lyuv -pthread
install -Dm644 "$SRC/system/config/etc/pipewire/client.conf.d/50-rungic-video.conf" \
    /etc/pipewire/client.conf.d/50-rungic-video.conf
lib=/usr/lib/rungic-codec
mkdir -p "$lib"
cc -O2 -g1 -fPIC -shared -pthread -Wl,-soname,librungiccodec.so -o "$lib/librungiccodec.so" "$M/codec-client.c" "$M/codec-v4l2.c"
plugins=$(pkg-config --variable=pluginsdir gstreamer-1.0)
cc -O2 -g1 -fPIC -shared -o "$plugins/libgstrungiccodec.so" "$M/gst-rungic-codec.c" \
    $(pkg-config --cflags --libs gstreamer-video-1.0) -L"$lib" -lrungiccodec -Wl,-rpath,"$lib"
