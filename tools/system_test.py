#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""System tests: Rungic's Linux system features on a headless KWin, without the phone (quality/README.md
分层). They run in a throwaway container on the Mac mini (Ubuntu 26.04 ARM64, tools/system/Dockerfile
on the build image), from the working tree as it is (uncommitted files too); what Android provides comes
from stand-ins of the interfaces' contracts (tools/contracts.py). The phone is never touched.

  system_test.py run [TEST...]   build what the tests use and run them (all of tools/system/tests/ by default)
  system_test.py list            the tests

Results: one JSON line per test, kept with the container's build log under .work/system-tests/<time>/.
"""
import argparse
import datetime
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_on_device  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / 'tools/system/tests'
RESULTS = ROOT / '.work/system-tests'
REMOTE = '/tmp/rungic-system'
# Not needed to build or run a system test, and large.
SKIP = ('benchmarks/', 'docs/images/', 'packages/', 'provenance/', 'android/app/assets/')


def tests():
    return sorted(p.stem for p in TESTS.glob('*.py'))


def image(host):
    """The system test image's tag, built on the Mac mini when missing."""
    base = host.image()
    dockerfile = (ROOT / 'tools/system/Dockerfile').read_bytes()
    policy = ROOT / 'system/config/etc/dpkg/dpkg.cfg.d/zz-rungic-apps'
    tag = 'rungic-system:' + hashlib.sha256(base.encode() + b'\0' + dockerfile + b'\0' + policy.read_bytes()).hexdigest()[:12]
    have = host.ssh(f'{host.DOCKER} image inspect {tag} >/dev/null 2>&1 && echo yes || true', 60).stdout.decode().strip()
    if have != 'yes':
        if host.ssh(f'{host.DOCKER} image inspect {base} >/dev/null 2>&1 && echo yes || true', 60).stdout.decode().strip() != 'yes':
            host.ensure()       # the build image itself
        print(f'system test image {tag}: building on {base}', flush=True)
        context = io.BytesIO()
        with tarfile.open(fileobj=context, mode='w', format=tarfile.USTAR_FORMAT) as tar:
            tar.add(ROOT / 'tools/system/Dockerfile', arcname='Dockerfile')
            tar.add(policy, arcname='rungic-apps')
        args = f'--build-arg BASE={base} ' + ''.join(f'--build-arg {k}={v} ' for k, v in host.proxy().items())
        host.ssh(f'{host.DOCKER} build -q {args}-t {tag} -', 3600, data=context.getvalue())
    return tag


def working_tree():
    out = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                         capture_output=True, check=True).stdout.decode()
    names = [n for n in out.split('\0') if n and not n.startswith(SKIP) and (ROOT / n).is_file()]
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz') as tar:
        for name in names:
            tar.add(ROOT / name, arcname=name)
    return data.getvalue(), hashlib.sha256(data.getvalue()).hexdigest()[:12]


def run(names):
    host = build_on_device.MacMini()
    tag = image(host)
    archive, digest = working_tree()
    src = f'{REMOTE}/src-{digest}'
    # One run at a time on the Mac mini: parallel runs built at once and the compiler was killed for
    # memory, and a run's cleanup removed another's tree. A lock directory (macOS has no flock); one
    # older than 40 minutes is a run that died.
    lock = f'{REMOTE}/run.lock'
    host.ssh(f'mkdir -p {REMOTE}; until mkdir {lock} 2>/dev/null; do '
             f'[ $(( $(date +%s) - $(stat -f %m {lock} 2>/dev/null || date +%s) )) -gt 2400 ] && rmdir {lock}; sleep 5; done',
             3600)
    try:
        # A temporary directory of its own: two runs of the same tree no longer extract into one.
        host.ssh(f'if test -d {src}; then touch {src}; else part=$(mktemp -d {src}.XXXXXX) && tar -xzf - -C "$part" '
                 f'&& {{ mv "$part" {src} 2>/dev/null || rm -rf "$part"; }}; fi', 600, data=archive)
        record = RESULTS / datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        record.mkdir(parents=True)
        print(f'system tests {" ".join(names)} on {tag} (working tree {digest})', flush=True)
        result = host.ssh(f'{host.DOCKER} run --rm --cap-add SYS_NICE -v {src}:/src:ro {tag} '
                          f'sh /src/tools/system/run-in-container.sh {" ".join(names)}', 1800, check=False)
    finally:
        host.ssh(f'rmdir {lock}', 60, check=False)
    lines = [json.loads(l) for l in result.stdout.decode(errors='replace').splitlines() if l.startswith('{')]
    (record / 'results.json').write_text(json.dumps(lines, indent=1, ensure_ascii=False) + '\n')
    (record / 'stderr.txt').write_text(result.stderr.decode(errors='replace'))
    for line in lines:
        mark = 'PASS' if line.get('passed') else 'FAIL'
        print(f'{mark} {line["test"]} ({line.get("seconds", "-")} s)' + (f': {line["error"]}' if line.get('error') else '')
              + (f': {line["log"]}' if line.get('log') else ''))
    # Trees of runs that may still be going stay: keeping only the last three removed /src under
    # other agents' running tests (files missing in /src). Older than three hours, a tree is done.
    host.ssh(f'find {REMOTE} -maxdepth 1 \\( -name "tree-*" -o -name "src-*" \\) -mmin +180 -exec rm -rf {{}} +', 120,
             check=False)
    passed = lines and all(l.get('passed') for l in lines) and len([l for l in lines if 'build' not in l['test']]) == len(names)
    print(f'{"passed" if passed else "FAILED"}; record {record.relative_to(ROOT)}')
    return 0 if passed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    r = sub.add_parser('run')
    r.add_argument('tests', nargs='*')
    sub.add_parser('list')
    args = parser.parse_args(argv)
    if args.command == 'list':
        print('\n'.join(tests()))
        return 0
    unknown = set(args.tests) - set(tests())
    if unknown:
        parser.error(f'no such test: {", ".join(sorted(unknown))}')
    return run(args.tests or tests())


if __name__ == '__main__':
    sys.exit(main())
