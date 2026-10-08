#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Versioned releases of the Plasma container: local APT repository, release metapackage,
deploy, rollback and status (docs/61).

A release is rungic-release=<version>: a metapackage with an exact dependency on every
package in release/packages.json (rebuilt Ubuntu packages, this project's rungic-*
packages, and the Ubuntu packages coupled to them), plus /usr/share/rungic/release.json with the
git commit. The repository is .work/apt/repo on this computer (the pool of .debs is build
output); deploy mirrors it to /var/lib/rungic-apt in the container, where it is a trusted file:
source pinned at 1001, so its versions win over the archive and older releases can be
reinstalled. The Android-side files listed under "android" are part of a release too.

  rungic_release.py import-installed    pull the .debs of the installed +moto versions from the
                                      phone's build directories into the pool
  rungic_release.py import DEB...       add .debs to the pool
  rungic_release.py build [--version V] metapackage for the current packages.json and git commit,
                                      regenerate the repository index
  rungic_release.py list                releases in the repository
  rungic_release.py deploy [V]          preflight, record, sync, install, restart, verify (latest by default)
  rungic_release.py rollback            deploy the release that was installed before the current one
  rungic_release.py rollback --snapshot return the whole rootfs to the snapshot the last deploy took
  rungic_release.py commit              keep the current system: drop that snapshot
  rungic_release.py status              installed release, its commit, rootfs, repository and integrity

The dev channel (docs/109): releases cut from origin/main only, one publishing point instead of a
pool and a numbering per machine, the APK part of the release.

  rungic_release.py dev [--host H] [--out DIR] [--apk FILE|--no-apk] [--publish [--yes]]
                                      on a clean origin/main: build the stale project packages and
                                      upstream components, the APK, release YYYYMMDD.N (channel dev),
                                      and a bundle (repository, APK, manifest) in DIR
  rungic_release.py export V [--out DIR] the bundle of release V
  rungic_release.py deploy [V] --all    deploy on every connected Rungic phone, a summary at the end
  rungic_release.py deploy --from FILE  deploy a bundle another machine made (with or without --all)
  rungic_release.py status --all        one row per connected phone: release, channel, commit, behind
                                      origin/main, APK, development overlays, how apt holds the release
  rungic_release.py drift [--all] [--against REF]
                                      how far a phone is from origin/main, part by part: each project
                                      package and upstream component (its release's or its development
                                      overlay's commit against main's, over the paths it is built from),
                                      the APK's versionCode and every Android-side file; "in sync" or
                                      what differs
  rungic_release.py publish V [--yes]   the GitHub pre-release dev-V (gh); without --yes only the command
                                      and the notes: publishing is confirmed by the owner first

With an image rootfs (docs/61 §7) deploy first takes a snapshot of the whole rootfs; a failed
install or verification returns to it automatically, a good release keeps it until commit.

