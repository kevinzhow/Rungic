#!/usr/bin/env python3
"""Run ARM64 rootfs commands in a private user/mount namespace with QEMU binfmt."""

import argparse
import os
import platform
from pathlib import Path
import subprocess
import sys
import tempfile


def run(*argv):
    subprocess.run(argv, check=True)


def guest_environment(host):
    # Host work-env.sh redirects Python bytecode beneath the developer's home.
    # Inheriting that absolute path creates a false /home/<login> in the image.
    # Guest-specific Python settings can still be supplied via /usr/bin/env.
    environment = {key: value for key, value in host.items()
                   if not key.startswith("PYTHON")}
    environment.update(PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                       HOME="/root", DEBIAN_FRONTEND="noninteractive")
    return environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--rootfs", type=Path, required=True)
    runner = parser.add_mutually_exclusive_group(required=True)
    runner.add_argument("--qemu", type=Path)
    runner.add_argument("--native", action="store_true", help="Use a native ARM64 runner without QEMU")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    rootfs = args.rootfs.resolve(strict=True)
    qemu = args.qemu.resolve(strict=True) if args.qemu else None
    if args.native and platform.machine() not in ("arm64", "aarch64"):
        raise ValueError("native ARM64 chroot requires an ARM64 runner")
    runner_args = ["--qemu", str(qemu)] if qemu else ["--native"]
    if not args.inside:
        # Podman's rootless user namespace maps the build user's UID to root
        # and subuids to guest users; package postinst scripts can chown files.
        os.execvp("podman", ["podman", "unshare", "unshare", "-mpf",
                            sys.executable, __file__, "--inside", "--rootfs", str(rootfs),
                            *runner_args, *args.command])
    # argparse REMAINDER treats a standalone -- as an argument.
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not command[0].startswith("/"):
        raise ValueError("command must begin with an absolute path inside the ARM64 rootfs")
    run("mount", "--make-rprivate", "/")
    mountdir = tempfile.mkdtemp(prefix="rungic-binfmt-")
    mounted = []
    try:
        if qemu:
            run("mount", "-t", "binfmt_misc", "binfmt_misc", mountdir)
            mounted.append(mountdir)
        # ARM64 ELF e_machine is 0x00b7. The F flag keeps QEMU open across chroot.
        if qemu:
            rule = b":rungic-aarch64:M:18:\xb7\x00::" + os.fsencode(qemu) + b":F\n"
            (Path(mountdir) / "register").write_bytes(rule)
        # A sanitized image tree has an empty /dev. Package scripts need actual
        # devices: redirecting to a missing /dev/null silently creates a file.
        # This bind lives only in our private mount namespace and is not imaged.
        run("mount", "--rbind", "/dev", str(rootfs / "dev"))
        mounted.append(str(rootfs / "dev"))
        run("mount", "-t", "proc", "proc", str(rootfs / "proc"))
        mounted.append(str(rootfs / "proc"))
        return subprocess.run(["chroot", str(rootfs), *command],
                              env=guest_environment(os.environ), check=False).returncode
    finally:
        for path in reversed(mounted):
            subprocess.run(["umount", "-l", path], check=False)
        Path(mountdir).rmdir()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"ARM64 chroot failed: {error}", file=sys.stderr)
        sys.exit(1)
