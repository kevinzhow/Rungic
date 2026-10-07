# Tool map and implementation limits

The independent-installation review dates from 2026-09-30.
The default stages are device base/GKI, independent OS image, and separate Rungic installation/upgrade.
This map distinguishes current tools from old full-bundle paths.
It is not a universal command sequence for every phone.
All paths are relative to the repository root.

## Source and tool entry points

| Stage | Current entry point | Scope |
|---|---|---|
| Stock extraction/checks | `tools/prepare_g100_stock.py`, `tools/verify_g100_stock.py` | Default portov. For new devices, use reviewed `--identity`, `--expected-fingerprint`, and `--logical-partitions`. Check the actual format. |
| Device/input preflight | `tools/ci/preflight.py` | v1 spec and checked OEM manifest/verification. Requires authorized ADB and matching stock state. This is not a bootloader-only prerequisite. |
| Component fingerprints/cache | `tools/build_artifact.py` | Check input fingerprints and output digests. New independent bundles require a build plan. See docs/94 for binary-baseline limits. |
| Upstream recipes | `packages/*/recipe.json`, `tools/pq.py` | Use prepare/export patch queues and the current CLI. |
| GKI build inputs | `packages/gki-android15-6.6/recipe.json`, `packages/gki-android16-6.12/recipe.json`, `kernel/targets/gki/`, `kernel/README.md` | Select pinned source, manifest, fragments, and symbol tables for the target. Do not transfer patch conclusions across kernel generations. |
| ABI | `tools/ci/module_abi.py` | Supports legacy/extended modversions. Compare symvers with OEM modules. Retain limits for uncovered references. |
| Module trust | `tools/ci/restore_module_trust.py` | Checks baseline/certificates and produces reports. Its restoration method does not automatically apply to arbitrary Images. |
| ARM64 packages | `tools/build_on_device.py`, `tools/rungic_release.py` | Pinned recipe builds, collect, and versioned package sets. See docs/77 for repository-snapshot maturity. |
| ARM64 rootfs installation | `tools/ci/prepare_rootfs.py`, `tools/ci/arm64_chroot.py`, `tools/ci/rootfs.Dockerfile` | Fresh root, configuration owner first, file-based installer with closed stdin, final source/release receipt. Native ARM64 or a QEMU/binfmt runner; check namespaces and capacity. Failed output directories cannot resume. |
| Rootfs image | `tools/ci/build_rootfs_image.py` | Packages a prepared root tree/release into ext4, compressed seeds, package locks, and reports. Check `system/ubuntu-excluded-packages.txt` and Emoji Selector exclusion. This is not a complete package downloader. |
| APK | `android/build-apk.sh`, `tools/ci/apk-builder.Dockerfile` | Builds the Android entry point. Preserve the specified development signing identity. Exclude other credentials. |
| Host seed | `tools/ci/build_host_seed.py` | Inputs: runtime, rootfs-tree, repo, lxc/plasma enter binaries, `--cast-jar` from `shared/android/rungic-cast/build.sh`. Casting is optional. First-boot casting installation failure produces a log only. |
| Independent first installation | `tools/ci/standalone.py pack/verify/install/status` | Developer USB/ADB entry. Requires exact serial/port, trusted manifest SHA, and matching boot. Rejects existing runtime replacement. Retries only the same payload. See docs/91 and docs/92. |
| Old bundle: clean product | `tools/ci/clean_product.py` | Assumes EROFS and product/preinstall naming, xattrs, and SKU policy. |
| Old bundle: complete product | `tools/ci/assemble_product.py` | Adds APK/JNI, seeds, first boot, and permissions. Inputs must match spec/capacity. |
| Old bundle: Magisk startup | `tools/ci/inject_magisk_seed.py` | Inserts bootstrap into a correctly patched init_boot. Does not perform general root patching. |
| Old bundle: assembly | `tools/ci/assemble_release.py` | Cross-checks reports, packages installer/fastboot, and creates manifest. Layout remains specific to verified implementations. |
| Old bundle: flashing | Generated `flash.sh` / `flash.py` | Source `tools/ci/flash_release.py` requires the adjacent manifest. Do not flash directly from the source directory. |
| Old bundle: installation checks | `tools/ci/accept_release.py` | Checks current conventions for specified release/serial/ADB port. Does not replace first-account setup or actual desktop evidence. |

## Current independent-installation gaps

`rungic_release.py deploy/rollback` provides versioned APT updates for existing Rungic installations.
`standalone.py` provides independent first installation on compatible bases.
The X70 test reused existing Termux/prefix and product APKs.
It checked ordinary ADB update installation, blank runtime/accounts, and reboot takeover.

A later test reflashed Android and removed data.
It installed ordinary Rungic/Termux with a new prefix/runtime on a base without old product apps.
See [docs/92](../../../../docs/92-x70-android-base-end-to-end.md).

User self-installation, offline Magisk readiness, full rootfs upgrade, and general rollback remain unverified.
The tool does not automatically migrate existing user data.
Do not bypass rejection of an existing runtime.
See [docs/91](../../../../docs/91-x70-independent-install.md) for parameters and backup limits.