Every deploy leaves a record under .work/deploy/<time>-<version>/, and a line in release/history.json
(version, commit, channel, phone, result), which is committed with the repository.
"""
import argparse
import datetime
import fnmatch
import gzip
import hashlib
import io
import json
import lzma
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import rungic_device
from rungic_device import DeviceError, WORKSPACE, out, push, run

SPEC = WORKSPACE / 'release/packages.json'
APT = WORKSPACE / '.work/apt'
POOL = APT / 'repo'                  # flat repository: .debs, Packages, Release
REMOTE = '.remote'   # <name>.deb.remote: a .deb kept on the build host for the phone (tools/rungic_dev.py)
RELEASES = APT / 'releases'          # <version>.json: what each metapackage pins
DEPLOY = WORKSPACE / '.work/deploy'
HISTORY = DEPLOY / 'history.json'
# Every deploy on every phone, committed with the repository (docs/109); HISTORY above is this
# computer's own record, which rollback reads.
RELEASE_HISTORY = WORKSPACE / 'release/history.json'
APKS = APT / 'apk'                    # the releases' APKs (docs/109)
ANDROID_STORE = APT / 'android'       # Android-side files of imported bundles, by SHA-256
COMPONENT_BUILDS = APT / 'component-builds.json'   # tree of packages/<name> each component build had
BUNDLES = WORKSPACE / '.work/release-bundles'      # dev and export write bundles here (--out, RUNGIC_RELEASE_OUT)
GITHUB = 'kevinzhow/Rungic'
DEV_TAG = 'dev-'                      # GitHub pre-release tags: dev-<version>
# Written by deploy next to the pins: unattended-upgrades leaves the release alone by name, even where
# a pin is missing (docs/109).
UNATTENDED = '/etc/apt/apt.conf.d/52rungic-release'
PINS = '/etc/apt/preferences.d/rungic-release'
# /var/lib/moto-apt before the Rungic rename; both name the same directory from phase C to D (docs/70).
DEVICE_REPO = rungic_device.first_path('/var/lib/rungic-apt', '/var/lib/moto-apt')
META = 'rungic-release'
# The metapackage and the release file before the Rungic rename (docs/70): releases up to
# 20260926.20 are moto-plasma-release, and a rollback may go back to one of them.
FORMER_META = 'moto-plasma-release'


def meta_of(version):
    """The metapackage name of release `version` in the repository."""
    return FORMER_META if (POOL / f'{FORMER_META}_{version}_all.deb').exists() else META
# The build directories on the phone that hold .debs of installed versions (import-installed).
DEVICE_DEB_DIRS = ['/root/rungic-build/*', '/root/moto-build/*', '/root/moto-mesa-debs', '/root/moto-display-packages',
                   '/root/moto-media-packages', '/root/rungic-packages/*', '/root/moto-packages/*', '/root']
# The source and pin that rungic-plasma-config ships; deploy installs the same bytes before the
# package exists, so dpkg later takes them over as unchanged conffiles.
SOURCES = (WORKSPACE / 'system/config/etc/apt/sources.list.d/rungic.sources').read_text()
PREFERENCES = (WORKSPACE / 'system/config/etc/apt/preferences.d/rungic').read_text()
APT_OURS = ('-o Dir::Etc::SourceList=/etc/apt/sources.list.d/rungic.sources -o Dir::Etc::SourceParts=- '
            '-o APT::Get::List-Cleanup=0')


def spec():
    data = json.loads(SPEC.read_text())
    for component in data.get('rebuilt', {}).values():
        # A patch-queue component (docs/71): its version is the first entry of its changelog, always
        # (a 'version' left in packages.json from the vendor days pinned the old build, docs/70).
        if component.get('source', '').startswith('packages/'):
            changelog = (WORKSPACE / component['source'] / 'debian/changelog').read_text()
            component['version'] = re.match(r'^\S+ \(([^)]+)\)', changelog)[1]
    return data


def deb_field(path, field):
    return subprocess.run(['dpkg-deb', '-f', str(path), field], capture_output=True, text=True,
                          check=True).stdout.strip()


def upstream_name(name, version):
    """File name version: without the epoch."""
    return f"{name}_{version.split(':', 1)[-1]}"


def pool_debs():
    result = {}
    for deb in POOL.glob('*.deb'):
        m = re.match(r'([^_]+)_([^_]+)_([^_.]+)\.deb$', deb.name)
        if m:
            result.setdefault(m[1], {})[m[2]] = deb
    return result


def pull(path, target, timeout=1800):
    """adb pull of a root-only file: staged through /data/local/tmp."""
    stage = f'/data/local/tmp/rungic-pull-{int(time.time() * 1000)}'
    run(f'cp {shlex.quote(path)} {stage} && chmod 644 {stage}', 'root', timeout=timeout)
    try:
        subprocess.run(rungic_device.adb('pull', stage, str(target)), check=True, capture_output=True,
                       timeout=timeout)
    finally:
        run(f'rm -f {stage}', 'root', check=False)


def git_state():
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=WORKSPACE, capture_output=True, text=True,
                            check=True).stdout.strip()
    dirty = bool(subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=WORKSPACE,
                                capture_output=True, text=True).stdout.strip())
    return commit, dirty


def git(*args, check=True):
    """A git command in the repository: its output (the dev channel's checks, notes and counts)."""
    result = subprocess.run(['git', *args], cwd=WORKSPACE, capture_output=True, text=True)
    if check and result.returncode:
        raise SystemExit(f'git {" ".join(args)}: {result.stderr.strip()}')
    return result.stdout.strip()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------- pool

def import_debs(paths):
    POOL.mkdir(parents=True, exist_ok=True)
    added = []
    for path in map(Path, paths):
        name, version, arch = (deb_field(path, f) for f in ('Package', 'Version', 'Architecture'))
        target = POOL / f'{upstream_name(name, version)}_{arch}.deb'
        if target.exists() and target.read_bytes() != path.read_bytes():
            raise SystemExit(f'{target.name} is already in the pool with different contents; '
                             'bump the version instead of replacing a published package')
        if not target.exists():
            shutil.copy2(path, target)
            added.append(target.name)
    return added


def import_installed():
    """Pull .debs of the installed +moto versions listed in packages.json, checked against dpkg's md5sums."""
    wanted = []
    for component in spec()['rebuilt'].values():
        for name in component['packages']:
            wanted.append((name, component['version']))
    installed = dict(line.split('\t') for line in out(
        "dpkg-query -W -f '${Package}\\t${Version}\\n'", 'container').splitlines() if '\t' in line)
    have = pool_debs()
    missing = [(n, v) for n, v in wanted if upstream_name(n, v).split('_', 1)[1] not in have.get(n, {})]
    if not missing:
        return {'imported': [], 'already': len(wanted)}
    for name, version in missing:
        if installed.get(name) != version:
            raise SystemExit(f'{name}: packages.json says {version}, the phone has {installed.get(name)}')
    patterns = ' '.join(f"{d}/{upstream_name(n, v)}_*.deb" for n, v in missing for d in DEVICE_DEB_DIRS)
    script = f'''set -e
tmp=$(mktemp -d /var/tmp/rungic-import.XXXXXX)
for f in {patterns}; do
  [ -f "$f" ] || continue
  b=$(basename "$f"); [ -e "$tmp/$b" ] && continue
  n=$(dpkg-deb -f "$f" Package)
  # The .deb must be the one dpkg installed: same md5sums as /var/lib/dpkg/info.
  info=/var/lib/dpkg/info/$n.md5sums; [ -f "$info" ] || info=$(ls /var/lib/dpkg/info/$n:*.md5sums 2>/dev/null | head -1)
  # Hand-assembled .debs (package-mesa.py) carry no md5sums; dpkg computed them at install.
  if ! dpkg-deb --ctrl-tarfile "$f" | tar -xO ./md5sums > "$tmp/.sums" 2>/dev/null; then
    dpkg-deb --fsys-tarfile "$f" | python3 -c '
import hashlib, sys, tarfile
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as t:
    for m in t:
        if m.isfile():
            print(hashlib.md5(t.extractfile(m).read()).hexdigest() + "  " + m.name.removeprefix("./"))' > "$tmp/.sums"
  fi
  # Every installed file matches; extra files may only be ones dpkg's path-exclude dropped.
  if [ -z "$(comm -13 <(sort "$tmp/.sums") <(sort "$info"))" ] && \
     ! comm -23 <(sort "$tmp/.sums") <(sort "$info") | grep -v -E '  usr/share/(doc|man|locale)/' | grep -q .; then
    cp "$f" "$tmp/"
  fi
  rm -f "$tmp/.sums"
done
tar -C "$tmp" -cf /var/tmp/rungic-import.tar .
rm -rf "$tmp"
'''
    run(f"bash -c {shlex.quote(script)}", 'container', timeout=600)
    local = APT / 'incoming'
    shutil.rmtree(local, ignore_errors=True)
    local.mkdir(parents=True)
    rungic_device.from_container('/var/tmp/rungic-import.tar', local / 'x.tar')
    run('rm -f /var/tmp/rungic-import.tar', 'container')
    with tarfile.open(local / 'x.tar') as tar:
        tar.extractall(local, filter='data')
    (local / 'x.tar').unlink()
    added = import_debs(sorted(local.glob('*.deb')))
    shutil.rmtree(local)
    have = pool_debs()
    still = [f'{n}={v}' for n, v in missing if upstream_name(n, v).split('_', 1)[1] not in have.get(n, {})]
    if still:
        raise SystemExit(f'not found on the phone (or not identical to what is installed): {still}')
    return {'imported': added}


# ---------------------------------------------------------------- build

def next_version(elsewhere=()):
    """Today's next YYYYMMDD.N: after this pool's releases and the versions in `elsewhere` (the dev
    channel's published tags and deployments, so two machines do not cut the same number)."""
    today = datetime.date.today().strftime('%Y%m%d')
    taken = [int(p.stem.split('.')[1]) for p in RELEASES.glob(f'{today}.*.json')] if RELEASES.exists() else []
    taken += [int(v.split('.')[1]) for v in elsewhere if re.fullmatch(rf'{today}\.\d+', v)]
    return f'{today}.{max(taken, default=0) + 1}'


def build_meta(version, deps, info, dest=None):
    """The release metapackage; dest: another directory than the pool (development overlays)."""
    root = Path(tempfile.mkdtemp(dir=WORKSPACE / '.work/cache'))
    root.chmod(0o755)   # dpkg-deb refuses mkdtemp's 0700
    try:
        (root / 'DEBIAN').mkdir(mode=0o755)
        doc = root / 'usr/share/rungic'
        doc.mkdir(parents=True)
        (doc / 'release.json').write_text(json.dumps(info, indent=1, ensure_ascii=False) + '\n')
        depends = ', '.join(f'{n} (= {v})' for n, v in sorted(deps.items()))
        channel = f' ({info["channel"]} channel)' if info.get('channel', 'release') != 'release' else ''
        (root / 'DEBIAN/control').write_text(f'''Package: {META}
Version: {version}
Architecture: all
Maintainer: range-dev <noreply@localhost>
Priority: optional
Section: metapackages
Protected: yes
Depends: {depends}
Conflicts: {FORMER_META}
Replaces: {FORMER_META}
Description: Rungic: release {version}{channel}
 Pins every package of this project's release {version} (git {info["commit"][:12]}).
 See docs/61-delivery-diagnostics-plan.md.
''')
        target = (dest or POOL) / f'{META}_{version}_all.deb'
        built = subprocess.run(['dpkg-deb', '--root-owner-group', '-Zxz', '--build', str(root), str(target)],
                               capture_output=True, text=True)
        if built.returncode:
            raise SystemExit(f'dpkg-deb: {built.stderr.strip()}')
        return target
    finally:
        shutil.rmtree(root)


def index(pool=None, label='rungic'):
    """Flat repository index: Packages(.gz,.xz) and Release with origin and label rungic (the pin of
    system/config/etc/apt/preferences.d/rungic; moto / moto-plasma before the Rungic rename).
    pool and label: the development overlay's repository (tools/rungic_dev.py, label rungic-dev)."""
    POOL = pool or globals()['POOL']
    POOL.mkdir(parents=True, exist_ok=True)
    archive = ['apt-ftparchive']
    if not shutil.which('apt-ftparchive'):
        engine = shutil.which('podman') or shutil.which('docker')
        if not engine:
            raise SystemExit('apt-ftparchive or a container engine is required to index the APT pool')
        archive = [engine, 'run', '--rm', '--security-opt', 'label=disable',
                   '-v', f'{POOL.resolve()}:/repo:ro', '-w', '/repo', 'rungic-pq:26.04',
                   'apt-ftparchive']
    packages = subprocess.run([*archive, 'packages', '.'], cwd=POOL, capture_output=True,
                              check=True).stdout
    # .debs kept on the build host for the phone (REMOTE): their entries, made where they are.
    for kept in sorted(POOL.glob('*.deb' + REMOTE)):
        packages += json.loads(kept.read_text())['stanza'].encode()
    (POOL / 'Packages').write_bytes(packages)
    (POOL / 'Packages.gz').write_bytes(gzip.compress(packages, mtime=0))
    (POOL / 'Packages.xz').write_bytes(lzma.compress(packages))
    release = subprocess.run([*archive, '-o', f'APT::FTPArchive::Release::Origin={label}',
                              '-o', f'APT::FTPArchive::Release::Label={label}',
                              '-o', f'APT::FTPArchive::Release::Suite={label}',
                              '-o', f'APT::FTPArchive::Release::Codename={label}', 'release', '.'],
                             cwd=POOL, capture_output=True, check=True).stdout
    (POOL / 'Release').write_bytes(release)


def build(version=None, allow_dirty=False, note='', coupled_override=None, channel='release', apk=None):
    """channel: 'release' (a formal release) or 'dev' (rungic_release.py dev, docs/109); apk: the
    release's APK (release_apk()), installed by deploy where the phone's is older."""
    commit, dirty = git_state()
    if dirty and not allow_dirty:
        raise SystemExit('tracked files have uncommitted changes; commit first (a release records its commit)')
    s = spec()
    deps = {}
    have = pool_debs()
    missing = []
    for component in s['rebuilt'].values():
        for name in component['packages']:
            if upstream_name(name, component['version']).split('_', 1)[1] not in have.get(name, {}):
                missing.append(f"{name}={component['version']}")
            deps[name] = component['version']
    # The project's own packages: the version built from the current commit (tools/rungic_package.py).
    if s.get('project'):
        import rungic_package
        definitions = rungic_package.definitions()
        built = rungic_package.builds()
        for name in s['project']:
            pkg = definitions.get(name)
            if pkg is None:
                raise SystemExit(f'{name} has no packaging definition')
            if not rungic_package.current(pkg):
                missing.append(f'{name} (not built for the current sources: rungic_package.py build {name})')
                continue
            deps[name] = built[name]['version']
    if missing:
        raise SystemExit(f'not in the pool: {missing} (build them, or import-installed)')
    coupled = {}
    if s.get('coupled'):
        if coupled_override is None:
            text = out('dpkg-query -W -f \'${Package}\\t${Version}\\n\' ' + ' '.join(map(shlex.quote, s['coupled'])),
                       'container')
            coupled = {n: v for n, v in (line.split('\t') for line in text.splitlines() if '\t' in line) if v}
        else:
            coupled = coupled_override
        if set(s['coupled']) - set(coupled):
            raise SystemExit(f"coupled packages not installed: {sorted(set(s['coupled']) - set(coupled))}")
        if set(coupled) - set(s['coupled']) or any(not isinstance(v, str) or not v for v in coupled.values()):
            raise SystemExit('coupled package override contains unexpected names or empty versions')
        deps.update(coupled)
    version = version or next_version()
    if (POOL / f'{META}_{version}_all.deb').exists() or (POOL / f'{FORMER_META}_{version}_all.deb').exists():
        raise SystemExit(f'release {version} exists already')
    info = {'version': version, 'channel': channel, 'commit': commit, 'dirty': dirty,
        'built': datetime.datetime.now().isoformat(timespec='seconds'), 'note': note, 'packages': deps,
        'coupled': sorted(coupled), 'apk': apk,
        'android': android_manifest(s), 'session_restart': s.get('session_restart', []),
        'service_restart': s.get('service_restart', {}), 'user_restart': s.get('user_restart', {})}
    meta = build_meta(version, deps, info)
    index()
    RELEASES.mkdir(parents=True, exist_ok=True)
    (RELEASES / f'{version}.json').write_text(json.dumps(info, indent=1, ensure_ascii=False) + '\n')
    return {'version': version, 'metapackage': meta.name, 'packages': len(deps), 'commit': commit[:12]}


def android_manifest(s):
    result = {}
    for item in s.get('android', []):
        data = (WORKSPACE / item['source']).read_bytes()
        result[item['path']] = {'source': item['source'], 'mode': item.get('mode', '644'),
                                'sha256': hashlib.sha256(data).hexdigest()}
    return result


def releases():
    if not RELEASES.exists():
        return []
    return sorted((json.loads(p.read_text()) for p in RELEASES.glob('*.json')),
                  key=lambda r: [int(x) for x in r['version'].split('.')])


# ---------------------------------------------------------------- device

def device_release():
    text = run(f'''cat /usr/share/rungic/release.json 2>/dev/null || cat /usr/share/moto/release.json 2>/dev/null
for p in {META} {FORMER_META}; do
  [ "$(dpkg-query -W -f '${{db:Status-Abbrev}}' $p 2>/dev/null)" = "ii " ] && dpkg-query -W -f '\n@@${{Version}}' $p && break
done''', 'container', check=False).stdout
    body, _, version = text.partition('\n@@')
    try:
        info = json.loads(body) if body.strip() else None
    except ValueError:
        info = None
    return (version.strip() or None), info


def preflight():
    problems = []
    state = run(f'''
[ -e /var/lib/dpkg/lock-frontend ] && fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 && echo "dpkg is locked by another process"
a=$(dpkg --audit 2>&1); [ -n "$a" ] && echo "dpkg --audit: $(echo "$a" | head -3)"
free=$(df -Pk / | awk 'NR==2 {{print $4}}'); [ "$free" -lt 2097152 ] && echo "less than 2 GiB free on /"
systemctl is-system-running >/dev/null 2>&1 || echo "systemd: $(systemctl is-system-running 2>&1)"
true''', 'container', check=False)
    if state.returncode:
        problems.append(f'container not reachable: {state.stderr.strip()}')
    problems += [l for l in state.stdout.splitlines() if l.strip()]
    # systemd "degraded" is common (failed units); record it but do not stop on it.
    fatal = [p for p in problems if not p.startswith('systemd:')]
    return problems, fatal


def installed_versions():
    text = out("dpkg-query -W -f '${db:Status-Abbrev}\\t${Package}\\t${Version}\\n'", 'container', timeout=120)
    return {name: version for status, name, version in
            (line.split('\t') for line in text.splitlines() if line.count('\t') == 2)
            if status.startswith(('ii', 'hi'))}


def integrity_summary():
    text = run('for p in /usr/bin/rungic-integrity /usr/bin/moto-integrity; do [ -x $p ] && exec $p --json; '
               'done; echo null', 'container', timeout=300, check=False).stdout
    try:
        report = json.loads(text)
    except ValueError:
        return None
    return report


def release_debs(info, version):
    """The pool's .debs a release pins (its packages and metapackage); coupled packages missing from
    the pool come from Ubuntu's archive."""
    have = pool_debs()
    found = (have.get(name, {}).get(wanted.split(':', 1)[-1])
             for name, wanted in {**info['packages'], meta_of(version): version}.items())
    return {deb.name for deb in found if deb is not None}


def kept_files(pool):
    """name -> its record, for the .debs of `pool` kept on the build host (REMOTE)."""
    return {p.name[:-len(REMOTE)]: json.loads(p.read_text()) for p in pool.glob('*.deb' + REMOTE)}


def fetch_kept(kept, device_repo):
    """The phone takes the .debs kept on the build host straight from it (AGENTS.md: devices that
    reach each other exchange files directly), checked by size and SHA-256, three tries each."""
    import build_on_device
    lines = ['set -e', f'cd {device_repo}', 'take() {', '  for try in 1 2 3; do',
             f'    {build_on_device.MacMini.PHONE_SSH} get "$2" </dev/null > "$1.part" || true',
             '    if [ "$(stat -c %s "$1.part")" = "$3" ] && [ "$(sha256sum < "$1.part" | cut -d" " -f1)" = "$4" ]; then',
             '      mv "$1.part" "$1"; return 0; fi', '  done', '  rm -f "$1.part"; echo "$1: not taken from the build host" >&2; return 1', '}']
    lines += [f'take {shlex.quote(name)} {shlex.quote(k["path"])} {k["size"]} {k["sha256"]}'
              for name, k in sorted(kept.items())]
    run('\n'.join(lines), 'container', timeout=600 + 120 * len(kept))


BUILD_POOL = 'release-pool'   # under build_on_device.BASE: every .deb the phones take from the build host


def stage_on_build_host(names, pool):
    """The .debs `names` of `pool` in the build host's release pool, for the phone to take straight
    from there (AGENTS.md: devices that reach each other exchange files directly; through this
    computer a release's new packages took about 20 minutes): what the build host built is linked in
    from its build directories (collect renames a .ddeb to .deb), anything else is copied there once.
    -> {name: record} for fetch_kept; empty when the build host is not a Mac mini or fails."""
    import build_on_device
    if not names or os.environ.get('RUNGIC_BUILD_HOST', 'macmini') != 'macmini':
        return {}
    host = build_on_device.MacMini()
    wanted = {name: sha256_file(pool / name) for name in names}
    listing = '\n'.join(f'{name} {digest}' for name, digest in sorted(wanted.items()))
    script = f'''cd {build_on_device.BASE} && mkdir -p {BUILD_POOL} || exit 1
while read -r n digest; do
  for f in {BUILD_POOL}/"$n" */"$n" */"${{n%.deb}}.ddeb" */*/"$n"; do
    [ -f "$f" ] || continue
    [ "$(sha256sum < "$f" | cut -d" " -f1)" = "$digest" ] || continue
    if [ "$f" != {BUILD_POOL}/"$n" ]; then ln -f "$f" {BUILD_POOL}/"$n"; fi
    echo "$n"
    break
  done
done <<'NAMES'
{listing}
NAMES'''
    try:
        there = set(host.out(script, timeout=120 + 2 * len(names)).split())
        for name in sorted(set(wanted) - there):
            print(f'{name}: not on the build host; copying it there once')
            host.put(pool / name, f'{build_on_device.BASE}/{BUILD_POOL}/{name}', '644')
    except Exception as error:  # noqa: BLE001 - the phone is sent the files from here instead
        print(f'the build host could not stage the packages ({error}); sending them from here')
        return {}
    return {name: {'path': f'{BUILD_POOL}/{name}', 'size': (pool / name).stat().st_size, 'sha256': digest}
            for name, digest in wanted.items()}


def sync_repo(pool=None, device_repo=None, only=None):
    """Mirror .work/apt/repo to /var/lib/rungic-apt: push missing .debs, replace the index.
    pool and device_repo: the development overlay's repositories (tools/rungic_dev.py), where a
    .deb may be kept on the build host (REMOTE): the phone takes it from there.
    only: the .debs to bring (a deploy: its release's, release_debs); others of the pool stay off the
    phone. Mirroring the whole pool sent every old build: 20261008.1 on a phone whose repository was
    another machine's started copying 829 .debs (3.2 GB) for a release of 89 packages."""
    POOL, DEVICE_REPO = pool or globals()['POOL'], device_repo or globals()['DEVICE_REPO']
    listing = run(f'mkdir -p {DEVICE_REPO} && cd {DEVICE_REPO} && ls -1', 'container').stdout.split()
    kept = kept_files(POOL)
    here = {p.name for p in POOL.iterdir() if p.is_file() and not p.name.endswith(REMOTE)}
    local = here | set(kept)
    if only is not None:
        here = {n for n in here if not n.endswith('.deb') or n in only}
        kept = {n: k for n, k in kept.items() if n in only}
    fetch = {name: k for name, k in kept.items() if name not in listing}
    if fetch:
        fetch_kept(fetch, DEVICE_REPO)
    # The phone takes the packages from the build host (stage_on_build_host); only the index goes
    # from here, and the packages too if the build host or the phone's key to it fails.
    direct = stage_on_build_host(sorted(n for n in here - set(listing) if n.endswith('.deb')), POOL)
    if direct:
        try:
            fetch_kept(direct, DEVICE_REPO)
        except Exception as error:  # noqa: BLE001 - sent from here instead
            print(f'the phone did not take the files from the build host ({error}); sending them from here')
            direct = {}
    send = sorted((here - set(listing) - set(direct)) | {'Packages', 'Packages.gz', 'Packages.xz', 'Release'})
    remove = sorted(set(listing) - local)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as tar:
        for name in send:
            tar.add(POOL / name, arcname=name)
    archive = DEPLOY / 'repo-sync.tar'
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(buffer.getvalue())
    rungic_device.extract_in_container(archive, DEVICE_REPO)
    archive.unlink()
    run(f'''set -e
chown -R root:root {DEVICE_REPO}; chmod 755 {DEVICE_REPO}
cd {DEVICE_REPO} && rm -f {' '.join(map(shlex.quote, remove)) or '/dev/null/none 2>/dev/null || true'}''',
        'container', check=False)
    return {'sent': len(send), 'fetched': len(fetch) + len(direct), 'removed': len(remove)}


def ensure_apt_source():
    """The source and pin (rungic-plasma-config ships the same files once installed)."""
    run(f'''set -e
cat > /etc/apt/sources.list.d/rungic.sources.new <<'EOF'
{SOURCES}EOF
# The repository's path on this system (before phase C only /var/lib/moto-apt is mounted, docs/70).
repo={DEVICE_REPO}
sed -i "s#file:/var/lib/rungic-apt#file:$repo#" /etc/apt/sources.list.d/rungic.sources.new
cmp -s /etc/apt/sources.list.d/rungic.sources.new /etc/apt/sources.list.d/rungic.sources 2>/dev/null \
  && rm /etc/apt/sources.list.d/rungic.sources.new || mv /etc/apt/sources.list.d/rungic.sources.new /etc/apt/sources.list.d/rungic.sources
cat > /etc/apt/preferences.d/rungic.new <<'EOF'
{PREFERENCES}EOF
cmp -s /etc/apt/preferences.d/rungic.new /etc/apt/preferences.d/rungic 2>/dev/null \
  && rm /etc/apt/preferences.d/rungic.new || mv /etc/apt/preferences.d/rungic.new /etc/apt/preferences.d/rungic
''', 'container')


def release_siblings(info):
    """Installed packages built from the same source as a package of the release but not in it ->
    their installed versions. plasma-workspace is coupled (its private ABI is used), and its private
    libraries (libtaskmanager6, libkworkspace6-6, ...) depend on each other only with >=: without a pin
    of their own an Ubuntu update moves them away from the pinned plasma-workspace (docs/109)."""
    text = getattr(run("dpkg-query -W -f '${db:Status-Abbrev}\\t${Package}\\t${source:Package}\\t${Version}\\n'",
                       'container', timeout=120, check=False), 'stdout', '') or ''
    installed = {name: (source, version) for status, name, source, version in
                 (line.split('\t') for line in text.splitlines() if line.count('\t') == 3)
                 if status.startswith(('ii', 'hi'))}
    sources = {installed[n][0] for n in info['packages'] if n in installed}
    return {n: v for n, (s, v) in sorted(installed.items())
            if s in sources and n not in info['packages'] and n not in (META, FORMER_META)}


def pin_body(info, siblings):
    lines = ['# Written by tools/rungic_release.py deploy: the packages of release ' + info['version'] + '.']
    for name, version in sorted({**info['packages'], meta_of(info['version']): info['version']}.items()):
        lines += ['', f'Package: {name}', f'Pin: version {version}', 'Pin-Priority: 1001']
    if siblings:
        lines += ['', '# Installed packages from the same sources, at their installed versions (docs/109).']
        for name, version in sorted(siblings.items()):
            lines += ['', f'Package: {name}', f'Pin: version {version}', 'Pin-Priority: 1001']
    return '\n'.join(lines) + '\n'


def unattended_body(info, siblings):
    """unattended-upgrades' blacklist of the release's packages by exact name. It honours the pins (a
    pinned installed version is not upgradable), so this holds only where a pin is missing; the static
    list rungic-plasma-config ships (51rungic-unattended-upgrades) misses components added since.
    Entries are Python regular expressions matched from the start of the name: '.' and '+' are
    bracketed, apt.conf has no escapes."""
    names = sorted({*info['packages'], *siblings, meta_of(info['version'])})
    entries = ''.join('\t"' + re.sub(r'([.+])', r'[\1]', n) + '$";\n' for n in names)
    return (f'// Written by tools/rungic_release.py deploy: release {info["version"]} (docs/109). Its packages move\n'
            f'// only through a release, also where /etc/apt/preferences.d/rungic-release is missing.\n'
            f'Unattended-Upgrade::Package-Blacklist {{\n{entries}}};\n')


def pin_release(info, siblings=None):
    """Pin every package of the installed release to its exact version (docs/61): above the Ubuntu
    archive and the repository's other builds, so neither Discover's updates nor apt upgrades change
    them. With the release metapackage Protected, apt also refuses to remove it to get around its
    exact dependencies; and the pins do not depend on it: they hold the versions if it goes anyway.
    Installed packages from the same sources are pinned as installed (release_siblings), and
    unattended-upgrades gets the release's names as a blacklist (docs/109)."""
    if siblings is None:
        siblings = release_siblings(info)
    run(f'''set -e
cat > {PINS}.new <<'EOF'
{pin_body(info, siblings)}EOF
mv {PINS}.new {PINS}
cat > {UNATTENDED}.new <<'EOF'
{unattended_body(info, siblings)}EOF
mv {UNATTENDED}.new {UNATTENDED}
apt-get -q update {APT_OURS} >/dev/null 2>&1 || true
''', 'container')
    return siblings


def apt_install(info, record):
    """apt-get install in a transient unit, so an adb disconnect does not interrupt dpkg.

    Every package of the release is named with its exact version: apt does not downgrade
    dependencies on its own, which a rollback needs."""
    version = info['version']
    pins = ' '.join(shlex.quote(f'{n}={v}') for n, v in sorted(info['packages'].items()))
    unit = f'rungic-deploy-{int(time.time())}'
    result = run(f'''set -e
# Our repository's Origin/Label changed with the Rungic rename (moto -> rungic, docs/70); apt refuses
# such a change unless allowed. The source is this project's own, local and trusted.
apt-get -q update --allow-releaseinfo-change {APT_OURS} >/dev/null
systemd-run --unit={unit} --wait --pipe --collect --quiet -p TimeoutStartSec=3600 \\
  --setenv=http_proxy=http://192.168.5.45:6152 --setenv=https_proxy=http://192.168.5.45:6152 \\
  --setenv=DEBIAN_FRONTEND=noninteractive \\
  apt-get -q -y --allow-downgrades --allow-change-held-packages --no-install-recommends \\
  -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold install {meta_of(version)}={version} {pins} 2>&1
''', 'container', timeout=3900, check=False)
    (record / 'apt.log').write_text(result.stdout + result.stderr)
    return result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]


