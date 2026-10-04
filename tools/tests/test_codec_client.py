# SPDX-License-Identifier: MIT
"""librungiccodec, the transport the GStreamer elements, the private FFmpeg and Firefox share
(shared/media/codec-client.c, docs/research/35), against a stand-in of the app's codec broker
(tools/system/android_media.py, CodecBridge.java's protocol): built with the C compiler and driven by
a small C program. A decoder's pictures come back in presentation order matched to the frames they
belong to; a consumer that stops mid-exchange (a seek's FLUSHING) still leaves the exchange read to its
end, so the FLUSH after it and the frames after that stay in step; without hardware an open fails,
never quietly something else."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/system'))
import android_media  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which('cc'), reason='no C compiler')

DRIVER = r'''
#include "codec-client.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int calls, stop_at;
static int collect(void *user, const RungicCodecFrame *f) {
  (void)user; calls++;
  printf("out %d %lld %dx%d\n", f->id, (long long)f->pts, f->width, f->height);
  return calls == stop_at ? -1 : 0;        /* the consumer stops, as a sink that is flushing */
}
static int frame(RungicCodec *c, int id, long long pts) {
  static const unsigned char au[] = {0, 0, 0, 1, 0x65, 0x88, 0x84};
  memcpy(c->memory, au, sizeof au);
  int r = rungic_codec_exchange(c, RUNGIC_FRAME, id, pts, 0, sizeof au, collect, NULL);
  printf("frame %d -> %d%s%s\n", id, r, r ? " " : "", r ? c->error : "");
  return r;
}
int main(int argc, char **argv) {
  RungicCodec c; rungic_codec_init(&c);
  RungicCodecConfig config = {.kind = 0, .width = 64, .height = 48};
  if (rungic_codec_open(&c, &config)) { printf("open-error %s\n", c.error); return 0; }
  printf("opened %s\n", c.name);
  /* Decode order of I P B B P B B ...: presentation times 0 3 1 2 6 4 5 9 7 8 (frames of 1/30 s). */
  static const int order[] = {0, 3, 1, 2, 6, 4, 5, 9, 7, 8};
  if (!strcmp(argv[1], "bframes")) {
    for (int i = 0; i < 10; i++) frame(&c, i, order[i] * 33333LL);
    int r = rungic_codec_exchange(&c, RUNGIC_DRAIN, 0, 0, 0, 0, collect, NULL);
    printf("drain -> %d ended %d\n", r, c.ended);
  } else if (!strcmp(argv[1], "seek")) {
    stop_at = 1;                      /* the first picture of the first burst finds the sink flushing */
    for (int i = 0; i < 4; i++) frame(&c, i, order[i] * 33333LL);
    printf("flush -> %d\n", rungic_codec_exchange(&c, RUNGIC_FLUSH, 0, 0, 0, 0, NULL, NULL));
    for (int i = 0; i < 6; i++) frame(&c, 100 + i, (60 + order[i]) * 33333LL);
    printf("drain -> %d\n", rungic_codec_exchange(&c, RUNGIC_DRAIN, 0, 0, 0, 0, collect, NULL));
  }
  rungic_codec_close(&c);
  return 0;
}
'''


@pytest.fixture(scope='module')
def driver(tmp_path_factory):
    build = tmp_path_factory.mktemp('codec')
    (build / 'driver.c').write_text(DRIVER)
    program = build / 'driver'
    subprocess.run(['cc', '-O1', '-pthread', f'-I{ROOT}/shared/media', '-o', str(program), str(build / 'driver.c'),
                    str(ROOT / 'shared/media/codec-client.c'), str(ROOT / 'shared/media/codec-v4l2.c')], check=True)
    return program


def run(driver, scenario, broker=None, **env):
    environ = {**os.environ, 'RUNGIC_CODEC_SOCKET': broker.path if broker else '/nonexistent/codec.sock', **env}
    if 'RUNGIC_CODEC_DISABLE' not in env:
        environ.pop('RUNGIC_CODEC_DISABLE', None)
    out = subprocess.run([str(driver), scenario], env=environ, capture_output=True, text=True, timeout=30).stdout
    return out.splitlines()


def pictures(lines):
    return [(int(f[1]), int(f[2])) for f in (line.split() for line in lines) if f[0] == 'out']


# covers: apps.hw-codec/E4
# covers[consumer]: iface:codec
def test_b_frames_come_back_in_presentation_order_with_their_own_times(driver):
    with android_media.Codec() as broker:
        lines = run(driver, 'bframes', broker)
    assert lines[0] == 'opened c2.qti.avc.decoder', lines
    order = [0, 3, 1, 2, 6, 4, 5, 9, 7, 8]
    out = pictures(lines)
    assert sorted(i for i, _ in out) == list(range(10))
    assert all(pts == order[i] * 33333 for i, pts in out), out           # each picture with its frame's time
    assert [pts for _, pts in out] == sorted(pts for _, pts in out)     # presentation order
    assert 'drain -> 0 ended 1' in lines and broker.problems == []


# covers: apps.hw-codec/E4
def test_a_consumer_stopped_by_a_seek_leaves_the_flush_and_later_frames_in_step(driver):
    with android_media.Codec() as broker:
        lines = run(driver, 'seek', broker)
        problems, commands = broker.problems, broker.commands
    stopped = next(line for line in lines if line.endswith('Output consumer stopped'))
    assert stopped.startswith('frame 3 -> -1')        # a burst of two, its first refused by the sink
    assert 'flush -> 0' in lines
    after = pictures(lines[lines.index('flush -> 0'):])
    assert sorted(i for i, _ in after) == list(range(100, 106))           # only the new frames, each once
    assert all(pts == (60 + [0, 3, 1, 2, 6, 4][i - 100]) * 33333 for i, pts in after)
    assert 'drain -> 0' in lines and problems == [], problems             # every record acknowledged in turn
    assert ('FLUSH',) in commands


# covers: apps.hw-codec/E3
def test_without_hardware_opening_fails_and_says_why(driver):
    lines = run(driver, 'bframes')                                      # no broker: the app is not there
    assert lines[0].startswith('open-error Open codec channel'), lines
    with android_media.Codec(refuse=True) as broker:                    # the app: no hardware component
        lines = run(driver, 'bframes', broker)
    assert lines == ['open-error java.io.IOException: Hardware codec unavailable'], lines
    with android_media.Codec() as broker:
        lines = run(driver, 'bframes', broker, RUNGIC_CODEC_DISABLE='1')
        assert broker.opened == []
    assert lines[0].startswith('open-error Hardware disabled by environment'), lines
