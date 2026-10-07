# SPDX-License-Identifier: MIT
"""The Android-side helpers resolve the active root provider (system/root-provider): Magisk while its
runtime is up, else KernelSU, else an error. The installer (standalone.ROOT_PROVIDER_SH) and the
first boot (rungic-firstboot.sh) carry the same rule; these tests run all three against the same
fake roots, so they cannot drift apart."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/ci'))
import standalone  # noqa: E402

PROVIDER = ROOT / 'system/root-provider'
FIRSTBOOT = ROOT / 'tools/ci/rungic-firstboot.sh'
PATHS = ('/debug_ramdisk/magiskpolicy', '/debug_ramdisk/magisk', '/data/adb/magisk', '/data/adb/ksud', '/data/adb/ksu/bin')


def relocate(text, root):
    # One pass, longest first: /debug_ramdisk/magiskpolicy is not also moved as /debug_ramdisk/magisk.
    return re.sub('|'.join(map(re.escape, sorted(PATHS, key=len, reverse=True))), lambda m: str(root) + m.group(0), text)


def tool(path, body='echo "$0 $*"'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\n{body}\n')
    path.chmod(0o755)


def magisk(root):
    # Magisk 31's magisk rejects --live like the real one ("Unrecognized argument: --live", G100).
    tool(root / 'debug_ramdisk/magisk', 'case "$1" in --live) echo "Unrecognized argument: --live" >&2; exit 1;; esac; echo "$0 $*"')
    tool(root / 'debug_ramdisk/magiskpolicy')
    tool(root / 'data/adb/magisk/busybox')


def kernelsu(root):
    tool(root / 'data/adb/ksud')
    tool(root / 'data/adb/ksu/bin/busybox')


def provider(root, body):
    script = root / 'root-provider'
    script.write_text(relocate(PROVIDER.read_text(), root) + '\n' + body + '\n')
    return subprocess.run(['sh', str(script)], capture_output=True, text=True)


def installer_rule(root):
    """standalone.ROOT_PROVIDER_SH as the installer runs it: BB and RUNGIC_ROOT, or an error."""
    script = relocate(standalone.ROOT_PROVIDER_SH, root) + '\necho "$RUNGIC_ROOT $BB"\n'
    return subprocess.run(['sh', '-c', script], capture_output=True, text=True)


def firstboot_rule(root):
    """The detection block of rungic-firstboot.sh, cut out at its markers."""
    text = FIRSTBOOT.read_text()
    block = text[text.index('if [ -x /debug_ramdisk/magisk ]'):text.index('export RUNGIC_BUSYBOX RUNGIC_MAGISK')]
    script = relocate(block, root) + 'if [ -n "$RUNGIC_MAGISK" ]; then k=magisk; else k=kernelsu; fi; echo "$k $RUNGIC_BUSYBOX"\n'
    return subprocess.run(['sh', '-c', script], capture_output=True, text=True)


# covers: install.first-run-progress/E6
def test_magisk_loads_live_policy_with_magiskpolicy_not_magisk(tmp_path):
    magisk(tmp_path)
    result = provider(tmp_path, 'echo "$RUNGIC_ROOT $RUNGIC_BUSYBOX"; rungic_sepolicy_apply /rule')
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f'magisk {tmp_path}/data/adb/magisk/busybox',
                                          f'{tmp_path}/debug_ramdisk/magiskpolicy --live --apply /rule']


# covers: install.first-run-progress/E6
def test_kernelsu_uses_its_busybox_and_ksud(tmp_path):
    kernelsu(tmp_path)
    result = provider(tmp_path, 'echo "$RUNGIC_ROOT $RUNGIC_BUSYBOX [$RUNGIC_MAGISK]"; rungic_sepolicy_apply /rule')
    assert result.stdout.splitlines() == [f'kernelsu {tmp_path}/data/adb/ksu/bin/busybox []',
                                          f'{tmp_path}/data/adb/ksud sepolicy apply /rule']


# covers: install.first-run-progress/E6
def test_magisk_files_left_on_a_kernelsu_phone_do_not_make_it_magisk(tmp_path):
    kernelsu(tmp_path)
    tool(tmp_path / 'data/adb/magisk/busybox')      # Magisk uninstalled, its /data/adb/magisk left
    assert provider(tmp_path, 'echo "$RUNGIC_ROOT"').stdout.strip() == 'kernelsu'


# covers: install.first-run-progress/E6
def test_no_active_root_provider_is_an_error_not_a_guess(tmp_path):
    result = provider(tmp_path, 'echo "reached $RUNGIC_BUSYBOX"')
    assert result.returncode != 0 and 'No active root provider' in result.stderr and 'reached' not in result.stdout


# covers: install.first-run-progress/E6
@pytest.mark.parametrize('setup, expected', [(magisk, 'magisk data/adb/magisk/busybox'),
                                             (kernelsu, 'kernelsu data/adb/ksu/bin/busybox'),
                                             (lambda root: (kernelsu(root), tool(root / 'data/adb/magisk/busybox')),
                                              'kernelsu data/adb/ksu/bin/busybox')])
def test_installer_and_first_boot_decide_as_the_helpers_do(tmp_path, setup, expected):
    setup(tmp_path)
    kind, busybox = expected.split()
    want = f'{kind} {tmp_path}/{busybox}'
    helpers = provider(tmp_path, 'echo "$RUNGIC_ROOT $RUNGIC_BUSYBOX"').stdout.strip()
    assert helpers == want
    assert installer_rule(tmp_path).stdout.strip() == want
    assert firstboot_rule(tmp_path).stdout.strip() == want


# covers: install.first-run-progress/E6
def test_installer_and_first_boot_refuse_without_a_provider(tmp_path):
    for result in (installer_rule(tmp_path), firstboot_rule(tmp_path)):
        assert result.returncode != 0 and 'No active root provider' in result.stderr


def test_no_helper_hardcodes_magisk_busybox():
    for path in ('system/rungic-runtime', 'system/rootfs-image', 'system/android-device', 'system/android-calls',
                 'system/android-clipboard', 'shared/android/rungic-wfd-sepolicy.sh'):
        text = (ROOT / path).read_text()
        assert not re.search(r'/data/adb/magisk/(busybox|magiskpolicy)|magisk --live', text), path
