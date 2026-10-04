# This project's GStreamer codec elements as the Flatpak extension org.freedesktop.Platform.GStreamer.rungic
# of the Freedesktop 25.08 runtimes, which the GNOME 50 runtime shares (docs/108): Flatpak apps get
# the same V4L2 decoders and encoders as the system's GStreamer. Runs in the Freedesktop SDK's
# image ("image" in package.json) to link against the runtime's GStreamer. The codec client is
# compiled in: the extension needs nothing outside the runtime; without the msm_vidc devices
# (an app without --device=all) its elements fail to open and GStreamer picks its software ones.
BRANCH=25.08
EXT=/var/lib/flatpak/extension/org.freedesktop.Platform.GStreamer.rungic/aarch64/$BRANCH
M=$SRC/shared/media
mkdir -p "$DESTDIR$EXT"
cc -O2 -g1 -fPIC -shared -pthread -fvisibility=hidden -o "$DESTDIR$EXT/libgstrungiccodec.so" \
   "$M/gst-rungic-codec.c" "$M/codec-client.c" "$M/codec-v4l2.c" $(pkg-config --cflags --libs gstreamer-video-1.0)