def android_content(info, item):
    """The bytes of an Android-side file as the release has it: the working tree's when they match,
    else the copy an imported bundle brought (ANDROID_STORE: deploy --from on a machine that did not
    build it), else the file at the release's commit (a rollback deploys an older release without
    checking it out). None when none matches the recorded sha256."""
    path = WORKSPACE / item['source']
    data = path.read_bytes() if path.exists() else b''
    if hashlib.sha256(data).hexdigest() == item['sha256']:
        return data
    stored = ANDROID_STORE / item['sha256']
    if stored.exists() and hashlib.sha256(stored.read_bytes()).hexdigest() == item['sha256']:
        return stored.read_bytes()
    shown = subprocess.run(['git', 'show', f"{info.get('commit', '')}:{item['source']}"], cwd=WORKSPACE,
                           capture_output=True)
    if shown.returncode == 0 and hashlib.sha256(shown.stdout).hexdigest() == item['sha256']:
        return shown.stdout
    return None


def android_source_changes(info):
    """Android-side files of the release that neither the working tree nor its commit has (a deploy
    would stop halfway on them)."""
    return [item['source'] for item in (info.get('android') or {}).values() if android_content(info, item) is None]


def android_layouts(info):
    """(the phone's, the release's) Android-side layout: 'rungic' after the phase C cutover
    (/data/adb/rungic-*, tools/rungic_cutover.py), 'moto' before it; the release's is None when it
    installs no Android-side files (docs/70)."""
    device = run('[ -d /data/adb/rungic-plasma ] && echo rungic || echo moto', 'root').stdout.strip()
    paths = [path for path in (info.get('android') or {})]
    release = ('rungic' if any(path.startswith('/data/adb/rungic-') for path in paths) else 'moto') if paths else None
    return device, release


def sync_android(info, record):
    """Android-side files of the release: back up what is there, install what the release names."""
    files = info.get('android') or {}
    if not files:
        return []
    changed = []
    backup = record / 'android-before'
    for path, item in files.items():
        current = run(f'sha256sum {shlex.quote(path)} 2>/dev/null | cut -d" " -f1', 'root', check=False).stdout.strip()
        if current == item['sha256']:
            continue
        data = android_content(info, item)
        if data is None:
            raise SystemExit(f"{item['source']} of release {info['version']} is neither in the working tree "
                             'nor at its commit')
        source = WORKSPACE / f".work/cache/android-{path.strip('/').replace('/', '__')}"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(data)
        backup.mkdir(parents=True, exist_ok=True)
        saved = backup / path.strip('/').replace('/', '__')
        if current:
            pull(path, saved)
        remote = push(source, 'rungic-android-file')
        run(f'install -m{item["mode"]} {remote} {shlex.quote(path)}.new && mv {shlex.quote(path)}.new '
            f'{shlex.quote(path)} && rm -f {remote}', 'root')
        changed.append(path)
        entry = {'path': path, 'saved': saved.name if current else None, 'mode': item['mode']}
        listing = backup / 'changed.json'
        # What restore_android() puts back: the old file, or nothing where there was none.
        listing.write_text(json.dumps([*(json.loads(listing.read_text()) if listing.exists() else []), entry],
                                      indent=1) + '\n')
    return changed


CONVERGE = '/data/adb/rungic-plasma/rungic-converge'


def converge_android(mode):
    """rungic-converge on the phone (docs/122): apply sets the Android settings that should always
    hold, check only reads them. -> [{'id', 'state', 'detail'}], or None on a phone without it."""
    text = run(f'[ -x {CONVERGE} ] || exit 0; {CONVERGE} {mode}', 'root', timeout=180, check=False).stdout or ''
    items = [dict(zip(('id', 'state', 'detail'), (line.split(' ', 2) + [''])[:3]))
             for line in text.splitlines() if len(line.split()) >= 2]
    return items or None


