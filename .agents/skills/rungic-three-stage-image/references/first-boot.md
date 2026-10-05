# First-boot contract and known failures

See [docs/79](../../../../docs/79-g100-ci-execution.md) for detailed evidence and [docs/80](../../../../docs/80-g100-image-installation-retrospective.md) for conclusions.
Read the relevant symptom records before repeating a failed operation.

## Current target and historical implementation

Since 2026-09-30, default acceptance covers independent Rungic installation on a prepared base without Android data removal.
Check user-data retention and recovery separately for upgrades.
`standalone.py` reuses state, locks, validation, mounts, and account gates for explicit payloads and USB/ADB installation.
See docs/91 for X70 scope.
See docs/92 for actual base reflashing and independent installation without old preinstalled applications.

Upgrade and offline Magisk readiness still need verification.
Old init_boot images can overwrite service.d on every boot.
Check that the Magisk product-forwarding module actually works after reboot.
Prepare dev/proc/sys in temporary SSH-key-generation chroots.
Clean those mounts after success and failure.
Historical data-removal failures are diagnostic references, not a default independent-installation step.

## Implementation entry points

- `tools/rungic-magisk-bootstrap.rc` / `.sh`: current offline Magisk startup.
- `tools/ci/rungic-firstboot-service.sh` / `rungic-firstboot.sh`: deployment, locks, state, payload checks, completion marker.
- `system/rungic-plasma`: shared controller, release gates, `start_container`, `account-prepare`.
- `system/android-audio`: shared directory preparation and audio services.
- `tools/rungic_plasma_enter.c`: controller environment mounts.
- `FirstBootState.java`, `MainActivity.java`, and `AccountSetup.java` under `android/app`: waiting state, startup, account form.

Trace the actual complete call chain before changes.
Fix the lowest shared preparation layer instead of adding separate account and desktop patches.

## Conditions for Magisk and first boot

1. Blank /data: with Magisk 31 and no `/data/adb`, early startup records deferred state in tmpfs.
   After Android boot-complete, it prepares the offline runtime and automatically reboots once.
   Check source before applying this sequence to another version or root mechanism.
2. Deployment holds its lock.
   Check current release and payload digests.
   Extract to the required location with required permissions and labels.
   Record the actual failing stage.
   Do not write the completion marker early.
3. Check real mount readiness.
   Android boot-complete does not establish storage readiness.
   Wait for shared storage before preparing audio/Wayland/shared directories and checking mounts.
   Do not create a substitute directory on an unmounted path.
4. Root writes the completion marker.
   Atomically write app-private `rungic-install.properties` with release/state/phase and required UID/MCS.
   The app reads state only, and the controller independently checks the marker.
   Error state must not open the form.
5. Start the container before account preparation.
   Check account tools, target UID/shared directories, and service state.
   The current entry accepts systemd running/degraded.
   Degraded alone does not establish required-service health.
   Check relevant dependencies.

   The current preparation limit is 180 seconds, and APK timeout is 240 seconds.
   Synchronize both sides when changing them.
6. Show actual stages and indeterminate progress during preparation.
   After ready, finish `account-prepare` before showing the form.
   After account creation, show desktop startup progress.
   Send passwords through protected input channels.
   Keep them out of argv, logs, and test screenshots.
7. Check the new path from a ready base with no installed/configured Rungic.
   Observe the required order above.
   Check upgrade data retention separately.
   Use bundle-driven Android data removal only for an explicitly selected old full-bundle workflow.
   Reopening the app must resume waiting, and repeated preparation must be idempotent.
   Do not claim untested power-loss recovery.

## Symptoms and first checks

