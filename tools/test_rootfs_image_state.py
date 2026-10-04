#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The data that must not roll back with the system (docs/61 §7): with an image rootfs, the home,
the crash reports and the release repository are bind mounts of the LXC directory's state/, not
part of the image the snapshot covers. Runs the controller's real container start block
(system/rungic-plasma) with stand-ins for the Android-side commands and reads what it hands
lxc-start; the image script (system/rootfs-image) keeps those directories out of the image."""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / 'system/rungic-plasma'
IMAGE = ROOT / 'system/rootfs-image'
KEPT = {'home': 'home', 'rungic-cores': 'var/lib/rungic-cores', 'rungic-apt': 'var/lib/rungic-apt'}


class StateOutsideTheSnapshot(unittest.TestCase):
    def start(self, image):
        """The lxc-start arguments of the controller's start block, with or without rootfs.img."""
        text = CONTROLLER.read_text()
        block = re.search(r'\n(    set -- -s "\$gpu_rule" -s "\$heap_rule"\n.*?\n    \(umask 022; "\$ENTER" /usr/bin/lxc-start[^\n]*\n)',
                          text, re.S)
        self.assertIsNotNone(block, 'the container start block is not in system/rungic-plasma')
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            base, lxc, images = temp / 'base', temp / 'lxc', temp / 'images'
            for d in (base, lxc, images):
                d.mkdir()
            if image:
                (images / 'rootfs.img').write_bytes(b'')
            (base / 'rootfs-image').write_text('#!/bin/sh\n[ "$1" = attach ] && echo /dev/block/dm-7\n')
            (base / 'rootfs-mount-hook').write_text('#!/bin/sh\n')
            (base / 'enter').write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done\n')
            for f in base.iterdir():
                f.chmod(0o755)
            script = (f'BASE={base}\nLXC={lxc}\nIMAGES={images}\nENTER={base}/enter\n'
                      "gpu_rule='lxc.cgroup2.devices.allow = c 1:2 rw'\nheap_rule='lxc.cgroup2.devices.allow = c 1:3 r'\nvideo_rule=\n"
                      + block.group(1))
            result = subprocess.run(['sh', '-c', 'set -eu\n' + script], capture_output=True, text=True, timeout=10,
                                    env=dict(os.environ))
            self.assertEqual(result.returncode, 0, result.stderr)
            hook = (lxc / 'rootfs-mount-hook').exists()
        return result.stdout.splitlines(), hook

    # covers: delivery.rootfs-snapshot/E3
    def test_home_cores_and_repository_come_from_state_with_an_image_rootfs(self):
        args, hook = self.start(image=True)
        self.assertTrue(hook)
        self.assertIn('lxc.hook.pre-mount=/var/lib/lxc/plasma/rootfs-mount-hook', args)
        for state, target in KEPT.items():
            self.assertIn(f'lxc.mount.entry=/var/lib/lxc/plasma/state/{state} {target} none bind,create=dir 0 0', args)
        # A directory rootfs has no snapshot, and keeps them where they are.
        args, hook = self.start(image=False)
        self.assertFalse([a for a in args if a.startswith('lxc.mount.entry=')])

    # covers: delivery.rootfs-snapshot/E3
    def test_the_image_leaves_them_out_and_its_snapshot_covers_only_the_image(self):
        text = IMAGE.read_text()
        for inner in KEPT.values():
            self.assertIn(f'--exclude=./{inner}/*', text)       # not copied into the image
        self.assertIn('for pair in home:home var/lib/rungic-cores:rungic-cores var/lib/rungic-apt:rungic-apt; do', text)
        # snapshot, rollback and commit work on the image and its copy-on-write store only.
        for action in ('snapshot', 'rollback', 'commit'):
            body = re.search(rf'\n{action}\(\) {{\n(.*?)\n}}\n', text, re.S)
            self.assertIsNotNone(body, action)
            self.assertNotIn('state/', body.group(1))
            self.assertNotIn('$LXC', body.group(1))


if __name__ == '__main__':
    unittest.main()
