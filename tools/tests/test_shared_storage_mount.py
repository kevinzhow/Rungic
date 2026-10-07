# SPDX-License-Identifier: MIT
"""~/Shared's mount (system/shared-storage, docs/69): a bindfs that died leaves a disconnected FUSE
mount where stat fails; the service's next start detaches it before making the directory, so it does
not fail and restart forever. And the mount bindfs makes is the one that lets Android's changes show
on the next open (no --direct-io, attribute and entry caches off). The system's tools are stand-ins on
PATH that record their calls; bindfs is one too."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'system/shared-storage'


def stub(bin_dir, name, body):
    path = bin_dir / name
    path.write_text(f'#!/bin/sh\necho "{name} $*" >> "$CALLS"\n{body}\n')
    path.chmod(0o755)


def run(tmp_path, disconnected, fusermount_works=True):
    home = tmp_path / 'home/me'
    home.mkdir(parents=True, exist_ok=True)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir(exist_ok=True)
    calls = tmp_path / 'calls'
    calls.write_text('')
    stub(bin_dir, 'getent', f'echo "me:x:1000:1000::{home}:/bin/bash"')
    # A disconnected FUSE mount: stat (and mkdir) on it fail until it is detached.
    stub(bin_dir, 'stat', 'exit 1' if disconnected else 'exit 0')
    stub(bin_dir, 'fusermount3', 'exit 0' if fusermount_works else 'exit 1')
    stub(bin_dir, 'umount', 'exit 0')
    stub(bin_dir, 'chown', 'exit 0')
    stub(bin_dir, 'bindfs', 'exit 0')
    # setpriv OPTIONS... COMMAND: records itself, then runs the command.
    stub(bin_dir, 'setpriv', 'while [ "${1#--}" != "$1" ]; do shift; done\nexec "$@"')
    script = SCRIPT.read_text()
    assert script.count('/usr/bin/bindfs') == 1
    copy = tmp_path / 'shared-storage'
    copy.write_text(script.replace('/usr/bin/bindfs', str(bin_dir / 'bindfs')))
    result = subprocess.run(['sh', str(copy)], env={'PATH': f'{bin_dir}:/usr/bin:/bin', 'CALLS': str(calls)},
                            capture_output=True, text=True)
    return result, calls.read_text().splitlines(), home / 'Shared'


# covers: apps.shared-storage/E6
def test_a_dead_bindfs_mount_is_detached_before_the_restart(tmp_path):
    result, calls, shared = run(tmp_path, disconnected=True)
    assert result.returncode == 0, result.stderr
    assert f'fusermount3 -uz {shared}' in calls
    assert calls.index(f'fusermount3 -uz {shared}') < next(i for i, c in enumerate(calls) if c.startswith('bindfs'))
    assert shared.is_dir()
    # fusermount3 cannot (not the mounter): a lazy unmount instead.
    result, calls, _ = run(tmp_path, disconnected=True, fusermount_works=False)
    assert result.returncode == 0 and f'umount -l {shared}' in calls


# covers: apps.shared-storage/E6
def test_a_healthy_mount_point_is_left_and_android_changes_show_on_next_open(tmp_path):
    result, calls, shared = run(tmp_path, disconnected=False)
    assert result.returncode == 0 and not any(c.startswith(('fusermount3', 'umount')) for c in calls)
    bindfs = next(c for c in calls if c.startswith('bindfs')).split()
    assert bindfs[-2:] == ['/mnt/android-shared', str(shared)] and '-f' in bindfs
    options = bindfs[bindfs.index('-o') + 1].split(',')
    assert 'attr_timeout=0' in options and 'entry_timeout=0' in options
    assert not any('direct' in arg for arg in bindfs)      # direct-io breaks shared writable mmap (docs/69)
    assert os.stat(shared).st_mode & 0o777 == 0o755


# covers: apps.shared-storage/E7
def test_bindfs_reaches_android_as_the_shell_uid_so_the_media_library_follows(tmp_path):
    result, calls, shared = run(tmp_path, disconnected=False)
    assert result.returncode == 0, result.stderr
    setpriv = next(c for c in calls if c.startswith('setpriv')).split()
    # MediaProvider records the shell uid's creates, renames and deletes, not root's.
    assert '--reuid=2000' in setpriv and '--regid=2000' in setpriv
    assert '--groups=1000' in setpriv                       # the account's group: the home is 0750
    assert '--ambient-caps=+sys_admin' in setpriv           # only what mounting needs
    assert calls.index(next(c for c in calls if c.startswith('setpriv'))) < \
        calls.index(next(c for c in calls if c.startswith('bindfs')))
    bindfs = next(c for c in calls if c.startswith('bindfs')).split()
    assert 'allow_other' in bindfs[bindfs.index('-o') + 1].split(',')   # bindfs adds it only as root