| Symptom | Known cause or limit | Check or action |
|---|---|---|
| Recovery after data removal, but existing data boots | Old G100 startup violated safe initialization order. The specific failing encryption call remains unconfirmed. | Preserve logs. Hold other inputs constant when comparing init_boot. Check root lifecycle instead of repeatedly erasing data. |
| Account page reports missing bind audio/shared | Evidence identifies missing audio-directory initialization. Direct evidence for the first shared-directory race is absent. | Check release/completion state, real storage, shared directories, and mounts. A failure before the account helper does not require a new password. |
| Valid username rejected without an existing account | X70 image inherited host Python caches and created a conflicting home. | Check passwd and home contents/provenance. Isolate host environment in `arm64_chroot.py`. Check both builders. Do not remove unchecked user directories. |
| Photo apps report missing Pictures | First installation lacked shared XDG directory preparation. | Reuse `system/user-dirs` after real Shared mounting. Preserve existing directories. Do not prepare separate directories for each app. |
| Preinstalled APK lacks libc++_shared | Compressed JNI libraries were absent from product/app. | Package matching lib/arm64 files and labels. An ordinary pm install correction is diagnostic evidence only. |
| Termux usr already exists | The app created an empty directory early. | Use rmdir only for an empty directory. Do not blindly remove nonempty directories or symbolic links. |
| Abnormal rootfs extraction or space growth | toybox sparse behavior differs from its help text. | Use the verified writer. Check full image digest and actual allocation. |
| Container fails to start again | loop autoclear left an invalid dm mapping. | Check rootfs attach reconstruction. Do not blindly remove mappings in use. |
| APK cold start reports user@1000 init.scope Permission denied | APK umask 0077 reaches LXC and creates payload cgroup mode 0700. | Check actual umask and cgroup mode. Set 022 only in the lxc-start subshell. Manual ADB root startup can hide this failure. See docs/93. |
| android-audio fails, assuming PulseAudio runs | A stale /data PID file survives reboot. Another Android process reuses the PID, which Termux cannot identify. | Root checks UID/exe/configuration under the private lock. Remove stale PID state. Stop only the owned daemon. Do not kill the unrelated app. See docs/93. |
| No ADB devices | Port, USB/Wi-Fi, authorization, or mode can change. | Check server, serial, and USB state. Do not infer bootloop immediately. |
| No fastboot output for a long time | Mode transitions, transfers, buffered logs, or actual disconnection can cause this. | Use streaming logs and bounded waits. Stop on timeout. Recheck after recovery. Do not loop reflashing. |

For Magisk 31, do not send queries that can return SQL NULL to the live device daemon.
Follow project docs/39.
Use the checked shell-stdin channel for multiple root commands.
Use transfers without PTY for binary backups.
Do not confuse permission/quoting failures with missing device capabilities.

Even without PTY, binary output can contain unexpected text.
X70 `adb exec-out su -c 'tar -czf - …'` mixed socket warnings into the compressed stream.
The command returned 0, but the archive CRC failed.

Before flashing, prefer writing the backup to a phone file and calculating its digest, then using `adb pull`.
Before data removal, check both endpoint SHAs, archive readability, and key file digests.
See [docs/92](../../../../docs/92-x70-android-base-end-to-end.md).

## Interpret regression evidence

G100 `.3` has first-boot-after-data-removal evidence and detailed installation checks.
G100 `.5` includes progress/account preparation, and the user reported successful reinstallation into Plasma.
Do not combine their reports.
G100 S offline seed tests with existing data do not replace final full-bundle data-removal acceptance.

X70 `.1` required manual home correction after data-removal deployment before account setup and Plasma worked.
Revised `.2`/`.3` bundles passed offline checks only.
Updates to the current account's directories/scale do not establish clean-install acceptance for those bundles.
See the target spec knowledge record and [docs/83](../../../../docs/83-x70-air-pro-onboarding.md).
See [build isolation](build-isolation.md) for template and first-screen-default checks.

Phone-side fastbootd progress remains unimplemented.
Host streaming logs improved.
RecoveryUI research describes a possible direction, not an available phone-log option.
A build/first-boot task does not authorize recovery UI changes.
