#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The dev release channel of rungic_release (docs/109) without a device: dev only from a clean
origin/main, the release record with its channel and APK, the bundle another machine deploys, the APK
step of a deploy, deploy --all and status --all over several phones, and the GitHub pre-release
(never run here: gh is a stand-in). How apt holds a release: tools/test_rungic_release_pins.py."""
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import rungic_device
import rungic_release
from test_rungic_release_deploy import Phone, Result, deb


class Workspace(unittest.TestCase):
    """A machine of its own: pool, releases, APKs, bundles and the committed history under a temp dir."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.machine(self.root / 'one')

    def machine(self, base):
        (base / '.work/cache').mkdir(parents=True, exist_ok=True)
        apt = base / '.work/apt'
        self.stub(WORKSPACE=base, APT=apt, POOL=apt / 'repo', RELEASES=apt / 'releases', APKS=apt / 'apk',
                  ANDROID_STORE=apt / 'android', COMPONENT_BUILDS=apt / 'component-builds.json',
                  BUNDLES=base / '.work/release-bundles', DEPLOY=base / '.work/deploy',
                  HISTORY=base / '.work/deploy/history.json', RELEASE_HISTORY=base / 'release/history.json')
        return base

    def stub(self, obj=rungic_release, **values):
        for name, value in values.items():
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)


SPEC = {'rebuilt': {'kwin': {'source': 'packages/kwin', 'version': '4:6.6.6-0ubuntu0.1+rungic9',
                             'packages': ['kwin-wayland']}},
        'project': {}, 'coupled': ['plasma-workspace'], 'android': []}
APK = {'file': 'Rungic-2.32-80-0123456789ab.apk', 'package': 'com.rungic.plasma', 'version_name': '2.32',
       'version_code': 80, 'sha256': None, 'size': 3}


