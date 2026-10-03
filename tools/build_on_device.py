#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build a patch-queue component (packages/<name>, docs/71) natively on Ubuntu 26.04 ARM64: in the
phone's container (--host phone) or in a Docker container on the Mac mini build host (--host
macmini: Apple M4, tools/pq/arm64-host.Dockerfile; faster and keeps the phone cool). The default
host is $RUNGIC_BUILD_HOST, else macmini (the phone is the fallback).

The source tree (tools/pq.py source: upstream + debian/ with the patches applied) is copied into a persistent
/root/rungic-build/<component>/src with `rsync --checksum`, so unchanged files
keep their timestamps and the kept obj-aarch64-linux-gnu tree rebuilds only
what changed.

  full         dpkg-buildpackage -b (clean configure; first build or packaging change)
  incremental  make in the existing obj dir, then `debian/rules binary` (skips
               configure/build through debhelper's stamp) to produce .debs
  targets      for trees without Debian packaging (recipe kind upstream/git): configure once in
               <component>/build (Ninja, /usr prefix) and build --target T...;
               a meson tree (Mesa) is configured with desktop/<component>-meson-options
  status       state of the build unit and the log tail
  install      dpkg -i the .debs of the last build (version from debian/changelog)
  collect      the last build's .debs and .ddebs into the release repository pool (rungic_release.py)
  divert       install built files over distribution ones with dpkg-divert
               (--file BUILT=INSTALLED, repeatable); the original stays as .distrib

On the phone builds run as the transient system unit rungic-build-<component>, so they survive adb
disconnects; on the Mac mini as a detached process of the container. The log is
/root/rungic-build/<component>/build.log on either host. install and divert change the phone's
system and exist only there.
"""
import argparse
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import tarfile
import time
from compression import zstd

import rungic_device
from rungic_device import WORKSPACE

BASE = '/root/rungic-build'
# This project's own packages build in PACKAGE_BASE/<name> (rungic_package.py); one built in another
# image (a Flatpak SDK) in BASE/packages/<name>.
PACKAGE_BASE = '/root/rungic-packages'
# Build trees are kept between builds, so a build compiles only what changed (sync above;
# rungic_package.sync_script). One not built for CACHE_DAYS is removed before the next build
# (expire): its marker MARKER says when it was last used.
CACHE_DAYS = 30
MARKER = '.rungic-last-build'


def cache_dirs():
    """The build trees expire() may remove: a component's (packages/<name>) and a package's
    (packaging/<name>); nothing else under BASE (pools, shims, tools)."""
    components = sorted(p.parent.name for p in (WORKSPACE / 'packages').glob('*/recipe.json'))
    own = sorted(p.parent.name for p in (WORKSPACE / 'packaging').glob('*/package.json'))
    return ([f'{BASE}/{c}' for c in components] + [f'{PACKAGE_BASE}/{n}' for n in own]
            + [f'{BASE}/packages/{n}' for n in own])


def expire_script(dirs, days=CACHE_DAYS):
    """Shell that removes each of `dirs` last built more than `days` ago (its MARKER's time, else
    the directory's own for a tree from before the markers) and says which."""
    quoted = ' '.join(shlex.quote(d) for d in dirs)
    return (f'now=$(date +%s); for d in {quoted}; do [ -d "$d" ] || continue; '
            f't=$(stat -c %Y "$d/{MARKER}" 2>/dev/null || stat -c %Y "$d"); '
            f'if [ $((now - t)) -gt {days * 86400} ]; then rm -rf "$d" && echo "build cache expired: $d"; fi; done; true')


def expire():
    """Remove the build trees on the build host not built for CACHE_DAYS days."""
    out = host.run(expire_script(cache_dirs()), timeout=600).stdout.strip()
    if out:
        print(out, flush=True)
# Line tables only (-g1): enough for symbolized backtraces (docs/61) at a fraction of -g2's
# compile memory; debhelper strips the packages and puts the symbols into -dbgsym packages
# for the release repository.
DEBUG_FLAGS = 'DEB_CFLAGS_MAINT_APPEND=-g1 DEB_CXXFLAGS_MAINT_APPEND=-g1'


class Phone:
    """The phone's Plasma container over adb (rungic_device)."""
    name, jobs = 'phone', 4

    def run(self, script, timeout=120, check=True):
        return rungic_device.run(script, 'container', timeout, check)

    def out(self, script, timeout=120):
        return self.run(script, timeout).stdout

    def put_tar(self, archive, directory):
        rungic_device.extract_in_container(archive, directory)

    def put(self, src, dest, mode):
        return rungic_device.to_container(src, dest, mode)

    def get(self, path, target):
        rungic_device.from_container(path, target)

    def background(self, component, steps):
        work = f'{BASE}/{component}'
        self.run(f'''set -e
# RemainAfterExit keeps the last build's result and MemoryPeak readable until the next one.
systemctl stop rungic-build-{component} 2>/dev/null || true
systemctl reset-failed rungic-build-{component} 2>/dev/null || true
systemd-run --unit=rungic-build-{component} --nice=10 --property=IOSchedulingClass=idle --property=MemoryAccounting=yes \\
  --property=RemainAfterExit=yes \\
  --setenv=HOME=/root --property=StandardOutput=truncate:{work}/build.log --property=StandardError=inherit \\
  /bin/sh -c "{steps.replace('$(', '\\$(')}"
''')

    def unit_state(self, component):
        return (f'systemctl show -p ActiveState -p SubState -p Result -p ExecMainStartTimestamp -p ExecMainExitTimestamp '
                f'-p ExecMainStatus -p MemoryPeak -p CPUUsageNSec rungic-build-{component}')


class MacMini:
    """A long-running Ubuntu 26.04 ARM64 container on the Mac mini build host (OrbStack Docker), reached
    with ssh (key login). The image is tools/pq/arm64-host.Dockerfile, tagged with its hash; build
    trees live in the rungic-build volume. Network use goes through the Mac's system proxy (read
    with scutil each session; neither ssh commands nor containers pick it up by themselves): every
    command in the container gets http(s)_proxy, a local proxy reached as host.docker.internal."""
    name, jobs = 'macmini', 10
    # One connection, kept ten minutes and shared by every command: a new one takes about 3.3 s to the
    # Mac mini, a shared one 0.6 s, and a package build makes dozens (most of a build's time was ssh).
    # The socket is under the runtime directory: a short path of this user only.
    SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ControlMaster=auto',
           '-o', f"ControlPath={os.environ.get('XDG_RUNTIME_DIR') or '/tmp'}/rungic-ssh-%C", '-o', 'ControlPersist=600',
           'choukevin@macmini.wire.net']
    DOCKER = '/usr/local/bin/docker'
    CONTAINER = 'rungic-build'
    # The phone reaches the build container directly with its own restricted key
    # (tools/pq/rungic-transfer, docs/71): put DIR / get FILE under /root/rungic-build. Devices
    # that reach each other directly exchange files directly (AGENTS.md): over wire.net or the
    # LAN, whichever answers (ssh's own failure is 255). Not macmini.wire.net: the container's
    # DNS answers it with public addresses. A shell command, used as `{PHONE_SSH} get FILE`.
    PHONE_HOSTS = ('10.77.0.20', '192.168.5.45')
    PHONE_SSH = ("sh -c 'for h in " + ' '.join(PHONE_HOSTS) + "; do "
                 "ssh -i /root/.ssh/id_ed25519_buildhost -o BatchMode=yes -o ConnectTimeout=5 "
                 "-o StrictHostKeyChecking=accept-new choukevin@$h \"$@\"; rc=$?; [ $rc = 255 ] || exit $rc; "
                 "done; exit 255' rungic-buildhost")
    DEV_POOL = 'dev-pool'               # under BASE: built files kept for the phone (rungic_dev.py)
    ready = False
    proxy_env = None

    def proxy(self):
        """{'http_proxy': ..., 'https_proxy': ...} for the container from the macOS system proxy, or {}."""
        if self.proxy_env is None:
            text = self.ssh('scutil --proxy', 30, check=False).stdout.decode(errors='replace')
            values = dict(re.findall(r'^\s*(\w+) : (\S+)$', text, re.M))
            env = {}
            for scheme, key in (('http', 'HTTP'), ('https', 'HTTPS')):
                if values.get(key + 'Enable') == '1' and values.get(key + 'Proxy') and values.get(key + 'Port'):
                    host = values[key + 'Proxy']
                    if host in ('127.0.0.1', 'localhost', '::1'):
                        host = 'host.docker.internal'
                    env[scheme + '_proxy'] = f'http://{host}:{values[key + "Port"]}'
            if env:
                env['no_proxy'] = 'localhost,127.0.0.1'
            self.proxy_env = env
        return self.proxy_env

    def env_flags(self):
        return ''.join(f'-e {k}={v} ' for k, v in self.proxy().items())

    def ssh(self, command, timeout, check=True, data=None, stdout=subprocess.PIPE):
        result = subprocess.run(self.SSH + [command], input=data, stdout=stdout, stderr=subprocess.PIPE,
                                timeout=timeout)
        if check and result.returncode:
            text = (result.stderr or b'').decode(errors='replace').strip()
            if stdout is subprocess.PIPE:
                text = text or result.stdout.decode(errors='replace').strip()[-2000:]
            raise rungic_device.DeviceError(f'Exit {result.returncode}: {text}')
        return result

    # Build context: the Dockerfile and the debug symbol source it copies (the phone's own file).
    IMAGE_FILES = {'Dockerfile': WORKSPACE / 'tools/pq/arm64-host.Dockerfile',
                   'rungic-ddebs.sources': WORKSPACE / 'system/config/etc/apt/rungic-ddebs.sources',
                   'arm64-host-packages.txt': WORKSPACE / 'tools/pq/arm64-host-packages.txt'}

    @classmethod
    def image(cls):
        """The build image's tag: its build context's hash (tools/system_test.py builds on it too)."""
        digest = hashlib.sha256()
        for name, path in cls.IMAGE_FILES.items():
            digest.update(name.encode() + b'\0' + path.read_bytes())
        return 'rungic-arm64-host:' + digest.hexdigest()[:12]

    def ensure(self):
        if self.ready:
            return
        files = self.IMAGE_FILES
        image = self.image()
        current = self.ssh(f"{self.DOCKER} inspect -f '{{{{.Config.Image}}}} {{{{.State.Running}}}}' {self.CONTAINER} "
                           f"2>/dev/null || true", 60).stdout.decode().split()
        if current[:1] != [image]:
            print(f'macmini: preparing {image}', flush=True)
            context = io.BytesIO()
            with tarfile.open(fileobj=context, mode='w', format=tarfile.USTAR_FORMAT) as tar:
                for name, path in files.items():
                    tar.add(path, arcname=name)
            build_args = ''.join(f'--build-arg {k}={v} ' for k, v in self.proxy().items())
            self.ssh(f'{self.DOCKER} image inspect {image} >/dev/null 2>&1 || '
                     f'{self.DOCKER} build -q {build_args}-t {image} -', 7200, data=context.getvalue())
            self.ssh(f'{self.DOCKER} rm -f {self.CONTAINER} >/dev/null 2>&1; {self.DOCKER} run -d --name {self.CONTAINER} '
                     f'--restart unless-stopped -v rungic-build:{BASE} {image} sleep infinity', 300)
        elif current[1:] != ['true']:
            self.ssh(f'{self.DOCKER} start {self.CONTAINER}', 120)
        self.ready = True

    def exec(self, command, interactive=False):
        return f"{self.DOCKER} exec {'-i ' if interactive else ''}{self.env_flags()}{self.CONTAINER} {command}"

    def run(self, script, timeout=120, check=True):
        self.ensure()
        result = self.ssh(self.exec('bash -s', True), timeout, check, script.encode())
        return subprocess.CompletedProcess(result.args, result.returncode, result.stdout.decode(errors='replace'),
                                           result.stderr.decode(errors='replace'))

    def out(self, script, timeout=120):
        return self.run(script, timeout).stdout

    def put_tar(self, archive, directory):
        # Compressed: the link to the Mac mini carries about 0.5 MB/s, and a Mesa source tree is a
        # 425 MB tar (108 MB with zstd -10, 3.6 s here; its upload did not finish in 30 minutes).
        self.ensure()
        with open(archive, 'rb') as data:
            packed = zstd.compress(data.read(), 10)
        self.ssh(self.exec(f"sh -c 'mkdir -p {directory} && zstd -dc | tar -xf - -C {directory} --no-same-owner'",
                           True), 3600, data=packed)

    def put(self, src, dest, mode):
        self.ensure()
        data = open(src, 'rb').read()
        self.ssh(self.exec(f"sh -c 'mkdir -p \"$(dirname {dest})\" && cat > {dest} && chmod {mode} {dest}'", True),
                 600, data=data)
        if self.out(f'sha256sum < {dest} | cut -d" " -f1').strip() != hashlib.sha256(data).hexdigest():
            raise rungic_device.DeviceError(f'{dest}: copy to {self.name} incomplete')
        return dest

    def get(self, path, target):
        """Copy a file out of the container, checked: `docker exec cat` through ssh has ended large
        files early with success (a 13.7 MB .deb arrived as 12.9 MB)."""
        self.ensure()
        size, digest = self.out(f'stat -c %s {path}; sha256sum < {path} | cut -d" " -f1').split()
        for attempt in range(3):
            with open(target, 'wb') as file:
                self.ssh(self.exec(f'cat {path}', True), 1800, stdout=file, data=b'')
            data = open(target, 'rb').read()
            if len(data) == int(size) and hashlib.sha256(data).hexdigest() == digest:
                return
        raise rungic_device.DeviceError(f'{path}: copy from {self.name} incomplete ({len(data)} of {size} bytes)')

    def keep_for_phone(self, path, name):
        """Keep a built .deb in the container as DEV_POOL/name, for the phone to take straight from
        here (rungic_release.sync_repo): -> {'path', 'size', 'sha256', 'stanza'}, its entry of a
        flat repository's Packages (Filename ./name, as rungic_release.index writes)."""
        self.ensure()
        pool = f'{BASE}/{self.DEV_POOL}'
        out = self.out(f'''set -e
mkdir -p {pool}/.scan && cd {pool}/.scan && rm -f ./*.deb
cp {path} ../{name}.part && mv ../{name}.part ../{name} && ln ../{name} {name}
stat -c %s {name}; sha256sum < {name} | cut -d" " -f1
dpkg-scanpackages -m . /dev/null 2>/dev/null
rm -f {name}''', 600)
        size, digest, stanza = out.split('\n', 2)
        if f'Filename: ./{name}' not in stanza:
            raise rungic_device.DeviceError(f'{name}: no Packages entry from dpkg-scanpackages')
        return {'path': f'{self.DEV_POOL}/{name}', 'size': int(size), 'sha256': digest.strip(),
                'stanza': stanza.strip('\n') + '\n\n'}

    def prune_kept(self, keep):
        """Remove the files kept for the phone but those named."""
        names = ' '.join(shlex.quote(n) for n in sorted(keep))
        self.run(f'''cd {BASE}/{self.DEV_POOL} 2>/dev/null || exit 0
for f in *.deb; do [ -e "$f" ] || continue; case " {names} " in *" $f "*) ;; *) rm -f "$f";; esac; done''', 120)

    def background(self, component, steps):
        work = f'{BASE}/{component}'
        # A detached process of the container; build.pid while it runs, build.rc when it ends.
        self.run(f'''set -e
if [ -f {work}/build.pid ] && kill -0 "$(cat {work}/build.pid)" 2>/dev/null; then kill -TERM -"$(cat {work}/build.pid)" || true; sleep 2; fi
rm -f {work}/build.rc
cat > {work}/build.sh <<'RUNGIC_STEPS'
export HOME=/root
{steps}
RUNGIC_STEPS
setsid nohup sh -c 'echo $$ > {work}/build.pid; nice -n 10 sh {work}/build.sh > {work}/build.log 2>&1; echo $? > {work}/build.rc; rm -f {work}/build.pid' rungic-build-{component} </dev/null >/dev/null 2>&1 &
''')

    def unit_state(self, component):
        work = f'{BASE}/{component}'
        return (f'if [ -f {work}/build.pid ] && kill -0 "$(cat {work}/build.pid)" 2>/dev/null; then '
                f'echo ActiveState=active; echo SubState=running; '
                f'else rc=$(cat {work}/build.rc 2>/dev/null || echo none); echo ActiveState=inactive; echo SubState=exited; '
                f'echo ExecMainStatus=$rc; [ "$rc" = 0 ] && echo Result=success || echo Result=exit-code; fi')


HOSTS = {'phone': Phone, 'macmini': MacMini}
host = Phone()


def use(name):
    global host
    host = HOSTS[name]()
    return host


def stage(component):
    if not (WORKSPACE / 'packages' / component / 'recipe.json').exists():
        raise SystemExit(f'{component}: no packages/{component}/recipe.json (docs/71)')
    # A patch-queue component (docs/71): pinned upstream + packages/<name>/debian, patches applied.
    import pq
    source = str(pq.source(component))
    archive = WORKSPACE / f'.work/cache/{component}-stage.tar'
    with tarfile.open(archive, 'w') as tar:
        tar.add(source, arcname='src')
        tar.add(WORKSPACE / 'desktop/build-shims', arcname='cmake-shims')
        options = WORKSPACE / f'desktop/{component}-meson-options'
        if options.exists():
            tar.add(options, arcname='meson-options')
    return archive


def sync(component):
    work = f'{BASE}/{component}'
    expire()
    host.run(f'rm -rf {work}/incoming')
    host.put_tar(stage(component), f'{work}/incoming')
    # Keep obj-* (build output) and debhelper state; everything else mirrors the stage.
    # Quilt gives applied headers a fresh mtime on every extraction. Do not copy that mtime
    # onto byte-identical destination files: it would force a full rebuild on every sync.
    print(host.out(f'''set -e
chown -R root:root {work}/incoming
mkdir -p {work}/src
rsync -a --checksum --no-times --delete --itemize-changes --exclude '/obj-*' --exclude '/debian/.debhelper' \\
  --exclude '/debian/*-build-stamp' --exclude '/debian/files' --exclude '/debian/*.substvars' \\
  --exclude '/debian/tmp' {work}/incoming/src/ {work}/src/ | grep -v '^\\.' | head -40
rm -rf {BASE}/cmake-shims && mv {work}/incoming/cmake-shims {BASE}/cmake-shims
if [ -f {work}/incoming/meson-options ]; then mv {work}/incoming/meson-options {work}/meson-options; fi
rm -rf {work}/incoming
touch {work}/{MARKER}
''', timeout=600))


def component_uses_meson(component):
    return (WORKSPACE / f'desktop/{component}-meson-options').exists()


def arch_only(component):
    """A patch-queue component whose recipe sets build_arch_only: its architecture-independent
    packages (documentation, examples) are neither built nor installed (docs/73)."""
    recipe = WORKSPACE / 'packages' / component / 'recipe.json'
    return recipe.exists() and bool(json.loads(recipe.read_text()).get('build_arch_only'))


def start(component, mode, jobs, targets=(), lto=True, cmake_args=()):
    work = f'{BASE}/{component}'
    maint = '' if lto else ' DEB_BUILD_MAINT_OPTIONS=optimize=-lto'
    obj = f'{work}/src/obj-aarch64-linux-gnu'
    if mode == 'targets' and component_uses_meson(component):
        build = f'{work}/build'
        steps = (f"cd {work} && (test -f {build}/build.ninja || meson setup {build} src $(cat {work}/meson-options)) && "
                 f"ninja -C {build} -j {jobs} {' '.join(targets)}")
    elif mode == 'targets':
        build = f'{work}/build'
        steps = (f"cd {work} && (test -f {build}/build.ninja || cmake -S src -B {build} -G Ninja "
                 f"-DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=RelWithDebInfo -DBUILD_TESTING=OFF {' '.join(cmake_args)}) && "
                 f"cmake --build {build} -j {jobs} --target {' '.join(targets)}")
    elif mode == 'full':
        steps = (f"export DEB_BUILD_OPTIONS='nocheck parallel={jobs}' {DEBUG_FLAGS}{maint}; "
                 f"cd {work}/src && dpkg-buildpackage {'-B' if arch_only(component) else '-b'} -uc -us")
    else:
        steps = (f"export DEB_BUILD_OPTIONS='nocheck parallel={jobs}' {DEBUG_FLAGS}{maint}; "
                 f"cd {work}/src && test -d {obj} && "
                 f"(if test -f {obj}/build.ninja; then ninja -C {obj} -j{jobs}; "
                 f"else make -C {obj} -j{jobs}; fi) && debian/rules binary")
    host.background(component, steps)
    print(f'started rungic-build-{component} on {host.name} ({mode}, {jobs} jobs); '
          f'follow with: build_on_device.py --host {host.name} {component} status')


def status(component):
    return host.out(f'''{host.unit_state(component)}
grep -E '^\\[ *[0-9]+%\\]|^\\[[0-9]+/[0-9]+\\]|error|Error|warning: unused|dpkg-deb: building' {BASE}/{component}/build.log 2>/dev/null | tail -n 8 | cut -c1-200
ls -1t {BASE}/{component}/*.deb 2>/dev/null | head -12''')


def phone_only(action):
    if host.name != 'phone':
        raise SystemExit(f'{action} changes the phone\'s system: use --host phone, or deploy a release (rungic_release.py)')


def changelog_version(component):
    return host.out(f"dpkg-parsechangelog -l {BASE}/{component}/src/debian/changelog -S Version | sed 's/^[0-9]*://'").strip()


def install(component):
    phone_only('install')
    version = changelog_version(component)
    return host.out(f'''set -e
cd {BASE}/{component}
debs=$(ls *_{version}_*.deb | grep -v -e -dbgsym)
echo "installing: $debs"
dpkg -i $debs   # apt holds on these packages stay in place; dpkg ignores them
''', timeout=600)


def build_deps(component):
    """apt-get build-dep for the staged Debian source (full builds). The phone's container reaches the
    archive through its proxy; the build host's image refreshes its package lists first."""
    update = 'apt-get -o DPkg::Lock::Timeout=1200 update -qq' if host.name != 'phone' else 'true'
    return host.out(f'''set -e
[ -r /etc/profile.d/proxy.sh ] && . /etc/profile.d/proxy.sh
cd {BASE}/{component}/src
if ! dpkg-checkbuilddeps {'-B ' if arch_only(component) else ''}2>/dev/null; then
  {update}
  # Waits for another apt (a crash symbolization on the build host); a failure stops the build here.
  DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=1200 build-dep -y -q {'--arch-only ' if arch_only(component) else ''}. > /tmp/build-dep.log 2>&1 || {{ tail -20 /tmp/build-dep.log; exit 1; }}
  tail -2 /tmp/build-dep.log
fi
''', timeout=3600)


def collect(component):
    """The last build's .debs (and .ddeb debug symbols, renamed .deb for the index) into the release
    repository pool (docs/61)."""
    import rungic_release
    version = changelog_version(component)
    names = host.out(f"cd {BASE}/{component} && ls *_{version}_*.deb *_{version}_*.ddeb 2>/dev/null || true").split()
    incoming = WORKSPACE / '.work/apt/incoming'
    incoming.mkdir(parents=True, exist_ok=True)
    for name in names:
        target = incoming / (name[:-5] + '.deb' if name.endswith('.ddeb') else name)
        host.get(f'{BASE}/{component}/{name}', target)
    added = rungic_release.import_debs(sorted(incoming.glob('*.deb')))
    for path in incoming.glob('*.deb'):
        path.unlink()
    rungic_release.index()
    return f'{version}: {len(names)} files from {host.name}, added {added}'


def divert(component, files):
    phone_only('divert')
    lines = ['set -e']
    for spec in files:
        built, installed = spec.split('=', 1)
        lines.append(f'''[ -e {installed}.distrib ] || dpkg-divert --local --add --rename --divert {installed}.distrib {installed}
install -m644 {BASE}/{component}/build/{built} {installed}
echo "{installed} <- {built}"''')
    return host.out('\n'.join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('component')
    parser.add_argument('action', choices=['full', 'incremental', 'targets', 'status', 'install', 'divert', 'wait',
                                           'collect'])
    parser.add_argument('--target', action='append', default=[], help='CMake target (targets mode)')
    parser.add_argument('--file', action='append', default=[], help='BUILT=INSTALLED (divert mode)')
    parser.add_argument('--cmake-arg', action='append', default=[], help='extra configure argument (targets mode)')
    parser.add_argument('--host', choices=sorted(HOSTS), default=os.environ.get('RUNGIC_BUILD_HOST', 'macmini'),
                        help='where to build (default $RUNGIC_BUILD_HOST, else macmini)')
    parser.add_argument('--jobs', type=int, help='compile jobs (phone 4: 8 cores, ~7 GiB RAM, and builds push it to '
                        'thermal throttling, so more jobs gain little; macmini 10)')
    parser.add_argument('--no-lto', action='store_true', help='development build: skip Ubuntu\'s default LTO '
                        '(much faster link; do not use for performance measurements)')
    args = parser.parse_args()
    use(args.host)
    if args.action in ('full', 'incremental', 'targets'):
        if args.action == 'targets' and not args.target and not component_uses_meson(args.component):
            parser.error('targets mode needs --target (a meson tree builds everything without one)')
        sync(args.component)
        if args.action == 'full':
            print(build_deps(args.component))
        start(args.component, args.action, args.jobs or host.jobs, args.target, not args.no_lto, args.cmake_arg)
    elif args.action == 'divert':
        print(divert(args.component, args.file))
    elif args.action == 'status':
        print(status(args.component))
    elif args.action == 'collect':
        print(collect(args.component))
    elif args.action == 'wait':
        while 'SubState=running' in (text := status(args.component)) or 'ActiveState=activating' in text:
            time.sleep(30)
        print(text)
    else:
        print(install(args.component))


if __name__ == '__main__':
    main()
