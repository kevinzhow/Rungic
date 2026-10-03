#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Development overlays: this project's packages built from the working tree, installed on top of the
phone's release, visibly and reversibly (docs/97). Releases stay tools/rungic_release.py's.

A deploy builds the named packages as they are in the working tree (uncommitted and new files
included) at <the release's version>+dev<UTC time>.<commit>[.dirty], and a development release
metapackage rungic-release=<release>+dev<UTC time>: the release's exact dependencies with the
overridden packages' development versions, and in /usr/share/rungic/release.json the release it
is based on and each override (version, commit, dirty, built). Both go to a repository of their
own (.work/apt/dev here, /var/lib/rungic-apt-dev on the phone, label rungic-dev) with a source
(/etc/apt/sources.list.d/rungic-dev.sources) and pins above the release's
(/etc/apt/preferences.d/rungic-dev, 1002): apt and Discover take the overlay for the installed
system and offer nothing back. No rootfs snapshot: a reset returns the packages to the release.

  rungic_dev.py deploy NAME... [--host macmini|phone] [--restart auto|never] [--clean]
                                  build NAME... from the working tree and install them over the release
                                  (earlier overrides stay). NAME is one of this project's packages
                                  (packaging/) or an upstream component the release rebuilds
                                  (packages/<name>, release/packages.json "rebuilt"): its source with our
                                  patch queue is built with tools/build_on_device.py, and the binary
                                  packages the release has from it are overridden
  rungic_dev.py reset [NAME...]   back to the release's versions (all overrides, or the named ones: a
                                  package, or an upstream component for all its packages)
  rungic_dev.py status            the release, the overrides and whether apt keeps them

tools/rungic_release.py deploy clears an overlay before it installs a release (clear_device()).
Every deploy and reset leaves a record under .work/dev-deploy/<time>/.
"""
import argparse
import datetime
import email.utils
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import rungic_device
from rungic_device import DeviceError, WORKSPACE, run
import rungic_release

POOL = rungic_release.APT / 'dev'                 # the overlay's .debs and index, here
RECORDS = WORKSPACE / '.work/dev-deploy'
DEVICE_REPO = '/var/lib/rungic-apt-dev'
SOURCE = '/etc/apt/sources.list.d/rungic-dev.sources'
PINS = '/etc/apt/preferences.d/rungic-dev'
LABEL = 'rungic-dev'
APT_DEV = (f'-o Dir::Etc::SourceList={SOURCE} -o Dir::Etc::SourceParts=- -o APT::Get::List-Cleanup=0')
PRIORITY = 1002                                   # above the release's exact pins (1001)


def git(*args):
    return subprocess.run(['git', *args], cwd=WORKSPACE, capture_output=True, text=True, check=True).stdout.strip()


def stamp_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dt%H%M%S')


def dev_version(base, stamp, commit, dirty):
    """<base>+dev<UTC time>.<commit>[.dirty]: after the release's build of the package, before the next
    one (0.510 < 0.510+dev20260930t221500.32b158c < 0.511), and naming its source."""
    return f'{base}+dev{stamp}.{commit}' + ('.dirty' if dirty else '')


def package_dirty(pkg):
    import rungic_package
    paths = rungic_package.identity_paths(pkg)
    return bool(git('status', '--porcelain', '--untracked-files=all', '--', *paths))


def base_of(info):
    """(release version, its packages) that an installed release.json stands on."""
    dev = info.get('dev')
    return (dev['base'], dev['base_packages']) if dev else (info['version'], info['packages'])


def overlay_info(info, overrides, stamp):
    """The development release: the base release's fields, its packages with the overrides' versions."""
    base, base_packages = base_of(info)
    new = {k: v for k, v in info.items() if k != 'dev'}
    new['packages'] = {**base_packages, **{n: o['version'] for n, o in overrides.items()}}
    new['version'] = f'{base}+dev{stamp}'
    new['built'] = datetime.datetime.now().isoformat(timespec='seconds')
    new['dev'] = {'base': base, 'base_packages': base_packages, 'overrides': overrides}
    return new


def config_script(info):
    """The overlay's source and pins, for `info` (a development release)."""
    pins = [f'# Written by tools/rungic_dev.py: development overlay {info["version"]} on release '
            f'{info["dev"]["base"]} (docs/97). tools/rungic_dev.py reset removes it.']
    for name, version in sorted({rungic_release.META: info['version'],
                                 **{n: o['version'] for n, o in info['dev']['overrides'].items()}}.items()):
        pins += ['', f'Package: {name}', f'Pin: version {version}', f'Pin-Priority: {PRIORITY}']
    source = (f'# The development overlay of tools/rungic_dev.py (docs/97); reset removes it.\n'
              f'Types: deb\nURIs: file:{DEVICE_REPO}\nSuites: ./\nTrusted: yes\n')
    return f'''set -e
cat > {SOURCE} <<'EOF'
{source}EOF
cat > {PINS} <<'EOF'
{chr(10).join(pins)}
EOF
apt-get -q update {APT_DEV} >/dev/null'''