class DevReleaseTests(Workspace):
    def git(self, head='c0ffee' * 7, main=None, porcelain='', tags='', log=''):
        calls = []

        def fake(*args, check=True):
            calls.append(args)
            if args[:2] == ('rev-parse', 'HEAD'):
                return head
            if args[:2] == ('rev-parse', 'origin/main'):
                return main or head
            if args[0] == 'status':
                return porcelain
            if args[0] == 'ls-remote':
                return tags
            if args[0] == 'log':
                return log
            return ''
        self.stub(git=fake)
        return calls

    # covers: delivery.dev-channel/E1
    def test_dev_refuses_anything_but_a_clean_origin_main(self):
        built = []
        self.stub(build_project=lambda: built.append('project'))
        self.git(head='a' * 40, main='b' * 40)
        with self.assertRaisesRegex(SystemExit, 'is not origin/main'):
            rungic_release.dev()
        self.git(porcelain=' M tools/rungic_release.py\n?? notes.txt')
        with self.assertRaisesRegex(SystemExit, 'not clean'):
            rungic_release.dev()
        self.assertEqual(built, [])
        calls = self.git()
        self.assertEqual(rungic_release.on_origin_main(), 'c0ffee' * 7)
        self.assertIn(('fetch', '-q', 'origin', 'main'), calls)       # compared with origin/main as it is now
        self.assertIn(('status', '--porcelain', '--untracked-files=normal'), calls)

    # covers: delivery.dev-channel/E1
    def test_the_number_skips_published_tags_and_deployed_releases(self):
        import datetime
        today = datetime.date.today().strftime('%Y%m%d')
        (self.root / 'one/.work/apt/releases').mkdir(parents=True)
        (self.root / 'one/.work/apt/releases' / f'{today}.1.json').write_text('{}')
        history = self.root / 'one/release/history.json'
        history.parent.mkdir(parents=True)
        history.write_text(json.dumps([{'version': f'{today}.3'}, {'version': '20200101.9'}]))
        self.git(tags=f'abc\trefs/tags/dev-{today}.2\nabd\trefs/tags/dev-{today}.2^{{}}\n')
        self.assertEqual(sorted(rungic_release.taken_elsewhere()), sorted([f'{today}.2', f'{today}.2', f'{today}.3', '20200101.9']))
        self.assertEqual(rungic_release.next_version(rungic_release.taken_elsewhere()), f'{today}.4')
        self.assertEqual(rungic_release.next_version(), f'{today}.2')

    # covers: delivery.dev-channel/E2
    def test_a_component_is_stale_without_its_version_and_a_changed_queue_needs_a_new_version(self):
        self.stub(spec=lambda: json.loads(json.dumps(SPEC)), component_tree=lambda name: 'tree-2')
        self.assertEqual(rungic_release.stale_components(), ['kwin'])
        pool = self.root / 'one/.work/apt/repo'
        pool.mkdir(parents=True)
        deb(pool, 'kwin-wayland', '6.6.6-0ubuntu0.1+rungic9')
        self.assertEqual(rungic_release.stale_components(), [])          # no record: the pool's build stands
        (self.root / 'one/.work/apt/component-builds.json').write_text(json.dumps(
            {'kwin': {'version': '4:6.6.6-0ubuntu0.1+rungic9', 'tree': 'tree-1'}}))
        with self.assertRaisesRegex(SystemExit, 'packages/kwin changed since its build'):
            rungic_release.stale_components()

    # covers: delivery.dev-channel/E2
    def test_a_component_builds_on_its_kept_tree_and_is_collected_and_recorded(self):
        import build_mesa
        import build_on_device
        steps = []
        host = types.SimpleNamespace(name='macmini', jobs=10, out=lambda script, timeout=120: steps.append('previous') or '0\n')
        self.stub(build_on_device, host=host, sync=lambda name: steps.append(('sync', name)),
                  build_deps=lambda name: steps.append(('build-deps', name)) or '',
                  start=lambda name, mode, jobs: steps.append(('start', name, mode, jobs)),
                  status=lambda name: 'ActiveState=inactive\nResult=success',
                  collect=lambda name: steps.append(('collect', name)) or 'collected')
        self.stub(build_mesa, package=lambda host, version, commit: steps.append(('package', version)))
        spec = {'rebuilt': {'kwin': SPEC['rebuilt']['kwin'], 'mesa': {'source': 'packages/mesa', 'version': '26.3+rungic4',
                                                                      'packages': ['mesa-libgallium']}}}
        self.stub(spec=lambda: json.loads(json.dumps(spec)), component_tree=lambda name: f'tree-{name}')
        self.git()
        (self.root / 'one/.work/apt').mkdir(parents=True)
        self.assertEqual(rungic_release.build_component('kwin')['mode'], 'incremental')
        self.assertEqual(rungic_release.build_component('mesa')['mode'], 'targets')
        self.assertEqual(steps, [('sync', 'kwin'), 'previous', ('start', 'kwin', 'incremental', 10), ('collect', 'kwin'),
                                 ('sync', 'mesa'), ('start', 'mesa', 'targets', 10), ('package', '26.3+rungic4'),
                                 ('collect', 'mesa')])
        builds = json.loads((self.root / 'one/.work/apt/component-builds.json').read_text())
        self.assertEqual({n: (b['version'], b['tree']) for n, b in builds.items()},
                         {'kwin': ('4:6.6.6-0ubuntu0.1+rungic9', 'tree-kwin'), 'mesa': ('26.3+rungic4', 'tree-mesa')})
        # No kept tree: a full build, with its build dependencies first; a failed build stops.
        host.out = lambda script, timeout=120: ''
        self.stub(build_on_device, status=lambda name: 'ActiveState=inactive\nResult=exit-code')
        with self.assertRaisesRegex(SystemExit, 'kwin: full build failed on macmini'):
            rungic_release.build_component('kwin')
        self.assertIn(('build-deps', 'kwin'), steps)

    # covers: delivery.dev-channel/E2
    def test_only_the_stale_project_packages_build(self):
        import build_on_device
        import rungic_package
        built = []
        self.stub(build_on_device, host=types.SimpleNamespace(jobs=10))
        self.stub(rungic_package, definitions=lambda: {'rungic-a': {'name': 'rungic-a'}, 'rungic-b': {'name': 'rungic-b'}},
                  current=lambda pkg: pkg['name'] == 'rungic-a',
                  build=lambda names, jobs: built.append((names, jobs)))
        self.stub(spec=lambda: {'project': {'rungic-a': {}, 'rungic-b': {}}})
        self.assertEqual(rungic_release.build_project(), ['rungic-b'])
        self.assertEqual(built, [(['rungic-b'], 10)])

    # covers: delivery.dev-channel/E2
    def test_dev_builds_what_the_pool_lacks_then_the_release_with_its_channel_and_apk(self):
        order = []
        self.git()
        apk = dict(APK, sha256='1' * 64)
        import build_on_device
        self.stub(build_on_device, use=lambda host: order.append(('use', host)))
        self.stub(build_project=lambda: order.append('project') or ['rungic-demo'],
                  stale_components=lambda: ['kwin'],
                  build_component=lambda name: order.append(('component', name)) or {'component': name},
                  release_apk=lambda given: order.append('apk') or apk,
                  build=lambda version, **kw: order.append(('build', kw['channel'], kw['apk']['version_code']))
                  or {'version': version},
                  export_bundle=lambda version, out: order.append('bundle') or Path(f'/b/rungic-{version}.tar'),
                  publish=lambda *a: self.fail('published without --publish'))
        result = rungic_release.dev(host='macmini')
        self.assertEqual(order, [('use', 'macmini'), 'project', ('component', 'kwin'), 'apk', ('build', 'dev', 80), 'bundle'])
        self.assertEqual((result['channel'], result['apk'], result['project_built']), ('dev', '2.32/80', ['rungic-demo']))

    # covers: delivery.dev-channel/E2
    def test_the_apk_record_comes_from_its_manifest(self):
        given = self.root / 'Rungic-2.32.apk'
        given.write_bytes(b'apk')
        import apk_manifest_info
        self.stub(apk_manifest_info, manifest_info=lambda path: {'manifest': {
            'package': 'com.rungic.plasma', 'versionName': '2.32', 'versionCode': 80}})
        apk = rungic_release.release_apk(given)
        digest = hashlib.sha256(b'apk').hexdigest()
        self.assertEqual(apk, {'file': f'Rungic-2.32-80-{digest[:12]}.apk', 'package': 'com.rungic.plasma',
                               'version_name': '2.32', 'version_code': 80, 'sha256': digest, 'size': 3})
        self.assertEqual((self.root / 'one/.work/apt/apk' / apk['file']).read_bytes(), b'apk')
        self.stub(apk_manifest_info, manifest_info=lambda path: {'manifest': {'package': 'org.other'}})
        with self.assertRaisesRegex(SystemExit, 'not com.rungic.plasma'):
            rungic_release.release_apk(given)