## Historical full bundles: recheck G100 assumptions

Current `assemble_release.py` / `flash_release.py` assume:

- `super.img_sparsechunk.*` names and OEM manifest structure.
  They require vendor_boot/dtbo/recovery/pvmfw and related inputs.
- Slot a, `product_a`, `boot_a`, `init_boot_a`, and specific vbmeta partitions.
  They restore super before writing product.
- Motorola `oem fb_mode_clear`, bootloader version representation, fastbootd transitions, and device responses.
- A specific flags=3 derivation from stock vbmeta.
  It is not a default AVB policy for other devices and does not authorize bootloader relocking.
- A code-constant bootloader voltage threshold of 3700 mV.
  This is separate from spec-based preflight battery percentage.
- Linux x86_64, Python 3, bundled fastboot, Magisk-patched init_boot, and the existing first-boot mechanism.

If any assumption fails for a new device, extend configuration/adapters and checks first.
Do not merely change JSON identity and run the old flasher.
G100 input has 32 chunks.
X70 input has 41.
Explicit identity supplies the extractor's expected count.
The assembler enumerates checked inputs.

`--fastboot-adapter` supports exact bootloader/securestate differences and checked modes.
The installer still operates on slot a.
It is not an arbitrary flash-plan engine.

## Parameterized examples

Bind variables to checked inputs for this task first.
Do not use old device serials or firmware as defaults.
From the repository root, run `source tools/work-env.sh` to keep caches in `.work/`.
Read each tool's `--help` for current parameters.

Before reusing an old root tree, remove packages listed in `system/ubuntu-excluded-packages.txt` inside the build chroot.
Install the current release's `rungic-plasma-config` before creating the image.
Updating only the positive installation list is insufficient.
See [preinstallation changes and checked scope](../../../../docs/75-image-build-separation.md#2026-09-30预装应用调整).

```bash
python3 tools/ci/preflight.py "$device_spec" "$stock_dir" \
  --serial "$device_serial" --adb-port "$adb_port" --output "$run_dir/preflight.json"

python3 tools/ci/module_abi.py "$kernel_symvers" "$oem_modules" \
  --output "$run_dir/kernel-abi.json"

python3 tools/ci/build_rootfs_image.py --root "$rootfs_tree" \
  --release "$package_release" --output "$rootfs_output" \
  --size-gib "$rootfs_size_gib" --firefox-version "$firefox_version"
```

Use old assemblers and flash entry points only for explicitly selected historical/recovery tasks.
Required assembler paths are:

- `--spec`, `--stock`, `--product-image`, `--product-report`.
- `--boot`, `--init-boot`, `--init-boot-report`.
- `--rootfs-report`, `--host-report`, `--kernel-abi-report`, `--package-lock`.
- `--img2simg`, `--fastboot`, `--output`.

Also provide the actual `--serial`, `--fastboot-bootloader-value`, and `--release-id`.
The assembler collects some payloads through hardlinks.
Inputs and outputs must share a filesystem that supports them.
Recheck integrity after cross-filesystem archiving.

The assembler checks multiple digests.
Its ABI report does not automatically establish all source relationships to the final boot image.
Connect kernel outputs, packaging, trust reports, and final digests explicitly.
An assembler exit code of 0 does not replace missing provenance.

Run offline checks only through the generated bundle:

```bash
bash "$release_dir/flash.sh" --verify-only
```

Run actual flashing through the bundle entry only after authorization and target checks.
`--yes-wipe` actually removes data.
Do not use it as a probe or harmless dry run.
`--verify-only` does not access the device or establish its identity.

Post-flash diagnosis requires normal Android ADB/root.
It must not become a mandatory manual step for a user's first installation:

```bash
python3 tools/ci/accept_release.py "$release_dir" \
  --serial "$device_serial" --adb-port "$adb_port" \
  --output "$run_dir/install-acceptance.json"
```

## Choose checks by changed scope

- Startup: `tools/ci/test_magisk_bootstrap.py` and applicable shell syntax checks.
- Host seed/first-boot casting installation: `tools/ci/test_firstboot_cast.py`.
- Flasher: isolated fake-device checks in `tools/ci/test_flash_progress.py`.
  New layouts also need plan and failure scenarios.
- APK/first-boot state: check `FirstBootState.java` and the actual shared controller entry.
  Missing, old-release, or failed state must prevent entry.
  Ready state must precede account preparation.
- Images: filesystem checks, package checks, manifest readback, and authorized device acceptance after data removal.
- Build environment/templates: `tools/ci/test_rootfs_isolation.py` and rootfs/host home/unconfigured-account checks.
  Directory preparation uses `tools/test_user_dirs.py`.
  See [build isolation](build-isolation.md) for entry points.

Isolated tests and controlled UI state do not replace the applicable first-installation evidence.
Record independent installation, upgrade, and old full-bundle data-removal acceptance separately.
Do not run phone tests or reflash devices for documentation or skill changes.