CLEAR_SCRIPT = f'''for f in {SOURCE} {PINS} {DEVICE_REPO}; do [ -e "$f" ] && echo "$f"; done
rm -rf {SOURCE} {PINS} {DEVICE_REPO}
rm -f /var/lib/apt/lists/_var_lib_rungic-apt-dev_* 2>/dev/null; true'''


def clear_device(runner=None):
    """Remove an overlay's source, pins and repository (after a release was installed over it, or on a
    reset). The files removed, or None when there was no overlay. runner: the caller's run, so that
    its tests' stand-in catches it (rungic_release.deploy passes its own: a test once cleared the
    real phone's overlay through this function, docs/97)."""
    result = (runner or run)(CLEAR_SCRIPT, 'container', check=False)
    return (getattr(result, 'stdout', '') or '').split() or None


def policy(names):
    """name -> (installed, candidate) as apt sees them."""
    text = run('LC_ALL=C apt-cache policy ' + ' '.join(map(shlex.quote, names)), 'container', check=False).stdout
    result, name = {}, None
    for line in text.splitlines():
        if line and not line.startswith(' ') and line.endswith(':'):
            name = line[:-1]
        elif name and line.strip().startswith(('Installed:', 'Candidate:')):
            key, _, value = line.strip().partition(': ')
            result.setdefault(name, {})[key.lower()] = value
    return {n: (v.get('installed'), v.get('candidate')) for n, v in result.items()}


class Record:
    def __init__(self, action):
        self.stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        self.dir = RECORDS / f'{self.stamp}-{action}'
        self.dir.mkdir(parents=True)
        self.log = {'action': action, 'started': self.stamp, 'steps': []}

    def step(self, name, **data):
        self.log['steps'].append({'step': name, 'time': datetime.datetime.now().isoformat(timespec='seconds'), **data})
        (self.dir / 'dev.json').write_text(json.dumps(self.log, indent=1, ensure_ascii=False) + '\n')
        print(f'[{name}] ' + ', '.join(f'{k}={v}' for k, v in data.items() if not isinstance(v, (dict, list))),
              flush=True)


def installed_release():
    version, info = rungic_release.device_release()
    if not version or not info or 'packages' not in info:
        raise SystemExit('the phone has no release installed (tools/rungic_release.py deploy first)')
    return version, info


def taker(host):
    """Where the build host's .debs go: a Mac mini keeps them, and the phone takes them straight from
    it at sync (AGENTS.md: devices that reach each other exchange files directly; through this
    computer a 67 MB package took 2-10 minutes). Here only <deb>.remote, its Packages entry and
    checksum. None: fetched here as before (a build on the phone)."""
    if not hasattr(host, 'keep_for_phone'):
        return None

    def take(remote, target):
        kept = host.keep_for_phone(remote, target.name)
        Path(str(target) + rungic_release.REMOTE).write_text(json.dumps(kept, ensure_ascii=False) + '\n')
        target.unlink(missing_ok=True)
    return take


def in_pool(name):
    return (POOL / name).exists() or (POOL / (name + rungic_release.REMOTE)).exists()