class BundleTests(Workspace):
    """A release built on one machine, deployed from its bundle on another."""

    def release(self):
        pool = self.root / 'one/.work/apt/repo'
        pool.mkdir(parents=True)
        deb(pool, 'kwin-wayland', '6.6.6-0ubuntu0.1+rungic9')
        android = self.root / 'one/system/rungic-plasma'
        android.parent.mkdir(parents=True)
        android.write_bytes(b'controller')
        apks = self.root / 'one/.work/apt/apk'
        apks.mkdir(parents=True)
        (apks / APK['file']).write_bytes(b'apk')
        spec = dict(SPEC, android=[{'source': 'system/rungic-plasma', 'path': '/data/adb/rungic-plasma/rungic-plasma',
                                    'mode': '755'}])
        self.stub(spec=lambda: json.loads(json.dumps(spec)), git_state=lambda: ('c0ffee' * 7, False))
        apk = dict(APK, sha256=hashlib.sha256(b'apk').hexdigest())
        result = rungic_release.build('20261004.1', coupled_override={'plasma-workspace': '4:6.6.6-0ubuntu0.1'},
                                      channel='dev', apk=apk)
        return result, apk

    # covers: delivery.dev-channel/E2
    def test_the_release_records_its_channel_commit_and_apk_in_the_metapackage(self):
        result, apk = self.release()
        meta = self.root / 'one/.work/apt/repo' / result['metapackage']
        self.assertIn('dev channel', rungic_release.deb_field(meta, 'Description'))
        inside = json.loads(subprocess.run(f'dpkg-deb --fsys-tarfile {meta} | tar -xO ./usr/share/rungic/release.json',
                                           shell=True, capture_output=True, text=True, check=True).stdout)
        self.assertEqual((inside['channel'], inside['commit'], inside['apk']), ('dev', 'c0ffee' * 7, apk))
        self.assertEqual(json.loads((self.root / 'one/.work/apt/releases/20261004.1.json').read_text())['apk'], apk)

    # covers: delivery.dev-channel/E3
    def test_a_bundle_carries_the_release_to_a_machine_that_did_not_build_it(self):
        self.release()
        bundle = rungic_release.export_bundle('20261004.1', self.root / 'out')
        self.assertEqual(bundle.name, 'rungic-20261004.1.tar')
        with tarfile.open(bundle) as tar:
            names = set(tar.getnames())
            manifest = json.loads(tar.extractfile('manifest.json').read())
        self.assertIn('repo/kwin-wayland_6.6.6-0ubuntu0.1+rungic9_all.deb', names)
        self.assertIn('repo/rungic-release_20261004.1_all.deb', names)
        self.assertIn('repo/Packages', names)
        self.assertIn(f"apk/{APK['file']}", names)
        self.assertIn('android/' + hashlib.sha256(b'controller').hexdigest(), names)
        self.assertEqual(manifest['from_archive'], ['plasma-workspace=4:6.6.6-0ubuntu0.1'])   # Ubuntu's, not ours
        self.assertEqual(manifest['release']['version'], '20261004.1')
        # Another machine: an empty pool, and no system/rungic-plasma in its tree at that content.
        other = self.machine(self.root / 'two')
        version = rungic_release.import_bundle(bundle)
        self.assertEqual(version, '20261004.1')
        info = rungic_release.release_info(version)
        self.assertEqual(info['channel'], 'dev')
        self.assertTrue((other / '.work/apt/repo/rungic-release_20261004.1_all.deb').exists())
        self.assertIn('Package: kwin-wayland', (other / '.work/apt/repo/Packages').read_text())
        self.assertEqual((other / '.work/apt/apk' / APK['file']).read_bytes(), b'apk')
        item = info['android']['/data/adb/rungic-plasma/rungic-plasma']
        self.assertEqual(rungic_release.android_content(info, item), b'controller')
        self.assertEqual(rungic_release.android_source_changes(info), [])
        self.assertEqual(rungic_release.import_bundle(bundle), version)   # again: nothing new, no complaint

    # covers: delivery.dev-channel/E3
    def test_a_bundle_never_mixes_two_machines_builds(self):
        self.release()
        bundle = rungic_release.export_bundle('20261004.1', self.root / 'out')
        # The other machine built its own 20261004.1 from another commit.
        other = self.machine(self.root / 'two')
        (other / '.work/apt/releases').mkdir(parents=True)
        (other / '.work/apt/releases/20261004.1.json').write_text(json.dumps({'version': '20261004.1', 'commit': 'f' * 40,
                                                                              'packages': {}}))
        with self.assertRaisesRegex(SystemExit, 'not the same release'):
            rungic_release.import_bundle(bundle)
        # Or a package of the same version with other contents.
        third = self.machine(self.root / 'three')
        pool = third / '.work/apt/repo'
        pool.mkdir(parents=True)
        deb(pool, 'kwin-wayland', '6.6.6-0ubuntu0.1+rungic9', depends='libc6')
        with self.assertRaisesRegex(SystemExit, 'different contents'):
            rungic_release.import_bundle(bundle)
        # A damaged bundle stops before anything is imported.
        damaged = self.root / 'damaged.tar'
        with tarfile.open(bundle) as tar, tarfile.open(damaged, 'w') as out:
            for member in tar.getmembers():
                data = tar.extractfile(member).read()
                if member.name.startswith('apk/'):
                    data, member.size = b'APK', 3
                out.addfile(member, __import__('io').BytesIO(data))
        self.machine(self.root / 'four')
        with self.assertRaisesRegex(SystemExit, 'missing or changed'):
            rungic_release.import_bundle(damaged)
        self.assertFalse((self.root / 'four/.work/apt/repo').exists())


