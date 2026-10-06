#!/usr/bin/env python3
"""Compose an ARM64 Termux seed from a pinned APK and authenticated Debian packages.

Runs host apt in isolated state for dependency resolution/download only. Android
maintainer scripts are retained, never executed or reported configured by Linux.
Use the emitted lock to replay exact package versions and hashes on later builds.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import tempfile
import subprocess
import tarfile
import zipfile

APK_VERSION = '0.118.3'
APK_SHA256 = '72fdb596045116bf5ba1b5bdf5b26fddb9acc0bd074ad9f2da9eb0ae85e83a4e'
APK_URL = 'https://github.com/termux/termux-app/releases/download/v0.118.3/termux-app_v0.118.3%2Bgithub-debug_arm64-v8a.apk'
REPOSITORY = 'https://packages.termux.dev/apt/termux-main'
ANDROID_PREFIX = '/data/data/com.termux/files/usr'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def relative(name):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts: raise ValueError('unsafe archive path: ' + name)
    return Path(*p.parts)


def destination(root, name):
    path = root / relative(name)
    parent = path.parent
    while parent != root:
        if parent.is_symlink(): raise ValueError('archive writes through a directory symlink: ' + name)
        parent = parent.parent
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def extract_bootstrap(apk, prefix, expected=APK_SHA256):
    if digest(apk) != expected: raise ValueError('Termux APK SHA-256 differs from the pinned input')
    with zipfile.ZipFile(apk) as outer: data = outer.read('lib/arm64-v8a/libtermux-bootstrap.so')
    start, end = data.find(b'PK\x03\x04'), data.rfind(b'PK\x05\x06')
    if start < 0 or end < start or len(data) < end + 22: raise ValueError('embedded bootstrap ZIP missing')
    length = struct.unpack_from('<H', data, end + 20)[0]
    archive = data[start:end + 22 + length]
    prefix.mkdir(parents=True, exist_ok=False, mode=0o700)
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        for entry in z.infolist():
            if entry.filename == 'SYMLINKS.txt': continue
            if stat.S_ISLNK(entry.external_attr >> 16): raise ValueError('unexpected ZIP symlink')
            path = destination(prefix, entry.filename)
            if entry.is_dir(): path.mkdir(exist_ok=True, mode=0o700); continue
            path.write_bytes(z.read(entry))
            # Match TermuxInstaller.java at tag v0.118.3, lines 192-196.
            executable = (entry.filename.startswith(('bin/', 'libexec', 'lib/apt/apt-helper', 'lib/apt/methods'))
                          or entry.filename == 'etc/termux/bootstrap/termux-bootstrap-second-stage.sh')
            path.chmod(0o700 if executable else 0o600)
        links = z.read('SYMLINKS.txt').decode().splitlines()
        if not links: raise ValueError('bootstrap symlink inventory is empty')
        for line in links:
            target, name = line.split('←')
            destination(prefix, name).symlink_to(target)
    return {'embedded_zip_sha256': hashlib.sha256(archive).hexdigest(), 'symlinks': len(links)}


def fields(text):
    result = {}; name = None
    for line in text.splitlines():
        if line.startswith((' ', '\t')) and name: result[name] += '\n' + line
        elif ': ' in line: name, value = line.split(': ', 1); result[name] = value
    return result


def unpack_deb(deb, prefix):
    control = fields(subprocess.check_output(['dpkg-deb', '-f', str(deb)], text=True))
    if control['Architecture'] not in ('aarch64', 'all'): raise ValueError('foreign package architecture')
    payload = subprocess.check_output(['dpkg-deb', '--fsys-tarfile', str(deb)])
    listing = []
    base = PurePosixPath(ANDROID_PREFIX.lstrip('/'))
    with tarfile.open(fileobj=io.BytesIO(payload)) as t:
        for entry in t:
            name = PurePosixPath(relative(entry.name).as_posix())
            if name == PurePosixPath('.') or name in base.parents or name == base: continue
            if not name.is_relative_to(base): raise ValueError('package writes outside the Termux prefix: ' + entry.name)
            rel = name.relative_to(base).as_posix(); path = destination(prefix, rel)
            if entry.isdir():
                if path.is_symlink(): raise ValueError('package directory is a symlink')
                path.mkdir(exist_ok=True); path.chmod(entry.mode); listing.append(ANDROID_PREFIX + '/' + rel); continue
            if path.is_symlink() or path.is_file(): path.unlink()
            if entry.issym(): path.symlink_to(entry.linkname)
            elif entry.islnk():
                link = PurePosixPath(relative(entry.linkname).as_posix())
                if not link.is_relative_to(base): raise ValueError('hardlink outside prefix')
                os.link(destination(prefix, link.relative_to(base).as_posix()), path)
            elif entry.isfile():
                path.write_bytes(t.extractfile(entry).read()); path.chmod(entry.mode)
            else: raise ValueError('unsupported package node')
            listing.append(ANDROID_PREFIX + '/' + rel)
    metadata = prefix / 'var/lib/dpkg/info'; metadata.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(['dpkg-deb', '--control', str(deb), tmp], check=True)
        for path in Path(tmp).iterdir():
            if path.name != 'control': shutil.copy2(path, metadata / (control['Package'] + '.' + path.name))
    (metadata / (control['Package'] + '.list')).write_text('\n'.join(listing) + '\n')
    # Do not fabricate configured package state: postinst requires Android.
    control['Status'] = 'install ok unpacked'
    return control


def run(command, log):
    with log.open('ab') as f:
        subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apk', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--lock', type=Path, help='Replay an emitted lock; reject version/hash drift')
    args = p.parse_args(); out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    prefix = out / 'prefix/usr'; bootstrap = extract_bootstrap(args.apk, prefix)
    resolver = out / 'resolver'
    for path in ('etc/apt/trusted.gpg.d', 'etc/apt/apt.conf.d', 'etc/apt/preferences.d',
                 'var/lib/apt/lists/partial', 'var/cache/apt/archives/partial'):
        (resolver / path).mkdir(parents=True, exist_ok=True)
    for key in (prefix / 'share/termux-keyring').glob('*.gpg'):
        shutil.copyfile(key, resolver / 'etc/apt/trusted.gpg.d' / key.name)
    (resolver / 'etc/apt/sources.list').write_text('deb ' + REPOSITORY + ' stable main\n')
    status = resolver / 'bootstrap.status'; shutil.copyfile(prefix / 'var/lib/dpkg/status', status)
    config = resolver / 'apt.conf'
    config.write_text('Dir ' + json.dumps(str(resolver)) + ';\nDir::State::status ' + json.dumps(str(status)) + ';\n'
                      'APT::Architecture "aarch64";\nAPT::Architectures { "aarch64"; };\n'
                      'APT::Sandbox::User "root";\nDir::Etc::main "apt.conf";\n')
    command = ['apt-get', '-c', str(config)]; log = out / 'apt.log'
    # apt authenticates the Release and package hashes with APK-bundled public keys.
    run(command + ['update'], log)
    if args.lock:
        lock = json.loads(args.lock.read_text())
        if lock['apk_sha256'] != APK_SHA256 or lock['repository'] != REPOSITORY: raise ValueError('lock source differs')
        archives = resolver / 'var/cache/apt/archives'
        # Download only, in resolver cache; no host or Android dpkg execution.
        with log.open('ab') as f:
            subprocess.run(command + ['download'] + [x['package'] + '=' + x['version'] for x in lock['packages']],
                           cwd=archives, stdout=f, stderr=subprocess.STDOUT, check=True)
    else:
        run(command + ['--download-only', '--yes', '--no-install-recommends', 'install', 'pulseaudio'], log)
    records = []; package_fields = []
    for deb in sorted((resolver / 'var/cache/apt/archives').glob('*.deb')):
        c = fields(subprocess.check_output(['dpkg-deb', '-f', str(deb)], text=True))
        records.append({'package': c['Package'], 'version': c['Version'], 'architecture': c['Architecture'],
                        'file': deb.name, 'sha256': digest(deb), 'bytes': deb.stat().st_size})
    if args.lock and records != lock['packages']: raise ValueError('locked package bytes differ')
    if not any(x['package'] == 'pulseaudio' for x in records): raise ValueError('PulseAudio package missing')
    for record in records:
        package_fields.append(unpack_deb(resolver / 'var/cache/apt/archives' / record['file'], prefix))
    status_file = prefix / 'var/lib/dpkg/status'
    existing = [fields(x) for x in status_file.read_text().split('\n\n') if x.strip()]
    replaced = {x['Package'] for x in package_fields}
    status_file.write_text('\n\n'.join('\n'.join(k + ': ' + v for k, v in x.items())
                                    for x in existing if x['Package'] not in replaced) + '\n\n' +
                           '\n\n'.join('\n'.join(k + ': ' + v for k, v in x.items()) for x in package_fields) + '\n')
    pulse = prefix / 'bin/pulseaudio'
    if not pulse.is_file() or not os.access(pulse, os.X_OK): raise ValueError('PulseAudio executable absent')
    lock = {'schema': 1, 'apk_version': APK_VERSION, 'apk_sha256': APK_SHA256, 'apk_url': APK_URL,
            'repository': REPOSITORY, **bootstrap, 'packages': records}
    (out / 'termux-prefix.lock.json').write_text(json.dumps(lock, indent=2) + '\n')
    artifact = out / 'termux-prefix.tar.gz'
    # Numeric ownership is applied on-device to the actual Termux UID by firstboot.
    run(['tar', '--sort=name', '--mtime=@0', '--owner=0', '--group=0', '--numeric-owner',
         '--use-compress-program=gzip -n', '-C', str(out / 'prefix'), '-cf', str(artifact), 'usr'], out / 'archive.log')
    scripts = out / 'configuration-scripts'; scripts.mkdir()
    pending = []
    second = prefix / 'etc/termux/bootstrap/termux-bootstrap-second-stage.sh'
    shutil.copyfile(second, scripts / 'bootstrap-second-stage.sh')
    pending.append({'step': 'bootstrap-second-stage', 'sha256': digest(second), 'executed': False})
    added = {x['package'] for x in records}
    for name in sorted({x['Package'] for x in existing} | added):
        source = prefix / 'var/lib/dpkg/info' / (name + '.postinst')
        row = {'package': name, 'origin': 'downloaded-deb' if name in added else 'APK-bootstrap', 'postinst_present': source.is_file(), 'executed': False}
        if source.is_file():
            shutil.copyfile(source, scripts / (name + '.postinst')); row['postinst_sha256'] = digest(source)
        pending.append(row)
    (out / 'configuration-pending.json').write_text(json.dumps(pending, indent=2) + '\n')
    metadata = {x.name: digest(x) for x in (resolver / 'var/lib/apt/lists').iterdir() if x.is_file()}
    report = {'schema': 1, 'artifact_sha256': digest(artifact), 'lock_sha256': digest(out / 'termux-prefix.lock.json'),
              'authenticated_apt_metadata': metadata, 'android_configuration_complete': False,
              'configuration_pending': 'Official bootstrap second stage and added package maintainer scripts require the Termux UID on Android.',
              'packages_added': len(records), 'bootstrap': bootstrap}
    (out / 'termux-prefix-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
