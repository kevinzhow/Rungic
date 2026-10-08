#!/usr/bin/env python3
"""system/rungic-migrate: one-time migrations by name, each once, recorded in a ledger (docs/122)."""
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'system/rungic-migrate'
NAME = re.compile(r'^\d{4}[01]\d[0-3]\d-[a-z0-9][a-z0-9-]*\.sh$')


class Runner(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.dir = self.root / 'migrations'
        self.dir.mkdir()
        self.ledger = self.root / 'state/migrations.done'
        self.ran = self.root / 'ran'

    def migration(self, name, ok=True):
        (self.dir / f'{name}.sh').write_text(f'echo {name} >> "{self.ran}"\n' + ('' if ok else 'exit 3\n'))

    def run_runner(self):
        return subprocess.run(['sh', str(RUNNER), str(self.dir), str(self.ledger)], capture_output=True, text=True,
                              timeout=30)

    def ran_names(self):
        return self.ran.read_text().split() if self.ran.exists() else []

    # covers: install.independent-runtime/E8
    def test_each_runs_once_in_name_order_whatever_order_they_arrived_in(self):
        self.migration('20261009-second')
        self.migration('20261008-first')
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ['20261008-first', '20261009-second'])
        self.assertEqual(self.ran_names(), ['20261008-first', '20261009-second'])
        # A branch merged later with an earlier date still runs; the done ones do not run again.
        self.migration('20261007-merged-late')
        self.assertEqual(self.run_runner().stdout.split(), ['20261007-merged-late'])
        self.assertEqual(self.ran_names(), ['20261008-first', '20261009-second', '20261007-merged-late'])
        self.assertEqual(self.ledger.read_text().split(), ['20261008-first', '20261009-second', '20261007-merged-late'])

    # covers: install.independent-runtime/E8
    def test_a_failure_is_not_recorded_and_stops_the_later_ones_until_the_next_run(self):
        self.migration('20261008-a')
        self.migration('20261009-b', ok=False)
        self.migration('20261010-c')
        result = self.run_runner()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('20261009-b failed', result.stderr)
        self.assertEqual(self.ledger.read_text().split(), ['20261008-a'])
        self.migration('20261009-b')                    # fixed by the next release
        self.assertEqual(self.run_runner().stdout.split(), ['20261009-b', '20261010-c'])

    # covers: install.independent-runtime/E8
    def test_odd_names_and_no_directory_are_left_alone(self):
        (self.dir / 'cleanup.sh').write_text('exit 9\n')
        result = self.run_runner()
        self.assertEqual(result.returncode, 0)
        self.assertIn('not named YYYYMMDD-what', result.stderr)
        self.assertEqual(subprocess.run(['sh', str(RUNNER), str(self.root / 'none'), str(self.ledger)]).returncode, 0)


class Shipped(unittest.TestCase):
    # covers: install.independent-runtime/E8
    def test_shipped_migrations_are_named_and_reach_the_phones(self):
        # Android-side ones go out in both Android file lists, at the path rungic-converge reads.
        spec = json.loads((ROOT / 'release/packages.json').read_text())
        release = {e['path']: e['source'] for e in spec['android']}
        seed = (ROOT / 'tools/ci/build_host_seed.py').read_text()
        for scope in ('android', 'system'):
            for path in sorted((ROOT / 'system/migrations' / scope).glob('*.sh')):
                self.assertRegex(path.name, NAME)
                if scope == 'android':
                    source = f'system/migrations/android/{path.name}'
                    self.assertEqual(release.get(f'/data/adb/rungic-plasma/migrations/{path.name}'), source)
                    self.assertIn(f'("{source}", "rungic-plasma/migrations/{path.name}"', seed)
        # Container ones come with rungic-plasma-config, which runs them after configuring.
        package = json.loads((ROOT / 'packaging/rungic-plasma-config/package.json').read_text())
        self.assertIn('system/migrations/system', package['paths'])
        self.assertIn('/usr/libexec/rungic-migrate /usr/lib/rungic/migrations/system /var/lib/rungic/migrations',
                      (ROOT / 'packaging/rungic-plasma-config/postinst').read_text())


if __name__ == '__main__':
    unittest.main()
