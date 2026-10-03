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

With an image rootfs (docs/61 §7) deploy first takes a snapshot of the whole rootfs; a failed
install or verification returns to it automatically, a good release keeps it until commit.

Every deploy leaves a record under .work/deploy/<time>-<version>/.
"""
import argparse
import datetime
import fnmatch
import gzip
import hashlib
import io
import json
import lzma
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

def next_version():
    today = datetime.date.today().strftime('%Y%m%d')
    taken = [int(p.stem.split('.')[1]) for p in RELEASES.glob(f'{today}.*.json')] if RELEASES.exists() else []
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
Description: Rungic: release {version}
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


def build(version=None, allow_dirty=False, note='', coupled_override=None):
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
    info = {'version': version, 'commit': commit, 'dirty': dirty, 'built': datetime.datetime.now().isoformat(
        timespec='seconds'), 'note': note, 'packages': deps, 'coupled': sorted(coupled),
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


def sync_repo(pool=None, device_repo=None):
    """Mirror .work/apt/repo to /var/lib/rungic-apt: push missing .debs, replace the index.
    pool and device_repo: the development overlay's repositories (tools/rungic_dev.py), where a
    .deb may be kept on the build host (REMOTE): the phone takes it from there."""
    POOL, DEVICE_REPO = pool or globals()['POOL'], device_repo or globals()['DEVICE_REPO']
    listing = run(f'mkdir -p {DEVICE_REPO} && cd {DEVICE_REPO} && ls -1', 'container').stdout.split()
    kept = kept_files(POOL)
    here = {p.name for p in POOL.iterdir() if p.is_file() and not p.name.endswith(REMOTE)}
    local = here | set(kept)
    fetch = {name: k for name, k in kept.items() if name not in listing}
    if fetch:
        fetch_kept(fetch, DEVICE_REPO)
    send = sorted((here - set(listing)) | {'Packages', 'Packages.gz', 'Packages.xz', 'Release'})
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
    return {'sent': len(send), 'fetched': len(fetch), 'removed': len(remove)}


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


def pin_release(info):
    """Pin every package of the installed release to its exact version (docs/61): above the Ubuntu
    archive and the repository's other builds, so neither Discover's updates nor apt upgrades change
    them. With the release metapackage Protected, apt also refuses to remove it to get around its
    exact dependencies."""
    lines = ['# Written by tools/rungic_release.py deploy: the packages of release ' + info['version'] + '.']
    for name, version in sorted({**info['packages'], meta_of(info['version']): info['version']}.items()):
        lines += ['', f'Package: {name}', f'Pin: version {version}', 'Pin-Priority: 1001']
    body = '\n'.join(lines) + '\n'
    run(f'''set -e
cat > /etc/apt/preferences.d/rungic-release.new <<'EOF'
{body}EOF
mv /etc/apt/preferences.d/rungic-release.new /etc/apt/preferences.d/rungic-release
apt-get -q update {APT_OURS} >/dev/null 2>&1 || true
''', 'container')


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
    else the file at the release's commit (a rollback deploys an older release without checking it
    out). None when neither matches the recorded sha256."""
    path = WORKSPACE / item['source']
    data = path.read_bytes() if path.exists() else b''
    if hashlib.sha256(data).hexdigest() == item['sha256']:
        return data
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
    """Stop the container, run a rootfs action (then before_start, e.g. restore_android), start it again."""
    outputs = []
    for step in ('stop', action, 'start'):
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


def deploy(version=None, restart='auto', acceptance='smoke', record_label=None, snapshot='auto'):
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
    log = {'version': version, 'started': stamp, 'steps': []}
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
        if state not in ('none', 'merging'):     # a finished rollback merge is completed by the snapshot step
            log['result'] = 'aborted'
            step('abort', reason=f'the rootfs has a kept snapshot (state {state}): rungic_release.py commit '
                 'to keep the current system, or rollback --snapshot to return to the snapshot, first')
            return log
        ok, text = with_container_stopped('snapshot')
        step('snapshot', ok=ok, output=text[-400:])
        if not ok:
            log['result'] = 'aborted'
            step('abort', reason='could not take the rootfs snapshot')
            return log
        # A freshly started session has its own start-up flakiness; install into a settled one.
        import rungic_acceptance
        settled = rungic_acceptance.session_ready({})
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
        step('sync', **sync_repo())
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
        pin_release(info)
        step('pins', packages=len(info['packages']) + 1)
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
        # 6 verify
        integrity_after = integrity_summary()
        (record / 'integrity-after.json').write_text(json.dumps(integrity_after, indent=1, ensure_ascii=False) + '\n')
        summary = (integrity_after or {}).get('summary', {})
        mismatch = (integrity_after or {}).get('release', {}).get('mismatch', [])
        step('integrity', state=summary.get('state'), release_mismatch=mismatch)
        passed = not mismatch
        if acceptance != 'none':
            import rungic_acceptance
            report = rungic_acceptance.run_level(acceptance, release=version, out_dir=record / 'acceptance',
                                               since=installed_at)
            step('acceptance', level=acceptance, passed=report['passed'], failed=report['failed_ids'])
            flaky = []
            if not report['passed']:
                # One retry of the failed scenarios: a pass on retry is recorded as flaky, not a failure.
                spec = rungic_acceptance.load()
                retry = rungic_acceptance.run_scenarios([s for s in spec['scenarios'] if s['id'] in report['failed_ids']],
                                                      release=version, out_dir=record / 'acceptance-retry',
                                                      since=installed_at)
                flaky = [i for i in report['failed_ids'] if i not in retry['failed_ids']]
                step('acceptance-retry', passed=retry['passed'], failed=retry['failed_ids'], flaky=flaky)
                report = {**report, 'passed': retry['passed']}
            log['flaky'] = flaky
            passed = passed and report['passed']
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
                    'record': str(record.relative_to(WORKSPACE))})
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


def status():
    version, info = device_release()
    report = integrity_summary()
    built = releases()
    return {
        'installed_release': version,
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
        else:
            p.add_argument('--snapshot', action='store_true',
                           help='return the whole rootfs to the snapshot the last deploy took')
        p.add_argument('--restart', choices=['auto', 'always', 'never'], default='auto')
        p.add_argument('--acceptance', choices=['smoke', 'full', 'none'], default='smoke')
    sub.add_parser('commit')
    sub.add_parser('status')
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
        result = [{k: r[k] for k in ('version', 'commit', 'built', 'note')} for r in releases()]
    elif a.cmd == 'deploy':
        result = deploy(a.version, a.restart, a.acceptance, snapshot=a.snapshot)
    elif a.cmd == 'rollback':
        result = rollback_snapshot() if a.snapshot else rollback(a.restart, a.acceptance)
    elif a.cmd == 'commit':
        result = commit()
    else:
        result = status()
    print(json.dumps(result, indent=1, ensure_ascii=False))
    if isinstance(result, dict) and result.get('result') not in (None, 'ok'):
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except DeviceError as error:
        sys.exit(f'rungic_release: {error}')
