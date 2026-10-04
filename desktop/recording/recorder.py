#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""KWin PipeWire node(s) -> Android H.264 + PulseAudio -> MP4. No portal bypass:
only plasmashell creates the restricted KWin screen streams and passes their nodes.

With several screens (the phone and a cast TV) every screen gets its own file.
All branches run in one pipeline on one clock, so the files start and end
together; the audio is encoded once and written into each file.
"""
import configparser
import fcntl
import os
from pathlib import Path
import signal
import threading
import sys
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

CONFIG = Path.home() / '.config/rungic-screen-recording.ini'
PROFILES = {'standard': (4000, 30), 'high': (8000, 30), 'smooth': (12000, 60)}
SOURCES = {'none': [], 'system': ['android.monitor'], 'microphone': ['android_microphone'],
           'both': ['android.monitor', 'android_microphone']}

def settings():
    c = configparser.ConfigParser()
    c.read(CONFIG)
    p = c['Recording'] if c.has_section('Recording') else {}
    return p.get('quality', 'high'), p.get('audio', 'system')

def gpu_convert():
    """Flip, scale and convert the screen on the GPU (GL) rather than the CPU: at 1080x2400 and
    30 fps the whole video branch took about 1.1 cores with the CPU conversion, 0.7 with GL, most
    of that the read-back of the converted picture (docs/108).
    RUNGIC_RECORDING_CONVERT=cpu|gl overrides; the default is GL in a session that has the
    elements and a display."""
    wanted = os.environ.get('RUNGIC_RECORDING_CONVERT', 'auto')
    if wanted in ('cpu', 'gl'):
        return wanted == 'gl'
    return bool(os.environ.get('WAYLAND_DISPLAY')) and all(
        Gst.ElementFactory.find(n) for n in ('glupload', 'glvideoflip', 'glcolorscale', 'glcolorconvert', 'gldownload'))

VIDIOC_QUERYCAP = 0x80685600   # _IOR('V', 0, struct v4l2_capability), 104 bytes


def v4l2_encoder():
    """Whether librungiccodec will encode through msm_vidc's V4L2 encoder (codec-v4l2.c finds it the
    same way: a /dev/video* node whose card is msm_vidc_encoder), so that the encoder element can draw
    textures into its picture buffers. Without it the element reads textures back."""
    if os.environ.get('RUNGIC_CODEC_V4L2') == '0' or os.environ.get('RUNGIC_CODEC_DISABLE'):
        return False
    forced = os.environ.get('RUNGIC_CODEC_V4L2_ENCODER')
    nodes = [forced] if forced else sorted(str(p) for p in Path('/dev').glob('video*'))
    for node in nodes:
        try:
            fd = os.open(node, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError:
            continue
        try:
            cap = bytearray(104)
            fcntl.ioctl(fd, VIDIOC_QUERYCAP, cap)
            if bytes(cap[16:48]).split(b'\0', 1)[0] == b'msm_vidc_encoder':
                return True
        except OSError:
            pass
        finally:
            os.close(fd)
    return False


def texture_input():
    """The GL texture format the encoder takes itself, or None: the GPU writes the picture into the
    V4L2 encoder's own picture buffers, nothing is read back (docs/108). RGBA (the encoder converts
    it with a shader) saves the last glcolorconvert; NV12 is copied as it is. Older elements without
    GLMemory input get the picture through gldownload. RGBA only with the V4L2 encoder: without it the
    element would read RGBA back and convert it on the CPU, NV12 textures are read back as they are."""
    factory = Gst.ElementFactory.find('rungich264enc')
    if not factory:
        return None
    formats = set()
    for template in factory.get_static_pad_templates():
        if template.direction == Gst.PadDirection.SINK:
            caps = template.get_caps()
            for i in range(caps.get_size()):
                if caps.get_features(i).contains('memory:GLMemory'):
                    text = caps.get_structure(i).to_string()
                    formats.update(f for f in ('RGBA', 'NV12') if f in text)
    if 'RGBA' in formats and v4l2_encoder():
        return 'RGBA'
    return 'NV12' if 'NV12' in formats else None

def partial_path(output):
    return output.with_suffix('.partial.mp4')

class Recorder:
    def __init__(self, streams):
        """streams: [(pipewire node, output path)], the first is the phone."""
        Gst.init(None)
        quality, audio = settings()
        bitrate, fps = PROFILES.get(quality, PROFILES['high'])
        sources = SOURCES.get(audio, SOURCES['system'])
        self.outputs = [Path(output) for _, output in streams]
        self.fps = fps
        # The Adreno/Qualcomm H.264 encoder takes 1080x2400 only at 30 fps and
        # alone; a second session or 60 fps needs every stream within the
        # 1080p class (measured 2026-09-24). Such streams are scaled to fit.
        self.fit_1080p = len(streams) > 1 or fps > 30
        self.gl = gpu_convert()
        self.textures = texture_input() if self.gl else None
        # The encoder's format: RGBA textures (it converts them), else NV12 (textures or read back).
        picture = self.textures or 'NV12'
        last = f'! video/x-raw(memory:GLMemory),format={picture} ' + ('' if self.textures else '! gldownload ')
        raw = 'video/x-raw(memory:GLMemory)' if self.textures else 'video/x-raw'
        video = ''
        for i, (node, _) in enumerate(streams):
            # GL: the stream's buffers go to the GPU as they come (DMA-BUFs where KWin gives them),
            # flipped, scaled to fit{i} and converted to NV12 there; the encoder takes NV12 as is.
            if self.gl:
                # glvideoflip and glcolorscale take RGBA textures: a first glcolorconvert gives them that.
                convert = (f'! glupload ! glcolorconvert ! glvideoflip name=flip{i} video-direction=auto '
                           f'! queue max-size-buffers=4 leaky=downstream ! glcolorscale ! capsfilter name=fit{i} ! glcolorconvert '
                           + last)
            else:
                convert = (f'! video/x-raw ! videoflip name=flip{i} video-direction=auto ! queue max-size-buffers=4 leaky=downstream '
                           f'! videoconvertscale n-threads=2 ! capsfilter name=fit{i} ')
            video += (f'pipewiresrc name=video{i} path={node} do-timestamp=true provide-clock=false keepalive-time={1000//fps} '
                      + convert +
                      f'! videorate ! {raw},format={picture},framerate={fps}/1 '
                      # The encoder runs in its own thread and drops what it cannot keep up with here
                      # (measured about 50 fps at 864x1920). Without this, videorate filled every gap
                      # with duplicates in the converter's thread: the stream fell behind real time
                      # (7 s per minute), the first queue dropped nearly every screen frame, and the
                      # backlog had to be encoded before the file could be finished.
                      f'! queue name=enc{i} max-size-buffers=4 max-size-bytes=0 max-size-time=0 leaky=downstream '
                      f'! rungich264enc bitrate={bitrate} ! h264parse ! queue ! mux{i}.video_0 ')
        audio_pipe = ''
        if sources:
            # Both sources use the pipeline clock; silence source keeps idle intervals timed.
            audio_pipe = ('audiomixer name=mix ignore-inactive-pads=true '
                          '! clocksync ! audioconvert ! audio/x-raw,format=F32LE,rate=48000,channels=2 '
                          '! avenc_aac bitrate=192000 ! aacparse ! tee name=aac '
                          'audiotestsrc name=silence is-live=true wave=silence ! audio/x-raw,rate=48000,channels=2 ! mix. ')
            for i in range(len(streams)):
                audio_pipe += f'aac. ! queue ! mux{i}.audio_0 '

            for index, source in enumerate(sources):
                gain = 0.5 if len(sources) == 2 else 1.0
                audio_pipe += (f'pulsesrc name=audio{index} device={source} provide-clock=false buffer-time=200000 '
                               '! audio/x-raw,rate=48000 ! queue ! audioconvert ! audioresample '
                               f'! audio/x-raw,rate=48000,channels=2 ! volume volume={gain} ! mix. ')
        muxers = ''.join(f'mp4mux name=mux{i} fragment-duration=1000 ! filesink name=file{i} '
                         for i in range(len(streams)))
        self.pipeline = Gst.parse_launch(video + audio_pipe + muxers)
        for i in range(len(streams)):
            pad = self.pipeline.get_by_name(f'flip{i}').get_static_pad('src')
            pad.add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, self.fit_size, i)
        for i, output in enumerate(self.outputs):
            self.pipeline.get_by_name(f'file{i}').set_property('location', str(partial_path(output)))
        self.pipeline.use_clock(Gst.SystemClock.obtain())
        self.loop = GLib.MainLoop()
        self.ok = False
        self.stopping = False
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message', self.message)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.stop)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.stop)
        GLib.timeout_add_seconds(15, self.start_timeout)
        self.started = False
        # Reserve names only after constructing the pipeline successfully.
        # A pre-existing partial file must also leave no empty final file behind.
        self.final_fds = []
        reserved = []
        try:
            for output in self.outputs:
                self.final_fds.append(os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
                reserved.append(output)
                os.close(os.open(partial_path(output), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
                reserved.append(partial_path(output))
        except Exception:
            for fd in self.final_fds:
                os.close(fd)
            for path in reserved:
                path.unlink(missing_ok=True)
            raise
        print(f'CONFIG quality={quality} bitrate={bitrate} fps={fps} audio={audio} screens={len(streams)} '
              f'convert={"gl-" + self.textures.lower() if self.textures else "gl" if self.gl else "cpu"}', flush=True)

    def start_timeout(self):
        if not self.started:
            print('ERROR Screen capture did not start', flush=True)
            self.loop.quit()
        return False

    def fit_size(self, pad, info, index):
        """Choose the encoded size once the screen's (rotated) size is known."""
        event = info.get_event()
        if event.type != Gst.EventType.CAPS:
            return Gst.PadProbeReturn.OK
        structure = event.parse_caps().get_structure(0)
        ok_w, width = structure.get_int('width')
        ok_h, height = structure.get_int('height')
        if not (ok_w and ok_h):
            return Gst.PadProbeReturn.OK
        target_w, target_h = width, height
        if self.fit_1080p:
            long_edge, short_edge = max(width, height), min(width, height)
            scale = min(1.0, 1920 / long_edge, 1088 / short_edge)
            target_w = max(16, int(width * scale) // 2 * 2)
            target_h = max(16, int(height * scale) // 2 * 2)
        memory = '(memory:GLMemory)' if self.gl else ''
        caps = Gst.Caps.from_string(f'video/x-raw{memory},width={target_w},height={target_h}')
        fit = self.pipeline.get_by_name(f'fit{index}')
        if not fit.get_property('caps') or not fit.get_property('caps').is_equal(caps):
            fit.set_property('caps', caps)
            print(f'SIZE screen {index} {width}x{height} -> {target_w}x{target_h}@{self.fps}', flush=True)
        return Gst.PadProbeReturn.OK

    def external_branch(self, element):
        """Index of the external screen's video branch an element belongs to, or None."""
        while element is not None:
            name = element.get_name()
            if name.startswith('video') and name[5:].isdigit() and int(name[5:]) > 0:
                return int(name[5:])
            element = element.get_parent()
        return None

    def message(self, bus, message):
        if message.type == Gst.MessageType.ERROR:
            error, detail = message.parse_error()
            branch = self.external_branch(message.src)
            if branch is not None and not self.stopping:
                # The TV went away mid-recording: finish its file, keep recording the phone.
                print(f'WARN screen {branch} ended: {error.message}', flush=True)
                source = self.pipeline.get_by_name(f'video{branch}')
                threading.Thread(target=lambda: source.send_event(Gst.Event.new_eos()), daemon=True).start()
                return
            print(f'ERROR {error.message} ({detail})', flush=True)
            self.loop.quit()
        elif message.type == Gst.MessageType.EOS:
            self.ok = True
            self.loop.quit()
        elif message.type == Gst.MessageType.STATE_CHANGED and message.src == self.pipeline:
            if message.parse_state_changed()[1] == Gst.State.PLAYING:
                self.started = True
                print('READY', flush=True)

    def stop(self):
        if not self.stopping:
            self.stopping = True
            print('STOPPING', flush=True)
            # Keep the clocked silence/video branches alive while PulseAudio
            # drains. Ending silence first lets the mixer race through padding
            # and can leave another source blocked behind a full queue.
            threading.Thread(target=self.end_sources, daemon=True).start()
            GLib.timeout_add_seconds(12, self.stop_timeout)
        return False

    def end_sources(self):
        def end(name):
            source = self.pipeline.get_by_name(name)
            if source:
                result = source.send_event(Gst.Event.new_eos())
                print(f'EOS {name} accepted={result}', flush=True)
        # A static screen may have no pending video buffer. End video and real
        # audio concurrently so the muxer can drain either queue. Keep clocked
        # silence until these sources have accepted EOS: ending it first lets
        # audiomixer pad indefinitely after a suspended PulseAudio monitor.
        names = [f'video{i}' for i in range(len(self.outputs))] + ['audio0', 'audio1']
        threads = [threading.Thread(target=end, args=(name,), daemon=True)
                   for name in names
                   if self.pipeline.get_by_name(name)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        end('silence')

    def stop_timeout(self):
        print('ERROR Timed out finalizing recording', flush=True)
        self.loop.quit()
        return False

    def run(self):
        try:
            self.pipeline.set_state(Gst.State.PLAYING)
            self.loop.run()
        finally:
            self.pipeline.set_state(Gst.State.NULL)
            for fd in self.final_fds:
                os.close(fd)
        if self.ok and all(partial_path(o).stat().st_size > 0 for o in self.outputs):
            for output in self.outputs:
                os.replace(partial_path(output), output)
                print(f'SAVED {output}', flush=True)
            return 0
        # Keep nonempty partial files for recovery; never claim an incomplete file is saved.
        for output in self.outputs:
            output.unlink(missing_ok=True)
            partial = partial_path(output)
            if partial.exists() and not partial.stat().st_size:
                partial.unlink()
        return 1

if __name__ == '__main__':
    args = sys.argv[1:]
    if len(args) < 2 or len(args) % 2 or not all(a.isdigit() for a in args[0::2]):
        raise SystemExit('usage: rungic-screen-recorder NODE OUTPUT.mp4 [NODE OUTPUT.mp4 ...]')
    try:
        raise SystemExit(Recorder([(int(n), o) for n, o in zip(args[0::2], args[1::2])]).run())
    except Exception as e:
        print(f'ERROR {e}', flush=True)
        raise SystemExit(1)
