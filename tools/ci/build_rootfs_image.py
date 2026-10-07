#!/usr/bin/env python3
"""Turn an installed ARM64 Rungic root tree into a checked, sparse ext4 image."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


EXCLUDES = (
    "/dev/***", "/proc/***", "/sys/***", "/run/***", "/tmp/***",
    "/target/***", "/host-rootfs/***", "/root/.cache/***",
    "/var/tmp/***", "/var/log/***", "/var/cache/apt/archives/***",
    "/var/lib/apt/lists/***", "/var/lib/rungic-apt/***",
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def packages(status):
    installed = {}
    for stanza in status.read_text().split("\n\n"):
        fields = {}
        for line in stanza.splitlines():
            if line and not line[0].isspace() and ": " in line:
                key, value = line.split(": ", 1)
                fields[key] = value
        if fields.get("Status") == "install ok installed":
            installed[fields["Package"]] = (fields["Version"], fields.get("Architecture", ""))
    return installed


def write_release_preferences(root, manifest):
    """A reused build tree may still pin an older release (docs/92)."""
    versions = {**manifest["packages"], "rungic-release": manifest["version"]}
    lines = ["# Rungic image release " + manifest["version"]]
    for name, version in sorted(versions.items()):
        lines += ["", f"Package: {name}", f"Pin: version {version}", "Pin-Priority: 1001"]
    path = root / "etc/apt/preferences.d/rungic-release"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o644)


def run(*args):
    subprocess.run(args, check=True)


def check_preinstalled_apps(root, installed):
    """Do not silently bring removed applications back through a reused root tree."""
    workspace = Path(__file__).resolve().parents[2]
    excluded = {line.strip() for line in
                (workspace / 'system/ubuntu-excluded-packages.txt').read_text().splitlines()
                if line.strip() and not line.lstrip().startswith('#')}
    unwanted = excluded.intersection(name.split(':')[0] for name in installed)
    if unwanted:
        raise ValueError('purge excluded preinstalled apps in the build root before imaging: ' +
                         ', '.join(sorted(unwanted)))
    config = Path('etc/dpkg/dpkg.cfg.d/zz-rungic-apps')
    required = {line for line in (workspace / 'system/config' / config).read_text().splitlines()
                if line.startswith('path-exclude=')}
    actual = set((root / config).read_text().splitlines()) if (root / config).is_file() else set()
    if not required.issubset(actual):
        raise ValueError('rebuild and install rungic-plasma-config: app exclusion rules missing')
    remaining = [line.split('=', 1)[1] for line in sorted(required)
                 if os.path.lexists(root / line.split('=', 1)[1].lstrip('/'))]
    if remaining:
        raise ValueError('excluded app files remain; reconfigure rungic-plasma-config: ' +
                         ', '.join(remaining))


def check_home_layout(root, home_path):
    """Reject build residue or extra homes instead of reserving user logins."""
    if not re.fullmatch(r"/home/[a-z][a-z0-9_-]{0,31}", home_path):
        raise ValueError("desktop home must be a direct /home/<login> directory")
    parent = root / "home"
    home = root / home_path.lstrip("/")
    if parent.is_symlink() or home.is_symlink() or not home.is_dir():
        raise ValueError("desktop home must be a real directory inside the root tree")
    unexpected = []
    for entry in parent.iterdir():
        if entry == home:
            continue
        # The legacy login alias is intentional, but must point to this template.
        if entry.name == "linux" and entry.is_symlink() and str(entry.readlink()) in (home.name, home_path):
            continue
        unexpected.append(entry.name)
    if unexpected:
        raise ValueError("unexpected /home entries (possible build residue): " +
                         ", ".join(sorted(unexpected)))


def check_fresh_account(root):
    """A first-install image must not carry a configured person's identity."""
    accounts = [line.split(":") for line in (root / "etc/passwd").read_text().splitlines()]
    regular = [entry for entry in accounts if 1000 <= int(entry[2]) < 65534]
    if len(regular) != 1 or regular[0][2] != "1000":
        raise ValueError("first-install image needs exactly one UID 1000 template account")
    shadow = {entry[0]: entry[1] for entry in
              (line.split(":") for line in (root / "etc/shadow").read_text().splitlines())}
    for name in ("root", regular[0][0]):
        # Do not include passwords or hashes in exceptions or reports.
        value = shadow.get(name, "")
        if not value.startswith(("!", "*")):
            raise ValueError("first-install root and template passwords must be locked")
    for rel in ("var/lib/rungic-host/account.json", "etc/moto-plasma/account.json"):
        if os.path.lexists(root / rel):
            raise ValueError("first-install image contains an account completion marker")
    for rel in ("root", regular[0][5].lstrip("/")):
        home = root / rel
        for pattern in (".ssh/id_*", ".ssh/authorized_keys", ".netrc", ".codex/auth.json",
                        ".aws/credentials", ".config/gcloud/credentials.db"):
            if any(home.glob(pattern)):
                raise ValueError("first-install image contains user credentials")