def restore_android(record):
    """Undo sync_android() of a deploy record: the Android side follows its rootfs back to the
    snapshot (an LXC configuration naming files the old rootfs lacks would not start, docs/70)."""
    listing = record / 'android-before' / 'changed.json'
    if not listing.exists():
        return []
    restored = []
    for item in json.loads(listing.read_text()):
        path = shlex.quote(item['path'])
        if item['saved']:
            remote = push(record / 'android-before' / item['saved'], 'rungic-android-file')
            run(f'install -m{item["mode"]} {remote} {path}.new && mv {path}.new {path} && rm -f {remote}', 'root')
        else:
            run(f'rm -f {path}', 'root')
        restored.append(item['path'])
    return restored


def installed_apk():
    """(versionName, versionCode) of the phone's desktop APK, or (None, None)."""
    text = getattr(run(f'dumpsys package {rungic_device.APK} | grep -m2 -E "versionCode=|versionName="',
                       'shell', timeout=30, check=False), 'stdout', '') or ''
    name, code = re.search(r'versionName=(\S+)', text), re.search(r'versionCode=(\d+)', text)
    return (name[1] if name else None), (int(code[1]) if code else None)


def adb_install(path):
    """adb install -r: the APK replaced and its data kept (docs/97). From this computer, as the Android
    shell: pm install as Magisk root has failed with Binder errors (AGENTS.md). -> (ok, output)"""
    result = subprocess.run(rungic_device.adb('install', '-r', str(path)), capture_output=True, text=True,
                            timeout=600, stdin=subprocess.DEVNULL)
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0 and 'Success' in output, output[-800:]


def install_apk(info, restart='auto'):
    """The release's APK where the phone has an older one (by versionCode): adb install -r keeps the
    app's data, then the activity starts again, as after installing an APK by hand (docs/96, 97). A
    phone without the APK is left alone: a first installation is tools/ci/standalone.py's. Replacing
    the APK restarts the desktop, so --restart never leaves it (recorded). None when the release has
    no APK."""
    apk = info.get('apk')
    if not apk:
        return None
    name, before = installed_apk()
    if before is None:
        return {'result': 'skipped: the APK is not installed', 'release': apk['version_code']}
    if before >= apk['version_code']:
        return {'result': 'current', 'installed': before, 'release': apk['version_code']}
    if restart == 'never':
        return {'result': 'skipped: --restart never', 'installed': before, 'release': apk['version_code']}
    path = APKS / apk['file']
    if not path.exists() or sha256_file(path) != apk['sha256']:
        raise SystemExit(f"the APK {apk['file']} of release {info['version']} is not in {APKS} (or differs): "
                         'deploy --from the release\'s bundle')
    ok, output = adb_install(path)
    if not ok:
        raise SystemExit(f"adb install -r {apk['file']}: {output}")
    run(f'am start -n {rungic_device.APK}/.MainActivity', 'shell', timeout=30, check=False)
    _, after = installed_apk()
    if after != apk['version_code']:
        raise SystemExit(f"the phone reports APK versionCode {after} after installing {apk['version_code']}")
    return {'result': 'installed', 'before': f'{name}/{before}', 'after': f"{apk['version_name']}/{after}",
            'file': apk['file']}


def needs_restart(before, after, patterns):
    changed = [n for n in set(before) | set(after) if before.get(n) != after.get(n)]
    hit = sorted(n for n in changed if any(fnmatch.fnmatch(n, p) for p in patterns))
    return hit, sorted(changed)


def rebrand_down():
    """Before a release from before the Rungic rename replaces this one (docs/70): the desktop user's
    settings get the names that release knows. /home is not in the rootfs snapshot, so neither a
    package rollback nor a snapshot rollback takes it back. The session is stopped first (its
    programs write their settings on exit) and started by the release's restart. None when this
    system has no Rungic names."""
    if run('test -x /usr/libexec/rungic-rebrand-user', 'container', check=False).returncode:
        return None
    run('systemctl stop rungic-plasma-session.service', 'container', timeout=180, check=False)
    # As the desktop user without its session's environment (the session is stopped).
    user = run('''u=$(getent passwd 1000 | cut -d: -f1); h=$(getent passwd 1000 | cut -d: -f6)
runuser -u "$u" -- env HOME="$h" /usr/libexec/rungic-rebrand-user down''', 'container', timeout=300, check=False)
    # Then the account (home /home/<login>, group rungic) with none of its processes left and the
    # shared storage unmounted from the home.
    system = run('''systemctl stop user@1000.service rungic-plasma-shared.service
for i in $(seq 50); do pgrep -u 1000 >/dev/null || break; sleep 0.2; done
/usr/libexec/rungic-rebrand-system down''', 'container', timeout=180, check=False)
    return {'ok': user.returncode == 0 and system.returncode == 0,
            'output': (user.stdout + user.stderr + system.stdout + system.stderr).strip()[-800:]}


def restart_session():
    result = subprocess.run([sys.executable, str(WORKSPACE / 'tools/rungic_plasma.py'), 'restart-session'],
                            capture_output=True, text=True, timeout=300)
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def restart_user_services(spec, before, after):
    """Restart the running desktop-user units of changed packages (plasmashell last: it loads the
    others' QML plugins and applets) and quit running instances of listed programs. Units that are
    not running stay as they are. None when nothing applies."""
    items = []
    for pkg, names in spec.items():
        if before.get(pkg) != after.get(pkg):
            items += [n for n in names if n not in items]
    if not items:
        return None
    units = sorted((n for n in items if n.endswith('.service')), key=lambda n: n == 'plasma-plasmashell.service')
    programs = [n for n in items if n.startswith('/')]
    script = ('for u in ' + ' '.join(shlex.quote(u) for u in units) + '; do systemctl --user is-active -q "$u" || continue; '
              'systemctl --user restart "$u" && echo "$u restarted" || echo "$u FAILED"; done; '
              + ''.join(f'pkill -xf {shlex.quote(p)} && echo {shlex.quote(p + " quit")}; ' for p in programs) + 'true')
    result = run('u=$(getent passwd 1000 | cut -d: -f1); '
                 f'runuser -u "$u" -- /usr/bin/rungic-plasma-user-exec sh -c {shlex.quote(script)}',
                 'container', timeout=240, check=False)
    return (result.stdout + result.stderr).strip()[-800:]


def restart_container():
    outputs = []
    for action in ('stop', 'start'):
        result = subprocess.run([sys.executable, str(WORKSPACE / 'tools/rungic_plasma.py'), action],
                                capture_output=True, text=True, timeout=300)
        outputs.append((result.stdout + result.stderr).strip())
        if result.returncode:
            return False, '\n'.join(outputs)
    return True, '\n'.join(outputs)


def rootfs(action):
    """system/rootfs-image through the Android-side launcher (system/rungic-plasma): status, snapshot, rollback, commit."""
    result = run(f'{rungic_device.PLASMA} rootfs {action}', 'root', timeout=900, check=False)
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def rootfs_state():
    ok, text = rootfs('status')
    fields = dict(part.split('=', 1) for part in text.split() if '=' in part) if ok else {}
    return fields.get('mode'), fields.get('state')


def with_container_stopped(action, before_start=None):
    """Stop the container, run a rootfs action, or several in order (then before_start, e.g.
    restore_android), start it again."""
    outputs = []
    actions = [action] if isinstance(action, str) else list(action)
    for step in ('stop', *actions, 'start'):
        if step == 'start' and before_start:
            outputs.append(f'android: restored {before_start()}')
        if step in ('stop', 'start'):
            result = subprocess.run([sys.executable, str(WORKSPACE / 'tools/rungic_plasma.py'), step],
                                    capture_output=True, text=True, timeout=300)
            ok, text = result.returncode == 0, (result.stdout + result.stderr).strip()
        else:
            ok, text = rootfs(step)
        outputs.append(f'{step}: {text}')
        if not ok:
            return False, '\n'.join(outputs)
    return True, '\n'.join(outputs)


def ext4_errors():
    """Kernel ext4 errors so far (dmesg), to count the ones a snapshot rollback adds."""
    result = run("dmesg 2>/dev/null | grep -c 'EXT4-fs error' || true", 'root', check=False)
    text = (getattr(result, 'stdout', '') or '').strip()
    return int(text) if text.isdigit() else 0


def rollback_to_snapshot(previous=None, record=None):
    """Roll the rootfs back to the deploy's snapshot. The home and the user's settings are outside it:
    for a release from before the Rungic rename they go back first (rebrand_down). Then the result is
    checked: a snapshot rollback once left the ext4 image corrupt (2026-09-27, docs/70), which only
    the next deploy noticed."""
    rebrand = rebrand_down() if previous and meta_of(previous) == FORMER_META else None
    errors_before = ext4_errors()
    ok, text = with_container_stopped('rollback', before_start=(lambda: restore_android(record)) if record else None)
    check = {'ext4_errors': ext4_errors() - errors_before}
    if ok:
        summary = (integrity_summary() or {}).get('summary', {})
        check.update(integrity=summary.get('state'), changed_files=summary.get('changed_files'),
                     missing_files=summary.get('missing_files'))
    return ok, text, {'rebrand_down': rebrand, 'check': check}


def history():
    return json.loads(HISTORY.read_text()) if HISTORY.exists() else []


def device_serial():
    """The phone's hardware serial (ro.serialno), else the configured one."""
    text = (getattr(run('getprop ro.serialno', 'shell', timeout=15, check=False), 'stdout', '') or '').strip()
    return text or rungic_device.serial()


def remember(entry):
    """Append a deployment to release/history.json, which is committed: which phone got which release
    (docs/109)."""
    entries = json.loads(RELEASE_HISTORY.read_text()) if RELEASE_HISTORY.exists() else []
    entries.append(entry)
    RELEASE_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    RELEASE_HISTORY.write_text(json.dumps(entries, indent=1, ensure_ascii=False) + '\n')


def deploy(version=None, restart='auto', acceptance='smoke', record_label=None, snapshot='auto'):
    """deploy_release(), and its result in release/history.json."""
    serial = device_serial()
    log = deploy_release(version, restart, acceptance, record_label, snapshot)
    info = next((r for r in releases() if r['version'] == log.get('version')), {})
    remember({'time': datetime.datetime.now().astimezone().isoformat(timespec='seconds'), 'version': log.get('version'),
              'commit': info.get('commit'), 'channel': info.get('channel', 'release'), 'serial': serial,
              'result': log.get('result'), 'flaky': log.get('flaky', [])})
    return log


