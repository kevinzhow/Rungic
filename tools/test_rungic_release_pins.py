#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""What keeps an installed release when Ubuntu publishes newer builds (docs/61, docs/109): the pins
and the unattended-upgrades list deploy writes, checked with the real apt and the real
unattended-upgrades of this computer against a root directory of their own (our repository, an
archive with newer Ubuntu builds of packages we rebuild or couple to), and how status reads them."""
import json
import os
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path

import rungic_release
from test_rungic_release_deploy import Phone, Workspace, deb


UNATTENDED = Path('/usr/bin/unattended-upgrade')


class ProtectionTests(Workspace):
    """What keeps an installed release when Ubuntu publishes newer builds of packages it rebuilds
    (kwin-wayland) or couples to (plasma-workspace and its private library libtaskmanager6): apt
    upgrades as Discover runs them, unattended-upgrades, and both after the metapackage is gone."""

    INFO = {'version': '20261004.1', 'commit': 'c0ffee',
            'packages': {'kwin-wayland': '1.0+rungic1', 'plasma-workspace': '2.0'}}
    SIBLINGS = {'libtaskmanager6': '2.0'}

    def setUp(self):
        super().setUp()
        root = self.root
        self.stub(WORKSPACE=root)
        ours, archive = root / 'ours', root / 'archive'
        ours.mkdir(), archive.mkdir()
        meta = rungic_release.build_meta(self.INFO['version'], self.INFO['packages'], self.INFO, dest=ours)
        deb(ours, 'kwin-wayland', '1.0+rungic1')
        for name, version in (('kwin-wayland', '1.1'), ('plasma-workspace', '2.1'), ('libtaskmanager6', '2.1'),
                              ('libdemo1', '1.1')):
            deb(archive, name, version)
        rungic_release.index(ours)
        rungic_release.index(archive, label='Ubuntu')
        self.etc, self.state = root / 'etc', root / 'state'
        for d in ('preferences.d', 'sources.list.d', 'apt.conf.d'):
            (self.etc / d).mkdir(parents=True)
        (self.state / 'lists/partial').mkdir(parents=True)
        (root / 'cache/archives/partial').mkdir(parents=True)
        (self.etc / 'sources.list.d/rungic.sources').write_text(rungic_release.SOURCES.replace('/var/lib/rungic-apt', str(ours)))
        (self.etc / 'sources.list.d/archive.sources').write_text(f'Types: deb\nURIs: file:{archive}\nSuites: ./\nTrusted: yes\n')
        (self.etc / 'preferences.d/rungic').write_text(rungic_release.PREFERENCES)
        # As on the phone: unattended-upgrades on for the archive, with the static list of
        # rungic-plasma-config (system/config/etc/apt/apt.conf.d/51rungic-unattended-upgrades).
        (self.etc / 'apt.conf.d/50unattended-upgrades').write_text('Unattended-Upgrade::Origins-Pattern { "o=Ubuntu"; };\n')
        static = Path(__file__).resolve().parent.parent / 'system/config/etc/apt/apt.conf.d/51rungic-unattended-upgrades'
        shutil.copy(static, self.etc / 'apt.conf.d')
        # What deploy writes after the install (pin_release), taken from its script.
        phone = Phone()
        self.stub(run=phone.run)
        rungic_release.pin_release(self.INFO, self.SIBLINGS)
        script = phone.find('rungic-release.new')[0]
        bodies = [part.split('\nEOF', 1)[0] for part in script.split("<<'EOF'\n")[1:]]
        self.pins, self.unattended = bodies[0] + '\n', bodies[1] + '\n'
        self.meta_fields = subprocess.run(['dpkg-deb', '-f', str(meta)], capture_output=True, text=True, check=True).stdout
        (root / 'apt.conf').write_text(textwrap.dedent(f'''\
            Dir "{root}/";
            Dir::State "{self.state}/";
            Dir::State::status "{self.state}/status";
            Dir::Cache "{root}/cache/";
            Dir::Etc "{self.etc}/";
            Dir::Etc::Parts "{self.etc}/apt.conf.d";
            Dir::Log "{root}/log";
            APT::Architecture "all";
            APT::Sandbox::User "{os.environ.get('USER') or 'root'}";
            Debug::NoLocking "true";
            '''))
        self.env = dict(os.environ, APT_CONFIG=str(root / 'apt.conf'), LC_ALL='C.UTF-8')

    def phone(self, pins=True, unattended=True, metapackage=True):
        """The phone's state: the release installed (and its metapackage), with or without what deploy writes."""
        (self.etc / 'preferences.d/rungic-release').unlink(missing_ok=True)
        (self.etc / 'apt.conf.d/52rungic-release').unlink(missing_ok=True)
        if pins:
            (self.etc / 'preferences.d/rungic-release').write_text(self.pins)
        if unattended:
            (self.etc / 'apt.conf.d/52rungic-release').write_text(self.unattended)
        installed = {**self.INFO['packages'], **self.SIBLINGS, 'libdemo1': '1.0'}
        status = ''.join(f'Package: {n}\nStatus: install ok installed\nVersion: {v}\nArchitecture: all\n'
                         f'Maintainer: t\nDescription: {n}\n\n' for n, v in installed.items())
        if metapackage:
            status += self.meta_fields.replace('Package: rungic-release\n', 'Package: rungic-release\nStatus: install ok installed\n') + '\n'
        (self.state / 'status').write_text(status)
        update = subprocess.run(['apt-get', 'update'], capture_output=True, text=True, env=self.env)
        self.assertEqual(update.returncode, 0, update.stdout + update.stderr)

    def upgrade(self):
        """apt-get -s dist-upgrade, as Discover's updates run: -> {package: new version}"""
        result = subprocess.run(['apt-get', '-s', 'dist-upgrade'], capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return dict(line.split()[1:4:2] for line in result.stdout.splitlines() if line.startswith('Inst '))

    def unattended_upgrades(self):
        """The packages this computer's unattended-upgrades would upgrade on that phone (its own
        candidate calculation, with its pins for origins and blacklist)."""
        if not UNATTENDED.exists() or subprocess.run(['/usr/bin/python3', '-c', 'import apt, distro_info'],
                                                     capture_output=True).returncode:
            self.skipTest('unattended-upgrades and python3-apt are not installed here')
        script = textwrap.dedent('''\
            import importlib.machinery, importlib.util, json, logging, types
            loader = importlib.machinery.SourceFileLoader('uu', '/usr/bin/unattended-upgrade')
            uu = importlib.util.module_from_spec(importlib.util.spec_from_loader('uu', loader))
            loader.exec_module(uu)
            logging.disable(logging.CRITICAL)
            cache = uu.UnattendedUpgradesCache(rootdir=None)
            print(json.dumps(sorted(p.name for p in uu.calculate_upgradable_pkgs(cache, types.SimpleNamespace(debug=False)))))
            ''')
        result = subprocess.run(['/usr/bin/python3', '-c', script], capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        return json.loads(result.stdout.strip().splitlines()[-1])

    # covers: delivery.release-deploy/E8
    def test_newer_ubuntu_builds_do_not_replace_the_release(self):
        self.phone()
        self.assertEqual(self.upgrade(), {'libdemo1': '(1.1'})
        self.assertEqual(self.unattended_upgrades(), ['libdemo1'])
        # The pins name the siblings too.
        self.assertIn('Package: libtaskmanager6\nPin: version 2.0\nPin-Priority: 1001', self.pins)

    # covers: delivery.release-deploy/E8
    def test_the_pins_hold_after_the_metapackage_is_gone(self):
        self.phone(metapackage=False, unattended=False)
        self.assertEqual(self.upgrade(), {'libdemo1': '(1.1'})
        self.assertEqual(self.unattended_upgrades(), ['libdemo1'])

    # covers: delivery.release-deploy/E8
    def test_without_pins_the_generated_list_keeps_unattended_upgrades_off(self):
        # The gap: without the pins only the static list protects, and it misses components added
        # since (here libtaskmanager6, and the rebuilt kwin-wayland of a renamed source is matched by
        # luck: its list has "kwin").
        self.phone(pins=False, unattended=False)
        self.assertEqual(self.unattended_upgrades(), ['libdemo1', 'libtaskmanager6'])
        self.phone(pins=False, unattended=True)
        self.assertEqual(self.unattended_upgrades(), ['libdemo1'])

    # covers: delivery.release-deploy/E8
    def test_the_list_names_exactly_the_release_packages(self):
        import re
        body = rungic_release.unattended_body({'version': 'v', 'packages': {'gir1.2-gst-plugins-base-1.0': '1', 'libfoo++1': '2'}},
                                              {'plasma-workspace-data': '3'})
        conf = self.root / 'u.conf'
        conf.write_text(body)
        dump = subprocess.run(['apt-config', '-c', str(conf), 'dump', 'Unattended-Upgrade::Package-Blacklist'],
                              capture_output=True, text=True, env=dict(os.environ, APT_CONFIG='/dev/null')).stdout
        entries = re.findall(r'Package-Blacklist:: "(.*)";', dump)
        self.assertEqual(entries, ['gir1[.]2-gst-plugins-base-1[.]0$', 'libfoo[+][+]1$', 'plasma-workspace-data$', 'rungic-release$'])
        match = lambda name: any(re.match(e, name) for e in entries)
        self.assertTrue(match('gir1.2-gst-plugins-base-1.0') and match('libfoo++1') and match('rungic-release'))
        self.assertFalse(match('gir1x2-gst-plugins-base-1.0') or match('plasma-workspace-data-extra') or match('plasma-workspace'))

    # covers: delivery.release-deploy/E8
    def test_siblings_are_the_installed_packages_of_the_same_sources(self):
        rows = ('ii \tplasma-workspace\tplasma-workspace\t4:6.6.6-0ubuntu0.1\n'
                'ii \tlibtaskmanager6\tplasma-workspace\t4:6.6.6-0ubuntu0.1\n'
                'rc \tlibkworkspace6-6\tplasma-workspace\t4:6.6.5-0ubuntu1\n'
                'ii \tkwin-wayland\tkwin\t4:6.6.6-0ubuntu0.1+rungic9\n'
                'ii \tlibdemo1\tdemo\t1.0\n'
                'ii \trungic-release\trungic-release\t20261004.1\n')
        self.stub(run=Phone(**{'dpkg-query': rows}).run)
        info = {'version': '20261004.1', 'packages': {'plasma-workspace': '4:6.6.6-0ubuntu0.1',
                                                       'kwin-wayland': '4:6.6.6-0ubuntu0.1+rungic9'}}
        self.assertEqual(rungic_release.release_siblings(info), {'libtaskmanager6': '4:6.6.6-0ubuntu0.1'})

    # covers: delivery.release-deploy/E8
    def test_status_reads_how_apt_holds_the_release(self):
        text = self.pins + 'Package: kwin-wayland\nPin: version 1.0+rungic1+dev1\nPin-Priority: 1002\n@@protected=yes\n'
        self.stub(run=Phone(**{'cat ': text}).run)
        info = {'packages': {'kwin-wayland': '1.0+rungic1+dev1', 'plasma-workspace': '2.0', 'rungic-new': '0.1'}}
        self.assertEqual(rungic_release.protection(info), {'unpinned': ['rungic-new'], 'protected': True, 'unattended': False})


if __name__ == '__main__':
    unittest.main()
