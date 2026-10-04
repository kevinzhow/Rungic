# SPDX-License-Identifier: MIT
"""How the hardware codecs reach applications that do not use the system GStreamer (docs/108):
Ubuntu's own FFmpeg rebuilt with this project's decoders registered ahead of every software
decoder (encoders only by name), the codec client compiled into libavcodec; and the Flatpak
GStreamer extension, one self-contained plugin in the runtimes' extension directory."""
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FFMPEG = ROOT / 'packages/ffmpeg-ubuntu'


def externs(lines):
    return [m.group(1) for line in lines for m in [re.match(r'[ +]extern const FFCodec ff_(\w+);', line)] if m]


# covers: apps.hw-codec/E11
def test_system_ffmpeg_takes_the_hardware_decoders_first_and_the_encoders_last():
    patch = (FFMPEG / 'debian/patches/rungic/system-hardware-decoders.patch').read_text().splitlines()
    assert (FFMPEG / 'debian/patches/series').read_text().split() == ['rungic/system-hardware-decoders.patch']
    hunks, current = [], None
    for line in patch:
        if line.startswith('--- a/libavcodec/allcodecs.c'):
            current = 'allcodecs'
        elif line.startswith('--- a/'):
            current = None
        if current == 'allcodecs' and line.startswith('@@'):
            hunks.append([])
        elif current == 'allcodecs' and hunks:
            hunks[-1].append(line)
    first, last = hunks
    # Before the first codec FFmpeg declares (the list order is avcodec_find_decoder's).
    added = [name for name in externs(l for l in first if l.startswith('+'))]
    assert added == ['h264_rungic_decoder', 'hevc_rungic_decoder', 'vp9_rungic_decoder']
    assert externs(l for l in first if l.startswith(' '))[0] == 'a64multi_encoder'
    # Encoders after every other video encoder: applications keep libx264/libx265 by default.
    assert externs(l for l in last if l.startswith('+')) == ['h264_rungic_encoder', 'hevc_rungic_encoder']
    assert 'vnull_encoder' in externs(l for l in last if l.startswith(' '))
    # The client and the V4L2 backend are compiled into libavcodec: nothing of ours to depend on.
    recipe = json.loads((FFMPEG / 'recipe.json').read_text())
    assert recipe['source'] == 'ffmpeg' and recipe['build_profiles'] == ['pkg.ffmpeg.noextra']
    overlay = recipe['overlay']
    assert overlay['libavcodec/rungic_codec_client.c'] == 'shared/media/codec-client.c'
    assert overlay['libavcodec/rungic_codec_v4l2.c'] == 'shared/media/codec-v4l2.c'
    makefile = '\n'.join(l for l in patch if l.startswith('+'))
    assert 'rungic_codec.o rungic_codec_client.o rungic_codec_v4l2.o' in makefile
    assert '+rungic' in (FFMPEG / 'debian/changelog').read_text().splitlines()[0]
    # The release replaces exactly the standard libraries the phone has (no -extra flavour).
    release = json.loads((ROOT / 'release/packages.json').read_text())['rebuilt']['ffmpeg-ubuntu']
    assert 'libavcodec62' in release['packages'] and not [p for p in release['packages'] if 'extra' in p]


# covers: apps.hw-codec/E12
def test_the_flatpak_extension_is_one_plugin_with_the_client_inside(tmp_path):
    # The build script with stand-ins for the compiler and pkg-config: what it compiles, where it puts it.
    bin_ = tmp_path / 'bin'
    bin_.mkdir()
    (bin_ / 'cc').write_text('#!/bin/sh\necho "$@" > "$LOG"\nfor a; do case $prev in -o) : > "$a";; esac; prev=$a; done\n')
    (bin_ / 'pkg-config').write_text('#!/bin/sh\necho -lgstvideo-1.0\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    dest, log = tmp_path / 'root', tmp_path / 'cc.log'
    env = {**os.environ, 'PATH': f'{bin_}:{os.environ["PATH"]}', 'SRC': str(ROOT), 'DESTDIR': str(dest), 'LOG': str(log)}
    subprocess.run(['sh', '-e', str(ROOT / 'packaging/rungic-flatpak-codec/build.sh')], env=env, check=True)
    plugin = dest / 'var/lib/flatpak/extension/org.freedesktop.Platform.GStreamer.rungic/aarch64/25.08/libgstrungiccodec.so'
    assert plugin.exists()
    args = log.read_text().split()
    for source in ('gst-rungic-codec.c', 'codec-client.c', 'codec-v4l2.c'):
        assert any(a.endswith(f'shared/media/{source}') for a in args), source
    assert '-fvisibility=hidden' in args and '-lrungiccodec' not in args
    package = json.loads((ROOT / 'packaging/rungic-flatpak-codec/package.json').read_text())
    assert package['image'].startswith('freedesktopsdk/sdk:25.08')
