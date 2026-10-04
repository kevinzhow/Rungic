#!/usr/bin/env python3
"""Package the reusable Android-side LXC controller and Rungic entry files."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from build_rootfs_image import check_home_layout, check_fresh_account

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import cast_payload
ANDROID_FILES = (
    ("system/rungic-plasma", "rungic-plasma/rungic-plasma", 0o755),
    ("system/android-device", "rungic-plasma/android-device", 0o755),
    ("system/android-calls", "rungic-plasma/android-calls", 0o755),
    ("system/android-clipboard", "rungic-plasma/android-clipboard", 0o755),
    ("system/android-audio", "rungic-plasma/android-audio", 0o755),
    ("system/android-audio.pa", "rungic-plasma/android-audio.pa", 0o644),
    ("system/rootfs-image", "rungic-plasma/rootfs-image", 0o755),
    ("system/rootfs.sepolicy.rule", "rungic-plasma/rootfs.sepolicy.rule", 0o644),
    ("system/rootfs-mount-hook", "rungic-plasma/rootfs-mount-hook", 0o755),
    ("system/plasma.config", "rungic-lxc/runtime/var/lib/lxc/plasma/config", 0o644),
    ("lxc/rungic-lxc", "rungic-lxc/rungic-lxc", 0o755),
    # Casting (docs/58); firstboot moves service.d/ into /data/adb/service.d.
    *((source, "rungic-wfd/" + dest, mode) for source, dest, mode in cast_payload.FILES),
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(*args):
    subprocess.run(args, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--rootfs-tree", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--lxc-enter", type=Path, required=True)
    parser.add_argument("--plasma-enter", type=Path, required=True)
    parser.add_argument("--cast-jar", type=Path, required=True,
                        help="rungic-cast.jar from shared/android/rungic-cast/build.sh")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = {name: getattr(args, name).resolve(strict=name != "output")
             for name in ("runtime", "rootfs_tree", "repo", "lxc_enter", "plasma_enter", "cast_jar",
                          "output")}
    if not args.inside:
        cast_payload.validate_build(paths["cast_jar"])
        command = ["podman", "unshare", sys.executable, __file__, "--inside"]
        for name, path in paths.items():
            command += ["--" + name.replace("_", "-"), str(path)]
        os.execvp(command[0], command)
    if paths["output"].exists():
        raise ValueError("host seed output already exists")
    cast_build = cast_payload.validate_build(paths["cast_jar"])
    if not (paths["runtime"] / "usr/bin/lxc-start").is_file():
        raise ValueError("LXC manager runtime has no lxc-start")
    home = paths["rootfs_tree"] / "home/rungic"
    check_home_layout(paths["rootfs_tree"], "/home/rungic")
    check_fresh_account(paths["rootfs_tree"])
    if home.stat().st_uid != 1000 or home.stat().st_gid != 1000:
        raise ValueError("desktop home seed must be owned by UID/GID 1000")
    stage = paths["output"].parent / "host-tree"
    if stage.exists():
        raise ValueError("host staging tree already exists")
    runtime = stage / "rungic-lxc/runtime"
    runtime.mkdir(parents=True)
    run("rsync", "-aHAX", "--numeric-ids", str(paths["runtime"]) + "/", str(runtime) + "/")
    plasma = runtime / "var/lib/lxc/plasma"
    (plasma / "rootfs").mkdir(parents=True, exist_ok=True)
    for name in ("home", "host", "rungic-cores", "rungic-apt"):
        (plasma / "state" / name).mkdir(parents=True, exist_ok=True)
    run("rsync", "-aHAX", "--numeric-ids", str(paths["rootfs_tree"] / "home") + "/",
        str(plasma / "state/home") + "/")
    check_home_layout(plasma / "state", "/home/rungic")
    if os.path.lexists(plasma / "state/host/account.json"):
        raise ValueError("host runtime seed contains an existing account marker")
    run("rsync", "-aHAX", "--numeric-ids", str(paths["repo"]) + "/",
        str(plasma / "state/rungic-apt") + "/")
    for name in ("plasma-shared", "plasma-wayland", "plasma-audio"):
        (runtime / "mnt" / name).mkdir(parents=True, exist_ok=True)
    for source, dest, mode in ANDROID_FILES:
        target = stage / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / source).read_bytes())
        target.chmod(mode)
    for source, dest, mode in (("lxc_enter", "rungic-lxc/rungic-lxc-enter", 0o755),
                               ("plasma_enter", "rungic-plasma/rungic-plasma-enter", 0o755),
                               ("cast_jar", "rungic-wfd/rungic-cast.jar", 0o644)):
        target = stage / dest
        target.write_bytes(paths[source].read_bytes())
        target.chmod(mode)
    cast_payload.manifest(stage / "rungic-wfd")
    paths["output"].parent.mkdir(parents=True, exist_ok=True)
    run("tar", "-C", str(stage), "--numeric-owner", "-czf", str(paths["output"]),
        "rungic-lxc", "rungic-plasma", "rungic-wfd")
    report = {"schema_version": 1, "arch": "aarch64", "home_layout_checked": True,
              "fresh_account_checked": True,
              "controller": "Alpine LXC",
              "lxc_version": "6.0.4-r0", "runtime_origin": str(paths["runtime"]),
              "runtime_sha256": sha256(paths["runtime"] / "usr/bin/lxc-start"),
              "repo_release_sha256": sha256(paths["repo"] / "Release"),
              "lxc_enter_sha256": sha256(paths["lxc_enter"]),
              "plasma_enter_sha256": sha256(paths["plasma_enter"]),
              "cast_jar_sha256": sha256(paths["cast_jar"]),
              "cast_build": cast_build,
              "archive_bytes": paths["output"].stat().st_size,
              "archive_sha256": sha256(paths["output"])}
    (paths["output"].parent / "host-seed-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"host seed build failed: {error}")
