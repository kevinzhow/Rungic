# SPDX-License-Identifier: MIT
"""The Linux side's memory ceiling as the launcher writes and reports it (system/rungic-plasma,
docs/61). Its memory functions run in sh against a stand-in of Android's v1 memory cgroup: plain
files, so the kernel's own rule (memsw >= limit at every step) is not modelled, only what is written
and what is reported."""
import json
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / 'system/rungic-plasma'

STAT = """cache 1089818624
rss 627212288
shmem 238481408
swap 983629824
total_cache 1089818624
total_rss 627212288
total_shmem 238481408
total_swap 983629824
"""


def memory_functions(memcg, choice_file):
    """The launcher's memory settings and functions, pointed at the stand-in."""
    text = LAUNCHER.read_text()
    start = text.index('MEMCG=/dev/memcg/rungic-plasma')
    end = text.index('\n}\n', text.index('memory_status() {')) + 3
    part = text[start:end]
    part = part.replace('MEMCG=/dev/memcg/rungic-plasma', f'MEMCG={memcg}', 1)
    part = part.replace('MEMORY_CHOICE=$BASE/memory-limit', f'MEMORY_CHOICE={choice_file}', 1)
    return part


def cgroup(swap_accounting=True):
    folder = Path(tempfile.mkdtemp(prefix='rungic-memcg-'))
    memcg = folder / 'rungic-plasma'
    memcg.mkdir()
    values = {'memory.limit_in_bytes': '9223372036854771712', 'memory.usage_in_bytes': '1717129216',
              'memory.max_usage_in_bytes': '4294967296', 'memory.swappiness': '100',
              'memory.move_charge_at_immigrate': '0', 'memory.stat': STAT}
    if swap_accounting:
        values['memory.memsw.limit_in_bytes'] = '9223372036854771712'
    for name, value in values.items():
        (memcg / name).write_text(value)
    return folder, memcg


def run(memcg, choice, script):
    choice_file = memcg.parent / 'memory-limit'
    choice_file.write_text(choice)
    result = subprocess.run(['sh', '-c', 'set -eu\n' + memory_functions(memcg, choice_file) + script],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


def read(memcg, name):
    return (memcg / name).read_text().strip()


# covers: install.memory-limit/E3
def test_swap_counts_against_the_ceiling_with_a_quarter_more():
    _, memcg = cgroup()
    run(memcg, '4096', 'apply_memory_limit\n')
    assert read(memcg, 'memory.limit_in_bytes') == '4096M'
    assert read(memcg, 'memory.memsw.limit_in_bytes') == '5120M'
    assert read(memcg, 'memory.swappiness') == '40'
    run(memcg, '6144', 'apply_memory_limit\n')
    assert read(memcg, 'memory.memsw.limit_in_bytes') == '7680M'     # 32-bit sh: MiB stay small


# covers: install.memory-limit/E3
def test_no_ceiling_lifts_both():
    _, memcg = cgroup()
    run(memcg, 'unlimited', 'apply_memory_limit\n')
    assert read(memcg, 'memory.limit_in_bytes') == '-1'
    assert read(memcg, 'memory.memsw.limit_in_bytes') == '-1'


# covers: install.memory-limit/E3
def test_a_kernel_without_swap_accounting_keeps_the_plain_limit():
    _, memcg = cgroup(swap_accounting=False)
    run(memcg, '3072', 'apply_memory_limit\n')
    assert read(memcg, 'memory.limit_in_bytes') == '3072M'
    assert not (memcg / 'memory.memsw.limit_in_bytes').exists()


# covers: install.memory-limit/E4
def test_the_status_says_what_the_use_is_made_of():
    _, memcg = cgroup()
    (memcg / 'memory.limit_in_bytes').write_text('4294967296')
    (memcg / 'memory.memsw.limit_in_bytes').write_text('5368709120')
    status = json.loads(run(memcg, '4096', 'memory_status\n'))
    assert status['limit_mib'] == 4096 and status['swap_limit_mib'] == 5120
    assert status['usage_mib'] == 1637 and status['peak_mib'] == 4096
    assert (status['programs_mib'], status['cache_mib'], status['shmem_mib'], status['swap_mib']) == (598, 1039, 227, 938)


# covers: install.memory-limit/E4
def test_the_status_without_limits_or_stat_still_parses():
    _, memcg = cgroup(swap_accounting=False)
    (memcg / 'memory.stat').unlink()
    status = json.loads(run(memcg, '4096', 'memory_status\n'))
    assert status['limit_mib'] is None and status['swap_limit_mib'] is None
    assert status['programs_mib'] == 0 and status['swap_mib'] == 0


def test_the_launcher_still_parses():
    assert subprocess.run(['sh', '-n', str(LAUNCHER)]).returncode == 0
    assert re.search(r'MEMORY_SWAP_SHARE=4\b', LAUNCHER.read_text())