def deploy_release(version=None, restart='auto', acceptance='smoke', record_label=None, snapshot='auto'):
    all_releases = releases()
    if not all_releases:
        raise SystemExit('no release built yet: rungic_release.py build')
    info = next((r for r in all_releases if r['version'] == version), None) if version else all_releases[-1]
    if not info:
        raise SystemExit(f'release {version} is not in {RELEASES}')
    version = info['version']
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    record = DEPLOY / f'{stamp}-{record_label or version}'
    record.mkdir(parents=True)
    log = {'version': version, 'started': stamp, 'steps': [], 'attempts': [], 'flaky': []}
    started_at = time.time()

    def step(name, **data):
        log['steps'].append({'step': name, 'time': datetime.datetime.now().isoformat(timespec='seconds'), **data})
        (record / 'deploy.json').write_text(json.dumps(log, indent=1, ensure_ascii=False) + '\n')
        print(f'[{name}] ' + ', '.join(f'{k}={v}' for k, v in data.items() if not isinstance(v, (dict, list))),
              flush=True)

    # 1 preflight
    problems, fatal = preflight()
    step('preflight', problems=problems)
    if fatal:
        log['result'] = 'aborted'
        step('abort', reason='; '.join(fatal))
        return log
    changed = android_source_changes(info)
    if changed:
        log['result'] = 'aborted'
        step('abort', reason=f'Android-side files of release {version} are neither in the working tree nor at its '
             f'commit: {", ".join(changed)}')
        return log
    # A release for the other Android-side layout (docs/70): a Rungic one needs the cutover first; one from
    # before it keeps the current Android side, which still serves those releases until phase D.
    device_layout, release_layout = android_layouts(info)
    if release_layout == 'rungic' and device_layout == 'moto':
        log['result'] = 'aborted'
        step('abort', reason='the Android side is from before the Rungic cutover: tools/rungic_cutover.py up first')
        return log
    keep_android = release_layout == 'moto' and device_layout == 'rungic'
    # 1b snapshot of the whole rootfs (image rootfs, docs/61 §7): a failed release rolls back to it
    mode, state = rootfs_state()
    use_snapshot = snapshot == 'always' or (snapshot == 'auto' and mode == 'image')
    if use_snapshot:
        # A snapshot kept from the last deploy: the system now running is kept (the user has used it
        # since), and the new snapshot replaces the old one as the way back (2026-10-05, the user: "why
        # not just deploy?"). Any other state (a rollback under way) still waits for a decision.
        replace = state == 'snapshot'
        if state not in ('none', 'merging') and not replace:     # a finished rollback merge is completed by the snapshot step
            log['result'] = 'aborted'
            step('abort', reason=f'the rootfs is in state {state}: finish it (rungic_release.py commit, or '
                 'rollback --snapshot) first')
            return log
        ok, text = with_container_stopped(('commit', 'snapshot') if replace else 'snapshot')
        if replace:
            step('kept', note='the system deployed before is kept; the new snapshot replaces its snapshot')
        step('snapshot', ok=ok, output=text[-400:])
        if not ok:
            log['result'] = 'aborted'
            step('abort', reason='could not take the rootfs snapshot')
            return log
        # A freshly started session has its own start-up flakiness; install into a settled one.
        import rungic_acceptance
        settled = rungic_acceptance.session_ready({'out_dir': str(record)})
        step('settled', ok=settled['passed'])
    # 2 record
    previous, _ = device_release()
    before = installed_versions()
    integrity_before = integrity_summary()
    (record / 'before.json').write_text(json.dumps({'release': previous, 'packages': before}, indent=1) + '\n')
    (record / 'integrity-before.json').write_text(json.dumps(integrity_before, indent=1, ensure_ascii=False) + '\n')
    step('record', previous=previous, integrity=(integrity_before or {}).get('summary', {}).get('state'))
    passed, error = False, None
    installed_at = time.time()
    try:
        rebrand = rebrand_down() if meta_of(version) == FORMER_META else None
        if rebrand:
            step('rebrand-down', **rebrand)
        elif previous and meta_of(previous) == FORMER_META and meta_of(version) == META:
            # Across the rename the other way (docs/70): the desktop stops before its moto-* packages go.
            # A running shell drops the favourites whose desktop files the removal deletes, before the
            # next session's rungic-rebrand-user could rename them.
            run('systemctl stop moto-plasma-session.service; systemctl stop user@1000.service', 'container',
                timeout=180, check=False)
            rebrand = {'ok': True, 'output': 'desktop stopped for the rename'}
            step('rebrand-up', **rebrand)
        # 3 sync and install
        ensure_apt_source()
        step('sync', **sync_repo(only=release_debs(info, version)))
        ok, tail = apt_install(info, record)
        step('install', ok=ok)
        # New crashes are counted from here: the restart for the snapshot runs the previous release, and
        # its crashes (collected later) are not this release's (docs/61). A session restart below moves
        # the start again: the old session's shutdown is not the new release running.
        installed_at = time.time()
        if not ok:
            log['result'] = 'install-failed'
            step('abort', reason=tail[-1500:])
            if use_snapshot:
                ok, text, after = rollback_to_snapshot(previous)
                step('snapshot-rollback', ok=ok, output=text[-400:], **after)
                log['result'] = 'install-failed, rolled back to the snapshot' if ok else log['result']
            return log
        # The installed release's exact versions win from now on; a failed install kept the previous pins.
        siblings = pin_release(info)
        step('pins', packages=len(info['packages']) + 1, siblings=sorted(siblings))
        # A development overlay (tools/rungic_dev.py, docs/97) the release replaced: its source and pins
        # would make its builds the candidates again. Kept when the install fails, with its packages.
        import rungic_dev
        overlay = rungic_dev.clear_device(run)
        if overlay:
            step('dev-overlay', cleared=overlay)
        # The Android side names paths inside the container: it follows a successful install,
        # so a failed one leaves both sides at the previous release.
        android = [] if keep_android else sync_android(info, record)
        step('android', changed=android, kept=keep_android)
        # The release's Android settings now, not at the next boot (docs/122, Kevin 2026-10-08). A
        # refusal is recorded, not a failed deploy: drift shows it until it holds.
        if not keep_android:
            settings = converge_android('apply') or []
            step('converge', refused=','.join(i['id'] for i in settings if i['state'] == 'refused') or None,
                 items=settings)
        after = installed_versions()
        (record / 'after.json').write_text(json.dumps({'release': version, 'packages': after}, indent=1) + '\n')
        # Protection is the release's pin and exact dependencies now; drop the holds they replace.
        held = run('apt-mark showhold', 'container').stdout.split()
        released = [n for n in held if n in info['packages']]
        if released:
            run('apt-mark unhold ' + ' '.join(released), 'container')
        step('holds', released=released)
        # System services of changed packages: maintainer scripts only enable them (policy-rc.d keeps
        # them from starting), so new ones would wait for the next container start and running ones
        # would keep the old code. Enabled units are restarted; disabled ones stay as they are.
        units = [u for pkg, names in info.get('service_restart', {}).items() if before.get(pkg) != after.get(pkg)
                 for u in names]
        if units:
            result = run('for u in ' + ' '.join(units) + '; do systemctl is-enabled -q "$u" && '
                         '{ systemctl restart "$u" && echo "$u restarted" || echo "$u FAILED"; }; done; true',
                         'container', timeout=180, check=False)
            step('services', output=result.stdout.strip())
        # 4 migrations run in maintainer scripts (system) and kded's kconf_update (user, next session).
        # 5 restart
        hit, changed = needs_restart(before, after, info.get('session_restart', []))
        step('changes', changed=changed, restart_for=hit)
        # The LXC configuration applies only at a container start; other Android-side scripts (the control
        # script, rootfs-image) take effect on their next use and need no restart.
        whole = any(path.endswith('/lxc/plasma/config') for path in android)
        if restart == 'always' or rebrand or (restart == 'auto' and (hit or whole)):
            # The LXC configuration (mounts, init) applies only when the container starts.
            ok, text = restart_container() if whole else restart_session()
            step('restart', ok=ok, container=whole, output=text[-500:])
            installed_at = time.time()
        elif restart != 'never':
            # A lighter restart than the session's: the desktop user's services of changed packages,
            # so plasmashell and the assistant load the new QML and code at once (docs/61).
            # A release from before this setting (a rollback target) uses the current one.
            spec = info.get('user_restart') or json.loads(SPEC.read_text()).get('user_restart', {})
            user_units = restart_user_services(spec, before, after)
            if user_units is not None:
                step('user-services', output=user_units)
        # 5b the release's APK (docs/109), after the container side it talks to. Its restart drops the
        # Wayland connection; the activity brings the session back (docs/96), which is waited for.
        apk = install_apk(info, restart)
        if apk is not None:
            step('apk', **apk)
            if apk['result'] == 'installed':
                import rungic_acceptance
                step('apk-session', ok=rungic_acceptance.session_ready({'out_dir': str(record)})['passed'])
                installed_at = time.time()
        # 6 verify
        integrity_after = integrity_summary()
        (record / 'integrity-after.json').write_text(json.dumps(integrity_after, indent=1, ensure_ascii=False) + '\n')
        summary = (integrity_after or {}).get('summary', {})
        mismatch = (integrity_after or {}).get('release', {}).get('mismatch', [])
        step('integrity', state=summary.get('state'), release_mismatch=mismatch)
        passed = not mismatch
        if acceptance != 'none':
            import rungic_acceptance
            def attempt(kind, directory, execute):
                # Each report is the source of truth. Keep only relative paths in the deploy record.
                path = str((directory / 'report.json').relative_to(record))
                log['attempts'].append(path)
                step('acceptance-start', kind=kind, path=path)
                try:
                    return execute()
                finally:
                    step('acceptance-record', kind=kind, path=path)

            report = attempt('initial', record / 'acceptance', lambda: rungic_acceptance.run_level(
                acceptance, release=version, out_dir=record / 'acceptance', since=installed_at))
            step('acceptance', level=acceptance, passed=report['passed'], failed=report['failed_ids'])
            accepted = report['passed']  # deployment's existing rollback policy is unchanged
            if not accepted and report['failed_ids']:
                spec = rungic_acceptance.load()
                retry = attempt('retry', record / 'acceptance-retry', lambda: rungic_acceptance.run_scenarios(
                    [s for s in spec['scenarios'] if s['id'] in report['failed_ids']],
                    release=version, out_dir=record / 'acceptance-retry', since=installed_at,
                    retry_of='../acceptance/report.json'))
                # A missing or skipped check is not a successful retry.
                log['flaky'] = [row['id'] for row in retry['scenarios']
                                if row['id'] in report['failed_ids'] and row['passed'] is True]
                step('acceptance-retry', passed=retry['passed'], failed=retry['failed_ids'], flaky=log['flaky'])
                accepted = retry['passed']
            passed = passed and accepted
    except (Exception, SystemExit) as failure:
        # Anything that stops a deploy after the snapshot returns to it, like a failed verification.
        error = f'{type(failure).__name__}: {failure}'
        step('error', reason=error[-1500:])
        passed = False
    # 7 save; a failed verification returns to the snapshot, a good one keeps it until commit
    log['result'] = 'ok' if passed else ('error' if error else 'verify-failed')
    if use_snapshot and not passed:
        # Evidence first: the journal is volatile and the rollback restarts the container.
        try:
            import rungic_agent
            evidence = rungic_agent.snapshot(f'deploy-{version}-failed', 900)
            step('evidence', folder=evidence['folder'])
        except Exception as error:   # evidence must not prevent the rollback
            step('evidence', error=f'{type(error).__name__}: {error}')
        ok, text, after = rollback_to_snapshot(previous, record)
        step('snapshot-rollback', ok=ok, output=text[-400:], **after)
        if ok:
            log['result'] += ', rolled back to the snapshot'
    elif use_snapshot:
        log['snapshot'] = 'kept: rungic_release.py commit once the release is accepted'
    step('done', result=log['result'])
    entries = history()
    entries.append({'time': stamp, 'version': version, 'previous': previous, 'result': log['result'],
                    'record': str(record.relative_to(WORKSPACE)), 'flaky': log['flaky']})
    HISTORY.write_text(json.dumps(entries, indent=1) + '\n')
    return log


def commit():
    """Keep the current system: drop the rootfs snapshot the last deploy took."""
    mode, state = rootfs_state()
    if state != 'snapshot':
        raise SystemExit(f'no kept snapshot (rootfs {mode}, state {state})')
    ok, text = with_container_stopped('commit')
    return {'ok': ok, 'output': text}


def rollback_snapshot():
    """Return the whole rootfs (Ubuntu base included) to the snapshot the last deploy took."""
    mode, state = rootfs_state()
    if state != 'snapshot':
        raise SystemExit(f'no kept snapshot (rootfs {mode}, state {state})')
    # The Android side of the deploy that took the snapshot goes back with it.
    kept = [e for e in history() if 'snapshot' not in e['result']]
    record = WORKSPACE / kept[-1]['record'] if kept else None
    ok, text, after = rollback_to_snapshot(kept[-1].get('previous') if kept else None, record)
    return {'ok': ok, 'output': text, 'release': device_release()[0], **after}


def rollback(restart='auto', acceptance='smoke'):
    current, _ = device_release()
    entries = [e for e in history() if e['result'] in ('ok', 'verify-failed')]
    target = None
    for entry in reversed(entries):
        if entry['version'] == current and entry.get('previous') and entry['previous'] != current:
            target = entry['previous']
            break
    if not target:
        raise SystemExit(f'no earlier release recorded before {current}')
    return deploy(target, restart, acceptance, record_label=f'rollback-to-{target}')


def protection(info):
    """How apt holds the installed release (docs/109): its packages whose pin (1001, or the development
    overlay's 1002) is not their installed release version, whether the metapackage is Protected, and
    whether the unattended-upgrades blacklist is there."""
    if not info or 'packages' not in info:
        return None
    text = run(f'''cat {PINS} /etc/apt/preferences.d/rungic-dev 2>/dev/null
echo "@@protected=$(dpkg-query -W -f '${{Protected}}' {META} 2>/dev/null)"
[ -f {UNATTENDED} ] && echo "@@unattended=yes"; true''', 'container', check=False).stdout or ''
    pins, name = {}, None
    for line in text.splitlines():
        if line.startswith('Package: '):
            name = line.split(': ', 1)[1].strip()
        elif line.startswith('Pin: version ') and name:
            pins.setdefault(name, set()).add(line.split('Pin: version ', 1)[1].strip())
    unpinned = sorted(n for n, v in info['packages'].items() if v not in pins.get(n, ()))
    return {'unpinned': unpinned, 'protected': '@@protected=yes' in text, 'unattended': '@@unattended=yes' in text}


