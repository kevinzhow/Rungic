# Kernel configuration changes and ABI comparison

Use for new ACK generations, LXC/namespace configuration, OEM module CRC differences, or Rust symbol failures.
The methods come from [X70 onboarding](../../../../docs/83-x70-air-pro-onboarding.md).
Its commits, reserved slots, toolchains, and acceptance counts apply only to that firmware.

## Establish a reproducible stock control

- Pin ACK commit/tree, every synchronized tool repository, Clang/Rust, BUILD_NUMBER, page size, LTO, and configuration.
  A branch name or common commit alone does not reproduce a Kleaf build.
- Build the stock-configuration control with the same toolchain before adding minimum required capabilities.
  If control CRCs differ, identify the source, configuration, or tool problem first.
  Do not change module CRCs, export names, or vermagic to force acceptance.
  Do not disable KMI strict checks.
- For sparse checkouts, use the actual pinned repository manifest and supported Kleaf parameters for version stamps.
  Missing unrelated repositories do not justify unknown versions.
  Check patched source against the build tree file by file.
  Read configuration from the final Image.
  Read payload and headers from the final boot image.

## Matching C layout can still change ABI

`gendwarfksyms` computes CRCs from type descriptions.
Replacing two KABI slots with one union can remove the second slot's type description despite unchanged sizeof and offsets.
First reproduce the difference with a small C/header example, DWARF dump, pahole, and the stock control.

Then select the version's supported single-slot macro or a typed helper that retains the original declarations.
For the helper, check size, alignment, and adjacent slots.
Do not copy X70 slot numbers or `task_struct` offsets into other targets.

## Rust Binder and extended modversions

- Compare pinned `kernel/module/version.c` and modpost.
  Pair extended CRC arrays with NUL-separated names correctly, including final terminators.
  Do not truncate long Rust symbols.
  When extended tables exist, do not check only legacy tables.
- namespace/SYSVIPC options can change C exports, bindgen anonymous type names, generated Default impl order, and Rust symbol disambiguation numbers.
  If Rust differences remain after C ABI repair, compare generated bindings and impl order.
  Source text or structure size alone is insufficient.
- Before adding exports, check the same commit's implementation and upstream corrections.
  Preserve namespace semantics.
  Use a separate KMI allowlist.
  Change bindgen arguments only for types supported by evidence.
  Do not globally suppress generated code.
  X70 patches belong to `packages/gki-android16-6.12/`.

## Report scope and check boot

Report module count, reference count, comparable GKI matches/differences, and non-GKI references separately.
Matching export names in other OEM modules identify providers only.
They do not establish cross-module CRC or signature verification.

Bind certificate-restoration reports to the actual Image.
Describe changed regions and unchanged surrounding regions.
Do not describe a modified kernel as an untouched stock kernel.

After offline ABI, module trust, and boot packaging pass, use the device's verified boot method.
Check the running candidate version, actual loaded modules, Android/SELinux, and required namespace capabilities.
Read the boot image back.
These results do not establish LXC/desktop acceptance or full-bundle first installation after data removal.
