# SPDX-License-Identifier: MIT
"""Decoding straight through Qualcomm's msm_vidc V4L2 decoder (shared/media/codec-v4l2.c, docs/108),
against tools/tests/codec_v4l2_driver.c's stand-in of the driver, which keeps the rules the real
one enforces with a firmware reset: DMA-BUFs only, nothing queued before the output stream, one
access unit before capture is set up, capture only after the source change. The consumer gets
the pictures with their own frames' times; a flush starts a new session and replays the parameter
sets; without the device, or turned off, decoding goes to the app's codec broker as before."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import contracts  # noqa: E402

DEVICE = '/dev/rungic-test-video32'


@pytest.fixture(scope='module')
def driver(tmp_path_factory):
    compiler = shutil.which('cc') or shutil.which('gcc')
    if not compiler:
        pytest.skip('no C compiler')
    out = tmp_path_factory.mktemp('v4l2') / 'codec_v4l2_driver'
    subprocess.run([compiler, '-std=gnu11', '-O1', f'-I{ROOT}/shared/media', str(ROOT / 'tools/tests/codec_v4l2_driver.c'),
                    str(ROOT / 'shared/media/codec-client.c'), str(ROOT / 'shared/media/codec-v4l2.c'),
                    '-Wl,--wrap=open,--wrap=ioctl,--wrap=poll,--wrap=close,--wrap=dup3', '-lpthread', '-o', str(out)],
                   check=True)
    return out


def run(driver, mode, kind=0, frames=6, broker=None, fake=True, **env):
    environ = {**os.environ, 'RUNGIC_CODEC_V4L2_DEVICE': DEVICE,
               'RUNGIC_CODEC_SOCKET': broker.path if broker else '/nonexistent/codec.sock', **env}
    for name in ('RUNGIC_CODEC_DISABLE', 'RUNGIC_CODEC_PRECONNECT', 'RUNGIC_CODEC_V4L2', 'FAKE_VIDC'):
        if name not in env:
            environ.pop(name, None)
    if fake:
        environ['FAKE_VIDC'] = '1'
    done = subprocess.run([str(driver), mode, str(kind), str(frames)], capture_output=True, text=True, timeout=30, env=environ)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# covers: apps.hw-codec/E9
def test_pictures_come_from_the_v4l2_decoder_in_the_drivers_order(driver):
    result = run(driver, 'decode')
    assert result['open'] is True and 'V4L2' in result['name'], result
    assert result['violations'] == 0, result['log']
    assert [r['id'] for r in result['records']] == list(range(6))
    for r in result['records']:
        assert (r['pts'], r['width'], r['height'], r['depth'], r['copied']) == (r['id'] * 33333, 176, 144, 8, True)
        assert (r['luma'], r['chroma']) == (16 + r['id'], 0x80)
    assert result['failures'] == 0 and result['ended'] == 1 and (result['inputs'], result['outputs']) == (6, 6)
    # The order the driver needs: output streaming, then the source change, then capture.
    log = result['log']
    assert log.index('stream output') < log.index('capture NV12') < log.index('stream capture') < log.index('stop')


# covers: apps.hw-codec/E9
def test_10_bit_pictures_come_out_as_p010(driver):
    result = run(driver, 'decode10', kind=1, frames=3)
    assert result['open'] is True and result['violations'] == 0, result
    assert 'capture P010' in result['log']
    assert [(r['depth'], r['copied'], r['luma'], r['chroma']) for r in result['records']] == \
        [(10, True, (16 + i) << 2, 512) for i in range(3)]


# covers: apps.hw-codec/E9
def test_a_flush_starts_a_new_session_with_the_parameter_sets_in_front(driver):
    result = run(driver, 'flush', frames=3, FAKE_VIDC_EVENT_DELAY='0')
    assert result['violations'] == 0 and result['failures'] == 0, result
    assert result['sessions'] == 2 and result['replayed_params'] is True
    assert [r['id'] for r in result['records']] == [0, 1, 2, 100, 101, 102]
    # With the source change late (the third look, as the real driver takes milliseconds), the
    # decoder waits for it briefly: what came before the flush may come out or be dropped (a seek
    # discards it anyway), never after the new session's pictures.
    result = run(driver, 'flush', frames=3)
    assert result['violations'] == 0 and result['failures'] == 0, result
    ids = [r['id'] for r in result['records']]
    assert ids[-3:] == [100, 101, 102] and set(ids[:-3]) <= {0, 1, 2}


# covers: apps.hw-codec/E9
def test_without_the_device_or_turned_off_decoding_goes_to_the_app(driver):
    def decode(config, frame):
        return []
    for env, fake in (({}, False), ({'RUNGIC_CODEC_V4L2': '0'}, True), ({'RUNGIC_CODEC_PRECONNECT': '1'}, True)):
        with contracts.CodecStandIn(output=decode) as broker:
            result = run(driver, 'decode', frames=2, broker=broker, fake=fake, **env)
        assert result['open'] is True and result['name'] == 'c2.qti.avc.decoder', (env, result)
        assert result['sessions'] == 0 and len(broker.configs) == 1


ENCODER = '/dev/rungic-test-video33'


# covers: apps.hw-codec/E10
def test_the_v4l2_encoder_takes_i420_and_gives_headers_then_frames(driver):
    result = run(driver, 'encode', frames=5, RUNGIC_CODEC_V4L2_ENCODER=ENCODER)
    assert result['open'] is True and result['name'] == 'msm_vidc_encoder (V4L2)', result
    assert result['violations'] == 0 and result['failures'] == 0 and result['ended'] == 1, result
    log = result['log']
    assert log.index('encoder stream pictures') < log.index('encoder stream coded') < log.index('encoder stop')
    records = result['records']
    headers = '000000016742c01f0000000168ce3c80'
    # The parameter sets once (the forced IDR repeats the same ones), then every frame without them.
    assert [(r['type'], r['data']) for r in records if r['type'] == 3] == [(3, headers)]
    frames = [r for r in records if r['type'] == 1]
    assert [r['id'] for r in frames] == list(range(5)) and [r['pts'] for r in frames] == [i * 33333 for i in range(5)]
    assert [r['flags'] for r in frames] == [1, 0, 0, 1, 0]                     # the first and the forced one
    for i, r in enumerate(frames):
        slice_type = '65' if i in (0, 3) else '41'
        assert r['data'] == f'00000001{slice_type}{16 + i:02x}{100 + i:02x}{200 + i:02x}'   # Y, then Cb Cr interleaved

