#!/usr/bin/env python3
# covers: install.first-run-progress/E6
"""Use case (docs/95): Rungic's app data is cleared after installation, then the app is opened.

Runs the controller's install-publish action and the real first-boot script in a temporary
path sandbox, for a standalone release on a reused base that still has an old product seed,
a legacy product install, an install in progress and a base without any managed install.
The APK's reading of these files is FirstBootStateTest; labels and su need the device test.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
FIRSTBOOT = HERE / "rungic-firstboot.sh"
CONTROLLER = HERE.parents[1] / "system/rungic-plasma"
APP = "com.rungic.plasma"


class InstallRepublishTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        stubs = self.root / "bin"
        stubs.mkdir()
        (stubs / "chcon").write_text("#!/bin/sh\n:\n")
        magisk = self.root / "debug_ramdisk/magisk"      # Magisk's runtime is up
        magisk.parent.mkdir(parents=True)
        magisk.write_text("#!/bin/sh\nexit 0\n")
        magisk.chmod(0o755)
        busybox = self.root / "data/adb/magisk/busybox"
        busybox.parent.mkdir(parents=True)
        # busybox flock -n LOCK sh SCRIPT SEED: run the command without the lock.
        busybox.write_text('#!/bin/sh\nshift 3\nexec "$@"\n')
        for tool in (stubs / "chcon", busybox):
            tool.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{stubs}:{os.environ['PATH']}")
        self.files = self.root / f"data/user/0/{APP}/files"
        self.files.mkdir(parents=True)
        self.payload = self.root / "data/adb/rungic-install/payload"
        self.product = self.root / "product/etc/rungic"

    def sandbox(self, text):
        text = re.sub(r"(?<![\w/])(/data/adb|/data/user/0|/product/etc/rungic|/debug_ramdisk)\b", f"{self.root}\\1", text)
        text = text.replace("/system/bin/sh", "bash")
        return text.replace("export PATH=", "export IGNORED_PATH=")

    def install(self, where, release):
        where.mkdir(parents=True, exist_ok=True)
        (where / "seed.env").write_text(f"RELEASE_ID='{release}'\n")
        script = where / "firstboot.sh"
        script.write_text(self.sandbox(FIRSTBOOT.read_text()))

    def standalone(self, release, complete=True):
        self.install(self.payload, release)
        (self.payload.parent / "active.env").write_text(f"RELEASE_ID={release}\n")
        if complete:
            (self.root / "data/adb/rungic-firstboot.complete").write_text(release + "\n")

    def publish_action(self):
        text = CONTROLLER.read_text()
        body = re.search(r"\n  install-publish\)\n(.*?)\n    ;;\n", text, re.S)
        self.assertIsNotNone(body, "install-publish action not found in system/rungic-plasma")
        script = "set -eu\n" + self.sandbox(body.group(1)) + "\n"
        return subprocess.run(["bash", "-c", script], env=self.env, capture_output=True, text=True, timeout=30)

    def status(self):
        path = self.files / "rungic-install.properties"
        return dict(line.split("=", 1) for line in path.read_text().splitlines()) if path.exists() else None

    def source(self):
        path = self.files / "rungic-install-source.properties"
        return path.read_text() if path.exists() else None

    def test_standalone_on_reused_base_republishes_status_and_source(self):
        self.install(self.product, "legacy-old")
        self.standalone("standalone-new")
        result = self.publish_action()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.source(), "RELEASE_ID=standalone-new\n")
        status = self.status()
        self.assertEqual((status["release"], status["state"], status["phase"]), ("standalone-new", "ready", "complete"))
        # The app names the provider when su is refused (RootAccess, install.desktop-entry/E7).
        self.assertEqual(status["root"], "magisk")

    def test_status_names_kernelsu_when_it_is_the_active_provider(self):
        (self.root / "debug_ramdisk/magisk").unlink()
        ksud = self.root / "data/adb/ksud"
        ksud.write_text("#!/bin/sh\nexit 0\n")
        busybox = self.root / "data/adb/ksu/bin/busybox"
        busybox.parent.mkdir(parents=True)
        busybox.write_text((self.root / "data/adb/magisk/busybox").read_text())
        for tool in (ksud, busybox):
            tool.chmod(0o755)
        self.standalone("standalone-new")
        result = self.publish_action()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.status()["state"], self.status()["root"]), ("ready", "kernelsu"))

    def test_legacy_product_republishes_status_and_drops_foreign_source(self):
        self.install(self.product, "legacy-new")
        (self.root / "data/adb/rungic-firstboot.complete").write_text("legacy-new\n")
        (self.files / "rungic-install-source.properties").write_text("RELEASE_ID=removed-standalone\n")
        result = self.publish_action()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(self.source())
        self.assertEqual(self.status()["release"], "legacy-new")

    def test_install_in_progress_is_left_to_first_boot(self):
        self.standalone("standalone-new", complete=False)
        result = self.publish_action()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not complete", result.stdout)
        self.assertIsNone(self.status())
        self.assertIsNone(self.source())

    def test_base_without_managed_install(self):
        result = self.publish_action()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No managed installation", result.stdout)
        self.assertEqual(list(self.files.iterdir()), [])

    def test_every_first_boot_run_republishes_the_source(self):
        # A reboot after cleared data takes the boot-time path, not install-publish.
        self.install(self.product, "legacy-old")
        self.standalone("standalone-new")
        result = subprocess.run(["bash", str(self.payload / "firstboot.sh"), str(self.payload)],
                                env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.source(), "RELEASE_ID=standalone-new\n")
        self.assertEqual(self.status()["state"], "ready")


if __name__ == "__main__":
    unittest.main()