def build(names, host, stamp, record, clean=False):
    """Development .debs of `names` from the working tree, in POOL. -> {name: override}"""
    import build_on_device
    import rungic_package
    definitions = rungic_package.definitions()
    commit = git('rev-parse', '--short=7', 'HEAD')
    _, info = installed_release()
    _, base_packages = base_of(info)
    POOL.mkdir(parents=True, exist_ok=True)
    build_on_device.use(host)
    components = upstream_components()
    own, upstream = resolve(names, definitions, components)
    overrides = {}
    earlier = info.get('dev', {}).get('overrides', {})
    for name in upstream:
        overrides.update(build_upstream(name, components[name], base_packages, stamp, commit, record, earlier))
    for name in own:
        pkg = definitions[name]
        dirty = package_dirty(pkg)
        version = dev_version(base_packages.get(name, '0'), stamp, commit, dirty)
        print(f'building {name} {version} ({pkg["build"]}{", on " + host if pkg["build"] == "device" else ""})', flush=True)
        dev = {'version': version, 'dest': POOL, 'take': taker(build_on_device.host)}
        started = time.time()
        deb = (rungic_package.build_host(pkg, None, dev) if pkg['build'] == 'host'
               else rungic_package.build_device(pkg, None, build_on_device.host.jobs, dev, clean=clean))
        overrides[name] = {'version': version, 'commit': git('rev-parse', 'HEAD'), 'dirty': dirty,
                           'built': datetime.datetime.now().isoformat(timespec='seconds'), 'file': deb.name}
        record.step('build', package=name, version=version, seconds=round(time.time() - started))
    return overrides


def upstream_components():
    """Upstream components the release rebuilds from a patch queue (docs/71): name -> its entry of
    release/packages.json "rebuilt" ('packages': its binary packages in the release; 'version': the
    first entry of its changelog)."""
    return {name: c for name, c in rungic_release.spec().get('rebuilt', {}).items()
            if c.get('source', '').startswith('packages/')}


def upstream_dirty(name):
    """Uncommitted changes in the component's patch queue or in the shared files its recipe overlays."""
    import pq
    paths = [f'packages/{name}', *[e['from'] for e in pq.overlay(name).values()]]
    return bool(git('status', '--porcelain', '--untracked-files=all', '--', *paths))


def changelog_entry(source, version, distribution, date, commit, dirty):
    """The development build's changelog entry, put on top of the synced tree's (never into packages/)."""
    state = 'with uncommitted changes' if dirty else 'as committed'
    return (f'{source} ({version}) {distribution}; urgency=medium\n\n'
            f'  * Development build of {commit} {state} (tools/rungic_dev.py, docs/97).\n\n'
            f' -- range-dev <noreply@localhost>  {date}\n\n')


def release_binaries(component, base_packages):
    """The component's binary packages that the release has: the ones an overlay replaces."""
    return [b for b in component.get('packages', []) if b in base_packages]


def distribution_bases(component, installed, earlier):
    """A component new to the release (in "rebuilt" since the installed release was built, docs/104)
    replaces its packages as installed from the distribution. -> {package: the version a reset goes
    back to}: kept from an earlier override of it, else the installed one."""
    return {b: earlier[b]['base'] if 'base' in earlier.get(b, {}) else installed[b]
            for b in component.get('packages', []) if b in installed}