def status():
    version, info = device_release()
    report = integrity_summary()
    built = releases()
    return {
        'installed_release': version,
        'channel': info.get('channel', 'release') if info else None,
        'apk': '/'.join(map(str, installed_apk())),
        'apt': protection(info),
        # A development overlay on the release (tools/rungic_dev.py, docs/97).
        'dev_overlay': ({'base': info['dev']['base'], 'overrides': {n: o['version'] for n, o in
                         info['dev']['overrides'].items()}} if (info or {}).get('dev') else None),
        'commit': (info or {}).get('commit'),
        'built': (info or {}).get('built'),
        'latest_in_repository': built[-1]['version'] if built else None,
        'releases_in_repository': [r['version'] for r in built][-8:],
        'rootfs': dict(zip(('mode', 'state'), rootfs_state())),
        'integrity': (report or {}).get('summary'),
        'release_mismatch': (report or {}).get('release', {}).get('mismatch'),
        'last_deploys': history()[-5:],
    }

# ---------------------------------------------------------------- dev channel (docs/109)

def release_info(version):
    info = next((r for r in releases() if r['version'] == version), None)
    if not info:
        raise SystemExit(f'release {version} is not in {RELEASES}')
    return info


def on_origin_main():
    """A dev release is origin/main as it is: HEAD must be origin/main (fetched now) and the working tree
    clean, untracked files included. -> the commit"""
    git('fetch', '-q', 'origin', 'main')
    head, main = git('rev-parse', 'HEAD'), git('rev-parse', 'origin/main')
    if head != main:
        raise SystemExit(f'HEAD {head[:12]} is not origin/main {main[:12]}: dev releases are cut from origin/main only '
                         '(a clean worktree at origin/main)')
    changes = git('status', '--porcelain', '--untracked-files=normal')
    if changes:
        raise SystemExit(f'the working tree is not clean:\n{changes[:2000]}')
    return head


def build_project():
    """The release's own packages not built for the current sources, built where device packages build
    (tools/rungic_package.py). -> their names"""
    import build_on_device
    import rungic_package
    definitions = rungic_package.definitions()
    stale = [n for n in spec().get('project', {}) if n in definitions and not rungic_package.current(definitions[n])]
    if stale:
        rungic_package.build(stale, jobs=build_on_device.host.jobs)
    return stale


def component_tree(name):
    """Content identity of an upstream component: the git trees of packages/<name> and of the shared
    files its recipe overlays (what tools/rungic_dev.py calls dirty)."""
    import pq
    paths = sorted({f'packages/{name}', *(e['from'] for e in pq.overlay(name).values())})
    return hashlib.sha256('\n'.join(f"{p}={git('rev-parse', f'HEAD:{p}')}" for p in paths).encode()).hexdigest()[:16]


def component_builds():
    return json.loads(COMPONENT_BUILDS.read_text()) if COMPONENT_BUILDS.exists() else {}


def stale_components():
    """Upstream components ("rebuilt" from packages/<name>) whose release packages at their changelog's
    version are not in the pool. A patch queue changed without a new changelog entry stops here: the
    pool has that version already, with the old contents (docs/71)."""
    builds, have, stale = component_builds(), pool_debs(), []
    for name, c in spec().get('rebuilt', {}).items():
        if not c.get('source', '').startswith('packages/'):
            continue
        if any(c['version'].split(':', 1)[-1] not in have.get(b, {}) for b in c['packages']):
            stale.append(name)
        elif builds.get(name, {}).get('version') == c['version'] and builds[name]['tree'] != component_tree(name):
            raise SystemExit(f'packages/{name} changed since its build {c["version"]}: add a changelog entry, so '
                             'the new build has a version of its own')
    return stale


def build_component(name):
    """An upstream component's release packages, built where tools/build_on_device.py builds and
    collected into the pool: incrementally on the kept obj tree after a good build, else a full build
    (as tools/rungic_dev.py does); Mesa with its Meson build and packager (build_mesa.py)."""
    import build_on_device
    host, work, started = build_on_device.host, f'{build_on_device.BASE}/{name}', time.time()
    version = spec()['rebuilt'][name]['version']
    build_on_device.sync(name)
    if name == 'mesa':
        mode = 'targets'
    else:
        previous = host.out(f'test -d {work}/src/obj-aarch64-linux-gnu && cat {work}/build.rc 2>/dev/null || true').strip()
        mode = 'incremental' if previous == '0' else 'full'
        if mode == 'full':
            print(build_on_device.build_deps(name), flush=True)
    build_on_device.start(name, mode, host.jobs)
    while 'SubState=running' in (state := build_on_device.status(name)) or 'ActiveState=activating' in state:
        time.sleep(20)
    if 'Result=success' not in state:
        raise SystemExit(f'{name}: {mode} build failed on {host.name}\n{state}')
    if name == 'mesa':
        import build_mesa
        build_mesa.package(host, version, git('rev-parse', 'HEAD'))
    print(build_on_device.collect(name), flush=True)
    builds = component_builds()
    builds[name] = {'version': version, 'tree': component_tree(name), 'commit': git('rev-parse', 'HEAD'),
                    'built': datetime.datetime.now().isoformat(timespec='seconds')}
    COMPONENT_BUILDS.write_text(json.dumps(builds, indent=1, sort_keys=True) + '\n')
    return {'component': name, 'version': version, 'mode': mode, 'seconds': round(time.time() - started)}


def build_native_libs(out):
    """The APK's native libraries with the Android host built from this commit
    (android/build-native-core.sh) -> their directory, for build-apk.sh (RUNGIC_NATIVE_LIBS).
    Libraries the host build does not make (libc++_shared, LiteRT, OCR, libxkbcommon) come
    from RUNGIC_NATIVE_LIBS or the reference directory. Releases 20261005.2-.4 took that
    directory whole: their host library was a build of 2026-09-29, without the host changes
    merged since (docs/109)."""
    base = Path(os.environ.get('RUNGIC_NATIVE_LIBS') or WORKSPACE / '.work/refs/plasma-mobile-20260923/native-libs')
    if not (base / 'lib/arm64-v8a').is_dir():
        raise SystemExit(f'{base}/lib/arm64-v8a: no native libraries to build the APK with (RUNGIC_NATIVE_LIBS)')
    stage = out / 'native-libs'
    shutil.copytree(base, stage, symlinks=True)
    (stage / 'lib/arm64-v8a/libuniffi_winland_core.so').unlink(missing_ok=True)
    built = subprocess.run(['bash', str(WORKSPACE / 'android/build-native-core.sh')], cwd=WORKSPACE,
                           env=dict(os.environ, RUNGIC_NATIVE_OUT=str(stage / 'lib/arm64-v8a'),
                                    RUNGIC_JNI_LIBS_DIR=str(base / 'lib/arm64-v8a')))
    if built.returncode or not (stage / 'lib/arm64-v8a/libuniffi_winland_core.so').is_file():
        raise SystemExit('android/build-native-core.sh failed: the release does not ship an old host library; '
                         'fix the build (RUNGIC_PROXY= without the project proxy), or pass --apk FILE, or --no-apk')
    return stage


def build_apk_file(out):
    """android/build-apk.sh into `out` -> the signed APK (Rungic-<versionName>.apk), with the host
    library built from this commit (build_native_libs)."""
    native = build_native_libs(out)
    built = subprocess.run(['bash', str(WORKSPACE / 'android/build-apk.sh')], cwd=WORKSPACE,
                           env=dict(os.environ, RUNGIC_APK_OUT=str(out), RUNGIC_NATIVE_LIBS=str(native)))
    apks = sorted(out.glob('Rungic-*.apk'))
    if built.returncode or not apks:
        raise SystemExit('android/build-apk.sh failed (its native libraries come from android/build-native-core.sh); '
                         'or pass --apk FILE, or --no-apk')
    return apks[-1]


def release_apk(given=None):
    """The APK of a release (docs/109): built from this commit, or a given file; kept in APKS under a name
    with its version and hash. -> {'file', 'package', 'version_name', 'version_code', 'sha256', 'size'}"""
    from apk_manifest_info import manifest_info
    if given:
        path = Path(given)
    else:
        out = WORKSPACE / '.work/cache/release-apk'
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        path = build_apk_file(out)
    manifest = manifest_info(path)['manifest']
    if manifest.get('package') != rungic_device.APK:
        raise SystemExit(f'{path} is {manifest.get("package")}, not {rungic_device.APK}')
    digest = sha256_file(path)
    name = f"Rungic-{manifest['versionName']}-{manifest['versionCode']}-{digest[:12]}.apk"
    APKS.mkdir(parents=True, exist_ok=True)
    if not (APKS / name).exists():
        shutil.copy2(path, APKS / name)
    return {'file': name, 'package': manifest['package'], 'version_name': str(manifest['versionName']),
            'version_code': int(manifest['versionCode']), 'sha256': digest, 'size': path.stat().st_size}


def taken_elsewhere():
    """Release numbers used beyond this pool: the dev tags on origin (published from any machine) and
    the committed deployment history."""
    tags = git('ls-remote', '--tags', 'origin', f'refs/tags/{DEV_TAG}*', check=False)
    versions = [line.rsplit(f'refs/tags/{DEV_TAG}', 1)[1].removesuffix('^{}') for line in tags.splitlines()
                if f'refs/tags/{DEV_TAG}' in line]
    if RELEASE_HISTORY.exists():
        versions += [e['version'] for e in json.loads(RELEASE_HISTORY.read_text()) if e.get('version')]
    return versions


def dev(host='macmini', out=None, apk=None, no_apk=False, note='', publish_release=False, confirm=False,
        coupled_override=None):
    """A dev release (docs/109): only from a clean origin/main, so every machine that cuts one cuts the
    same thing and nothing merged lives only in overlays on phones. Builds what the pool lacks for this
    commit (project packages, upstream components), the APK, the release (channel dev) and its bundle;
    with publish_release the GitHub pre-release (published only with confirm)."""
    commit = on_origin_main()
    import build_on_device
    build_on_device.use(host)
    print(f'dev release of origin/main {commit[:12]}; device packages build on {host}', flush=True)
    project = build_project()
    components = [build_component(name) for name in stale_components()]
    apk_info = None if no_apk else release_apk(apk)
    last = next((r for r in reversed(releases()) if r.get('apk')), None)
    if apk_info and last and last['apk']['version_code'] == apk_info['version_code'] \
            and last['apk']['sha256'] != apk_info['sha256']:
        print(f"note: the APK differs from release {last['version']}'s at the same versionCode "
              f"{apk_info['version_code']}: phones that have that versionCode keep their APK", flush=True)
    result = build(next_version(taken_elsewhere()), note=note, coupled_override=coupled_override, channel='dev',
                   apk=apk_info)
    result.update(channel='dev', project_built=project, components_built=components,
                  apk=apk_info and f"{apk_info['version_name']}/{apk_info['version_code']}")
    result['bundle'] = str(export_bundle(result['version'], out))
    if publish_release:
        result['publish'] = publish(result['version'], result['bundle'], confirm)
    return result


def bundle_path(version, out=None):
    return Path(out or os.environ.get('RUNGIC_RELEASE_OUT') or BUNDLES) / f'rungic-{version}.tar'


def link(source, target):
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def export_bundle(version, out=None):
    """Release `version` as one file another machine deploys (deploy --from): repo/ with the metapackage,
    the pool's .debs it pins and their index; apk/; android/<sha256>, its Android-side files; and
    manifest.json, the release record with every file's SHA-256. Packages coupled to Ubuntu's come from
    the archive: listed in from_archive, not included."""
    info = release_info(version)
    have, target = pool_debs(), bundle_path(version, out)
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(dir=WORKSPACE / '.work/cache', prefix=f'bundle-{version}-'))
    try:
        (stage / 'repo').mkdir()
        archive = []
        for name, wanted in sorted({**info['packages'], meta_of(version): version}.items()):
            deb = have.get(name, {}).get(wanted.split(':', 1)[-1])
            if deb is None and name in info.get('coupled', []):
                archive.append(f'{name}={wanted}')
            elif deb is None:
                raise SystemExit(f'{name}={wanted} of release {version} is not in the pool {POOL}')
            else:
                link(deb, stage / 'repo' / deb.name)
        index(stage / 'repo')
        if info.get('apk'):
            source = APKS / info['apk']['file']
            if not source.exists() or sha256_file(source) != info['apk']['sha256']:
                raise SystemExit(f"the APK {info['apk']['file']} of release {version} is not in {APKS} (or differs)")
            (stage / 'apk').mkdir()
            link(source, stage / 'apk' / source.name)
        for item in (info.get('android') or {}).values():
            data = android_content(info, item)
            if data is None:
                raise SystemExit(f"{item['source']} of release {version} is neither in the working tree nor at its commit")
            (stage / 'android').mkdir(exist_ok=True)
            (stage / 'android' / item['sha256']).write_bytes(data)
        files = {str(p.relative_to(stage)): sha256_file(p) for p in sorted(stage.rglob('*')) if p.is_file()}
        (stage / 'manifest.json').write_text(json.dumps({'schema': 1, 'release': info, 'from_archive': archive,
                                                         'files': files}, indent=1, ensure_ascii=False) + '\n')
        partial = target.with_name(target.name + '.part')
        with tarfile.open(partial, 'w') as tar:
            for name in ['manifest.json', *files]:
                tar.add(stage / name, arcname=name)
        partial.replace(target)
    finally:
        shutil.rmtree(stage)
    return target


