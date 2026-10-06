"""Run the APK root query in local filesystems. No handset or ADB access."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

class AppRemoval(unittest.TestCase):
    def test_controller_removal_message_matches_java_contract(self):
        # covers: install.removal-app-status/E1
        java=(ROOT/'android/app/src/com/rungic/plasma/RemovalState.java').read_text()
        literal=re.search(r'INCOMPLETE_MESSAGE = "(.*?)";',java).group(1)
        import json
        expected=json.loads('"'+literal+'"')
        for name in ('system/rungic-plasma','system/rungic-runtime','tools/ci/rungic-firstboot.sh'):
            with self.subTest(script=name):
                messages=[]
                for line in (ROOT/name).read_text().splitlines():
                    if 'rungic-uninstalling' not in line:continue
                    messages.extend(match.group(2) for match in re.finditer(r"echo\s+(['\"])(.*?)\1",line))
                self.assertTrue(messages,'The removal guard no longer emits a checked message')
                for message in messages:self.assertEqual(message,expected,name)

    def test_marker_query_without_controller_and_unknown_root_access(self):
        # covers: install.removal-app-status/E1 install.removal-app-status/E2
        source = (ROOT / 'android/app/src/com/rungic/plasma/RemovalState.java').read_text()
        literal = re.search(r'ROOT_COMMAND = "(.*?)";', source).group(1)
        import json
        command = json.loads('"' + literal + '"')
        for shell in (['bash'], ['/usr/bin/busybox', 'ash']):
            with tempfile.TemporaryDirectory() as directory:
                adb = Path(directory) / 'adb'
                adb.mkdir()
                marker = adb / 'rungic-uninstalling'
                script = command.replace('/data/adb', str(adb))
                def run(prelude='id() { echo 0; }\n'):
                    return subprocess.run([*shell, '-c', prelude + script], capture_output=True, text=True, timeout=5)
                for content in ('', 'operation:0:-\n'):
                    marker.write_text(content)
                    result = run()
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), 'RUNGIC_REMOVAL_PENDING')
                    self.assertFalse((adb / 'rungic-plasma').exists())
                marker.unlink()
                result = run()
                self.assertEqual(result.stdout.strip(), 'RUNGIC_REMOVAL_ABSENT')
                marker.symlink_to(adb / 'missing')
                self.assertEqual(run().stdout.strip(), 'RUNGIC_REMOVAL_PENDING')
                marker.unlink()
                for prelude in ('id() { echo 2000; }\n', 'id() { return 1; }\n', 'id() { echo 0; }\nls() { return 1; }\n', 'id() { echo 0; }\ngrep() { return 2; }\n'):
                    result = run(prelude)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn('RUNGIC_REMOVAL_ABSENT', result.stdout)
                self.assertEqual(list(adb.iterdir()), [])