def build_upstream(name, component, base_packages, stamp, commit, record, earlier=None):
    """Development .debs of an upstream component's release packages, in POOL. -> {package: override}"""
    import build_on_device
    host = build_on_device.host
    binaries = release_binaries(component, base_packages)
    bases = {}
    if not binaries:
        bases = distribution_bases(component, rungic_release.installed_versions(), earlier or {})
        binaries = list(bases)
    if not binaries:
        raise SystemExit(f'{name}: neither the installed release nor the phone has any of its packages '
                         f'{component.get("packages")}')
    dirty = upstream_dirty(name)
    version = dev_version(component['version'], stamp, commit, dirty)
    print(f'building {name} {version} (upstream, on {host.name}): {" ".join(binaries)}', flush=True)
    started = time.time()
    work = f'{build_on_device.BASE}/{name}'
    build_on_device.sync(name)
    source = host.out(f'dpkg-parsechangelog -l {work}/src/debian/changelog -S Source').strip()
    distribution = host.out(f'dpkg-parsechangelog -l {work}/src/debian/changelog -S Distribution').strip()
    date = email.utils.format_datetime(datetime.datetime.now(datetime.timezone.utc))
    entry = changelog_entry(source, version, distribution, date, commit, dirty)
    host.run(f"cd {work}/src && {{ printf '%s' {shlex.quote(entry)}; cat debian/changelog; }} > debian/changelog.dev "
             f"&& mv debian/changelog.dev debian/changelog")
    # An earlier successful build keeps its obj tree: build only what changed. Otherwise configure afresh.
    previous = host.out(f'test -d {work}/src/obj-aarch64-linux-gnu && cat {work}/build.rc 2>/dev/null || true').strip()
    # Mesa's recipe has patches and a changelog, but no Debian build rules. Use its
    # existing Meson build and runtime packager rather than apt build-dep/dpkg-buildpackage.
    mode = 'targets' if name == 'mesa' else 'incremental' if previous == '0' else 'full'
    if mode == 'full':
        print(build_on_device.build_deps(name), flush=True)
    build_on_device.start(name, mode, host.jobs)
    while 'SubState=running' in (state := build_on_device.status(name)) or 'ActiveState=activating' in state:
        time.sleep(20)
    if 'Result=success' not in state:
        raise SystemExit(f'{name}: {mode} build failed on {host.name}\n{state}\n'
                         + host.out(f'tail -40 {work}/build.log', timeout=60))
    if name == 'mesa':
        import build_mesa
        build_mesa.package(host, version, commit)
    file_version = version.split(':', 1)[-1]
    listing = host.out(f'cd {work} && ls *_{file_version}_*.deb *_{file_version}_*.ddeb 2>/dev/null || true').split()
    POOL.mkdir(parents=True, exist_ok=True)
    take = taker(host) or host.get
    overrides = {}
    for binary in binaries:
        debs = [f for f in listing if f.startswith(f'{binary}_') and f.endswith('.deb')]
        if not debs:
            raise SystemExit(f'{name}: the build made no {binary}_{file_version} package')
        take(f'{work}/{debs[0]}', POOL / debs[0])
        for symbols in (f for f in listing if f.startswith(f'{binary}-dbgsym_')):
            take(f'{work}/{symbols}', POOL / (symbols[:-5] + '.deb' if symbols.endswith('.ddeb') else symbols))
        overrides[binary] = {'version': version, 'commit': git('rev-parse', 'HEAD'), 'dirty': dirty,
                             'built': datetime.datetime.now().isoformat(timespec='seconds'), 'file': debs[0],
                             'component': name, **({'base': bases[binary]} if binary in bases else {})}
    record.step('build', package=name, version=version, mode=mode, binaries=binaries,
                seconds=round(time.time() - started))
    return overrides


def resolve(names, definitions, components):
    """Split NAME... into this project's packages and upstream components; unknown names stop."""
    unknown = [n for n in names if n not in definitions and n not in components]
    if unknown:
        raise SystemExit(f'no package or upstream component {", ".join(unknown)} (packaging/, release/packages.json '
                         f'"rebuilt" with packages/<name>)')
    return [n for n in names if n in definitions], [n for n in names if n not in definitions]


def restored(before, after):
    """Removed overrides of packages outside the release -> {package: its distribution version}."""
    return {n: o['base'] for n, o in before.items() if n not in after and 'base' in o}


def reset_names(names, overrides):
    """The overrides a reset of NAME... removes: a package, or every package of an upstream component."""
    return {key for key, o in overrides.items() if key in names or o.get('component') in names}


