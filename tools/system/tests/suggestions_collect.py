# SPDX-License-Identifier: MIT
"""The compatibility catalog matches exact versions (docs/research/proactive-system-care.md): the
suggestions service built from the working tree, its system collector (`rungic-suggestions --collect`,
what rungic-suggestions-collect.service runs) over the catalog the package ships (compatibility/entries),
with the installed packages given by a stand-in dpkg-query. The Qt Multimedia that carries the local
fix (a +rungic version) is not reported by the rule for the unpatched one; the unpatched one is, once;
Blender's CPU rendering policy never becomes a suggestion. The service's unit tests (care-tests) run too."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import harness

# Built as the package builds it, inside a copy of the sources (CMake's AutoGen did not find
# service.h with the build directory outside the read-only /src).
SOURCE = '/tmp/suggestions-src'
BUILD = SOURCE + '/build'
FAKES = Path('/tmp/suggestions-fakes')


def collect(packages):
    """The collector's observations with these installed packages (name, version)."""
    FAKES.mkdir(exist_ok=True)
    listing = ''.join(f'{name}\\t{version}\\tinstalled\\n' for name, version in packages)
    for name, script in (('dpkg-query', f"printf '{listing}'"), ('dpkg', 'exit 0'), ('systemctl', 'exit 0')):
        path = FAKES / name
        path.write_text(f'#!/bin/sh\n{script}\n')
        path.chmod(0o755)
    feed = Path('/tmp/observations.json')
    feed.unlink(missing_ok=True)
    done = subprocess.run([f'{BUILD}/rungic-suggestions', '--collect'], capture_output=True, text=True, timeout=60,
                          env={**os.environ, 'PATH': f'{FAKES}:{os.environ["PATH"]}',
                               'RUNGIC_COMPATIBILITY': '/src/compatibility/entries', 'RUNGIC_SUGGESTIONS_FEED': str(feed)})
    if done.returncode:
        raise harness.Failed('collect: ' + done.stderr[-800:])
    data = json.loads(feed.read_text())
    if 'compatibility' not in data['sources']:
        raise harness.Failed(f'the catalog was not used: {data["coverage"]}')
    return [i for i in data['items'] if i.get('source') == 'compatibility']


# covers[system]: agent.compat-knowledge/E2
def test():
    steps = []
    log = open('/tmp/suggestions-build.log', 'w')
    shutil.copytree('/src/agent/suggestions', SOURCE, dirs_exist_ok=True)
    for argv in (['cmake', '-S', SOURCE, '-B', BUILD, '-DCMAKE_BUILD_TYPE=RelWithDebInfo'],
                 ['cmake', '--build', BUILD, '-j', str(os.cpu_count() or 4), '--target', 'rungic-suggestions', 'care-tests', 'rungicsuggestions', 'pofiles']):
        if subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT).returncode:
            raise harness.Failed('build failed: ' + open('/tmp/suggestions-build.log').read()[-1500:])
    steps.append('built service, care-tests, native QML module and translations')
    entries = {json.loads(p.read_text())['id']: json.loads(p.read_text())
               for p in Path('/src/compatibility/entries').glob('*.json')}
    rule = entries['qt-pulseaudio-target-latency']
    unpatched = rule['match']['versions'][0]
    patched = unpatched + '+rungic1'
    items = collect([(rule['match']['package'], patched), ('blender', '4.5.3+dfsg-1')])
    if items:
        raise harness.Failed(f'the patched Qt Multimedia or Blender was reported: {items}')
    steps.append(f'{rule["match"]["package"]} {patched} (with the local fix) and blender: no suggestion')
    items = collect([(rule['match']['package'], unpatched), ('blender', '4.5.3+dfsg-1')])
    if [i['knowledge'] for i in items] != ['qt-pulseaudio-target-latency'] or items[0]['evidence']['version'] != unpatched:
        raise harness.Failed(f'the unpatched version: {items}')
    steps.append(f'{rule["match"]["package"]} {unpatched}: one suggestion, from its rule, for that version')
    if entries['blender-cpu-policy']['kind'] != 'policy':
        raise harness.Failed('the Blender entry is no longer a policy')
    done = subprocess.run([f'{BUILD}/care-tests'], capture_output=True, text=True, timeout=300,
                          env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
    if done.returncode:
        raise harness.Failed('care-tests: ' + (done.stdout + done.stderr)[-1500:])
    steps.append('care-tests passed')
    return steps


if __name__ == '__main__':
    harness.run('suggestions_collect', test)
