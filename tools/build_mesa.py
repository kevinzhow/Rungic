#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build and package Mesa on the phone from its patch queue (packages/mesa, docs/73).

  build_mesa.py [--host phone|macmini]
                           meson build (build_on_device.py mesa targets, options from
                           desktop/mesa-meson-options), install into a stage, assemble the
                           packages with desktop/package-mesa.py, collect them into the release pool

The version is the first entry of packages/mesa/debian/changelog. The build runs where
build_on_device.py builds (--host, default $RUNGIC_BUILD_HOST, else macmini); its log is build.log.
"""
import argparse
import os
import re
import subprocess
import sys

import build_on_device
from rungic_device import WORKSPACE

BASE = '/root/rungic-build/mesa'


def step(host, *argv, timeout):
    result = subprocess.run([sys.executable, str(WORKSPACE / 'tools/build_on_device.py'), '--host', host, 'mesa', *argv],
                            capture_output=True, text=True, timeout=timeout)
    print((result.stdout + result.stderr).strip()[-1500:])
    if result.returncode:
        raise SystemExit(f'build_on_device.py mesa {" ".join(argv)} failed')


def package(host, version, commit):
    """Package a completed Meson build, for a release or a development overlay."""
    script = host.put(WORKSPACE / 'desktop/package-mesa.py', f'{BASE}/package-mesa.py', '755')
    print(host.run(f'''set -e
rm -rf {BASE}/stage {BASE}/*.deb {BASE}/debs
DESTDIR={BASE}/stage meson install -C {BASE}/build >/dev/null
RUNGIC_MESA_STAGE={BASE}/stage RUNGIC_MESA_PACKAGES={BASE}/debs RUNGIC_MESA_VERSION={version} \\
RUNGIC_MESA_SOURCE={BASE}/src RUNGIC_MESA_COMMIT={commit} python3 {script}
mv {BASE}/debs/*.deb {BASE}/
ls {BASE}/*.deb''', timeout=1800).stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--host', choices=sorted(build_on_device.HOSTS), default=os.environ.get('RUNGIC_BUILD_HOST', 'macmini'))
    name = parser.parse_args().host
    host = build_on_device.use(name)
    changelog = (WORKSPACE / 'packages/mesa/debian/changelog').read_text()
    version = re.match(r'^\S+ \(([^)]+)\)', changelog)[1]
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=WORKSPACE, capture_output=True, text=True).stdout.strip()
    step(name, 'targets', timeout=3600)
    step(name, 'wait', timeout=4 * 3600)
    package(host, version, commit)
    step(name, 'collect', timeout=900)


if __name__ == '__main__':
    main()