def prune(info):
    """Keep only the overlay's current .debs (and their -dbgsym) and metapackage in POOL."""
    keep = {f'{rungic_release.META}_{info["version"]}_all.deb'}
    for name, o in info['dev']['overrides'].items():
        keep.add(o['file'])
        keep.add(o['file'].replace(f'{name}_', f'{name}-dbgsym_', 1))
    for deb in POOL.glob('*.deb'):
        if deb.name not in keep:
            deb.unlink()
    kept = rungic_release.kept_files(POOL)
    for name in set(kept) - keep:
        (POOL / (name + rungic_release.REMOTE)).unlink()
    if kept:
        # And on the build host, what the phone has taken already for earlier overlays.
        import build_on_device
        try:
            build_on_device.MacMini().prune_kept(set(kept) & keep)
        except (DeviceError, OSError, subprocess.SubprocessError) as error:
            print(f'build host not pruned: {error}', flush=True)


def restarts(info, before, after, restart, record):
    """As a release deploy does: changed system services, the desktop user's services of changed
    packages, and the session when a package that needs it changed."""
    if restart == 'never':
        return
    current = json.loads(rungic_release.SPEC.read_text())
    services = {**info.get('service_restart', {}), **current.get('service_restart', {})}
    units = [u for pkg, names in services.items() if before.get(pkg) != after.get(pkg)
             for u in names]
    if units:
        result = run('for u in ' + ' '.join(units) + '; do systemctl is-enabled -q "$u" && '
                     '{ systemctl restart "$u" && echo "$u restarted" || echo "$u FAILED"; }; done; true',
                     'container', timeout=180, check=False)
        record.step('services', output=result.stdout.strip())
    hit, changed = rungic_release.needs_restart(before, after, info.get('session_restart', []))
    record.step('changes', changed=changed, restart_for=hit)
    if hit:
        ok, text = rungic_release.restart_session()
        record.step('restart', ok=ok, output=text[-500:])
    else:
        # The release's list, and the working tree's over it: an overlay is built from the working
        # tree, whose packages may have restarts the release does not know yet (polkit-kde-agent-1).
        spec = {**info.get('user_restart', {}), **current.get('user_restart', {})}
        output = rungic_release.restart_user_services(spec, before, after)
        if output is not None:
            record.step('user-services', output=output)


def verify(info, record):
    """apt keeps the overlay (installed = candidate for each override and the metapackage), and the
    integrity summary."""
    names = [rungic_release.META, *info.get('dev', {}).get('overrides', {})]
    seen = policy(names)
    want = {rungic_release.META: info['version'], **info['packages']}
    problems = [f'{n}: installed {seen.get(n, (None, None))[0]}, candidate {seen.get(n, (None, None))[1]}, '
                f'want {want[n]}' for n in names if seen.get(n) != (want[n], want[n])]
    report = rungic_release.integrity_summary() or {}
    (record.dir / 'integrity.json').write_text(json.dumps(report, indent=1, ensure_ascii=False) + '\n')
    summary = report.get('summary', {})
    record.step('verify', apt=problems or 'ok', integrity=summary.get('state'),
                release_mismatch=report.get('release', {}).get('mismatch'))
    return not problems


def apply(info, record, restart, restore=None):
    """Install the development release `info` (with its repository in POOL) or, when it has no
    overrides left, the base release again. restore: packages outside the release that removed
    overrides replaced, with the distribution's version to go back to."""
    version, current = installed_release()
    before = rungic_release.installed_versions()
    (record.dir / 'before.json').write_text(json.dumps({'release': version, 'packages': before}, indent=1) + '\n')
    problems, fatal = rungic_release.preflight()
    record.step('preflight', release=version, problems=problems)
    if fatal:
        raise SystemExit('; '.join(fatal))
    if info.get('dev', {}).get('overrides'):
        prune(info)
        rungic_release.build_meta(info['version'], info['packages'], info, dest=POOL)
        rungic_release.index(POOL, LABEL)
        run(f'mkdir -p {DEVICE_REPO}', 'container')
        record.step('sync', **rungic_release.sync_repo(POOL, DEVICE_REPO))
        run(config_script(info), 'container', timeout=300)
        target = info
    else:
        # Back to the base release. The overlay's source and pins stay until that succeeded: apt installs
        # the exact versions named, whatever the pins say.
        base = info['version']
        target = next((r for r in rungic_release.releases() if r['version'] == base), None) or info
    ok, tail = rungic_release.apt_install({**target, 'packages': {**target['packages'], **(restore or {})}}
                                          if restore else target, record.dir)
    record.step('install', ok=ok, version=target['version'])
    if not ok:
        # apt left the previous versions installed: put the previous overlay's configuration back.
        if current.get('dev'):
            run(config_script(current), 'container', timeout=300, check=False)
        else:
            clear_device()
        record.step('abort', reason=tail[-1500:])
        record.log['result'] = 'install-failed'
        return record.log
    if not target.get('dev'):
        record.step('overlay', removed=clear_device())
        rungic_release.pin_release(target)
        shutil.rmtree(POOL, ignore_errors=True)
    after = rungic_release.installed_versions()
    (record.dir / 'after.json').write_text(json.dumps({'release': target['version'], 'packages': after}, indent=1) + '\n')
    restarts(target, before, after, restart, record)
    record.log['result'] = 'ok' if verify(target, record) else 'verify-failed'
    record.step('done', result=record.log['result'], release=target['version'])
    return record.log


