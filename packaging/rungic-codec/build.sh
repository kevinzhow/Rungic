# rungic-codec
M=$SRC/shared/media
lib=$DESTDIR/usr/lib/rungic-codec
mkdir -p "$lib" "$DESTDIR/usr/lib/aarch64-linux-gnu/gstreamer-1.0"
cc -O2 -g1 -fPIC -shared -pthread -Wl,-soname,librungiccodec.so -o "$lib/librungiccodec.so" "$M/codec-client.c" "$M/codec-v4l2.c"
cc -O2 -g1 -fPIC -shared -o "$DESTDIR/usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstrungiccodec.so" "$M/gst-rungic-codec.c" \
    $(pkg-config --cflags --libs gstreamer-video-1.0) -L"$lib" -lrungiccodec -Wl,-rpath,/usr/lib/rungic-codec
# Private FFmpeg (packages/ffmpeg: the release with the codec registration patches, docs/71).
cd "$SRC/upstream/ffmpeg"
set -- --prefix=/usr/lib/rungic-codec/ffmpeg --enable-shared --disable-static \
 --disable-doc --disable-autodetect --disable-network --disable-devices \
 --enable-gpl --enable-libx264 --enable-libx265 --enable-libvpx --enable-libopus --enable-libdav1d \
 --extra-cflags=-g1 --extra-ldflags="-L$lib -Wl,-rpath,/usr/lib/rungic-codec:/usr/lib/rungic-codec/ffmpeg/lib" \
 --extra-libs='-lrungiccodec -pthread'
# The tree is kept between builds (rungic_package.sync_script): configure again only when its
# arguments or the configure script changed; it rewrites config.h, and every object then rebuilds.
stamp="$(printf '%s\n' "$@"; sha256sum configure)"
if [ ! -f ffbuild/config.mak ] || [ "$(cat .rungic-configure 2>/dev/null)" != "$stamp" ]; then
  ./configure "$@" >/dev/null
  printf '%s' "$stamp" > .rungic-configure
fi
make -j"${JOBS:-4}" >/dev/null
make install DESTDIR="$DESTDIR" >/dev/null
rm -rf "$DESTDIR/usr/lib/rungic-codec/ffmpeg/include" "$DESTDIR/usr/lib/rungic-codec/ffmpeg/lib/pkgconfig" \
       "$DESTDIR/usr/lib/rungic-codec/ffmpeg/share"
