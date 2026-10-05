# Build isolation, first-use defaults, and artifact retention

Use for CI2/rootfs, host seeds, first-account/display-default acceptance, and build cleanup.
See [docs/83](../../../../docs/83-x70-air-pro-onboarding.md) and [docs/85](../../../../docs/85-phone-display-size-policy.md).
Reuse the methods, not device scale factors, directory capacities, ports, or user names.

## Keep host state out of templates

`tools/work-env.sh` sets Python caches to absolute host paths.
Passing them into a chroot creates a developer-named home inside the image.
That can prevent a legitimate first-installation user name.
`tools/ci/arm64_chroot.py` isolates host `PYTHON*` variables.
Pass explicit guest paths when necessary.
Do not forward the entire host development environment.

Check account state in both rootfs and host seeds.
The current builders check one UID1000 template, locked passwords, permitted home layouts, absent account-completion markers, and listed common credential paths.
The final tree has an empty machine-id.
These checks are not a complete secret scan.
Do not generate a release template from a configured device's home.

`system/user-dirs` prepares standard directories after the real Android Shared mount becomes available.
Common account preparation and session entry reuse it.
Check preservation of existing files, blank first entry, and standard Qt/GLib directory queries.
One photo app opening temporarily does not establish acceptance.

## Preserve package and APK provenance

Pin the final package set and external coupled dependencies in addition to upstream sources and patch queues.
Use a new version for changed binaries.
Do not replace repository objects under the same version.
Record build results, dpkg audit, filesystem checks, and package lint separately.
If a missing `.dsc` prevents lintian from completing, do not report lint success.

Reuse only checked architecture-independent or shared ARM64 payloads from other devices.
Do not reuse their kernels or partition images.
For unchanged native source with reused APK JNI, record the original APK SHA and each library's SHA.
Preserve signing identity.

Read-only `product/app` must include the applicable `lib/arm64` files.
Temporary `pm install -r` does not replace preinstallation checks.

## Test first-use display defaults

- Defaults apply only when no saved preference exists.
  A saved choice, explicit “standard,” and continuous Android following have different meanings.
- Android density is logical density.
  Obtain reference width from the same display-metrics snapshot.
  Do not use a low-resolution Surface as another density reference.
  Do not multiply by fontScale.
- KWin and KScreen share the current policy.
  See docs/85 for the formula, default width protection at 360, and coefficients that need cross-device calibration.
  Check native/low resolution, valid/missing metadata, and saved/absent display configuration separately.
  One device's parameters are not universal physical rules.
- Before temporarily moving display configuration, wait for the actual KWin process to exit and finish writing.
  Stopping the outer systemd service does not necessarily stop the logind scope.
  If rapid tests trigger StartLimit, record the result and restore the service.
  Do not use a fixed delay to change product defaults.
- Automatic refresh can add or remove current display modes.
  Do not cache mode IDs across configurations.
  Resolve modes from current dimensions and refresh rates each time.
  Check configuration response text and final state, not only the exit code.
- Check both KScreen enumeration and the Android host's actual refresh policy.
  An automatic label alone does not establish automatic host behavior.
  Restore the original Android density override, render mode, user scale, and test-helper state afterward.

## Preserve the next build during cleanup

1. Separate release/recovery inputs, exact dependencies, reports/logs, reproducible expanded trees, and obsolete candidates.
   Cross-run access to Termux or sparse writers in old `assembled-vN/root` trees is a hidden dependency.
   Move those inputs into independent `.work/deps/` snapshots first.
   Check hashes against original reports before updating consumers and removing the old tree.
2. Check active builds, mounts, retained artifact digests, and links before cleanup.
   Archive small reports, seed configuration, and metadata.
   Remove only explicit, authorized temporary paths and old candidates.
   Keeping old release bundles and removing them are different scopes.
   Do not perform global pruning.
3. Btrfs reflinks, compression, sparse files, and hardlinks make `du` size different from reclaimable space.
   Removing one image path can merely decrease its hardlink count.
   Measure actual filesystem free space before and after cleanup.
   Do not sum retained directory sizes to estimate recovered space.
4. Record removed paths, new dependency locations, retained image hashes, and cleanup results.
   After removing expanded trees or reconstructed super images, identify the retained OEM inputs needed to rebuild them.
   Do not leave tools dependent on missing temporary trees.

Existing-account upgrades, configuration-isolation tests, and offline package checks do not replace fresh Rungic installation with the same artifact.
The new path does not require Android data removal.
Check independent installation and upgrade separately.

For old full bundles, `clean_install_accepted` retains its original Android data-removal meaning.
Do not reuse it as evidence for the new flow.
Record packages that require manual correction to reach the desktop.
After revision, repeat the applicable installation before increasing acceptance status.

## Keep the casting JAR and desktop interface together

X70 `.7` incorrectly reused an old host-v2 JAR.
Android found the TV, but the new interface lacked `receivers` and the list was empty.
See docs/86 and docs/93.

Build the JAR and `rungic-cast.build.json` with `shared/android/rungic-cast/build.sh`.
Deployment, host seeds, and CI3 packing check current source and JAR digests.
Rebuild if the sidecar is missing or inputs are stale.
Do not fabricate provenance or bypass checks.

Update desktop packages together.
At runtime, check `protocol_version: 1` and `receivers`.
Basic smoke tests do not cover casting.
Check the real receiver list through phone quick settings.
Accept TV picture and audio separately.

## Reuse components by input fingerprint

On 2026-09-30, the user required reuse decisions based on versions/input hashes rather than component categories.
`tools/build_artifact.py` constructs the cache key from:

- Current source and patches.
- Dependency artifacts and their input fingerprints.
- Toolchain and target.
- Parameters and command.

Check every output SHA even when the key matches.
Missing records, mismatched inputs, changes during a build, or damaged outputs are not cache hits.
New `standalone.py pack` requires `--build-plan` and produces a schema 2 component manifest.
Digest checks for old bundles do not establish the new provenance requirements.

A binary baseline can be explicitly pinned.
Do not fabricate its original source provenance.
See [docs/94](../../../../docs/94-build-fingerprints.md) for tools, recipe fields, 8 measured stages, and direct builders not yet migrated.
