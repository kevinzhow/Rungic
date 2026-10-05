---
name: rungic-three-stage-image
description: >-
  Prepare a device-specific GKI/base, build an independent RungicOS rootfs, and install or upgrade Rungic on a compatible Android base.
  Use for new devices, staged builds, installation, and first-boot diagnosis.
  Complete Android flashing bundles apply only to explicitly requested historical or recovery workflows.
---

# Rungic three-stage builds and independent installation

The user changed the target on 2026-09-30:

1. CI1: device base and GKI.
2. CI2: independent RungicOS image.
3. CI3: independent Rungic installation or upgrade.

A combined Android/kernel/Rungic flashing bundle is no longer the default delivery.
When the existing base is compatible, reuse it.
Rungic updates do not normally reflash Android partitions or remove Android data.
See the [contract and tool gaps](../../../docs/75-image-build-separation.md#2026-09-30rungic-独立安装的三段式目标).

This is a project skill.
Source paths are relative to the repository root.
Read `AGENTS.md` first.
Track source, specs, and records in Git.
Keep artifacts, caches, logs, and credentials only in `.work/`.
Retain the skill name for existing callers.

## Start the task

1. Identify whether the task covers base preparation, OS building, first installation, upgrade, or recovery.
   Follow the user's existing goal and authorization.
   A build request does not authorize device writes.
   Independent installation does not authorize Android reflashing, unlocking, or data removal.
   Handle explicitly requested base flashing according to the device.
2. Read `profiles/devices/<vendor>/<device>/<firmware>.json`, its knowledge record, and relevant device evidence.
   For new devices or firmware changes, read [device onboarding](references/device-onboarding.md).
   Before execution, read the [tool map](references/tool-map.md).
   For first-boot failures, read the [first-boot guide](references/first-boot.md).
   Historical bundle acceptance does not establish acceptance of the new independent installer.
3. Check local and remote identity, architecture, routes, and proxies as `AGENTS.md` requires.
   Before adaptation, check pinned upstream source, comparable solutions, licenses, and local interfaces.
   Record why you reuse or change a component.
   Put device differences in specs or adapters.
   Do not copy G100 slot or partition commands.
4. Record source SHA, spec SHA, stage artifact digests, package locks, and task scope in `.work/ci/runs/<run-id>/`.
   Preserve a patch for uncommitted source changes.
   CI3 must identify the actual base version and capabilities.
   An unchanged CI1 need not run again.
   A matching model name alone does not establish compatibility.
5. Read [kernel compatibility](references/kernel-compatibility.md) for ABI, CRC, or Rust differences.
   Read [build isolation](references/build-isolation.md) for rootfs/host payloads, account templates, and cleanup.
   Keep device-specific values in spec records.
   Put reusable experience in the appropriate reference.

## CI1: device base and GKI

Start from the device's stock firmware.
Pin these inputs:

- ACK/Kleaf and toolchain.
- Page size, kernel configuration, and patches.
- Boot format.

Check OEM module ABI/CRC, signature trust, and the root provider.
Add only the capabilities that Rungic/LXC requires.
Maintain modified upstream components through patch queues in `packages/`.

Use a candidate boot or flashing method already checked for this device.
Do not assume `fastboot boot` works.
Check Android boot, SELinux, storage, LXC mechanisms, and the selected hardware backends.

Deliver GKI/boot candidates, required base preparation and recovery materials, sources, and a compatibility report.
Unlocking or a particular base installation can remove data.
Describe the actual operation.
Data removal is not an inherent step of later Rungic upgrades.

Reuse verified base artifacts only after checking the current state.
Matching Android version, KMI name, or SoC does not establish compatibility.
Recheck affected paths when the base changes.

## CI2: independent RungicOS image

Pin Ubuntu ARM64, Plasma, and Rungic package versions.
Pin architecture, graphics/media backends, and the host-bridge protocol.
The rootfs shares CI1's kernel with Android.
It includes no separate kernel and is not Android `system.img`.

Install exact dependencies into a clean root tree.
An x86 runner must use QEMU and a real chroot for ARM64 installation scripts.
Build ARM64 packages on an appropriate native runner where possible.
Keep runner configuration separate from device recipes.

Check the package lock, `dpkg --audit`, project venv `pip check`, permissions/xattrs, log directories, and filesystem.
Follow `system/ubuntu-excluded-packages.txt` for preinstalled packages.
Exclude personal accounts, passwords, credentials, and home-directory backups.

Deliver an ext4 image, compressed payload, package lock, digests, and report.
Do not require embedding it in `product` or copying encrypted userdata.
Check image capacity and backend requirements for the target.
G100's 16 GiB/KGSL values are not universal defaults.

`build_rootfs_image.py` packages a prepared root tree.
It does not download packages or perform complete automatic installation.

## CI3: independent Rungic installation and upgrade

Deliver and install Rungic separately on a prepared compatible Android base.
`tools/ci/standalone.py` provides a developer USB/ADB first-installation entry point.
See [X70 installation on an existing base](../../../docs/91-x70-independent-install.md).
See [installation without old product apps and actual Android data removal](../../../docs/92-x70-android-base-end-to-end.md).
See [cold-start fixes, three reboots, and 9 smoke checks](../../../docs/93-x70-independent-image-revalidation.md).

User self-installation, offline Magisk readiness, and general full-image upgrades still require verification.
Use the [tool map](references/tool-map.md) for current entry points and gaps.
Do not describe old `flash.sh` as an independent installer.

### Delivery and prerequisites

Deliver the CI2 rootfs, required Rungic APK/JNI, LXC/host-bridge runtime, versions/protocols/digests, installer, and recovery instructions.
Android OEM partitions and GKI are external prerequisites.
Do not include them again in routine Rungic releases.

Before installation, check firmware/kernel capabilities, root authorization, SELinux, architecture, backend/host protocols, APK signature, capacity, and existing installation state.
Select the transfer entry point verified for this task.
USB/ADB is a developer installation method.
It does not establish completed user self-installation support.

First-installation acceptance starts with a ready base and no installed or configured Rungic.
Check payloads, storage/mount preparation, accounts, and the real desktop.
Reuse release-linked atomic state and progress that reflects actual stages.
Check permission/JNI differences between ordinary APK installation and old `product` preinstallation.

### Upgrade and recovery

For an existing installation, prefer `rungic_package.py` / `build_on_device.py` package builds.
Use `rungic_release.py` for versioned APT deployment, acceptance, and rollback.
Full rootfs replacement is a separate path that still needs implementation or verification.
Do not apply first-installation seed scripts over an existing installation.

A full-image update should stop the container and preserve the old version.
Write the new image separately before checking its digest and switching versions.
Check the result after the switch.
On failure, restore the old image and matching host version.

Preserve user accounts, files, Agent sign-ins, and Android data.
Check data/schema migration rollback separately.
Restoring only the rootfs does not establish complete recovery.

If base capabilities are insufficient, return to CI1.
CI3 must not automatically expand into Android reflashing or data removal.
Bind every device command to the exact port and serial number.

## First entry and acceptance

The required order is:

1. Compatible base ready.
2. Independent payload checked.
3. Installation and mounts prepared.
4. Release ready published.
5. `account-prepare` completed.
6. Account form shown.
7. Desktop startup progress shown.
8. Plasma available.

The root controller independently checks the completion marker.
State must identify the release.
Missing, old, or failed state must not permit early entry.
Check actual shared-storage and account-tool readiness instead of waiting a fixed time.
Account setup, normal startup, and retry reuse the common preparation layer.
Keep passwords out of logs and argv.

Record candidate, offline check, installation, first-installation acceptance, upgrade acceptance, and recovery acceptance separately.
These workflow states are not necessarily automatic tool outputs.
Bind every result to artifact digests and the device.
Installation, an ordinary reboot, or temporary manual installation does not establish complete first-installation acceptance.

Upgrade acceptance checks data retention, functionality, and rollback.
Android data removal is no longer an acceptance requirement for every Rungic version.
Follow the user's requested acceptance scope.
Do not expand hardware testing without authorization.

## Historical full bundles and recovery

`assemble_product.py`, `assemble_release.py`, `flash_release.py`, and product first-boot seeds remain available for explicit historical or recovery tasks.
Retain their sources and device evidence.
They are not the current default CI3.

Only for an explicitly selected historical path, read [docs/77](../../../docs/77-g100-three-ci-assessment.md) and [docs/80](../../../docs/80-g100-image-installation-retrospective.md).
Check the tool map's device assumptions.
Execute only the authorized scope.
Old G100 first boot after data removal does not establish independent-installation acceptance.

At delivery, report artifacts, source/spec/digests, base prerequisites, actual acceptance, and remaining work.
Clean reproducible temporary files by run-id.
Preserve final artifacts, recovery materials, and reports.
Do not perform global pruning or remove another task's dependencies to continue a build.
