"""Run against the real native lifecycle with fake audio processes, never real audio."""
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile
import time
import unittest

BINARY = os.environ.get('RUNGIC_VOICE_RECORDING_TEST_BINARY', '')


@unittest.skipUnless(BINARY, 'set RUNGIC_VOICE_RECORDING_TEST_BINARY to a built native helper')
class RecordingTest(unittest.TestCase):
    def start(self, recorded=False, fail=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        pcm = root / 'speech.pcm'
        pcm.write_bytes(b'\x01\x00' * 2400)
        route = root / 'rungic-audio-route'
        route.write_text('#!/usr/bin/python3\nimport sys,os\nprint("ready",flush=True)\n'
            + ('print("routed source-output 42 -> linux_microphone",flush=True)\n' if recorded else '')
            + 'sys.stdin.read()\nopen(os.environ["RECORD_LOG"]+".closed","w").write("closed")\n')
        route.chmod(0o755)
        player = root / 'pacat'
        player.write_text('#!/usr/bin/python3\nimport sys,os\ndata=sys.stdin.buffer.read()\n'
            'with open(os.environ["RECORD_LOG"],"ab") as f: f.write(data)\n'
            + ('sys.exit(1)\n' if fail else ''))
        player.chmod(0o755)
        process = subprocess.Popen([BINARY, 'test-app', str(pcm)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, 'PATH': str(root) + ':' + os.environ['PATH'], 'RECORD_LOG': str(root / 'played')})
        def cleanup():
            if process.poll() is None:
                process.stdin.close()
                process.wait(8)
            if not process.stdin.closed:
                process.stdin.close()
            process.stdout.close()
            process.stderr.close()
        self.addCleanup(cleanup)
        self.read(process)
        return process, root

    def read(self, process):
        self.assertTrue(select.select([process.stdout], [], [], 8)[0], 'helper did not reply')
        return json.loads(process.stdout.readline())

    def request(self, process, phase):
        process.stdin.write(json.dumps({'phase': phase}) + '\n')
        process.stdin.flush()
        return self.read(process)

    # covers: agent.voice-message/E1
    def test_no_recording_cannot_play(self):
        process, root = self.start()
        result = self.request(process, 'play')
        self.assertIn('error', result)
        self.assertFalse((root / 'played').exists())
        self.request(process, 'close')
        process.wait(8)
        self.assertTrue((root / 'played.closed').exists())

    # covers: agent.voice-message/E1
    def test_playback_once_and_route_cleanup_on_eof(self):
        process, root = self.start(recorded=True)
        for _ in range(20):
            state = self.request(process, 'status')
            if state['recording']:
                break
            time.sleep(0.02)
        self.assertTrue(state['recording'])
        self.assertTrue(self.request(process, 'play')['played'])
        self.assertTrue(self.request(process, 'play')['played'])
        self.assertEqual((root / 'played').read_bytes(), (root / 'speech.pcm').read_bytes())
        process.stdin.close()
        process.wait(8)
        self.assertTrue((root / 'played.closed').exists())

    # covers: agent.voice-message/E1
    def test_failed_playback_is_not_replayed(self):
        process, root = self.start(recorded=True, fail=True)
        for _ in range(20):
            if self.request(process, 'status')['recording']:
                break
            time.sleep(0.02)
        self.assertIn('error', self.request(process, 'play'))
        self.assertIn('error', self.request(process, 'play'))
        self.assertEqual((root / 'played').read_bytes(), (root / 'speech.pcm').read_bytes())


if __name__ == '__main__':
    unittest.main()
