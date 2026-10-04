# SPDX-License-Identifier: MIT
"""The screen recorder (desktop/recording/recorder.py, installed as rungic-screen-recorder) run for
real, as the recording quick setting runs it: NODE OUTPUT pairs, the phone first, SIGTERM to stop.
What it records from is not here: KWin's PipeWire streams are videotestsrc, PulseAudio's sources
audiotestsrc (each in place of the very element the recorder names, so its pipeline is otherwise its
own), and Android's hardware H.264 encoder (rungich264enc, the codec interface) is a stand-in element
of that name encoding with x264. The files are read back with GStreamer's discoverer.

Requires GStreamer's Python bindings with the base, good and libav plugins (Ubuntu's gstreamer1.0-*);
skipped where they are missing."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

gi = pytest.importorskip('gi')
try:
    gi.require_version('Gst', '1.0')
    gi.require_version('GstPbutils', '1.0')
    from gi.repository import Gst, GstPbutils
    Gst.init(None)
except (ValueError, ImportError):
    pytest.skip('GStreamer bindings missing', allow_module_level=True)
NEEDED = ('x264enc', 'avenc_aac', 'mp4mux', 'h264parse', 'aacparse', 'audiomixer', 'clocksync', 'videotestsrc',
          'audiotestsrc', 'videoconvertscale', 'videoflip', 'videorate', 'qtdemux')
if not all(Gst.ElementFactory.find(n) for n in NEEDED):
    pytest.skip('GStreamer plugins missing', allow_module_level=True)

RECORDER = Path(__file__).resolve().parents[2] / 'desktop/recording/recorder.py'

# Runs recorder.py as its __main__, with the stand-ins in place. Environment:
#   STANDIN_LOG       where to write what the recorder asked for (its sources and pipeline)
#   STANDIN_ENCODER   1: register the stand-in rungich264enc (0: no hardware encoder at all)
#   STANDIN_SLOW_US   the encoder's time per frame, to make it slower than the screen
#   STANDIN_SIZES     WxH of each screen, the phone first
#   STANDIN_TV_GONE   ms after the start when the second screen's stream fails (the TV unplugged)
HELPER = r'''
import json, os, re, runpy, sys
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GObject, GLib
Gst.init(None)

class StandInEncoder(Gst.Bin):
    """Android's H.264 encoder as the recorder uses it: raw video in, H.264 out, bitrate in kbit/s."""
    __gstmetadata__ = ('Android H.264 encoder (stand-in)', 'Codec/Encoder/Video', 'x264 for the test', 'test')
    __gproperties__ = {'bitrate': (int, 'bitrate', 'kbit/s', 1, 1000000, 4000, GObject.ParamFlags.READWRITE)}

    def __init__(self):
        super().__init__()
        self.bitrate = 4000
        slow = Gst.ElementFactory.make('identity')
        slow.set_property('sleep-time', int(os.environ.get('STANDIN_SLOW_US', '0')))
        self.enc = Gst.ElementFactory.make('x264enc')
        self.enc.set_property('speed-preset', 'ultrafast')
        self.enc.set_property('tune', 'zerolatency')
        self.enc.set_property('key-int-max', 30)
        for e in (slow, self.enc):
            self.add(e)
        slow.link(self.enc)
        self.add_pad(Gst.GhostPad.new('sink', slow.get_static_pad('sink')))
        self.add_pad(Gst.GhostPad.new('src', self.enc.get_static_pad('src')))

    def do_get_property(self, prop):
        return self.bitrate

    def do_set_property(self, prop, value):
        self.bitrate = value
        self.enc.set_property('bitrate', value)

if os.environ.get('STANDIN_ENCODER') == '1':
    GObject.type_register(StandInEncoder)
    Gst.Element.register(None, 'rungich264enc', Gst.Rank.NONE, StandInEncoder)

sizes = os.environ.get('STANDIN_SIZES', '360x800,640x360').split(',')
real_parse = Gst.parse_launch

def parse(text):
    asked = {'video': re.findall(r'pipewiresrc name=(video\d+) path=(\d+)', text),
             'audio': re.findall(r'pulsesrc name=(audio\d+) device=(\S+)', text), 'pipeline': text}
    with open(os.environ['STANDIN_LOG'], 'w') as f:
        json.dump(asked, f)
    def screen(m):
        w, h = sizes[int(m[1])].split('x')
        return f'videotestsrc name=video{m[1]} is-live=true pattern=ball ! capsfilter caps="video/x-raw,width={w},height={h},framerate=60/1" '
    text = re.sub(r'pipewiresrc name=video(\d+) path=\d+[^!]*', screen, text)
    text = re.sub(r'pulsesrc name=(audio\d+) device=\S+[^!]*', lambda m: f'audiotestsrc name={m[1]} is-live=true wave=sine ', text)
    pipeline = real_parse(text)
    gone = os.environ.get('STANDIN_TV_GONE')
    if gone:
        def fail():
            source = pipeline.get_by_name('video1')
            source.post_message(Gst.Message.new_error(source, GLib.Error.new_literal(
                Gst.ResourceError.quark(), 'stream ended', int(Gst.ResourceError.READ)), 'stand-in: the TV went away'))
            return False
        GLib.timeout_add(int(gone), fail)
    return pipeline