class ApkTests(Workspace):
    def phone(self, codes, install_ok=True):
        """A phone whose APK reports versionCode codes[0], then codes[1] after an install."""
        self.installs = []
        state = {'code': codes[0]}

        def dumpsys(script):
            return Result('' if state['code'] is None else f"    versionCode={state['code']} minSdk=30\n    versionName=2.x\n")

        def install(path):
            self.installs.append(Path(path).name)
            state['code'] = codes[1]
            return install_ok, 'Success' if install_ok else 'Failure [INSTALL_FAILED]'
        phone = Phone(**{'dumpsys package': dumpsys})
        self.stub(run=phone.run, adb_install=install)
        return phone

    def info(self):
        apks = self.root / 'one/.work/apt/apk'
        apks.mkdir(parents=True, exist_ok=True)
        (apks / APK['file']).write_bytes(b'apk')
        return {'version': '20261004.1', 'apk': dict(APK, sha256=hashlib.sha256(b'apk').hexdigest())}

    # covers: delivery.dev-channel/E4
    def test_an_older_apk_is_replaced_keeping_its_data_and_the_activity_starts_again(self):
        phone = self.phone([79, 80])
        result = rungic_release.install_apk(self.info())
        self.assertEqual(result['result'], 'installed')
        self.assertEqual((result['before'], result['after']), ('2.x/79', '2.32/80'))
        self.assertEqual(self.installs, [APK['file']])
        self.assertTrue(phone.find('am start -n com.rungic.plasma/.MainActivity'))

    # covers: delivery.dev-channel/E4
    def test_a_current_or_newer_apk_or_none_at_all_stays(self):
        self.phone([81, None])
        self.assertEqual(rungic_release.install_apk(self.info())['result'], 'current')
        self.phone([80, None])
        self.assertEqual(rungic_release.install_apk(self.info())['result'], 'current')
        self.phone([None, None])
        self.assertTrue(rungic_release.install_apk(self.info())['result'].startswith('skipped'))   # no first install
        self.phone([79, None])
        self.assertEqual(rungic_release.install_apk(self.info(), restart='never')['result'], 'skipped: --restart never')
        self.assertEqual(self.installs, [])
        self.assertIsNone(rungic_release.install_apk({'version': 'x'}))

    # covers: delivery.dev-channel/E4
    def test_a_failed_or_missing_apk_stops_the_deploy(self):
        self.phone([79, 79], install_ok=False)
        with self.assertRaisesRegex(SystemExit, 'INSTALL_FAILED'):
            rungic_release.install_apk(self.info())
        self.phone([79, 80])
        info = self.info()
        (self.root / 'one/.work/apt/apk' / APK['file']).write_bytes(b'other')
        with self.assertRaisesRegex(SystemExit, 'deploy --from'):
            rungic_release.install_apk(info)