def import_bundle(path):
    """A bundle from export_bundle() into this machine's pool, APKs and Android-side files, each file
    checked against the manifest. A pool file of the same name with other contents stops it, and so does
    a release of the same version built from something else: two machines' builds are never mixed
    (docs/109). -> the release's version"""
    stage = Path(tempfile.mkdtemp(dir=WORKSPACE / '.work/cache', prefix='bundle-import-'))
    try:
        with tarfile.open(path) as tar:
            tar.extractall(stage, filter='data')
        manifest = json.loads((stage / 'manifest.json').read_text())
        bad = [n for n, digest in manifest['files'].items()
               if not (stage / n).is_file() or sha256_file(stage / n) != digest]
        if bad:
            raise SystemExit(f'{path}: files missing or changed: {bad[:5]}')
        info, version = manifest['release'], manifest['release']['version']
        known = next((r for r in releases() if r['version'] == version), None)
        if known and (known.get('commit'), known.get('packages')) != (info.get('commit'), info.get('packages')):
            raise SystemExit(f"release {version} here was built from {known.get('commit', '')[:12]}, the bundle's "
                             f"from {info.get('commit', '')[:12]}: not the same release")
        import_debs(sorted((stage / 'repo').glob('*.deb')))
        for name in manifest['files']:
            folder = APKS if name.startswith('apk/') else ANDROID_STORE if name.startswith('android/') else None
            if folder:
                folder.mkdir(parents=True, exist_ok=True)
                if not (folder / Path(name).name).exists():
                    shutil.copy2(stage / name, folder / Path(name).name)
        index()
        if not known:
            RELEASES.mkdir(parents=True, exist_ok=True)
            (RELEASES / f'{version}.json').write_text(json.dumps(info, indent=1, ensure_ascii=False) + '\n')
        return version
    finally:
        shutil.rmtree(stage)


def phones():
    """The connected adb devices that are Rungic phones (rooted, with the Android-side launcher):
    ([{'transport', 'serial', 'model'}], the others with why). A phone on two transports (USB and
    Wi-Fi) counts once."""
    found, others = [], []
    for transport, state, model in rungic_device.devices():
        if state != 'device':
            others.append({'transport': transport, 'why': state})
            continue
        try:
            with rungic_device.selected(transport=transport):
                serial = (run('getprop ro.serialno', 'shell', timeout=15, check=False).stdout or '').strip()
                launcher = run(f'{rungic_device.LAUNCHER_SH}; [ -x "$p" ] && echo yes || echo no', 'root',
                               timeout=30, check=False).stdout or ''
        except DeviceError as error:
            others.append({'transport': transport, 'why': str(error)[:200]})
            continue
        if not launcher.strip().endswith('yes'):
            others.append({'transport': transport, 'serial': serial, 'why': 'no Rungic launcher (or no root)'})
        elif any(p['serial'] == serial for p in found):
            others.append({'transport': transport, 'serial': serial, 'why': 'the same phone on another transport'})
        else:
            found.append({'transport': transport, 'serial': serial or transport, 'model': model})
    return found, others


def table(rows, columns):
    """rows as an aligned text table (the summaries of --all)."""
    cells = [['-' if r.get(c) is None else str(r.get(c)) for c in columns] for r in rows]
    widths = [max([len(c), *(len(row[i]) for row in cells)]) for i, c in enumerate(columns)]

    def line(values):
        return '  '.join(v.ljust(w) for v, w in zip(values, widths)).rstrip()
    return '\n'.join([line(columns), line(['-' * w for w in widths]), *map(line, cells)])


def deploy_all(version=None, **options):
    """deploy on every connected Rungic phone in turn (docs/109). A phone that fails or cannot be reached
    is recorded and the next one goes on; a table sums up."""
    found, others = phones()
    if not found:
        raise SystemExit(f'no Rungic phone among the adb devices: {others}')
    if not version:
        built = releases()
        version = built[-1]['version'] if built else None
    rows = []
    for phone in found:
        print(f"== {phone['serial']} ({phone['model']}, {phone['transport']})", flush=True)
        row = {**phone, 'before': None, 'after': None, 'apk': None}
        with rungic_device.selected(phone['serial'], phone['transport']):
            try:
                row['before'] = device_release()[0]
                log = deploy(version, record_label=f"{version}-{phone['serial']}", **options)
                row['result'] = log['result']
                row['apk'] = next((s['result'] for s in log['steps'] if s['step'] == 'apk'), None)
                row['after'] = device_release()[0]
            except (Exception, SystemExit) as failure:
                row['result'] = f'error: {type(failure).__name__}: {failure}'[:300]
                info = next((r for r in releases() if r['version'] == version), {})
                remember({'time': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
                          'version': version, 'commit': info.get('commit'), 'channel': info.get('channel', 'release'),
                          'serial': phone['serial'], 'result': row['result']})
        rows.append(row)
    print(table(rows, ('serial', 'model', 'before', 'after', 'apk', 'result')), flush=True)
    return {'result': 'ok' if all(r['result'] == 'ok' for r in rows) else 'failed', 'version': version,
            'phones': rows, 'skipped': others}


def phone_status():
    """One phone's row of status --all."""
    version, info = device_release()
    info = info or {}
    commit = info.get('commit')
    behind = git('rev-list', '--count', f'{commit}..origin/main', check=False) if commit else ''
    name, code = installed_apk()
    held = protection(info) if info else None
    apt = None
    if held:
        problems = ([f"{len(held['unpinned'])} unpinned"] if held['unpinned'] else []) + \
                   ([] if held['protected'] else ['metapackage not Protected']) + \
                   ([] if held['unattended'] else ['no unattended-upgrades list'])
        apt = ', '.join(problems) or 'ok'
    return {'release': version, 'channel': info.get('channel', 'release') if info else None,
            'commit': commit[:12] if commit else None, 'behind_main': int(behind) if behind.isdigit() else None,
            'apk': f'{name}/{code}' if code else None, 'overlays': len(info.get('dev', {}).get('overrides', {})),
            'apt': apt}


def main_file(path, against='origin/main'):
    """A file as the explicit comparison ref has it (bytes), None where it has none."""
    result = subprocess.run(['git', 'show', f'{against}:{path}'], cwd=WORKSPACE, capture_output=True)
    return result.stdout if result.returncode == 0 else None


def differing(commit, paths, against='origin/main'):
    """Compare the files under `paths` between `commit` and `against`. Return None if this repository does not contain `commit`."""
    if subprocess.run(['git', 'cat-file', '-e', f'{commit}^{{commit}}'], cwd=WORKSPACE, capture_output=True).returncode:
        return None
    return [f for f in git('diff', '--name-only', commit, against, '--', *paths).splitlines() if f]


# Android-side programs built from source (tools/build_enter.sh) that the host seed installs but a
# release does not: only whether their source changed since the phone's release can be said.
BUILT_HOST_PROGRAMS = {'/data/adb/rungic-plasma/rungic-plasma-enter': 'tools/rungic_plasma_enter.c',
                       '/data/adb/rungic-lxc/rungic-lxc-enter': 'tools/rungic_lxc_enter.c'}


def android_files(against='origin/main'):
    """The comparison ref's Android-side files: {path on the phone: source}, the release's (packages.json
    "android") and the host seed's (tools/ci/build_host_seed.py), which a release does not update."""
    files = {e['path']: e['source'] for e in json.loads(main_file('release/packages.json', against) or b'{}').get('android', [])}
    seed = (main_file('tools/ci/build_host_seed.py', against) or b'').decode()
    for source, dest in re.findall(r'\(\s*"([^"]+)",\s*"([^"]+)",\s*0o\d+\s*\)', seed):
        if not source.startswith(('lxc_', 'plasma_')):
            files.setdefault('/data/adb/' + dest, source)
    cast = (main_file('tools/cast_payload.py', against) or b'').decode()
    for source, dest in re.findall(r'\(\s*\'([^\']+)\',\s*\'([^\']+)\',\s*0o\d+\s*\)', cast):
        files.setdefault('/data/adb/rungic-wfd/' + dest, source)
    # Built artifacts (the cast JAR, the entry programs) are named, not paths: not compared here.
    return {path: source for path, source in files.items() if '/' in source}


def drift_parts(info, apk_code, android, against='origin/main', settings=None):
    """The parts of a phone that differ from the explicit comparison ref. `info` is its release.json, `apk_code` its
    APK's versionCode, `android` {path: sha256 or None} of the Android-side files on it.
    Compare each project package and upstream component at its development overlay commit (docs/97), or else its release commit.
    Use the package input paths (rungic_package.identity_paths and component paths). Content, not ancestry: squash merges leave a
    merged branch's commits outside main."""
    import pq
    import rungic_package

    def overlaid(name):
        """The shared files a component's recipe places in its source (none without a recipe)."""
        try:
            return {e['from'] for e in pq.overlay(name).values()}
        except SystemExit:
            return set()
    base = info.get('commit')
    overrides = info.get('dev', {}).get('overrides', {})
    main_spec = json.loads(main_file('release/packages.json', against) or b'{}')
    definitions = rungic_package.definitions()
    parts = []
    for name in sorted(main_spec.get('project', {})):
        pkg = definitions.get(name)
        paths = sorted({*pkg['paths'], str(pkg['dir'].relative_to(WORKSPACE)),
                        *(p for u in pkg.get('upstream', []) for p in {f'packages/{u}', *overlaid(u)})}) \
            if pkg else [f'packaging/{name}']
        parts.append((name, paths))
    for name, component in sorted(main_spec.get('rebuilt', {}).items()):
        if component.get('source', '').startswith('packages/'):
            parts.append((name, sorted({f'packages/{name}', *overlaid(name)})))
    found = []
    for name, paths in parts:
        override = overrides.get(name)
        commit = (override or {}).get('commit') or base
        source = 'overlay' if override else 'release'
        if not commit:
            found.append({'part': name, 'from': source, 'state': 'unknown commit'})
            continue
        files = differing(commit, paths, against)
        if files is None:
            found.append({'part': name, 'from': source, 'commit': commit[:12], 'state': 'commit not in this repository'})
        elif files or (override or {}).get('dirty'):
            found.append({'part': name, 'from': source, 'commit': commit[:12],
                          'state': f'{len(files)} files differ' + (', built from uncommitted changes' if override.get('dirty') else '')
                          if override else f'{len(files)} files differ', 'files': files[:6]})
    manifest = (main_file('android/app/AndroidManifest.xml', against) or b'').decode()
    main_code = re.search(r'versionCode="(\d+)"', manifest)
    main_code = int(main_code.group(1)) if main_code else None
    if apk_code != main_code:
        found.append({'part': 'apk', 'state': f'versionCode {apk_code} on the phone, {main_code} on main'})
    release_paths = {e['path'] for e in main_spec.get('android', [])}
    for path, source in android_files(against).items():
        content = main_file(source, against)
        want = hashlib.sha256(content).hexdigest() if content is not None else None
        have = android.get(path)
        if have != want and not (have is None and path not in release_paths):
            state = 'missing on the phone' if have is None else 'not on main' if want is None else 'differs from main'
            found.append({'part': path, 'state': state + ('' if path in release_paths else ' (host seed only: a release does not update it)')})
    # Android settings (docs/122): rungic-converge check on the phone; what is left to the user
    # (report) is listed by phone_drift, not counted here.
    for item in settings or []:
        if item['state'] not in ('ok', 'report'):
            found.append({'part': f"android setting {item['id']}",
                          'state': f"{item['state']} (rungic-converge check)" + (f": {item['detail']}" if item['detail'] else '')})
    for path, source in BUILT_HOST_PROGRAMS.items():
        if base and differing(base, [source], against):
            found.append({'part': path, 'state': f'cannot compare a built program; {source} changed since the '
                          "phone's release, and a release does not update it"})
    return found


def phone_drift(against='origin/main'):
    """drift of the selected phone (see drift_parts)."""
    version, info = device_release()
    if not info:
        raise SystemExit('the phone has no Rungic release (release.json)')
    _name, code = installed_apk()
    if main_file('release/packages.json', against) is None:
        raise SystemExit(f'cannot read release/packages.json from comparison {against}')
    paths = list(android_files(against))
    text = run('for f in ' + ' '.join(shlex.quote(p) for p in paths) + '; do [ -f "$f" ] && sha256sum "$f"; done; true',
               'root', timeout=60, check=True).stdout or ''
    android = {line.split()[1]: line.split()[0] for line in text.splitlines() if len(line.split()) == 2}
    settings = converge_android('check')
    parts = drift_parts(info, int(code) if code else None, android, against, settings)
    commit = info.get('commit')
    behind = git('rev-list', '--count', f'{commit}..{against}', check=False) if commit else ''
    return {'release': version, 'commit': commit[:12] if commit else None,
            'against': against, 'installed_commit': commit, 'apk': {'name': _name, 'version_code': code},
            'development': info.get('dev', {}),
            'release_behind_main': int(behind) if behind.isdigit() else None,
            'overlays': len(info.get('dev', {}).get('overrides', {})),
            'in_sync': not parts, 'differs': parts,
            'reported': [f"{i['id']}: {i['detail']}" for i in settings or [] if i['state'] == 'report']}


