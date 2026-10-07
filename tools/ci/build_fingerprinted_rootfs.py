#!/usr/bin/env python3
"""Prepare a new root from an immutable binary baseline and source-built updates.

Run inside podman unshare. The outer build_artifact recipe hashes the entire
baseline (including ownership), update artifacts and this recipe's toolchain.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import build_artifact as artifact
from build_rootfs_image import packages, write_release_preferences


def run(*command):
    subprocess.run(command, check=True, stdin=subprocess.DEVNULL)


def inventory(root):
    installed = packages(root / 'var/lib/dpkg/status')
    records = {}
    cache = {}
    for name, (version, arch) in sorted(installed.items()):
        info = root / 'var/lib/dpkg/info'
        listing = next((p for p in (info / f'{name}.list', info / f'{name}:{arch}.list') if p.is_file()), None)
        if listing is None:
            raise ValueError(f'package file inventory missing: {name}')
        files = {}
        for entry in listing.read_text().splitlines():
            relative = entry.lstrip('/')
            path = root / relative
            # Directory contents belong to many packages. Record only this package's
            # actual non-directory entries, including links without following them.
            if path.is_symlink() or path.is_file():
                if relative not in cache:
                    cache[relative] = artifact.identity(path)
                files[relative] = cache[relative]
            elif not path.is_dir():
                files[relative] = {'kind': 'absent'}
        records[name] = {'version': version, 'architecture': arch,
                         'installed_files_sha256': artifact.digest(files), 'listed_files': len(files)}
    return {'schema': 1, 'kind': 'installed-package-content-inventory', 'packages': records,
            'scope': 'Installed bytes, not proof of each binary package original source build.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('base-root', 'packages', 'qemu', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--size-gib', type=int, default=16)
    p.add_argument('--firefox-version', required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True)
    root = args.output / 'prepared-root'
    root.mkdir()
    run('cp', '-a', '--reflink=auto', str(args.base_root) + '/.', str(root))
    # Binary-baseline composition has its own fingerprint, not a fresh-install proof.
    # Updating dpkg invalidates a copied receipt; never refresh it to claim full installation.
    (root / 'var/lib/rungic-apt/root-install.complete').unlink(missing_ok=True)
    release = json.loads((args.packages / 'release.json').read_text())
    write_release_preferences(root, release)
    debs = sorted(args.packages.glob('*.deb'))
    for deb in debs:
        run('cp', '--reflink=auto', str(deb), str(root / 'tmp' / deb.name))
    command = ('set -eu\n'
               'test -c /dev/null\n'
               'dpkg -i ' + ' '.join('/tmp/' + deb.name for deb in debs) + '\n'
               'apt-get check\ndpkg --audit\n')
    run('unshare', '-mpf', sys.executable, str(HERE / 'arm64_chroot.py'), '--inside',
        '--rootfs', str(root), '--qemu', str(args.qemu), '--', '/bin/sh', '-ec', command)
    # Do not put an installed account or build machine identity in the image.
    for path in (root / 'etc/ssh').glob('ssh_host_*_key*'):
        path.unlink()
    info = inventory(root)
    (args.output / 'packages.inventory.json').write_text(json.dumps(info, indent=2) + '\n')
    metadata = root / 'usr/share/rungic/build'
    metadata.mkdir(parents=True, exist_ok=True)
    (metadata / 'packages.inventory.json').write_bytes((args.output / 'packages.inventory.json').read_bytes())
    (metadata / 'input-baseline.json').write_text(json.dumps({
        'schema': 1, 'package_release': release['version'],
        'packages_inventory_sha256': artifact.file_hash(args.output / 'packages.inventory.json'),
        'scope': 'Composition from a fingerprinted binary root baseline and source-built updates; see payload build-manifest.json.'}, indent=2) + '\n')
    run(sys.executable, str(HERE / 'build_rootfs_image.py'), '--inside', '--root', str(root),
        '--release', str(args.packages / 'release.json'), '--output', str(args.output / 'image/rootfs.img'),
        '--size-gib', str(args.size_gib), '--firefox-version', args.firefox_version, '--unverified-root')


if __name__ == '__main__':
    main()
