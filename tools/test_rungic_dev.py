#!/usr/bin/env python3
"""rungic_dev without a device (docs/97): development versions sort between the release's build and
the next one, overlays stack on the release they started from, and the metapackage and pins name
the overrides."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import rungic_dev
import rungic_release

RELEASE = {'version': '20260930.9', 'commit': 'be86132', 'packages': {'rungic-design': '0.510', 'kwin-wayland': '6.6.5-0+rungic8'},
           'session_restart': ['kwin-*'], 'user_restart': {'rungic-design': ['plasma-plasmashell.service']}}


def newer(a, b):
    return subprocess.run(['dpkg', '--compare-versions', a, 'gt', b]).returncode == 0


class VersionTests(unittest.TestCase):
    # covers: delivery.dev-overlay/E1
    def test_between_release_builds(self):
        dev = rungic_dev.dev_version('0.510', '20260930t221500', '32b158c', True)
        self.assertEqual(dev, '0.510+dev20260930t221500.32b158c.dirty')
        self.assertTrue(newer(dev, '0.510'))
        self.assertTrue(newer('0.511', dev))
        self.assertTrue(newer(dev, '0.510+dev20260930t221459.32b158c'))   # a later build wins

    # covers: delivery.dev-overlay/E1
    def test_release_metapackage_between(self):
        self.assertTrue(newer('20260930.9+dev20260930t221500', '20260930.9'))
        self.assertTrue(newer('20260930.10', '20260930.9+dev20260930t221500'))


class OverlayTests(unittest.TestCase):
    def override(self, version):
        return {'version': version, 'commit': 'x', 'dirty': True, 'built': 'now', 'file': f'rungic-design_{version}_arm64.deb'}

    # covers: delivery.dev-overlay/E2
    def test_overlay_on_release(self):
        info = rungic_dev.overlay_info(RELEASE, {'rungic-design': self.override('0.510+dev1.a')}, '20260930t221500')
        self.assertEqual(info['version'], '20260930.9+dev20260930t221500')
        self.assertEqual(info['packages'], {'rungic-design': '0.510+dev1.a', 'kwin-wayland': '6.6.5-0+rungic8'})
        self.assertEqual(info['dev']['base'], '20260930.9')
        self.assertEqual(info['dev']['base_packages'], RELEASE['packages'])
        self.assertEqual(info['user_restart'], RELEASE['user_restart'])

    # covers: delivery.dev-overlay/E3
    def test_overlay_on_overlay_keeps_the_base(self):
        first = rungic_dev.overlay_info(RELEASE, {'rungic-design': self.override('0.510+dev1.a')}, '1')
        second = rungic_dev.overlay_info(first, {'rungic-design': self.override('0.510+dev2.a')}, '2')
        self.assertEqual(second['version'], '20260930.9+dev2')
        self.assertEqual(second['dev']['base_packages'], RELEASE['packages'])
        self.assertEqual(rungic_dev.base_of(second), ('20260930.9', RELEASE['packages']))

    # covers: delivery.dev-overlay/E1
    def test_pins_above_the_release(self):
        info = rungic_dev.overlay_info(RELEASE, {'rungic-design': self.override('0.510+dev1.a')}, '1')
        script = rungic_dev.config_script(info)
        self.assertIn('Package: rungic-design\nPin: version 0.510+dev1.a\nPin-Priority: 1002', script)
        self.assertIn('Package: rungic-release\nPin: version 20260930.9+dev1\nPin-Priority: 1002', script)
        self.assertIn(f'URIs: file:{rungic_dev.DEVICE_REPO}', script)
        self.assertNotIn('kwin-wayland', script)          # the release's own pins stay in charge

    # covers: delivery.dev-overlay/E2
    def test_metapackage(self):
        info = rungic_dev.overlay_info(RELEASE, {'rungic-design': self.override('0.510+dev1.a')}, '1')
        with tempfile.TemporaryDirectory(dir=rungic_release.WORKSPACE / '.work/cache') as temp:
            deb = rungic_release.build_meta(info['version'], info['packages'], info, dest=Path(temp))
            depends = rungic_release.deb_field(deb, 'Depends')
            self.assertIn('rungic-design (= 0.510+dev1.a)', depends)
            self.assertIn('kwin-wayland (= 6.6.5-0+rungic8)', depends)
            self.assertEqual(rungic_release.deb_field(deb, 'Protected'), 'yes')
            listing = subprocess.run(['dpkg-deb', '--fsys-tarfile', str(deb)], capture_output=True, check=True).stdout
            extracted = subprocess.run(['tar', '-xOf', '-', './usr/share/rungic/release.json'], input=listing,
                                       capture_output=True, check=True).stdout
            self.assertEqual(json.loads(extracted)['dev']['base'], '20260930.9')



class UpstreamTests(unittest.TestCase):
    """Upstream components (packages/<name>) as overlays: their release packages, a changelog entry of
    the development version, and resets by component."""
    COMPONENTS = {'plasma-mobile': {'source': 'packages/plasma-mobile', 'version': '6.6.5-0ubuntu1+rungic3',
                                    'packages': ['plasma-mobile', 'plasma-mobile-tweaks', 'plasma-mobile-dev']}}

    # covers: delivery.dev-overlay/E5
    def test_mesa_uses_its_meson_packager_and_records_the_development_version(self):
        import build_on_device
        import build_mesa
        component = {'version': '26.3.0+rungic1', 'packages': ['libgbm1']}
        version = rungic_dev.dev_version(component['version'], '20261003t120000', 'abc1234', False)
        host = Mock(name='build-host', jobs=4)
        host.name = 'macmini'
        host.out.side_effect = ['mesa', 'resolute', '', f'libgbm1_{version}_arm64.deb']
        take = Mock()
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(rungic_dev, 'POOL', Path(temp)), \
                patch.object(rungic_dev, 'upstream_dirty', return_value=False), \
                patch.object(rungic_dev, 'git', return_value='abc1234'), \
                patch.object(rungic_dev, 'taker', return_value=take), \
                patch.object(build_on_device, 'host', host), \
                patch.object(build_on_device, 'sync'), \
                patch.object(build_on_device, 'build_deps') as deps, \
                patch.object(build_on_device, 'start') as start, \
                patch.object(build_on_device, 'status', return_value='Result=success'), \
                patch.object(build_mesa, 'package') as package:
            result = rungic_dev.build_upstream('mesa', component, {'libgbm1': component['version']},
                                               '20261003t120000', 'abc1234', Mock())
            deps.assert_not_called()
            start.assert_called_once_with('mesa', 'targets', 4)
            package.assert_called_once_with(host, version, 'abc1234')
            self.assertEqual(result['libgbm1']['version'], version)
            self.assertEqual(result['libgbm1']['component'], 'mesa')
            take.assert_called_once()

    # covers: delivery.dev-overlay/E5
    def test_names_split_and_unknown_stop(self):
        own, upstream = rungic_dev.resolve(['rungic-design', 'plasma-mobile'], {'rungic-design': {}}, self.COMPONENTS)
        self.assertEqual((own, upstream), (['rungic-design'], ['plasma-mobile']))
        with self.assertRaises(SystemExit):
            rungic_dev.resolve(['plasma-mobil'], {'rungic-design': {}}, self.COMPONENTS)

    # covers: delivery.dev-overlay/E5
    def test_only_the_release_packages(self):
        base = {'plasma-mobile': '6.6.5-0ubuntu1+rungic3', 'plasma-mobile-tweaks': '6.6.5-0ubuntu1+rungic3'}
        self.assertEqual(rungic_dev.release_binaries(self.COMPONENTS['plasma-mobile'], base),
                         ['plasma-mobile', 'plasma-mobile-tweaks'])

    # covers: delivery.dev-overlay/E5
    def test_component_new_to_the_release(self):
        # ksystemstats joined "rebuilt" after the installed release: its distribution build is the base
        component = {'packages': ['ksystemstats']}
        self.assertEqual(rungic_dev.release_binaries(component, RELEASE['packages']), [])
        installed = {'ksystemstats': '6.6.6-0ubuntu0.1', 'kwin-wayland': '6.6.5-0+rungic8'}
        self.assertEqual(rungic_dev.distribution_bases(component, installed, {}), {'ksystemstats': '6.6.6-0ubuntu0.1'})
        # a second overlay keeps the first one's base, not the overlay it finds installed
        installed = {'ksystemstats': '6.6.6-0ubuntu0.1+rungic1+dev20261003t040000.abc1234'}
        earlier = {'ksystemstats': {'version': installed['ksystemstats'], 'base': '6.6.6-0ubuntu0.1'}}
        self.assertEqual(rungic_dev.distribution_bases(component, installed, earlier), {'ksystemstats': '6.6.6-0ubuntu0.1'})
        self.assertEqual(rungic_dev.distribution_bases(component, {}, {}), {})

    # covers: delivery.dev-overlay/E4
    def test_reset_restores_the_distribution_build(self):
        before = {'ksystemstats': {'version': '6.6.6-0ubuntu0.1+rungic1+dev1', 'base': '6.6.6-0ubuntu0.1'},
                  'rungic-design': {'version': '0.510+dev1'}}
        self.assertEqual(rungic_dev.restored(before, {'rungic-design': before['rungic-design']}),
                         {'ksystemstats': '6.6.6-0ubuntu0.1'})
        self.assertEqual(rungic_dev.restored(before, {}), {'ksystemstats': '6.6.6-0ubuntu0.1'})
        self.assertEqual(rungic_dev.restored(before, before), {})

    # covers: delivery.dev-overlay/E1 delivery.dev-overlay/E5
    def test_versions_with_an_epoch(self):
        dev = rungic_dev.dev_version('4:6.6.6-0ubuntu0.1+rungic9', '20261001t040000', 'abc1234', False)
        self.assertTrue(newer(dev, '4:6.6.6-0ubuntu0.1+rungic9'))
        self.assertTrue(newer('4:6.6.6-0ubuntu0.1+rungic10', dev))

    # covers: delivery.dev-overlay/E5
    def test_changelog_entry_parses(self):
        version = rungic_dev.dev_version('6.6.5-0ubuntu1+rungic3', '20261001t040000', 'abc1234', True)
        entry = rungic_dev.changelog_entry('plasma-mobile', version, 'resolute', 'Wed, 01 Oct 2026 04:00:00 +0000',
                                           'abc1234', True)
        with tempfile.TemporaryDirectory() as temp:
            changelog = Path(temp, 'changelog')
            changelog.write_text(entry + 'plasma-mobile (6.6.5-0ubuntu1+rungic3) resolute; urgency=medium\n\n'
                                 '  * Release.\n\n -- range-dev <noreply@localhost>  Mon, 29 Sep 2026 00:00:00 +0000\n')
            parsed = subprocess.run(['dpkg-parsechangelog', '-l', str(changelog), '-S', 'Version'],
                                    capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(parsed, version)

    # covers: delivery.dev-overlay/E4
    def test_reset_by_component(self):
        overrides = {'plasma-mobile': {'component': 'plasma-mobile'}, 'plasma-mobile-tweaks': {'component': 'plasma-mobile'},
                     'rungic-design': {}}
        self.assertEqual(rungic_dev.reset_names(['plasma-mobile'], overrides), {'plasma-mobile', 'plasma-mobile-tweaks'})
        self.assertEqual(rungic_dev.reset_names(['plasma-mobile-tweaks'], overrides), {'plasma-mobile-tweaks'})
        self.assertEqual(rungic_dev.reset_names(['rungic-design'], overrides), {'rungic-design'})

    # covers: delivery.dev-overlay/E5
    def test_components_of_the_release(self):
        components = rungic_dev.upstream_components()
        self.assertIn('plasma-mobile', components)
        self.assertTrue(all(c['source'].startswith('packages/') and c.get('version') for c in components.values()))


class KeptOnBuildHostTests(unittest.TestCase):
    """A Mac mini keeps the .debs it built; the phone takes them straight from it (AGENTS.md)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pool = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    # covers: delivery.dev-overlay/E6
    def test_taker_leaves_a_record_not_the_deb(self):
        class Host:
            def keep_for_phone(self, path, name):
                return {'path': f'dev-pool/{name}', 'size': 3, 'sha256': 'ab', 'stanza': f'Package: x\nFilename: ./{name}\n\n'}
        take = rungic_dev.taker(Host())
        take('/root/rungic-build/packages/x/x_1_arm64.deb', self.pool / 'x_1_arm64.deb')
        self.assertFalse((self.pool / 'x_1_arm64.deb').exists())
        self.assertEqual(json.loads((self.pool / 'x_1_arm64.deb.remote').read_text())['path'], 'dev-pool/x_1_arm64.deb')
        self.assertIsNone(rungic_dev.taker(object()))       # a build on the phone: fetched here as before

    # covers: delivery.dev-overlay/E6
    def test_sync_sends_what_is_here_and_the_phone_takes_the_rest(self):
        for name in ('meta_1_all.deb', 'Packages', 'Packages.gz', 'Packages.xz', 'Release'):
            (self.pool / name).write_text(name)
        (self.pool / 'big_1_arm64.deb.remote').write_text(json.dumps({'path': 'dev-pool/big_1_arm64.deb', 'size': 9,
                                                                    'sha256': 'cd', 'stanza': ''}))
        sent, fetched, scripts = [], [], []

        def run(script, level, **kwargs):
            scripts.append(script)
            return subprocess.CompletedProcess(script, 0, 'old_1_arm64.deb\n' if 'ls -1' in script else '', '')

        def extract(archive, dest):
            import tarfile
            with tarfile.open(archive) as tar:
                sent.extend(tar.getnames())
        saved = (rungic_release.run, rungic_release.fetch_kept, rungic_release.rungic_device.extract_in_container)
        rungic_release.run = run
        rungic_release.fetch_kept = lambda kept, repo: fetched.extend(kept)
        rungic_release.rungic_device.extract_in_container = extract
        try:
            result = rungic_release.sync_repo(self.pool, '/var/lib/rungic-apt-dev')
        finally:
            rungic_release.run, rungic_release.fetch_kept, rungic_release.rungic_device.extract_in_container = saved
        self.assertEqual(fetched, ['big_1_arm64.deb'])
        self.assertIn('meta_1_all.deb', sent)
        self.assertFalse([n for n in sent if n.endswith('.remote') or n.startswith('big_')])
        self.assertEqual(result, {'sent': 5, 'fetched': 1, 'removed': 1})
        self.assertIn('old_1_arm64.deb', scripts[-1])          # the phone's stale file goes

    # covers: delivery.dev-overlay/E6 delivery.build-hosts/E3
    def test_fetch_checks_size_and_hash_over_either_way(self):
        scripts = []
        saved = rungic_release.run
        rungic_release.run = lambda script, level, **kwargs: scripts.append(script)
        try:
            rungic_release.fetch_kept({'big_1_arm64.deb': {'path': 'dev-pool/big_1_arm64.deb', 'size': 9, 'sha256': 'cd'}},
                                      '/var/lib/rungic-apt-dev')
        finally:
            rungic_release.run = saved
        self.assertIn('take big_1_arm64.deb dev-pool/big_1_arm64.deb 9 cd', scripts[0])
        self.assertIn('10.77.0.20 192.168.5.45', scripts[0])
        self.assertIn('sha256sum', scripts[0])

    # covers: delivery.dev-overlay/E6
    def test_index_has_the_kept_entries(self):
        (self.pool / 'big_1_arm64.deb.remote').write_text(json.dumps(
            {'stanza': 'Package: big\nVersion: 1\nArchitecture: arm64\nFilename: ./big_1_arm64.deb\nSize: 9\n\n'}))
        rungic_release.index(self.pool, 'rungic-dev')
        self.assertIn('Filename: ./big_1_arm64.deb', (self.pool / 'Packages').read_text())

    # covers: delivery.dev-overlay/E6
    def test_prune_drops_old_records(self):
        info = rungic_dev.overlay_info(RELEASE, {'rungic-design': {'version': '0.510+dev1', 'file': 'rungic-design_0.510+dev1_arm64.deb'}}, '1')
        for name in ('rungic-design_0.510+dev1_arm64.deb', 'rungic-design_0.510+dev0_arm64.deb'):
            (self.pool / (name + rungic_release.REMOTE)).write_text('{}')
        saved_pool, saved_mac = rungic_dev.POOL, rungic_dev_build_host()
        rungic_dev.POOL = self.pool
        pruned = []
        import build_on_device
        build_on_device.MacMini.prune_kept = lambda self, keep: pruned.append(keep)
        try:
            rungic_dev.prune(info)
        finally:
            rungic_dev.POOL = saved_pool
            build_on_device.MacMini.prune_kept = saved_mac
        self.assertEqual(sorted(p.name for p in self.pool.iterdir()), ['rungic-design_0.510+dev1_arm64.deb.remote'])
        self.assertEqual(pruned, [{'rungic-design_0.510+dev1_arm64.deb'}])


def rungic_dev_build_host():
    import build_on_device
    return build_on_device.MacMini.prune_kept


if __name__ == '__main__':
    unittest.main()