Gst.parse_launch = parse
sys.argv = [sys.argv[1]] + sys.argv[2:]
runpy.run_path(sys.argv[0], run_name='__main__')
'''


class Run:
    def __init__(self, lines, code, stop_seconds, asked):
        self.lines, self.code, self.stop_seconds, self.asked = lines, code, stop_seconds, asked

    def config(self):
        line = next(l for l in self.lines if l.startswith('CONFIG '))
        return dict(kv.split('=') for kv in line.split()[1:])


def record(tmp_path, screens=1, seconds=2.0, quality=None, audio=None, encoder=True, slow_us=0, sizes=None,
           tv_gone_ms=None):
    """Records `screens` screens for `seconds`, then stops it as the quick setting does (SIGTERM)."""
    home = tmp_path / 'home'
    (home / '.config').mkdir(parents=True, exist_ok=True)
    videos = home / 'Videos'
    videos.mkdir(exist_ok=True)
    if quality or audio:
        (home / '.config/rungic-screen-recording.ini').write_text(
            '[Recording]\n' + (f'quality = {quality}\n' if quality else '') + (f'audio = {audio}\n' if audio else ''))
    helper = tmp_path / 'helper.py'
    helper.write_text(HELPER)
    log = tmp_path / 'asked.json'
    names = ['screen-recording - Phone.mp4', 'screen-recording - External.mp4'] if screens > 1 else ['screen-recording.mp4']
    args = []
    for i, name in enumerate(names):
        args += [str(40 + i), str(videos / name)]
    # The CPU conversion here; the GL one needs the phone's GPU (docs/108, recording.quicksetting).
    env = {**os.environ, 'RUNGIC_RECORDING_CONVERT': 'cpu', 'HOME': str(home), 'STANDIN_LOG': str(log), 'STANDIN_ENCODER': '1' if encoder else '0',
           'STANDIN_SLOW_US': str(slow_us), 'STANDIN_SIZES': sizes or '360x800,640x360', 'GST_DEBUG': '0'}
    env.pop('STANDIN_TV_GONE', None)
    if tv_gone_ms:
        env['STANDIN_TV_GONE'] = str(tv_gone_ms)
    process = subprocess.Popen([sys.executable, str(helper), str(RECORDER)] + args, env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    lines = []
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        lines.append(line.rstrip('\n'))
        if line.startswith('READY'):
            break
    stop_seconds = None
    if lines and lines[-1] == 'READY':
        time.sleep(seconds)
        stopped = time.monotonic()
        process.send_signal(signal.SIGTERM)
        out, _ = process.communicate(timeout=30)
        stop_seconds = time.monotonic() - stopped
        lines += out.splitlines()
    else:
        process.kill()
        lines += process.communicate()[0].splitlines()
    asked = json.loads(log.read_text()) if log.exists() else None
    return Run(lines, process.returncode, stop_seconds, asked), [videos / n for n in names]


def streams(path):
    """The file's streams as (kind, caps name, duration in s)."""
    info = GstPbutils.Discoverer.new(10 * Gst.SECOND).discover_uri(path.as_uri())
    out = []
    for s in info.get_stream_list():
        caps = s.get_caps().get_structure(0).get_name()
        if isinstance(s, GstPbutils.DiscovererVideoInfo):
            out.append(('video', caps))
        elif isinstance(s, GstPbutils.DiscovererAudioInfo):
            out.append(('audio', caps))
    return sorted(out), info.get_duration() / Gst.SECOND


def video_seconds(path):
    """How long the file's video track runs: the end of its last frame."""
    pipeline = Gst.parse_launch(f'filesrc location="{path}" ! qtdemux name=d d.video_0 ! fakesink name=s signal-handoffs=true')
    end = [0]

    def handoff(sink, buffer, pad):
        end[0] = max(end[0], buffer.pts + (buffer.duration if buffer.duration != Gst.CLOCK_TIME_NONE else 0))
    pipeline.get_by_name('s').connect('handoff', handoff)
    pipeline.set_state(Gst.State.PLAYING)
    pipeline.get_bus().timed_pop_filtered(30 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    pipeline.set_state(Gst.State.NULL)
    return end[0] / Gst.SECOND


def leftovers(videos):
    return sorted(p.name for p in videos.iterdir())


# covers: desktop.screen-recording/E1 desktop.screen-recording/E2
def test_a_recording_is_a_playable_mp4_with_h264_and_aac_finished_quickly(tmp_path):
    run, (output,) = record(tmp_path, seconds=2.5)
    assert run.code == 0, run.lines
    assert f'SAVED {output}' in run.lines
    assert run.config() == {'quality': 'high', 'bitrate': '8000', 'fps': '30', 'audio': 'system', 'screens': '1',
                            'convert': 'cpu'}
    kinds, duration = streams(output)
    assert kinds == [('audio', 'audio/mpeg'), ('video', 'video/x-h264')]
    assert 2.0 <= duration <= 3.6, duration
    assert leftovers(output.parent) == [output.name], 'no .partial.mp4 is left beside the saved file'
    assert run.stop_seconds < 3, f'stopping took {run.stop_seconds:.1f} s'
    assert run.asked['video'] == [['video0', '40']] and run.asked['audio'] == [['audio0', 'android.monitor']]


# covers: desktop.screen-recording/E2
def test_an_encoder_slower_than_the_screen_drops_frames_instead_of_falling_behind(tmp_path):
    # 60 fps asked of an encoder that manages about 25: the backlog must not grow (it once fell
    # 7 s per minute behind and had to be encoded before the file could be finished).
    run, (output,) = record(tmp_path, seconds=4, quality='smooth', slow_us=40000)
    assert run.code == 0, run.lines
    _, duration = streams(output)
    assert 3.3 <= duration <= 5, f'{duration:.1f} s of video for 4 s recorded'
    assert run.stop_seconds < 3, f'stopping took {run.stop_seconds:.1f} s'


# covers: desktop.screen-recording/E5
def test_the_settings_choose_quality_and_sound_sources(tmp_path):
    run, (output,) = record(tmp_path / 'both', quality='smooth', audio='both', sizes='1080x2400')
    assert run.code == 0, run.lines
    assert run.config()['bitrate'] == '12000' and run.config()['fps'] == '60'
    assert [d for _, d in run.asked['audio']] == ['android.monitor', 'android_microphone']
    assert 'volume volume=0.5' in run.asked['pipeline'], 'two sources are mixed at half volume each'
    assert 'SIZE screen 0 1080x2400 -> 864x1920@60' in run.lines, 'the encoder takes 60 fps only within the 1080p class'

    run, (output,) = record(tmp_path / 'mic', audio='microphone')
    assert run.code == 0 and [d for _, d in run.asked['audio']] == ['android_microphone']

    run, (output,) = record(tmp_path / 'none', quality='standard', audio='none')
    assert run.code == 0, run.lines
    assert run.config()['bitrate'] == '4000' and run.asked['audio'] == []
    assert streams(output)[0] == [('video', 'video/x-h264')], 'no sound: no audio track'


# covers: desktop.screen-recording/E5
def test_without_the_hardware_encoder_it_fails_and_does_not_encode_in_software(tmp_path):
    run, (output,) = record(tmp_path, encoder=False)
    assert run.code == 1
    assert any(l.startswith('ERROR') and 'rungich264enc' in l for l in run.lines), run.lines
    pipeline = run.asked['pipeline']
    assert not any(enc in pipeline for enc in ('x264enc', 'openh264enc', 'avenc_h264', 'x265enc', 'vp8enc'))
    assert leftovers(output.parent) == [], 'no empty or partial file claims a recording'


# covers: desktop.screen-recording/E4
def test_each_screen_gets_its_own_file_and_a_tv_that_goes_away_keeps_both(tmp_path):
    run, (phone, tv) = record(tmp_path, screens=2, seconds=3, tv_gone_ms=1500)
    assert run.code == 0, run.lines
    assert any(l.startswith('WARN screen 1 ended') for l in run.lines), run.lines
    assert f'SAVED {phone}' in run.lines and f'SAVED {tv}' in run.lines
    phone_kinds, phone_duration = streams(phone)
    tv_kinds, tv_duration = streams(tv)
    assert phone_kinds == tv_kinds == [('audio', 'audio/mpeg'), ('video', 'video/x-h264')]
    phone_video, tv_video = video_seconds(phone), video_seconds(tv)
    assert 1 <= tv_video < phone_video - 0.8, ('the TV stops where it went away, the phone goes on', tv_video, phone_video)
    assert phone_video >= 2.5 and phone_duration >= 2.5
    assert leftovers(phone.parent) == sorted([phone.name, tv.name])
