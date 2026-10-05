# Onboard a new device or firmware

Use for unverified devices, firmware changes, or changes to kernel, partitions, or root provider.
Check each new baseline independently.
The same sales name does not establish the same device identity.

## 1. Establish the facts

Read the [compatibility contract](../../../../docs/75-image-build-separation.md), then compare the [G100 implementation review](../../../../docs/80-g100-image-installation-retrospective.md).
The G100 review establishes only its specific combination.
Some contract interfaces remain designs.

| Category | Required facts |
|---|---|
| Identity | Vendor, codename, SKU/channel, complete fingerprint, bootloader, Android/API, CPU architecture. |
| Stock inputs | Source, firmware, archive and partition digests, recovery method, AVB chain, rollback-version restrictions. |
| Boot and partitions | A/B status, actual boot/init_boot/vendor_boot/recovery layout, header/DTB, dynamic partition geometry/filesystems, flashing mode. |
| Kernel | Pinned ACK/Kleaf, build number/toolchain, page size, KMI, OEM module version symbols/signatures, minimum capability changes. |
| Host and backends | Root provider, SELinux, container/storage mechanisms, APK/bridge protocol, display/GPU/touch, selected shared backends. |
| Capacity | Payload staging, expanded rootfs, old-version recovery copy, /data headroom, local/remote build peaks. Check read-only seed space for old bundles. |
| Preinstallation | RungicOS packages and exclusions. Android cleanup applies only to an explicitly selected base modification. |
| Acceptance | Boot requirements, user requirements, optional capabilities, observation methods, expectations, evidence paths. |

Missing Magisk/LXC does not establish that adaptation is impossible.
Distinguish CI1 root/kernel preparation from CI3 Rungic runtime installation.
Existing components do not replace independent first-installation checks.
The actual base operation determines whether data removal is necessary.
CI3 does not normally reset Android.

## 2. Create the spec and run record

Track the spec at `profiles/devices/<vendor>/<codename>/<firmware>.json`.
The current schema v1 example is `profiles/devices/motorola/portov_cn/W1VT36H.1-51-8.json`.
Its root fields are:

- `schema_version`, `id`: schema version and unique device/firmware identifier.
- `identity`: `product/device/sku/fingerprint/bootloader`.
- `stock`: OEM archive and boot/init_boot/vbmeta/vbmeta_system/super/product digests, product capacity, and AVB public-key digest.
- `kernel`: manifest/common repositories and commits, stock_release, page_size, and module_trust_certificate_sha256.
- `release_requirements`: architecture, API, battery, and space thresholds.
- `deployment`: phone proxy configuration in the current implementation.
- `purity`: `remove_preinstall/remove_files/disable_packages`.

This schema does not describe every phone.
Version 1 does not fully express flash plans, root entry points outside init_boot, or backend variants.
For new requirements, extend the data contract and its producers/consumers.
Preserve regression coverage for supported devices.
Do not add unused fields and claim adaptation.
Do not fabricate partitions or hashes to satisfy old tools.

Record runtime details separately:

- Serial number and ADB port.
- Current slot, runner, and proxy.
- Run-id and artifact paths.
- Current authorization scope.

Version 1 stores proxy settings in `deployment`.
Check the new device's actual environment instead of copying old private-network addresses.
Active slots and individual connections are not model capabilities.

Fastboot adapters and release bundles bind the delivered spec SHA.
Add knowledge in adjacent `<firmware>-knowledge.md` files where possible.
State firmware, spec SHA, evidence, and acceptance limits.
Do not change a bound spec merely to add notes.

For real execution-parameter changes, update consumers, adapter bindings, and new bundles together.
Keep old bundles auditable.

Store stock extraction identity in `<firmware>-stock-identity.json` for actual use by `prepare_g100_stock.py --identity`.
Derive it from independently checked device/firmware identity.
Do not establish trust from the ZIP under review.
X70 already has this input.

`verify_g100_stock.py --logical-partitions` must cover the actual liblp/AVB set.
A/B labels in info text do not override the real partition table.
Some tools named G100 already expose explicit parameters.
Check their current CLI before copying defaults or replacing the complete toolchain.

## 3. Adapt tools instead of copying the pipeline

Read the [tool map](tool-map.md).
List differences between current implementation and target.
Choose the smallest correct reuse boundary:

- Same format, different values: put values in the spec and check them against the actual device.
- Different OEM container, flashing protocol, root startup, or graphics interface: add a scoped adapter.
  Reuse hashing, reports, lifecycle, and first-boot contracts.
- Non-GKI device, unavailable matching baseline, or unmet hard requirements: record unsupported or blocked scope honestly.
  Do not substitute another device's image because the SoC matches.
  Continue independent research and userspace work.

Add offline plans and failure checks for the target adapter.
Regress existing device plans.
Do not send write commands before static checks pass.
Synchronize protocol changes across host, rootfs, first boot, and acceptance entry points.

Android properties and fastboot can report different bootloader strings.
Record exact values from both interfaces and the adapter bound to the spec SHA.
Do not use fuzzy matching for unlock state or relax every device's rules.

Check mode transitions in both directions.
One successful ADB-to-fastbootd transition does not establish bootloader-to-fastbootd or reverse USB reliability.
For lost connections, distinguish host USB/sandbox permissions from device state.

An interrupted flash does not justify unconditional repetition.
Preserve completed-stage results, the stock manifest, and log digests first.
Then recheck current identity, slot, and mode before planning only the remaining stages.
A dedicated X70 script verified stage 7/8 continuation.
This does not give the general installer arbitrary resume support.

## 4. Record capabilities and results

Each conclusion must identify:

- Spec/release and actual artifact digests.
- Stock device or candidate.
- Method/version, expected value, and observed value.
- Result: pass, fail, unknown, or skipped.
- Time and evidence path.

Explain skipped checks.
Do not mark unknown hard requirements as passed.

Save actual outputs from existing tools.
Do not invent an existing unified `capability-report.json` generator.
If the task requires a shared format, implement its producers and consumers before combining current reports.
Preserve ABI comparison limits.

## 5. Promote a candidate to release

Check offline first.
Then separately check the authorized candidate-base boot, independent Rungic installation, and upgrade.
Bind final acceptance to actual base/OS/host versions and digests.
Record first-use progress, account setup, and the real desktop.
Check upgrade data retention and recovery separately.

Perform old full-bundle data-removal acceptance only when that workflow is explicitly selected.
Check the KWin desktop for a new GPU backend.
A rendering probe alone is insufficient.
Check shared capabilities within the requested task scope.

Describe root-patched artifact device scope.
Current G100 bundles bind a tested serial number.
Without cross-device evidence, do not remove the binding and claim support for every device of that model.
Check release-bundle host OS and architecture support separately.
