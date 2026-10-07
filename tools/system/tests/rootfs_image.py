# SPDX-License-Identifier: MIT
# system-test: as root
"""The RungicOS image builder (tools/ci/build_rootfs_image.py, docs/75) on a small root tree that a
reused build tree could leave behind: an APT pin of the previous release, SSH host keys and a
machine ID of the build. The real builder runs (rsync, mkfs.ext4 -d, e2fsck, gzip) as root in the
throwaway container; the image is read back with debugfs. The tree's packages are records in its
dpkg status only: dpkg --audit and apt-get check of a real installation run where
build_fingerprinted_rootfs.py installs the packages (an ARM64 chroot under podman, not here)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import harness

SRC = Path('/src')
WORK = Path('/tmp/rootfs-image')
LOG = []


def sh(*argv, check=True):
    result = subprocess.run(argv, capture_output=True, text=True)
    LOG.append(f'$ {" ".join(map(str, argv))} -> {result.returncode}\n{result.stdout[-1500:]}{result.stderr[-1500:]}')
    if check and result.returncode:
        raise harness.Failed(f'{argv[:3]} failed:\n{result.stdout[-1500:]}{result.stderr[-1500:]}')
    return result


def tree(root, release):
    """A root tree as the builder expects one, with the leftovers of an earlier build."""
    def write(rel, text, mode=0o644):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)

    installed = {**release['packages'], 'firefox': '140.0', 'rungic-release': release['version'],
                 'base-files': '14ubuntu1'}
    write('var/lib/dpkg/status', '\n'.join(
        f'Package: {name}\nStatus: install ok installed\nVersion: {version}\nArchitecture: arm64\n'
        for name, version in installed.items()))
    write('etc/passwd', 'root:x:0:0::/root:/bin/sh\nrungic:x:1000:1000::/home/rungic:/bin/sh\n')
    write('etc/shadow', 'root:*:0::::::\nrungic:!:0::::::\n', 0o640)
    write('usr/share/rungic/account-protocol', '2\n')
    config = 'etc/dpkg/dpkg.cfg.d/zz-rungic-apps'
    write(config, (SRC / 'system/config' / config).read_text())
    (root / 'home/rungic').mkdir(parents=True)
    os.chown(root / 'home/rungic', 1000, 1000)
    (root / 'root').mkdir(mode=0o700)
    # The previous release's pin, which would hold a package back on the new image (docs/92).
    write('etc/apt/preferences.d/rungic-release',
          '# Rungic image release old.1\n\nPackage: rungic-plasma-config\nPin: version 0.3\nPin-Priority: 1001\n\n'
          'Package: rungic-gone\nPin: version 0.1\nPin-Priority: 1001\n')
    for kind in ('rsa', 'ecdsa', 'ed25519'):
        write(f'etc/ssh/ssh_host_{kind}_key', 'build host key\n', 0o600)
        write(f'etc/ssh/ssh_host_{kind}_key.pub', 'build host key\n')
    write('etc/ssh/sshd_config', '# kept\n')
    write('etc/machine-id', '0123456789abcdef0123456789abcdef\n')


def image_file(image, path):
    return sh('debugfs', '-R', f'cat {path}', str(image)).stdout


def image_names(image, directory):
    return sh('debugfs', '-R', f'ls -p {directory}', str(image)).stdout


# covers[system]: install.rungicos-image/E3 install.ssh-access/E3
def test():
    steps = []

    def check(condition, what):
        steps.append(what)
        if not condition:
            raise harness.Failed(what + ': not so\n' + '\n'.join(LOG[-4:]))

    shutil.rmtree(WORK, ignore_errors=True)
    root = WORK / 'root'
    root.mkdir(parents=True)
    release = {'version': 'test.2', 'packages': {'rungic-plasma-config': '0.5', 'rungic-plasma-session': '0.5'}}
    (WORK / 'release.json').write_text(json.dumps(release))
    tree(root, release)
    image = WORK / 'out/rootfs.img'
    sh('python3', str(SRC / 'tools/ci/build_rootfs_image.py'), '--inside', '--unverified-root', '--root', str(root),
       '--release', str(WORK / 'release.json'), '--output', str(image), '--size-gib', '8', '--firefox-version', '140.0')
    report = json.loads((WORK / 'out/rootfs-report.json').read_text())
    check(report['install_receipt'] is None, 'explicit binary-root mode has no installation receipt')
    check(report['filesystem_check'] in (0, 1), 'the builder checks the ext4 image (e2fsck)')
    check(sh('e2fsck', '-fn', str(image), check=False).returncode == 0, 'the image is a clean ext4 file system')
    check(report['rootfs_sha256'] and (WORK / 'out/rootfs.img.gz').stat().st_size == report['compressed_bytes'],
          'the report describes the image and its compressed payload')

    pins = image_file(image, '/etc/apt/preferences.d/rungic-release')
    LOG.append(pins)
    blocks = {b.split('\n')[0].split(': ', 1)[1]: b for b in pins.split('\n\n') if b.startswith('Package: ')}
    check(set(blocks) == {'rungic-plasma-config', 'rungic-plasma-session', 'rungic-release'},
          'the pins are this release\'s packages and its metapackage, nothing of the old release')
    check('Pin: version 0.5' in blocks['rungic-plasma-config'] and 'Pin: version test.2' in blocks['rungic-release'],
          'each pinned at this release\'s version')
    check('0.3' not in pins and 'old.1' not in pins, 'the old release\'s pin is gone')
    lock = (WORK / 'out/packages.lock.tsv').read_text()
    check('rungic-plasma-config\t0.5\tarm64' in lock, 'the package lock lists what the image has')

    ssh = image_names(image, '/etc/ssh')
    LOG.append(ssh)
    check('sshd_config' in ssh and 'ssh_host_' not in ssh, 'the image has no SSH host keys of the build')
    check(image_file(image, '/etc/machine-id') == '', 'nor its machine ID')

    # A tree that does not have this release's versions installed does not become an image.
    other = WORK / 'other.json'
    other.write_text(json.dumps({**release, 'packages': {**release['packages'], 'rungic-plasma-config': '0.6'}}))
    refused = sh('python3', str(SRC / 'tools/ci/build_rootfs_image.py'), '--inside', '--unverified-root', '--root', str(root),
                 '--release', str(other), '--output', str(WORK / 'other/rootfs.img'), '--size-gib', '8',
                 '--firefox-version', '140.0', check=False)
    check(refused.returncode != 0 and 'release package mismatch' in refused.stderr + refused.stdout
          and not (WORK / 'other/rootfs.img').exists(), 'a tree without the release\'s versions is refused')
    shutil.rmtree(WORK, ignore_errors=True)
    return steps


if __name__ == '__main__':
    harness.run('rootfs_image', test)
