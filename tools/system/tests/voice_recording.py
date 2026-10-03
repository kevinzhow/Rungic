# SPDX-License-Identifier: MIT
"""Build the native recording helper and run its lifecycle checks on Ubuntu ARM64.

Three lifecycle cases use audio process stand-ins. Another records a one-second tone
through real PulseAudio, the production router and playback, then checks source restoration.
No hardware microphone or real recipient is involved.
"""
import importlib.util
import json
import math
import os
from pathlib import Path
import select
import shlex
import shutil
import signal
import struct
import subprocess
import tempfile
import time
import unittest

import harness


def pulse_recording(binary):
    """Real PulseAudio, production router and playback, with an isolated null device."""
    with tempfile.TemporaryDirectory(prefix='voice-pulse-') as temporary:
        root = Path(temporary)
        config = root / 'pulse.pa'
        config.write_text(f'load-module module-native-protocol-unix socket={root}/native auth-anonymous=1\n'
                          'load-module module-null-sink sink_name=original rate=24000 channels=1\n'
                          'load-module module-null-sink sink_name=linux_microphone_input rate=24000 channels=1\n'
                          'load-module module-remap-source master=linux_microphone_input.monitor '
                          'source_name=linux_microphone rate=24000 channels=1\n'
                          'set-default-sink original\nset-default-source original.monitor\n')
        env = {**os.environ, 'PULSE_SERVER': f'unix:{root}/native', 'XDG_RUNTIME_DIR': str(root)}
        tone = b''.join(struct.pack('<h', round(10000 * math.sin(2 * math.pi * 440 * i / 24000)))
                        for i in range(24000))
        (root / 'tone.pcm').write_bytes(tone)
        shutil.copy('/usr/bin/pacat', root / 'voice-test-app')
        children = []
        log = open(root / 'pulse.log', 'w')
        recorded = open(root / 'recording.pcm', 'wb')
        def wait_for(condition, detail):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if condition():
                    return
                time.sleep(.05)
            raise harness.Failed(detail)
        def request(helper, phase):
            helper.stdin.write(json.dumps({'phase': phase}) + '\n')
            helper.stdin.flush()
            if not select.select([helper.stdout], [], [], 8)[0]:
                raise harness.Failed('native helper did not answer ' + phase)
            return json.loads(helper.stdout.readline())
        def sources():
            done = subprocess.run(['pactl', '-f', 'json', 'list', 'source-outputs'], env=env,
                                  capture_output=True, text=True, check=True, timeout=8)
            return json.loads(done.stdout)
        try:
            pulse = subprocess.Popen(['pulseaudio', '-n', '-F', str(config), '--daemonize=no',
                                      '--exit-idle-time=-1', '--use-pid-file=no'], env=env,
                                     stdout=log, stderr=log)
            children.append(pulse)
            wait_for(lambda: (root / 'native').exists(), 'private PulseAudio did not start')
            recorder = subprocess.Popen([str(root / 'voice-test-app'), '--record', '--raw',
                                         '--format=s16le', '--rate=24000', '--channels=1', '--latency-msec=20'],
                                        env=env, stdout=recorded, stderr=log)
            children.append(recorder)
            wait_for(lambda: sources(), 'real application did not open its capture stream')
            original = sources()[0]['source']
            helper = subprocess.Popen([binary, 'voice-test-app', str(root / 'tone.pcm')], env=env,
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
            children.append(helper)
            if not select.select([helper.stdout], [], [], 8)[0]:
                raise harness.Failed('native helper did not become ready')
            initial = json.loads(helper.stdout.readline())
            if not initial.get('ready'):
                raise harness.Failed('native helper startup: ' + str(initial))
            wait_for(lambda: request(helper, 'status').get('recording'), 'production router did not report recording')
            if not request(helper, 'play').get('played'):
                raise harness.Failed('native helper did not finish real PulseAudio playback')
            helper.stdin.close()
            helper.wait(8)
            wait_for(lambda: sources()[0]['source'] == original, 'EOF did not restore the real capture stream')
            recorder.send_signal(signal.SIGINT)
            recorder.wait(5)
            recorded.close()
            data = (root / 'recording.pcm').read_bytes()
            samples = struct.unpack(f'<{len(data) // 2}h', data)
            loud = [i for i, value in enumerate(samples) if abs(value) > 500]
            duration = (loud[-1] - loud[0] + 1) / 24000 if loud else 0
            if not .97 <= duration <= 1.03 or len(loud) < 22000 or max(samples, default=0) < 8000:
                raise harness.Failed('recorded audio did not contain the complete one-second playback')
        finally:
            for process in reversed(children):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(5)
            recorded.close()
            log.close()
    return 'real capture stream received one-second audio; EOF restored its original source'


# covers[system]: agent.voice-message/E1
def test():
    binary = '/tmp/rungic-voice-recording-test'
    flags = subprocess.check_output(['pkg-config', '--cflags', '--libs', 'Qt6Core'], text=True)
    build = subprocess.run(['c++', '-std=c++17', '-fPIC',
                            '/src/agent/computer-use/voice-recording.cpp', '-o', binary,
                            *shlex.split(flags)], capture_output=True, text=True, timeout=120)
    if build.returncode:
        raise harness.Failed('native helper build: ' + build.stderr[-1500:])
    os.environ['RUNGIC_VOICE_RECORDING_TEST_BINARY'] = binary
    spec = importlib.util.spec_from_file_location('native_recording', '/src/tools/tests/test_voice_recording_native.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(module))
    if not result.wasSuccessful() or result.skipped or result.testsRun != 3:
        raise harness.Failed(f'native lifecycle: {result.testsRun} run, {len(result.skipped)} skipped, '
                             f'{len(result.failures)} failures, {len(result.errors)} errors')
    return ['built the real Qt recording helper', '3 native lifecycle checks passed; 0 skipped', pulse_recording(binary)]


if __name__ == '__main__':
    harness.run('voice_recording', test)