class AllPhonesTests(Workspace):
    """deploy --all and status --all: four adb devices, two of them Rungic phones."""

    def setUp(self):
        super().setUp()
        devices = [('10.0.0.1:5555', 'device', 'XT2537_4'), ('ZY32M9MRVP', 'device', 'XT2533_4'),
                   ('10.0.0.9:5555', 'unauthorized', None), ('emulator-5554', 'device', 'sdk'),
                   ('10.0.0.2:5555', 'device', 'XT2533_4')]
        serials = {'10.0.0.1:5555': 'ZY32MVJS25', 'ZY32M9MRVP': 'ZY32M9MRVP', 'emulator-5554': 'EMU',
                   '10.0.0.2:5555': 'ZY32M9MRVP'}
        self.stub(rungic_device, devices=lambda: devices)
        saved = {k: os.environ.get(k) for k in ('RUNGIC_SERIAL', 'RUNGIC_TRANSPORT')}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in saved.items()])
        os.environ['RUNGIC_SERIAL'] = 'BEFORE'

        def run(script, level='root', timeout=None, check=True):
            transport = os.environ.get('RUNGIC_TRANSPORT')
            if 'getprop ro.serialno' in script:
                return Result(serials[transport] + '\n')
            if 'rungic-plasma' in script and level == 'root':
                return Result('no\n' if transport == 'emulator-5554' else 'yes\n')
            return Result()
        self.stub(run=run)

    # covers: delivery.dev-channel/E5
    def test_only_rungic_phones_each_once(self):
        found, others = rungic_release.phones()
        self.assertEqual([p['serial'] for p in found], ['ZY32MVJS25', 'ZY32M9MRVP'])
        self.assertEqual({o['transport']: o['why'] for o in others}, {
            '10.0.0.9:5555': 'unauthorized', 'emulator-5554': 'no Rungic launcher (or no root)',
            '10.0.0.2:5555': 'the same phone on another transport'})
        self.assertEqual(os.environ['RUNGIC_SERIAL'], 'BEFORE')         # the selection ends with the block
        self.assertNotIn('RUNGIC_TRANSPORT', os.environ)

    # covers: delivery.dev-channel/E5 delivery.dev-channel/E8
    def test_deploy_all_goes_on_after_a_failure_and_sums_up(self):
        seen = []
        (self.root / 'one/.work/apt/releases').mkdir(parents=True)
        (self.root / 'one/.work/apt/releases/20261004.1.json').write_text(json.dumps(
            {'version': '20261004.1', 'commit': 'c0ffee', 'channel': 'dev', 'packages': {}}))

        def deploy(version, record_label=None, **options):
            seen.append((os.environ.get('RUNGIC_SERIAL'), os.environ.get('RUNGIC_TRANSPORT'), version, record_label, options))
            if os.environ['RUNGIC_SERIAL'] == 'ZY32MVJS25':
                raise rungic_device.DeviceError('Timed out after 600s: adb shell')
            return {'result': 'ok', 'steps': [{'step': 'apk', 'result': 'installed'}]}
        self.stub(deploy=deploy, device_release=lambda: ('20260930.10', {}))
        summary = rungic_release.deploy_all(acceptance='none', snapshot='never')
        self.assertEqual([s[:4] for s in seen], [('ZY32MVJS25', '10.0.0.1:5555', '20261004.1', '20261004.1-ZY32MVJS25'),
                                                 ('ZY32M9MRVP', 'ZY32M9MRVP', '20261004.1', '20261004.1-ZY32M9MRVP')])
        self.assertEqual(seen[0][4], {'acceptance': 'none', 'snapshot': 'never'})
        self.assertEqual(summary['result'], 'failed')
        rows = {r['serial']: r for r in summary['phones']}
        self.assertTrue(rows['ZY32MVJS25']['result'].startswith('error: DeviceError: Timed out'))
        self.assertEqual((rows['ZY32M9MRVP']['result'], rows['ZY32M9MRVP']['apk']), ('ok', 'installed'))
        # The phone that could not be deployed is in the committed history too.
        history = json.loads((self.root / 'one/release/history.json').read_text())
        self.assertEqual([(e['serial'], e['version'], e['commit'], e['channel']) for e in history],
                         [('ZY32MVJS25', '20261004.1', 'c0ffee', 'dev')])
        table = rungic_release.table(summary['phones'], ('serial', 'result'))
        self.assertEqual(table.splitlines()[0].split(), ['serial', 'result'])
        self.assertIn('ZY32M9MRVP  ok', table)

    # covers: delivery.dev-channel/E6
    def test_status_all_has_a_row_per_phone(self):
        def git(*args, check=True):
            return {('rev-list', '--count', 'c0ffee..origin/main'): '7'}.get(args, '')
        releases = {'ZY32MVJS25': ('20261004.1+dev20261004t0101', {'version': '20261004.1+dev', 'channel': 'dev',
                    'commit': 'c0ffee', 'packages': {'a': '1'}, 'dev': {'overrides': {'a': {}, 'b': {}}}}),
                    'ZY32M9MRVP': ('20260930.10', {'version': '20260930.10', 'commit': 'unknown-here', 'packages': {}})}
        self.stub(git=git, device_release=lambda: releases[os.environ['RUNGIC_SERIAL']],
                  installed_apk=lambda: ('2.33', 81),
                  protection=lambda info: {'unpinned': ['a'] if info.get('dev') else [], 'protected': True,
                                           'unattended': bool(info.get('dev'))})
        rows = {r['serial']: r for r in rungic_release.status_all()['phones']}
        self.assertEqual({k: rows['ZY32MVJS25'][k] for k in ('release', 'channel', 'commit', 'behind_main', 'apk', 'overlays', 'apt')},
                         {'release': '20261004.1+dev20261004t0101', 'channel': 'dev', 'commit': 'c0ffee', 'behind_main': 7,
                          'apk': '2.33/81', 'overlays': 2, 'apt': '1 unpinned'})
        # A release from before channels is a formal one; a commit this clone lacks has no count.
        self.assertEqual((rows['ZY32M9MRVP']['channel'], rows['ZY32M9MRVP']['behind_main'], rows['ZY32M9MRVP']['apt']),
                         ('release', None, 'no unattended-upgrades list'))


