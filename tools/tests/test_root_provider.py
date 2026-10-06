# SPDX-License-Identifier: MIT
"""The Android-side helpers resolve the root provider's tools, not a hardcoded Magisk path."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / 'system/root-provider'


def run(tmp_path, roots, body):
    text = PROVIDER.read_text()
    for real, place in roots.items():
        text = text.replace(real, place)
    script = tmp_path / 'root-provider'
    script.write_text(text + '\n' + body + '\n')
    return subprocess.run(['sh', str(script)], capture_output=True, text=True, check=True)


def stub(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\necho "$0 $*"\n')
    path.chmod(0o755)


# covers: install.first-run-progress/E6
def test_provider_prefers_magisk_tools_and_its_live_policy_loader(tmp_path):
    magisk = tmp_path / 'magisk'
    stub(magisk / 'busybox')
    stub(magisk / 'magisk')
    roots = {'/data/adb/magisk': str(magisk), '/debug_ramdisk/magisk': str(tmp_path / 'debug/magisk')}
    result = run(tmp_path, roots, 'echo "$RUNGIC_BUSYBOX"; rungic_sepolicy_apply /rule')
    assert result.stdout.splitlines() == [str(magisk / 'busybox'), f'{magisk}/magisk --live --apply /rule']


# covers: install.first-run-progress/E6
def test_provider_falls_back_to_kernelsu_busybox_and_ksud(tmp_path):
    ksu = tmp_path / 'ksu'
    stub(ksu / 'busybox')
    ksud = tmp_path / 'ksud'
    stub(ksud)
    roots = {'/data/adb/ksu/bin': str(ksu), '/data/adb/ksud': str(ksud)}
    result = run(tmp_path, roots, 'echo "$RUNGIC_BUSYBOX"; echo "magisk=[$RUNGIC_MAGISK]"; rungic_sepolicy_apply /rule')
    assert result.stdout.splitlines() == [str(ksu / 'busybox'), 'magisk=[]', f'{ksud} sepolicy apply /rule']