def drift(every=False, against=None):
    """How far the selected phone, or every connected one, is from origin/main (fetched now; if the
    fetch fails, the local origin/main, said so) or from the commit `against`."""
    note = None
    comparison = against or 'origin/main'
    if not against:
        try:
            fetched = subprocess.run(['git', 'fetch', '-q', 'origin', 'main'], cwd=WORKSPACE, capture_output=True,
                                     text=True, timeout=60).returncode == 0
        except subprocess.TimeoutExpired:
            fetched = False
        if not fetched:
            note = 'origin/main could not be fetched: compared with the local copy'
    ref = git('rev-parse', '--verify', f'{comparison}^{{commit}}', check=False)
    if not ref:
        raise SystemExit(f'drift: no commit {comparison} in this repository')
    when = git('log', '-1', '--format=%cI', ref, check=False)
    print(f"comparing with {comparison} = {ref[:12]} ({when})" + (f"; {note}" if note else ''), flush=True)
    found, others = phones() if every else ([None], [])
    rows = []
    for phone in found:
        def one():
            try:
                return phone_drift(ref)
            except (Exception, SystemExit) as failure:
                return {'error': f'{type(failure).__name__}: {failure}'[:200]}
        if phone:
            with rungic_device.selected(phone['serial'], phone['transport']):
                row = {**phone, **one()}
        else:
            row = one()
        rows.append(row)
        label = row.get('serial') or 'phone'
        if 'error' in row:
            print(f"{label}: {row['error']}", flush=True)
        elif row['in_sync']:
            print(f"{label}: in sync (release {row['release']})", flush=True)
        else:
            print(f"{label}: {len(row['differs'])} parts differ (release {row['release']}, "
                  f"{row['overlays']} overlays)", flush=True)
            for part in row['differs']:
                where = f" [{part['from']} {part.get('commit', '')}]".rstrip() + ']' if part.get('from') else ''
                where = where.replace(']]', ']')
                print(f"  {part['part']}{where}: {part['state']}", flush=True)
        for reported in row.get('reported') or []:
            # Left to the user (runtime permissions, KernelSU's grant): not drift (docs/122).
            print(f"  reported, not changed: {reported}", flush=True)
    return {'result': 'ok' if all(r.get('in_sync') for r in rows) else 'drift', 'against': ref, 'against_time': when,
            'note': note, 'phones': rows, 'skipped': others}


def status_all():
    """status of every connected Rungic phone, one row each (docs/109)."""
    git('fetch', '-q', 'origin', 'main', check=False)
    found, others = phones()
    rows = []
    for phone in found:
        with rungic_device.selected(phone['serial'], phone['transport']):
            try:
                rows.append({**phone, **phone_status()})
            except (Exception, SystemExit) as failure:
                rows.append({**phone, 'error': f'{type(failure).__name__}: {failure}'[:200]})
    print(table(rows, ('serial', 'model', 'release', 'channel', 'commit', 'behind_main', 'apk', 'overlays', 'apt',
                       'error')), flush=True)
    return {'phones': rows, 'skipped': others}


def previous_dev(info):
    """The published dev release before `info`: the newest dev-* tag on origin older than it (a
    pre-release on GitHub). Dev releases that were only deployed, never published, do not count: the
    notes of a published release cover everything since the last one people could download.
    -> {'version', 'commit', 'packages' (None when this pool has no record of it)} or None"""
    key = lambda v: [int(x) for x in v.split('.')]
    tags = {}
    for line in git('ls-remote', '--tags', 'origin', f'refs/tags/{DEV_TAG}*', check=False).splitlines():
        sha, _, ref = line.partition('\t')
        name = ref.removeprefix(f'refs/tags/{DEV_TAG}')
        peeled = name.endswith('^{}')
        name = name.removesuffix('^{}')
        if re.fullmatch(r'\d{8}\.\d+', name) and (peeled or name not in tags):
            tags[name] = sha
    older = sorted((v for v in tags if key(v) < key(info['version'])), key=key)
    if not older:
        return None
    version = older[-1]
    record = next((r for r in releases() if r['version'] == version), None)
    return {'version': version, 'commit': record['commit'] if record else tags[version],
            'packages': record.get('packages') if record else None}


def release_notes(version):
    """Notes of a dev release: the pull requests merged since the previous published dev release (squash merges
    on main end with "(#N)"), other commits, the package versions that changed, the APK, how to deploy."""
    info = release_info(version)
    previous = previous_dev(info)
    since = previous['version'] if previous else None
    span = [f"{previous['commit']}..{info['commit']}"] if previous and previous.get('commit') else ['-n', '30', info['commit']]
    prs, other = [], []
    for line in git('log', '--first-parent', '--format=%h%x09%s', *span, check=False).splitlines():
        sha, _, subject = line.partition('\t')
        (prs if re.search(r'\(#\d+\)$', subject) else other).append(f'- {subject} ({sha})')
    lines = [f"Rungic dev release {version}, origin/main at {info['commit'][:12]}.", '',
             f"## Merged pull requests {'since ' + since if since else '(no earlier dev release: the last 30 commits)'}",
             '', *(prs or ['- none']), '']
    if other:
        lines += ['## Other commits', '', *other, '']
    lines += ['## Package versions', '']
    if previous and previous.get('packages'):
        before, after = previous['packages'], info['packages']
        lines += [f"- {n}: {before.get(n, '(new)')} -> {after.get(n, '(removed)')}"
                  for n in sorted(set(before) | set(after)) if before.get(n) != after.get(n)] or ['- unchanged']
    else:
        lines += [f"- {len(info['packages'])} packages; no earlier dev release here to compare with"]
    apk = info.get('apk')
    lines += ['', '## APK', '', f"- {apk['file']}: {apk['version_name']} (versionCode {apk['version_code']}), "
              f"sha256 {apk['sha256']}" if apk else '- not part of this release', '',
              '## Deploy', '', '```sh', f'python3 tools/rungic_release.py deploy --all --from rungic-{version}.tar', '```', '']
    return '\n'.join(lines)


def gh(argv):
    """The GitHub CLI (publish)."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=3600)


def publish(version, bundle=None, confirm=False):
    """The GitHub pre-release dev-<version> on kevinzhow/Rungic: notes from git (release_notes()), the
    bundle and the APK as assets, the tag at the release's commit. Others see it and it creates a tag:
    without confirm only the command and the notes come back, for the owner to confirm (docs/109)."""
    info = release_info(version)
    if info.get('channel') != 'dev':
        raise SystemExit(f'release {version} is not a dev release; only dev releases become pre-releases')
    bundle = Path(bundle) if bundle else bundle_path(version)
    if not bundle.exists():
        raise SystemExit(f'{bundle} does not exist: rungic_release.py export {version}')
    notes = bundle.with_name(f'rungic-{version}.notes.md')
    notes.write_text(release_notes(version))
    argv = ['gh', 'release', 'create', f'{DEV_TAG}{version}', '--repo', GITHUB, '--prerelease',
            '--target', info['commit'], '--title', f'Rungic dev {version}', '--notes-file', str(notes), str(bundle),
            *([str(APKS / info['apk']['file'])] if info.get('apk') else [])]
    if not confirm:
        return {'published': False, 'command': shlex.join(argv), 'notes': str(notes),
                'next': f'after the owner confirms: rungic_release.py publish {version} --yes'}
    result = gh(argv)
    if result.returncode:
        raise SystemExit(f'gh release create: {(result.stdout + result.stderr).strip()[-1500:]}')
    return {'published': True, 'tag': f'{DEV_TAG}{version}', 'url': result.stdout.strip()}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('import-installed')
    p = sub.add_parser('import'); p.add_argument('debs', nargs='+')
    p = sub.add_parser('build'); p.add_argument('--version'); p.add_argument('--allow-dirty', action='store_true')
    p.add_argument('--note', default='')
    p.add_argument('--coupled-json', type=Path, help='exact coupled package versions from a new rootfs; avoids a phone query')
    sub.add_parser('list')
    for name in ('deploy', 'rollback'):
        p = sub.add_parser(name)
        if name == 'deploy':
            p.add_argument('version', nargs='?')
            p.add_argument('--snapshot', choices=['auto', 'always', 'never'], default='auto',
                           help='rootfs snapshot before installing (auto: when the rootfs is an image)')
            p.add_argument('--all', action='store_true', help='every connected Rungic phone, one after another')
            p.add_argument('--from', dest='bundle', type=Path, help='a release bundle (rungic_release.py dev/export)')
        else:
            p.add_argument('--snapshot', action='store_true',
                           help='return the whole rootfs to the snapshot the last deploy took')
        p.add_argument('--restart', choices=['auto', 'always', 'never'], default='auto')
        p.add_argument('--acceptance', choices=['smoke', 'full', 'none'], default='smoke')
    sub.add_parser('commit')
    p = sub.add_parser('status'); p.add_argument('--all', action='store_true', help='every connected Rungic phone')
    p = sub.add_parser('drift'); p.add_argument('--all', action='store_true', help='every connected Rungic phone')
    p.add_argument('--against', help='compare with this commit instead of origin/main fetched now (no fetch)')
    p = sub.add_parser('dev')
    p.add_argument('--host', choices=['macmini', 'phone'], default=os.environ.get('RUNGIC_BUILD_HOST', 'macmini'),
                   help='where device packages build (default $RUNGIC_BUILD_HOST, else macmini)')
    p.add_argument('--out', type=Path, help='where the bundle goes (default $RUNGIC_RELEASE_OUT, else .work/release-bundles)')
    p.add_argument('--apk', type=Path, help='this APK instead of building one')
    p.add_argument('--no-apk', action='store_true', help='a release without an APK')
    p.add_argument('--note', default='')
    p.add_argument('--coupled-json', type=Path, help='exact coupled package versions; avoids a phone query')
    p.add_argument('--publish', action='store_true', help='the GitHub pre-release: the command and notes, run with --yes')
    p.add_argument('--yes', action='store_true', help='with --publish: the owner confirmed, publish')
    p = sub.add_parser('export'); p.add_argument('version'); p.add_argument('--out', type=Path)
    p = sub.add_parser('publish'); p.add_argument('version'); p.add_argument('--bundle', type=Path)
    p.add_argument('--yes', action='store_true', help='the owner confirmed: create the pre-release')
    a = parser.parse_args()
    if a.cmd == 'import-installed':
        result = import_installed()
    elif a.cmd == 'import':
        result = import_debs(a.debs)
        index()
    elif a.cmd == 'build':
        result = build(a.version, a.allow_dirty, a.note,
                       json.loads(a.coupled_json.read_text()) if a.coupled_json else None)
    elif a.cmd == 'list':
        result = [{k: r.get(k) for k in ('version', 'channel', 'commit', 'built', 'note')} for r in releases()]
    elif a.cmd == 'deploy':
        version = import_bundle(a.bundle) if a.bundle else a.version
        if a.bundle and a.version and a.version != version:
            parser.error(f'the bundle is release {version}, not {a.version}')
        options = dict(restart=a.restart, acceptance=a.acceptance, snapshot=a.snapshot)
        result = deploy_all(version, **options) if a.all else deploy(version, **options)
    elif a.cmd == 'rollback':
        result = rollback_snapshot() if a.snapshot else rollback(a.restart, a.acceptance)
    elif a.cmd == 'commit':
        result = commit()
    elif a.cmd == 'dev':
        if a.apk and a.no_apk:
            parser.error('--apk or --no-apk')
        result = dev(a.host, a.out, a.apk, a.no_apk, a.note, a.publish, a.yes,
                     json.loads(a.coupled_json.read_text()) if a.coupled_json else None)
    elif a.cmd == 'export':
        result = {'bundle': str(export_bundle(a.version, a.out))}
    elif a.cmd == 'publish':
        result = publish(a.version, a.bundle, a.yes)
    elif a.cmd == 'drift':
        result = drift(a.all, a.against)
    else:
        result = status_all() if a.all else status()
    print(json.dumps(result, indent=1, ensure_ascii=False))
    if isinstance(result, dict) and result.get('result') not in (None, 'ok'):
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except DeviceError as error:
        sys.exit(f'rungic_release: {error}')