def check_install_completion(root, release, source_commit):
    """Require the final installation receipt for the fresh-root build path."""
    try:
        receipt = json.loads((root / "var/lib/rungic-apt/root-install.complete").read_text())
        manifest = json.loads(release.read_text())
        expected = {"schema": 1, "source_commit": source_commit,
                    "release": manifest["version"], "release_sha256": sha256(release),
                    "dpkg_status_sha256": sha256(root / "var/lib/dpkg/status"),
                    "all_installation_steps_completed": True}
        if not re.fullmatch(r"[0-9a-f]{40}", source_commit) or any(
                receipt.get(key) != value for key, value in expected.items()):
            raise ValueError("installation completion receipt does not match source, release or root")
    except (OSError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError("installation completion receipt is missing or invalid") from error
    return receipt


def installation_provenance(root, release, source_commit):
    if source_commit:
        receipt = check_install_completion(root, release, source_commit)
        return {"source_commit": receipt["source_commit"],
                "sha256": sha256(root / "var/lib/rungic-apt/root-install.complete")}
    if os.path.lexists(root / "var/lib/rungic-apt/root-install.complete"):
        raise ValueError("unverified root must not carry an installation completion receipt")
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size-gib", type=int, default=16)
    parser.add_argument("--firefox-version", required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--unverified-root", action="store_true", help="Explicitly compose a historical binary root without complete installation proof")
    modes.add_argument("--install-source", help="Require fresh installation completion for this source SHA")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    release = args.release.resolve(strict=True)
    output = args.output.resolve()
    if not args.inside:
        os.execvp("podman", ["podman", "unshare", sys.executable, __file__, "--inside",
                            "--root", str(root), "--release", str(release),
                            "--output", str(output), "--size-gib", str(args.size_gib),
                            "--firefox-version", args.firefox_version,
                            *(["--install-source", args.install_source] if args.install_source else ["--unverified-root"])])
    if output.exists() or args.size_gib < 8 or args.size_gib > 128:
        raise ValueError("output exists or image size is outside 8–128 GiB")
    install_receipt = installation_provenance(root, release, args.install_source)
    installed = packages(root / "var/lib/dpkg/status")
    check_preinstalled_apps(root, installed)
    manifest = json.loads(release.read_text())
    expected = dict(manifest["packages"], firefox=args.firefox_version,
                    **{"rungic-release": manifest["version"]})
    mismatched = {name: {"want": version, "have": installed.get(name)}
                  for name, version in expected.items()
                  if installed.get(name, (None,))[0] != version}
    if mismatched:
        raise ValueError(f"release package mismatch: {mismatched}")
    protocol_path = root / "usr/share/rungic/account-protocol"
    if not protocol_path.is_file() or protocol_path.read_text().strip() != "2":
        raise ValueError("rootfs needs account protocol 2; rebuild rungic-plasma-session before composing the new launcher")
    account = next((line.split(":") for line in (root / "etc/passwd").read_text().splitlines()
                    if line.split(":")[2] == "1000"), None)
    if account is None or not account[5].startswith("/home/"):
        raise ValueError("rootfs has no regular UID 1000 desktop account")
    home = root / account[5].lstrip("/")
    check_home_layout(root, account[5])
    check_fresh_account(root)
    if home.stat().st_uid != 1000 or home.stat().st_gid != 1000:
        raise ValueError(f"desktop home owner is not 1000:1000: {home}")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / "root-tree"
    if stage.exists():
        raise ValueError(f"stage already exists: {stage}")
    stage.mkdir()
    run("rsync", "-aHAX", "--numeric-ids", *("--exclude=" + path for path in EXCLUDES),
        str(root) + "/", str(stage) + "/")
    write_release_preferences(stage, manifest)
    check_home_layout(stage, account[5])
    check_fresh_account(stage)
    for name, mode in (("dev", 0o755), ("proc", 0o555), ("sys", 0o555),
                       ("run", 0o755), ("tmp", 0o1777), ("var/tmp", 0o1777),
                       ("var/log", 0o755), ("var/log/plasma", 0o755),
                       ("var/cache/apt/archives", 0o755),
                       ("var/lib/apt/lists", 0o755), ("var/lib/rungic-apt", 0o755)):
        path = stage / name
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(mode)
    # systemd generates a device-unique ID at first boot; never clone the build host ID.
    # Generated by the installer on the target; never clone the build's SSH identity.
    for path in (stage / "etc/ssh").glob("ssh_host_*_key*"):
        path.unlink()
    for name in ("etc/machine-id", "var/lib/dbus/machine-id"):
        path = stage / name
        if path.is_file() and not path.is_symlink():
            path.write_bytes(b"")
    # Android owns the physical network. A first-boot adapter replaces this placeholder.
    (stage / "etc/resolv.conf").write_text("# Set from Android network on first boot\n")
    lock = "".join(f"{name}\t{version}\t{arch}\n"
                   for name, (version, arch) in sorted(installed.items()))
    (output.parent / "packages.lock.tsv").write_text(lock)
    with output.open("wb") as image:
        image.truncate(args.size_gib * 1024 ** 3)
    run("mkfs.ext4", "-F", "-q", "-L", "RungicOS", "-m", "0",
        "-E", "lazy_itable_init=0,lazy_journal_init=0", "-d", str(stage), str(output))
    check = subprocess.run(["e2fsck", "-fn", str(output)], capture_output=True, text=True)
    if check.returncode not in (0, 1):
        raise RuntimeError(f"e2fsck failed: {check.stdout[-2000:]} {check.stderr[-2000:]}")
    compressed = output.with_suffix(".img.gz")
    with compressed.open("wb") as destination:
        subprocess.run(["gzip", "-1", "-n", "-c", str(output)], stdout=destination, check=True)
    report = {"schema_version": 1, "install_receipt": install_receipt, "account_status_protocol": 2, "home_layout_checked": True,
              "fresh_account_checked": True, "preinstalled_apps_checked": True,
              "release_version": manifest["version"],
              "release_sha256": sha256(release), "arch": "arm64",
              "package_count": len(installed), "package_lock_sha256": sha256(output.parent / "packages.lock.tsv"),
              "rootfs_bytes": output.stat().st_size, "rootfs_sha256": sha256(output),
              "compressed_bytes": compressed.stat().st_size, "compressed_sha256": sha256(compressed),
              "filesystem_check": check.returncode}
    (output.parent / "rootfs-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(f"rootfs image build failed: {error}")
