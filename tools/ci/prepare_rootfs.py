#!/usr/bin/env python3
"""Install a fresh ARM64 CI2 root and compose it with the existing image builder.

Run as root inside the build runner (or its user namespace). Each --output must
be new; a failed tree is evidence, never a resume point. Ubuntu repositories must
be authenticated; the caller supplies a checked local Rungic package repository.
"""
import argparse
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from build_rootfs_image import check_install_completion, packages, sha256

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def run(*command):
    return subprocess.run(command, check=True, stdin=subprocess.DEVNULL)


def prepare(args):
    if not args.qemu and platform.machine() not in ('aarch64', 'arm64'):
        raise ValueError('native installation requires ARM64; use --qemu on an emulated runner')
    output = args.output.resolve()
    # mkdir's exclusive creation is the concurrency lock. Do not delete a failed
    # attempt, copy installed state from it, or reuse its success marker.
    output.mkdir(parents=True)
    root = output / 'prepared-root'
    manifest = json.loads((args.packages / 'release.json').read_text())
    if 'rungic-plasma-config' not in manifest['packages']:
        raise ValueError('release does not include rungic-plasma-config')
    if not re.fullmatch(r'[0-9a-f]{40}', args.source_commit):
        raise ValueError('source commit must be a full Git SHA')
    sources = [f'deb {args.mirror} {suite} main universe multiverse restricted'
               for suite in (args.suite, args.suite + '-updates', args.suite + '-security')]
    run('mmdebstrap', '--mode=root', '--variant=minbase', '--architectures=arm64',
        '--components=main,universe,multiverse,restricted',
        '--include=ca-certificates,locales,systemd-sysv,udev,apt-utils,sudo',
        args.suite, str(root), *sources)
    state = root / 'var/lib/rungic-apt'
    shutil.copytree(args.packages, state)
    # The temporary index selection does not hand-write package-owned /etc files.
    (state / 'bootstrap.sources').write_text('\n'.join([*sources,
        'deb [trusted=yes] file:/var/lib/rungic-apt ./']) + '\n')
    exact = {**manifest['packages'], 'rungic-release': manifest['version']}
    (state / 'exact-packages.txt').write_text(''.join(f'{name}={version}\n' for name, version in sorted(exact.items())))
    runtime = [line.strip() for line in (ROOT / 'system/ubuntu-packages.txt').read_text().splitlines()
               if line.strip() and not line.lstrip().startswith('#')]
    (state / 'runtime-packages.txt').write_text('\n'.join(runtime) + '\n')
    shutil.copy2(HERE / 'install_rootfs.sh', state / 'install-rootfs.sh')
    runner = ['--qemu', str(args.qemu.resolve())] if args.qemu else ['--native']
    run('unshare', '-mpf', sys.executable, str(HERE / 'arm64_chroot.py'), '--inside',
        '--rootfs', str(root), *runner, '--', '/bin/bash', '/var/lib/rungic-apt/install-rootfs.sh',
        args.source_commit, args.firefox_version)
    receipt = check_install_completion(root, state / 'release.json', args.source_commit)
    installed = packages(root / 'var/lib/dpkg/status')
    mismatched = {name: version for name, version in {**exact, 'firefox': args.firefox_version}.items()
                  if installed.get(name, (None,))[0] != version}
    if mismatched:
        raise ValueError(f'installed release mismatch: {mismatched}')
    (output / 'root-install.complete.json').write_text(json.dumps(receipt, indent=2) + '\n')
    (output / 'packages.lock.tsv').write_text(''.join(f'{name}\t{version}\t{arch}\n'
          for name, (version, arch) in sorted(installed.items())))
    if not args.prepare_only:
        run(sys.executable, str(HERE / 'build_rootfs_image.py'), '--inside', '--root', str(root),
            '--release', str(state / 'release.json'), '--output', str(output / 'image/rootfs.img'),
            '--size-gib', str(args.size_gib), '--firefox-version', args.firefox_version,
            '--install-source', args.source_commit)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packages', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--firefox-version', required=True)
    parser.add_argument('--suite', default='resolute')
    parser.add_argument('--mirror', default='http://ports.ubuntu.com/ubuntu-ports')
    parser.add_argument('--qemu', type=Path, help='QEMU binary when using an x86 runner with ARM64 binfmt')
    parser.add_argument('--size-gib', type=int, default=16)
    parser.add_argument('--prepare-only', action='store_true', help='Stop after checked installation, before imaging')
    args = parser.parse_args()
    print(prepare(args))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f'root installation failed; retain this attempt and choose a new --output: {error}')
