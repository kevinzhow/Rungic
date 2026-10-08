#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""rungic_release's deploy without a device (docs/61): what it asks of the phone's apt, and what the
phone's apt then does with it, the restarts it chooses, the record it leaves, the rollback to the
previous release, the checks after a snapshot rollback, and status.

The phone is a stand-in: device commands are recorded and answered here. Where a command's effect
is the point (apt's choice of versions with the release's pins, the restart scripts), the real
program runs on this computer: apt with a root directory of its own, the scripts in sh with a
systemctl that answers for the units."""
import json
import os
import shlex
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import rungic_release


class Result:
    def __init__(self, stdout='', returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, '', returncode


class Phone:
    """Device commands of rungic_release: recorded, answered by the handlers given."""

    def __init__(self, **answers):
        self.scripts = []
        self.answers = answers

    def run(self, script, level='root', timeout=None, check=True):
        self.scripts.append(script)
        for key, answer in self.answers.items():
            if key in script:
                return answer(script) if callable(answer) else Result(answer)
        return Result()

    def find(self, text):
        return [s for s in self.scripts if text in s]


def deb(root, name, version, depends='', protected=False):
    """A minimal .deb in `root`."""
    tree = root / f'tree-{name}-{version}'
    (tree / 'DEBIAN').mkdir(parents=True)
    control = f'Package: {name}\nVersion: {version}\nArchitecture: all\nMaintainer: t <t@localhost>\nDescription: {name}\n'
    if depends:
        control += f'Depends: {depends}\n'
    if protected:
        control += 'Protected: yes\n'
    (tree / 'DEBIAN/control').write_text(control)
    target = root / f'{name}_{version}_all.deb'
    subprocess.run(['dpkg-deb', '--root-owner-group', '--build', str(tree), str(target)], check=True, capture_output=True)
    return target


class Workspace(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / '.work/cache').mkdir(parents=True)

    def stub(self, **values):
        for name, value in values.items():
            p = patch.object(rungic_release, name, value)
            p.start()
            self.addCleanup(p.stop)


class ReleasePinsTests(Workspace):
    """The phone's apt with the release's pins: installed versions stay, the archive's other
    packages update, and the metapackage cannot be removed."""

    def apt(self, *args):
        config = self.root / 'apt.conf'
        env = dict(os.environ, APT_CONFIG=str(config), LC_ALL='C.UTF-8')
        return subprocess.run(['apt-get', *args], capture_output=True, text=True, env=env)

    # covers: delivery.release-deploy/E1
    def test_installed_release_wins_over_newer_builds_and_is_protected(self):
        info = {'version': '20261001.2', 'commit': 'abc', 'packages': {'rungic-demo': '0.5'}}
        # The release metapackage as build() makes it.
        self.stub(WORKSPACE=self.root)
        meta = rungic_release.build_meta(info['version'], info['packages'], info, dest=self.root)
        self.assertEqual(rungic_release.deb_field(meta, 'Protected'), 'yes')
        self.assertEqual(rungic_release.deb_field(meta, 'Depends'), 'rungic-demo (= 0.5)')
        # Our repository: the release's build and a newer one; the archive: a newer build too, and an
        # Ubuntu package with an update.
        ours, archive = self.root / 'ours', self.root / 'archive'
        ours.mkdir(), archive.mkdir()
        os.replace(meta, ours / meta.name)
        deb(ours, 'rungic-demo', '0.5'), deb(ours, 'rungic-demo', '0.6')
        deb(archive, 'rungic-demo', '0.7'), deb(archive, 'libdemo1', '1.1')
        rungic_release.index(ours)
        rungic_release.index(archive, label='Ubuntu')
        # The phone: the release installed (dpkg status), the sources and pins deploy writes.
        etc, state = self.root / 'etc', self.root / 'state'
        (etc / 'preferences.d').mkdir(parents=True)
        (etc / 'sources.list.d').mkdir()
        (state / 'lists/partial').mkdir(parents=True)
        (self.root / 'cache/archives/partial').mkdir(parents=True)
        (etc / 'sources.list.d/rungic.sources').write_text(rungic_release.SOURCES.replace('/var/lib/rungic-apt', str(ours)))
        (etc / 'sources.list.d/archive.sources').write_text(
            f'Types: deb\nURIs: file:{archive}\nSuites: ./\nTrusted: yes\n')
        (etc / 'preferences.d/rungic').write_text(rungic_release.PREFERENCES)
        phone = Phone()
        self.stub(run=phone.run)
        rungic_release.pin_release(info)
        body = phone.find('preferences.d/rungic-release')[0].split("<<'EOF'\n", 1)[1].split('\nEOF', 1)[0]
        (etc / 'preferences.d/rungic-release').write_text(body + '\n')
        status = ''.join(f'Package: {n}\nStatus: install ok installed\nVersion: {v}\nArchitecture: all\n'
                         f'Maintainer: t\nDescription: {n}\n\n' for n, v in (('rungic-demo', '0.5'), ('libdemo1', '1.0')))
        # The installed metapackage: its own control fields, as dpkg keeps them.
        fields = subprocess.run(['dpkg-deb', '-f', str(ours / meta.name)], capture_output=True, text=True,
                                check=True).stdout
        status += fields.replace('Package: rungic-release\n', 'Package: rungic-release\nStatus: install ok installed\n') + '\n'
        (state / 'status').write_text(status)
        (self.root / 'apt.conf').write_text(textwrap.dedent(f'''\
            Dir "{self.root}/";
            Dir::State "{state}/";
            Dir::State::status "{state}/status";
            Dir::Cache "{self.root}/cache/";
            Dir::Etc "{etc}/";
            Dir::Etc::Parts "{etc}/apt.conf.d";
            Dir::Log "{self.root}/log";
            APT::Architecture "all";
            APT::Sandbox::User "{os.environ.get('USER') or 'root'}";
            Debug::NoLocking "true";
            '''))
        update = self.apt('update')
        self.assertEqual(update.returncode, 0, update.stdout + update.stderr)
        upgrade = self.apt('-s', '-o', 'Debug::NoLocking=1', 'dist-upgrade')
        self.assertEqual(upgrade.returncode, 0, upgrade.stdout + upgrade.stderr)
        # Discover offers what is newer than the installed version as the candidate: for ours that is
        # the release's own version, for the Ubuntu package the update.
        env = dict(os.environ, APT_CONFIG=str(self.root / 'apt.conf'), LC_ALL='C.UTF-8')
        policy = subprocess.run(['apt-cache', 'policy', 'rungic-demo', 'libdemo1'], capture_output=True, text=True,
                                env=env).stdout
        candidates = dict(zip(*[iter(l.split(':', 1)[1].strip() for l in policy.splitlines()
                                     if l.strip().startswith(('Installed:', 'Candidate:')))] * 2))
        self.assertEqual(candidates, {'0.5': '0.5', '1.0': '1.1'}, policy)
        # Discover and apt upgrades: the Ubuntu package updates, ours stays at the release.
        self.assertIn('Inst libdemo1 [1.0] (1.1', upgrade.stdout)
        self.assertNotIn('Inst rungic-demo', upgrade.stdout)
        # The metapackage is not removed to get around its exact dependencies: apt takes it for an
        # essential package (Protected), which it removes only on an explicit, typed confirmation.
        remove = self.apt('-s', '-o', 'Debug::NoLocking=1', 'remove', 'rungic-release')
        self.assertIn('WARNING: The following essential packages will be removed.\n'
                      'This should NOT be done unless you know exactly what you are doing!\n  rungic-release',
                      remove.stdout)

    # covers: delivery.release-deploy/E1 delivery.release-deploy/E2
    def test_install_names_every_exact_version_in_a_transient_unit(self):
        phone = Phone()
        self.stub(run=phone.run)
        record = self.root / 'record'
        record.mkdir()
        info = {'version': '20261001.1', 'packages': {'rungic-demo': '0.4', 'kwin-wayland': '6.6.5-0+rungic7'}}
        ok, _ = rungic_release.apt_install(info, record)
        self.assertTrue(ok)
        script = phone.find('apt-get')[0]
        command = script.split('systemd-run', 1)[1]
        self.assertIn('--unit=rungic-deploy-', command)        # survives an adb disconnect
        self.assertIn('--allow-downgrades', command)            # a rollback goes down to exact versions
        words = [w for w in shlex.split(command.split(' install ', 1)[1]) if w != '2>&1']
        self.assertEqual(sorted(words), ['kwin-wayland=6.6.5-0+rungic7', 'rungic-demo=0.4', 'rungic-release=20261001.1'])
        # Every source's lists before the install, not only ours: a fresh rootfs has none (X70 2026-10-08).
        before = script.split('systemd-run', 1)[0]
        self.assertRegex(before, r'(?m)^(\S+=\S+ )*apt-get -q update >/dev/null')


class DeployTests(Workspace):
    """A whole deploy against the stand-in phone, from preflight to the record."""

    def setUp(self):
        super().setUp()
        self.calls = []
        self.versions = [{'rungic-demo': '0.4', 'kwin-wayland': '1', 'rungic-other': '7'}]
        self.info = {'version': '20261001.2', 'commit': 'c0ffee', 'android': {},
                     'packages': {'rungic-demo': '0.5', 'kwin-wayland': '1', 'rungic-other': '7'},
                     'session_restart': ['kwin-*'],
                     'service_restart': {'rungic-demo': ['rungic-demo.service'], 'rungic-other': ['rungic-other.service']},
                     'user_restart': {'rungic-demo': ['plasma-plasmashell.service', 'rungic-demo-user.service',
                                                     'rungic-idle.service', '/usr/bin/rungic-demo-tool']}}
        self.phone = Phone(**{'dmesg': self.dmesg, 'apt-mark showhold': 'rungic-demo\n'})
        self.ext4 = [3, 3]
        self.stub(WORKSPACE=self.root, DEPLOY=self.root / '.work/deploy', HISTORY=self.root / '.work/deploy/history.json',
                  RELEASE_HISTORY=self.root / 'release/history.json',
                  releases=lambda: [self.older, self.info], preflight=lambda: ([], []),
                  rootfs_state=lambda: ('image', 'none'), with_container_stopped=self.stopped,
                  device_release=lambda: ('20261001.1', None), android_layouts=lambda info: ('rungic', 'rungic'),
                  installed_versions=self.installed, integrity_summary=lambda: {'summary': {'state': 'clean'}},
                  ensure_apt_source=lambda: None, sync_repo=lambda **kw: {}, run=self.phone.run,
                  apt_install=self.install, restart_session=self.restart_session)
        self.older = {'version': '20261001.1', 'commit': 'beef', 'android': {},
                      'packages': {'rungic-demo': '0.4', 'kwin-wayland': '1', 'rungic-other': '7'}}
        import rungic_acceptance
        import rungic_dev
        # session_ready saves its picture in ctx['out_dir'] (69a3219); a stub ignoring ctx hid a deploy that passed {}
        for obj, name, value in ((rungic_acceptance, 'session_ready', lambda ctx: {'passed': Path(ctx['out_dir']).is_dir()}),
                                 (rungic_dev, 'clear_device', lambda run: None)):
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    # R15-R17: deployment policy stays compatible while both observations survive.
    # covers: delivery.release-deploy/E6 delivery.acceptance/E6
    def test_acceptance_attempts_preserve_first_failure_and_retry_without_changing_policy(self):
        import rungic_acceptance as acc
        self.stub(rootfs_state=lambda: ('directory', 'none'))  # isolate reporting from snapshot evidence
        for retry_status, retry_passed, expected in [('pass', True, 'ok'), ('fail', False, 'verify-failed'),
                                                      ('skipped', True, 'ok')]:
            with self.subTest(retry=retry_status):
                initial = {'passed': False, 'failed_ids': ['A'], 'verdict': 'fail', 'release': self.info['version'],
                           'scenarios': [{'id': 'A', 'passed': False, 'status': 'fail'}], 'path': 'initial/report.json'}
                retry = {'passed': retry_passed, 'failed_ids': ['A'] if retry_status == 'fail' else [],
                         'verdict': 'incomplete' if retry_status == 'skipped' else retry_status,
                         'scenarios': [{'id': 'A', 'passed': retry_passed if retry_status != 'skipped' else None,
                                        'status': retry_status}], 'path': 'retry/report.json', 'release': self.info['version']}
                with patch.object(acc, 'run_level', return_value=initial), patch.object(acc, 'run_scenarios', return_value=retry) as retried, \
                     patch.object(acc, 'load', return_value={'scenarios': [{'id': 'A'}]}):
                    log = rungic_release.deploy('20261001.2', snapshot='never', record_label=f'retry-{retry_status}')
                self.assertEqual(log['result'], expected)
                self.assertEqual(log['attempts'], ['acceptance/report.json', 'acceptance-retry/report.json'])
                self.assertEqual(retried.call_args.kwargs['retry_of'], '../acceptance/report.json')
                self.assertEqual(log['flaky'], ['A'] if retry_status == 'pass' else [])
                self.assertFalse(initial['passed'])
                self.assertEqual(json.loads(rungic_release.RELEASE_HISTORY.read_text())[-1]['flaky'], log['flaky'])
                self.assertEqual(json.loads(rungic_release.HISTORY.read_text())[-1]['flaky'], log['flaky'])

    # R15: actual deployment and runner write separate evidence, not just summary flags.
    # covers: delivery.release-deploy/E6 delivery.acceptance/E6
    def test_real_runner_keeps_both_files_and_links_retry_to_initial(self):
        import rungic_acceptance as acc
        self.stub(rootfs_state=lambda: ('directory', 'none'), git=lambda *args, **kw: 'a' * 40 if args[0] == 'rev-parse' else '',
                  phone_drift=lambda against: {'release': self.info['version'], 'against': against, 'in_sync': True})
        observations = iter([False, True])
        plan = {'scenarios': [{'id': 'A', 'title': 'observable result', 'check': 'fixture', 'level': 'smoke',
                               'screenshot_on_failure': False}], 'manual': ['real speech']}
        with patch.object(acc, 'load', return_value=plan), \
             patch.object(acc, 'device_snapshot', return_value={'serial': 'fixture-phone', 'fingerprint': 'fixture'}), \
             patch.object(acc, 'bring_to_front', return_value={'in_front': True}), \
             patch.object(acc, 'previous_report', return_value=(None, None)), \
             patch.dict(acc.CHECKS, fixture=lambda ctx: acc.result(next(observations))):
            log = rungic_release.deploy('20261001.2', snapshot='never')
        self.assertEqual(log['result'], 'ok')
        record = next(rungic_release.DEPLOY.glob('*-20261001.2'))
        initial = json.loads((record / log['attempts'][0]).read_text())
        retry = json.loads((record / log['attempts'][1]).read_text())
        self.assertEqual(initial['verdict'], 'fail')
        self.assertFalse(initial['scenarios'][0]['passed'])
        self.assertEqual(retry['verdict'], 'pass')
        self.assertTrue(retry['scenarios'][0]['passed'])
        self.assertEqual((record / log['attempts'][1]).parent.joinpath(retry['retry_of']).resolve(),
                         (record / log['attempts'][0]).resolve())
        self.assertEqual(log['flaky'], ['A'])

    def dmesg(self, script):
        return Result(f'{self.ext4.pop(0)}\n')

    def stopped(self, action, before_start=None):
        self.calls.append(action)
        if before_start:
            before_start()
        return True, action

    def installed(self):
        return dict(self.versions[-1])

    def install(self, info, record):
        self.calls.append(('install', dict(info['packages'])))
        self.versions.append(dict(info['packages']))
        return True, ''

    def restart_session(self):
        self.calls.append('restart-session')
        return True, 'restarted'

    def steps(self, log, name):
        return [s for s in log['steps'] if s['step'] == name]

    def run_script(self, script, active, enabled=()):
        """A restart script of the deploy in sh, with a systemctl that knows which units run."""
        bin_dir = self.root / 'bin'
        bin_dir.mkdir(exist_ok=True)
        log = self.root / 'systemctl.log'
        (bin_dir / 'systemctl').write_text(textwrap.dedent(f'''\
            #!/bin/sh
            [ "$1" = --user ] && shift
            echo "$*" >> {log}
            case "$1" in
              is-active) case " {' '.join(active)} " in *" $3 "*) exit 0 ;; esac; exit 3 ;;
              is-enabled) case " {' '.join(enabled)} " in *" $3 "*) exit 0 ;; esac; exit 1 ;;
            esac
            exit 0
            '''))
        (bin_dir / 'pkill').write_text(f'#!/bin/sh\necho "pkill $*" >> {log}\nexit 0\n')
        for tool in ('systemctl', 'pkill'):
            (bin_dir / tool).chmod(0o755)
        out = subprocess.run(['sh', '-c', script], capture_output=True, text=True,
                             env=dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}'))
        calls = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return out.stdout, calls

    # covers: delivery.release-deploy/E5
    def test_changed_services_restart_and_running_user_units_with_plasmashell_last(self):
        log = rungic_release.deploy('20261001.2', acceptance='none')
        self.assertEqual(log['result'], 'ok')
        self.assertNotIn('restart-session', self.calls)          # no session_restart package changed
        # System services: only the changed package's units, and only enabled ones restart.
        services = self.phone.find('systemctl is-enabled -q')[0]
        self.assertIn('rungic-demo.service', services)
        self.assertNotIn('rungic-other.service', services)
        output, calls = self.run_script(services, active=(), enabled=('rungic-demo.service',))
        self.assertEqual(output.strip(), 'rungic-demo.service restarted')
        output, calls = self.run_script(services, active=(), enabled=())
        self.assertNotIn('restart rungic-demo.service', calls)    # a disabled unit stays as it is
        # The desktop user's units: running ones restart, plasmashell last; a stopped one is not started.
        command = self.phone.find('rungic-plasma-user-exec')[0]
        script = shlex.split(command.split('rungic-plasma-user-exec', 1)[1])[2]
        output, calls = self.run_script(script, active=('plasma-plasmashell.service', 'rungic-demo-user.service'))
        restarts = [c.split()[1] for c in calls if c.startswith('restart ')]
        self.assertEqual(restarts, ['rungic-demo-user.service', 'plasma-plasmashell.service'])
        self.assertNotIn('rungic-idle.service', ' '.join(c for c in calls if not c.startswith('is-active')))
        self.assertIn('pkill -xf /usr/bin/rungic-demo-tool', calls)

    # covers: delivery.release-deploy/E5 delivery.release-deploy/E2
    def test_a_session_package_change_restarts_the_session(self):
        self.info['packages']['kwin-wayland'] = '2'
        log = rungic_release.deploy('20261001.2', acceptance='none')
        self.assertEqual(log['result'], 'ok')
        self.assertIn('restart-session', self.calls)
        self.assertEqual(self.steps(log, 'changes')[0]['restart_for'], ['kwin-wayland'])
        self.assertFalse(self.phone.find('rungic-plasma-user-exec'))   # the session restart covers them

    # covers: delivery.release-deploy/E1 delivery.release-deploy/E6
    def test_pins_after_the_install_and_the_record_of_each_step(self):
        log = rungic_release.deploy('20261001.2', acceptance='none')
        self.assertEqual(log['result'], 'ok')
        pins = self.phone.find('preferences.d/rungic-release')[0]
        for name, version in {**self.info['packages'], 'rungic-release': '20261001.2'}.items():
            self.assertIn(f'Package: {name}\nPin: version {version}\nPin-Priority: 1001', pins)
        self.assertLess(self.phone.scripts.index(pins), self.phone.scripts.index(self.phone.find('apt-mark showhold')[0]))
        self.assertIn('apt-mark unhold rungic-demo', self.phone.scripts)   # holds give way to the pins
        # The record: .work/deploy/<time>-<version>/ with every step, the result, before and after.
        records = list((self.root / '.work/deploy').glob('*-20261001.2'))
        self.assertEqual(len(records), 1)
        record = json.loads((records[0] / 'deploy.json').read_text())
        self.assertEqual(record['result'], 'ok')
        self.assertEqual([s['step'] for s in record['steps']][:3], ['preflight', 'snapshot', 'settled'])
        self.assertEqual(record['steps'][-1], {**record['steps'][-1], 'step': 'done', 'result': 'ok'})
        self.assertEqual(json.loads((records[0] / 'before.json').read_text())['packages']['rungic-demo'], '0.4')
        self.assertEqual(json.loads((records[0] / 'after.json').read_text())['packages']['rungic-demo'], '0.5')
        self.assertTrue((records[0] / 'integrity-before.json').exists() and (records[0] / 'integrity-after.json').exists())
        history = json.loads((self.root / '.work/deploy/history.json').read_text())
        self.assertEqual(history[-1]['record'], str(records[0].relative_to(self.root)))
        self.assertEqual((history[-1]['version'], history[-1]['previous'], history[-1]['result']), ('20261001.2', '20261001.1', 'ok'))

    # covers: delivery.release-deploy/E5
    def test_a_snapshot_kept_from_the_last_deploy_is_replaced_by_the_new_one(self):
        # 2026-10-05, the user: "why not just deploy?" The system running since the last deploy is
        # kept and the new snapshot is the way back; it used to abort until someone committed.
        rungic_release.rootfs_state = lambda: ('image', 'snapshot')
        log = rungic_release.deploy('20261001.2', acceptance='none')
        self.assertEqual(log['result'], 'ok')
        self.assertIn(('commit', 'snapshot'), self.calls, 'kept, then a new snapshot, in one stop of the container')
        self.assertTrue(self.steps(log, 'kept'))

    def test_a_rollback_under_way_still_waits_for_a_decision(self):
        rungic_release.rootfs_state = lambda: ('image', 'rollback')
        log = rungic_release.deploy('20261001.2', acceptance='none')
        self.assertEqual(log['result'], 'aborted')
        self.assertFalse([c for c in self.calls if isinstance(c, tuple) and c[0] == 'install'])

    # covers: delivery.dev-channel/E4 delivery.dev-channel/E8
    def test_the_apk_follows_the_container_side_and_every_deploy_is_in_the_history(self):
        self.info.update(channel='dev', apk={'file': 'Rungic-2.32-80-x.apk', 'version_code': 80})
        self.phone.answers['getprop ro.serialno'] = 'ZY32MVJS25\n'
        self.stub(install_apk=lambda info, restart: self.calls.append(('apk', restart)) or
                  {'result': 'installed', 'before': '2.31/79', 'after': '2.32/80'})
        log = rungic_release.deploy('20261001.2', acceptance='none')
        self.assertEqual(log['result'], 'ok')
        names = [s['step'] for s in log['steps']]
        self.assertLess(names.index('pins'), names.index('apk'))
        self.assertEqual(names[names.index('apk') + 1], 'apk-session')    # the desktop is back before verifying
        self.assertLess(names.index('apk-session'), names.index('integrity'))
        self.assertEqual(self.calls[-1], ('apk', 'auto'))
        # release/history.json: committed, one line per deploy and phone.
        self.stub(preflight=lambda: (['dpkg is locked by another process'], ['dpkg is locked by another process']))
        self.assertEqual(rungic_release.deploy('20261001.2', record_label='again')['result'], 'aborted')
        history = json.loads((self.root / 'release/history.json').read_text())
        self.assertEqual([{k: e[k] for k in ('version', 'commit', 'channel', 'serial', 'result')} for e in history],
                         [{'version': '20261001.2', 'commit': 'c0ffee', 'channel': 'dev', 'serial': 'ZY32MVJS25', 'result': 'ok'},
                          {'version': '20261001.2', 'commit': 'c0ffee', 'channel': 'dev', 'serial': 'ZY32MVJS25', 'result': 'aborted'}])
        self.assertTrue(all(e['time'] for e in history))

    # covers: install.independent-runtime/E7
    def test_the_android_settings_converge_right_after_the_android_files(self):
        # docs/122: the release's settings now, not at the next boot.
        self.phone.answers['rungic-converge apply'] = ('background-network refused Can_t_find_service\n'
                                                       'overlay changed\npermissions report not-granted:CAMERA\n')
        log = rungic_release.deploy('20261001.2', acceptance='none')
        self.assertEqual(log['result'], 'ok', 'a refusal is recorded, not a failed deploy')
        names = [s['step'] for s in log['steps']]
        self.assertEqual(names[names.index('android') + 1], 'converge')
        step = next(s for s in log['steps'] if s['step'] == 'converge')
        self.assertEqual(step['refused'], 'background-network')
        self.assertEqual(step['items'][1], {'id': 'overlay', 'state': 'changed', 'detail': ''})
        self.assertEqual(step['items'][2]['detail'], 'not-granted:CAMERA')

    # covers: delivery.release-deploy/E2
    def test_rollback_goes_back_to_the_previous_release_by_exact_versions(self):
        rungic_release.deploy('20261001.2', acceptance='none')
        self.stub(device_release=lambda: ('20261001.2', None))
        log = rungic_release.rollback(acceptance='none')
        self.assertEqual(log['result'], 'ok')
        self.assertEqual(log['version'], '20261001.1')
        self.assertEqual(self.calls[-1] if self.calls[-1] != 'restart-session' else self.calls[-2],
                         ('install', self.older['packages']))
        self.assertTrue(list((self.root / '.work/deploy').glob('*-rollback-to-20261001.1')))
        # Nothing earlier recorded: no guess.
        self.stub(device_release=lambda: ('20261001.1', None), HISTORY=self.root / 'none.json')
        with self.assertRaises(SystemExit):
            rungic_release.rollback(acceptance='none')

    # covers: delivery.rootfs-snapshot/E5
    def test_a_snapshot_rollback_counts_ext4_errors_and_checks_integrity_in_the_record(self):
        self.ext4 = [3, 5]                                      # the rollback added two kernel ext4 errors
        self.stub(integrity_summary=lambda: {'summary': {'state': 'drift', 'changed_files': 1, 'missing_files': 0},
                                             'release': {'mismatch': ['rungic-demo']}})
        import sys
        import types
        with patch.dict(sys.modules, {'rungic_agent': types.SimpleNamespace(snapshot=lambda label, since: {'folder': 'e'})}):
            log = rungic_release.deploy('20261001.2', acceptance='none')
        self.assertEqual(log['result'], 'verify-failed, rolled back to the snapshot')
        self.assertEqual(self.calls[-1], 'rollback')
        rollback = self.steps(log, 'snapshot-rollback')[0]
        self.assertEqual(rollback['check'], {'ext4_errors': 2, 'integrity': 'drift', 'changed_files': 1, 'missing_files': 0})
        record = next((self.root / '.work/deploy').glob('*-20261001.2'))
        saved = self.steps(json.loads((record / 'deploy.json').read_text()), 'snapshot-rollback')[0]
        self.assertEqual(saved['check']['ext4_errors'], 2)


class StatusTests(Workspace):
    # covers: delivery.release-deploy/E6
    def test_status_names_the_release_its_commit_the_repository_the_rootfs_and_the_overlay(self):
        info = {'version': '20261001.2+dev1', 'commit': 'c0ffee', 'built': '2026-10-01T10:00:00',
                'dev': {'base': '20261001.2', 'overrides': {'rungic-demo': {'version': '0.5+dev1.abc'}}}}
        history = self.root / 'history.json'
        history.write_text(json.dumps([{'version': '20261001.2', 'result': 'ok'}]))
        self.stub(device_release=lambda: ('20261001.2+dev1', info),
                  integrity_summary=lambda: {'summary': {'state': 'development'}, 'release': {'mismatch': []}},
                  releases=lambda: [{'version': '20261001.1'}, {'version': '20261001.2'}],
                  rootfs_state=lambda: ('image', 'snapshot'), HISTORY=history, run=Phone().run)
        status = rungic_release.status()
        self.assertEqual(status['installed_release'], '20261001.2+dev1')
        self.assertEqual(status['commit'], 'c0ffee')
        self.assertEqual(status['latest_in_repository'], '20261001.2')
        self.assertEqual(status['rootfs'], {'mode': 'image', 'state': 'snapshot'})
        self.assertEqual(status['dev_overlay'], {'base': '20261001.2', 'overrides': {'rungic-demo': '0.5+dev1.abc'}})
        self.assertEqual(status['integrity'], {'state': 'development'})
        self.assertEqual(status['release_mismatch'], [])
        self.assertEqual(status['last_deploys'], [{'version': '20261001.2', 'result': 'ok'}])


if __name__ == '__main__':
    unittest.main()
