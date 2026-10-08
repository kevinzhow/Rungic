#!/usr/bin/env python3
"""Read-only, exact-release installation checks; desktop scenarios run separately."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shlex
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb-port", type=int, default=5037)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.release / "manifest.json").read_text())
    spec_bytes = (args.release / "device-spec.json").read_bytes()
    if hashlib.sha256(spec_bytes).hexdigest() != manifest["device_spec_sha256"]:
        parser.error("spec does not match release manifest")
    spec = json.loads(spec_bytes)
    if args.serial != manifest["target_serial"]:
        parser.error("serial does not match this release")
    adb = ["adb", "-P", str(args.adb_port), "-s", args.serial]
    checks = []

    def command(script, root=False, timeout=180):
        cmd = adb + ["shell", "-T"] + (["/debug_ramdisk/su", "-c", "sh"] if root else ["sh"])
        result = subprocess.run(cmd, input=script + "\n", capture_output=True,
                                text=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout)[-1200:])
        return result.stdout.strip()

    def check(name, probe, expected=None, predicate=None):
        try:
            actual = probe()
            passed = predicate(actual) if predicate else actual == expected
            checks.append(dict(id=name, passed=bool(passed), observed=actual, expected=expected))
        except Exception as error:
            checks.append(dict(id=name, passed=False, error=str(error)))
        print(("PASS " if checks[-1]["passed"] else "FAIL ") + name, flush=True)

    for prop, expected in {
        "ro.serialno": args.serial,
        "ro.product.device": spec["identity"]["product"],
        "ro.boot.hardware.sku": spec["identity"]["sku"],
        "ro.build.fingerprint": spec["identity"]["fingerprint"],
        "ro.boot.slot_suffix": "_a", "sys.boot_completed": "1",
    }.items():
        check(prop, lambda p=prop: command("getprop " + shlex.quote(p)), expected)
    check("selinux", lambda: command("getenforce"), "Enforcing")
    check("kernel.release", lambda: command("uname -r"),
          manifest.get("kernel_release", spec["kernel"]["stock_release"]))
    for partition, image in (("boot_a", "boot.img"), ("init_boot_a", "init_boot.img"),
                             ("product_a", "product.erofs.img")):
        info = manifest["files"]["images/" + image]
        block_dir = "mapper" if partition == "product_a" else "by-name"
        script = f"set -o pipefail; head -c {int(info['bytes'])} /dev/block/{block_dir}/{partition} | sha256sum | cut -d ' ' -f1"
        check("partition." + partition, lambda s=script: command(s, True, 300), info["sha256"])
    check("seed.complete", lambda: command("cat /data/adb/rungic-firstboot.complete", True),
          manifest["release_id"])
    rootfs_report = json.loads((args.release / "reports/rootfs.json").read_text())
    check("seed.rootfs", lambda: command("cat /data/adb/rungic-lxc/images/rootfs.seeded", True),
          rootfs_report["rootfs_sha256"])
    # The root provider CI1 chose for the device (profiles/devices/*.json "root"; Magisk before it had one).
    provider = spec.get("root", {}).get("provider", "magisk")
    system_packages = set(command("pm list packages -s").splitlines())
    user_packages = set(command("pm list packages -3").splitlines())
    if provider == "magisk":
        check("magisk.environment", lambda: command(
            "MAGISKBIN=/data/adb/magisk; MAGISKTMP=$(/debug_ramdisk/magisk --path); "
            ". $MAGISKBIN/app_functions.sh; env_check 31.0 31000 && echo ready", True), "ready")
        check("magisk.ordinary-app", lambda: "package:com.topjohnwu.magisk" in user_packages and
              "package:com.topjohnwu.magisk" not in system_packages, True)
        def manager_matches():
            path = command("pm path com.topjohnwu.magisk").removeprefix("package:")
            hashes = command("sha256sum " + shlex.quote(path) + " /product/etc/magisk/Magisk.apk", True)
            values = [line.split()[0] for line in hashes.splitlines()]
            return len(values) == 2 and values[0] == values[1]
        check("magisk.full-apk", manager_matches, True)
    else:
        # KernelSU (LKM, docs/121): its userspace at the spec's release, and its manager an ordinary app.
        check("kernelsu.version", lambda: command("/data/adb/ksud --version", True),
              predicate=lambda s: s.split()[:2] == ["ksud", spec["root"]["release"].lstrip("v")])
        check("kernelsu.manager", lambda: "package:me.weishu.kernelsu" in user_packages and
              "package:me.weishu.kernelsu" not in system_packages, True)
    for package in ("com.rungic.plasma", "com.termux"):
        check("preinstall." + package, lambda p=package: command("pm path " + shlex.quote(p)),
              predicate=lambda s: s.startswith("package:/product/app/"))
    installed = set(command("pm list packages").splitlines())
    for item in spec["purity"]["remove_preinstall"]:
        check("removed." + item["package"], lambda p=item["package"]: "package:" + p not in installed, True)
    for package in spec["purity"]["disable_packages"]:
        check("inactive." + package, lambda p=package: command("dumpsys package " + shlex.quote(p)),
              predicate=lambda s: any("User 0:" in line and "installed=false" in line
                                      for line in s.splitlines()))
        # Keep only the user state, not the full PackageManager dump.
        if "observed" in checks[-1]:
            checks[-1]["observed"] = [s.strip() for s in checks[-1]["observed"].splitlines()
                                      if "User 0:" in s and "installed=" in s]
    control = "/data/adb/rungic-plasma/rungic-plasma"
    if rootfs_report.get("account_status_protocol") == 2:
        check("account.protocol", lambda: command(control +
              " exec cat /usr/share/rungic/account-protocol", True), "2")
    check("account.configured", lambda: json.loads(command(control + " account-status", True)),
          predicate=lambda s: s.get("configured") is True and s.get("uid") == 1000
          and s.get("pending") is not True)
    check("container.systemd", lambda: command(control + " exec systemctl is-system-running", True), "running")
    check("packages.audit", lambda: command(control + " exec dpkg --audit", True), "")
    check("python.dependencies", lambda: command(control +
          " exec /usr/lib/rungic-clicker/venv/bin/python -m pip check", True), "No broken requirements found.")
    report = {"schema_version": 1, "release_id": manifest["release_id"], "serial": args.serial,
              "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "passed": all(item["passed"] for item in checks), "checks": checks,
              "scope": "installation checks; cold-boot evidence and desktop scenario report are also required"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