def deploy(names, host, restart, clean=False):
    record = Record('deploy')
    stamp = stamp_now()
    _, info = installed_release()
    overrides = build(names, host, stamp, record, clean)
    earlier = info.get('dev', {}).get('overrides', {})
    if set(earlier) - set(overrides):
        # Earlier overrides stay: their .debs are still in the pool (a reset takes them back).
        missing = [n for n in set(earlier) - set(overrides) if not in_pool(earlier[n]['file'])]
        if missing:
            raise SystemExit(f'earlier overrides {missing} are not in {POOL} any more: deploy or reset them too')
    new = overlay_info(info, {**earlier, **overrides}, stamp)
    return apply(new, record, restart)


def reset(names, restart):
    record = Record('reset')
    _, info = installed_release()
    if not info.get('dev'):
        leftovers = clear_device()
        record.step('overlay', none=True, removed=leftovers)
        record.log['result'] = 'ok'
        return record.log
    overrides = dict(info['dev']['overrides'])
    for name in reset_names(names, overrides) if names else list(overrides):
        overrides.pop(name, None)
    restore = restored(info['dev']['overrides'], overrides)
    if overrides:
        new = overlay_info(info, overrides, stamp_now())
    else:
        base, base_packages = base_of(info)
        new = {**{k: v for k, v in info.items() if k != 'dev'}, 'version': base, 'packages': base_packages}
    return apply(new, record, restart, restore)


def status():
    version, info = rungic_release.device_release()
    dev = (info or {}).get('dev')
    files = run(f'for f in {SOURCE} {PINS}; do [ -e "$f" ] && echo "$f"; done; true', 'container',
                check=False).stdout.split()
    result = {'installed_release': version, 'base_release': dev['base'] if dev else version,
              'overrides': dev['overrides'] if dev else {}, 'overlay_files': files}
    if dev:
        names = [rungic_release.META, *dev['overrides']]
        result['apt'] = {n: dict(zip(('installed', 'candidate'), v)) for n, v in policy(names).items()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('deploy'); p.add_argument('names', nargs='+')
    p.add_argument('--host', choices=['macmini', 'phone'], default=os.environ.get('RUNGIC_BUILD_HOST', 'macmini'),
                   help='where device packages build (default $RUNGIC_BUILD_HOST, else macmini)')
    p.add_argument('--restart', choices=['auto', 'never'], default='auto')
    p.add_argument('--clean', action='store_true', help='build this project\'s packages from nothing, not incrementally')
    p = sub.add_parser('reset'); p.add_argument('names', nargs='*')
    p.add_argument('--restart', choices=['auto', 'never'], default='auto')
    sub.add_parser('status')
    a = parser.parse_args()
    if a.cmd == 'deploy':
        result = deploy(a.names, a.host, a.restart, a.clean)
    elif a.cmd == 'reset':
        result = reset(a.names, a.restart)
    else:
        result = status()
    print(json.dumps(result, indent=1, ensure_ascii=False))
    if isinstance(result, dict) and result.get('result') not in (None, 'ok'):
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except DeviceError as error:
        sys.exit(f'rungic_dev: {error}')
