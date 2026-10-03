# rungic-cua
C=$SRC/agent/computer-use
lib=$DESTDIR/usr/lib/rungic-cua
mkdir -p "$lib"
cp -r "$SRC/upstream/arc-cua/src/arc_cua" "$C/rungic_cua" "$lib/"
install -Dm755 "$C/rungic-cua" "$DESTDIR/usr/bin/rungic-cua"
# Captions and questions in the desktop's language (rungic_cua.i18n): po/<lang>/rungic-cua.po.
for po in "$C"/po/*/rungic-cua.po; do
    lang=$(basename "$(dirname "$po")")
    mkdir -p "$DESTDIR/usr/share/locale/$lang/LC_MESSAGES"
    msgfmt -c --check-format -o "$DESTDIR/usr/share/locale/$lang/LC_MESSAGES/rungic-cua.mo" "$po"
done
# KWin grants ScreenShot2 to this executable's desktop file only (docs/64).
mkdir -p "$DESTDIR/usr/libexec"
cc -O2 -g1 -Wall -o "$DESTDIR/usr/libexec/rungic-screenshot" "$C/screenshot/rungic-screenshot.c" \
    $(pkg-config --cflags --libs gio-unix-2.0)
c++ -std=c++20 -O2 -g1 -Wall -o "$DESTDIR/usr/libexec/rungic-voice-recording" "$C/voice-recording.cpp" \
    $(pkg-config --cflags --libs Qt6Core)
install -Dm644 "$C/screenshot/rungic-screenshot.desktop" "$DESTDIR/usr/share/applications/com.rungic.screenshot.desktop"
# rungic-clicker: typesafe-computer-use in its own venv; the system site packages supply gi,
# Pillow and onnxruntime. Dependencies satisfied by the builder's system Python
# must also appear in package.json (clean G100 rootfs, docs/79).
# The pins are what was validated on the phone (docs/64).
clicker=$DESTDIR/usr/lib/rungic-clicker
python3 -m venv --system-site-packages "$clicker/venv"
( [ -r /etc/profile.d/proxy.sh ] && . /etc/profile.d/proxy.sh
  "$clicker/venv/bin/python" -m pip install -q --no-compile --disable-pip-version-check \
      typesafe-sdk==0.6.0 anthropic==1.6.0 openai==2.54.0 rapidocr==3.9.2 )
# Check our entry points; global pip check also inspects unrelated Debian Python
# tools in the shared builder. The clean rootfs runs the full dependency check.
"$clicker/venv/bin/python" -c 'from rapidocr import RapidOCR; import openai, anthropic, typesafe_sdk'
# The venv was made under DESTDIR: point its scripts and config at the installed path.
grep -rlI --null "$DESTDIR" "$clicker/venv" | xargs -0 -r sed -i "s#$DESTDIR##g"
find "$clicker" -name __pycache__ -prune -exec rm -rf {} +
cp -r "$SRC/upstream/typesafe-computer-use/typesafe_computer_use" "$clicker/"
install -m644 "$C/rungic_clicker.py" "$clicker/rungic_clicker.py"
install -Dm755 "$C/rungic-clicker" "$DESTDIR/usr/bin/rungic-clicker"
find "$DESTDIR" -name __pycache__ -prune -exec rm -rf {} +