class PublishTests(Workspace):
    def setUp(self):
        super().setUp()
        releases = self.root / 'one/.work/apt/releases'
        releases.mkdir(parents=True)
        for version, commit, packages, channel in (('20261003.2', 'aaa111', {'kwin-wayland': '1', 'rungic-design': '0.5'}, 'dev'),
                                                   ('20261004.1', 'bbb222', {'kwin-wayland': '2', 'rungic-cua': '0.6'}, 'dev'),
                                                   ('20261004.2', 'ccc333', {}, 'release')):
            (releases / f'{version}.json').write_text(json.dumps({'version': version, 'commit': commit, 'channel': channel,
                                                                  'packages': packages,
                                                                  'apk': dict(APK, sha256='e' * 64) if version == '20261004.1' else None}))
        self.bundle = self.root / 'out/rungic-20261004.1.tar'
        self.bundle.parent.mkdir()
        self.bundle.write_bytes(b'tar')
        self.ran = []
        self.stub(gh=lambda argv: self.ran.append(argv) or Result('https://github.com/kevinzhow/Rungic/releases/tag/dev-20261004.1\n'))

        def git(*args, check=True):
            if args[0] == 'log':
                self.assertIn('aaa111..bbb222', args)          # since the previous dev release
                return ('1a2b3c4\tAdd Android SMS sending and filtered inbox access (#11)\n'
                        '5d6e7f8\tAgent 应用：给 Agent 打电话的入口与通话状态 (#10)\n9a8b7c6\tFix a typo in docs\n')
            return ''
        self.stub(git=git)

    # covers: delivery.dev-channel/E7
    def test_notes_name_the_merged_prs_the_package_changes_and_the_apk(self):
        notes = rungic_release.release_notes('20261004.1')
        self.assertIn('## Merged pull requests since 20261003.2', notes)
        self.assertIn('- Add Android SMS sending and filtered inbox access (#11) (1a2b3c4)', notes)
        self.assertIn('- Agent 应用：给 Agent 打电话的入口与通话状态 (#10) (5d6e7f8)', notes)
        self.assertIn('## Other commits\n\n- Fix a typo in docs (9a8b7c6)', notes)
        for line in ('- kwin-wayland: 1 -> 2', '- rungic-cua: (new) -> 0.6', '- rungic-design: 0.5 -> (removed)'):
            self.assertIn(line, notes)
        self.assertIn(f"- {APK['file']}: 2.32 (versionCode 80), sha256 {'e' * 64}", notes)
        self.assertIn('deploy --all --from rungic-20261004.1.tar', notes)

    # covers: delivery.dev-channel/E7
    def test_without_confirmation_nothing_is_published(self):
        result = rungic_release.publish('20261004.1', self.bundle)
        self.assertEqual(self.ran, [])
        self.assertFalse(result['published'])
        command = result['command']
        for part in ('gh release create dev-20261004.1', '--repo kevinzhow/Rungic', '--prerelease', '--target bbb222',
                     str(self.bundle), APK['file']):
            self.assertIn(part, command)
        self.assertTrue(Path(result['notes']).read_text().startswith('Rungic dev release 20261004.1'))
        # Confirmed: gh runs once with that command.
        published = rungic_release.publish('20261004.1', self.bundle, confirm=True)
        self.assertEqual(len(self.ran), 1)
        self.assertEqual(' '.join(self.ran[0]).replace("'", ''), command.replace("'", ''))
        self.assertEqual(published['tag'], 'dev-20261004.1')
        with self.assertRaisesRegex(SystemExit, 'not a dev release'):
            rungic_release.publish('20261004.2', self.bundle, confirm=True)
        self.assertEqual(len(self.ran), 1)


class SelectedPhoneTests(unittest.TestCase):
    # covers: delivery.dev-channel/E5
    def test_selected_points_the_tools_at_one_phone_and_restores(self):
        saved = {k: os.environ.get(k) for k in ('RUNGIC_SERIAL', 'RUNGIC_TRANSPORT')}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in saved.items()])
        os.environ['RUNGIC_SERIAL'], os.environ['RUNGIC_TRANSPORT'] = 'A', 'a:1'
        rungic_device.config.cache_clear()
        self.assertEqual(rungic_device.transport(), 'a:1')
        with rungic_device.selected('B', 'b:2'):
            self.assertEqual((rungic_device.serial(), rungic_device.transport()), ('B', 'b:2'))
        self.assertEqual((rungic_device.serial(), rungic_device.transport()), ('A', 'a:1'))
        with rungic_device.selected(transport='c:3'):
            self.assertNotIn('RUNGIC_SERIAL', os.environ)
            self.assertEqual(rungic_device.transport(), 'c:3')
        rungic_device.config.cache_clear()
        rungic_device.transport.cache_clear()


if __name__ == '__main__':
    unittest.main()
