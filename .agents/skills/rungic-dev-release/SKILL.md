---
name: rungic-dev-release
description: >-
  Deploy project changes as reversible development overlays, dev releases from origin/main, or formal releases.
  Use for phone previews, deployment, release creation, fleet updates, rollback to a release, and UI verification.
  Dev releases include the APK and support deploy --all and status --all.
  GitHub prereleases require explicit user approval.
---

# Rungic development overlays and releases

The user separated development and release workflows on 2026-09-30.
The user approved the dev release channel on 2026-10-04.
Read `AGENTS.md` first.

Use these records:

- [docs/97](../../../docs/97-local-development-deploy.md): development overlays and measured results.
- [docs/61](../../../docs/61-delivery-diagnostics-plan.md): release delivery and integrity checks.
- [docs/109](../../../docs/109-dev-release-channel.md): dev releases and APT protection.
- [docs/96](../../../docs/96-desktop-recovery-after-apk-restart.md): deployment recovery experience.

## Choose the workflow

| Request | Workflow |
|---|---|
| Test a change, show a preview, or debug | Development overlay: `rungic_dev.py deploy`. No commit required. |
| Update one phone or all phones to merged main changes | Dev release: `rungic_release.py dev`, then `deploy --all`. |
| Commit changes, create a release, or perform formal deployment | Formal release: commit, build, and deploy. |
| Remove an experiment | `rungic_dev.py reset [package]`. |

Do not install packages with `dpkg -i` or replace container files directly.
APT and Discover can restore release versions, and integrity checks can report drift.
G100 S (`ZY32MVJS25`) is the user's daily phone.
Use the overlay tool so changes remain visible and reversible.

Development overlays cover container deb packages only.
The APK, `rungic-plasma` controller, and release manifest's `android` files need separate installation and documentation.
Dev releases include the APK.
Deployment installs it only when the phone has a lower versionCode.

## Before each operation

1. Check the local machine identity, architecture, route, and system proxy, as `AGENTS.md` requires.
   Use `hostnamectl`, `uname -m`, and `ip route`.
2. Check the build machine.
   On Mac mini, run `hostname; uname -m; route -n get default; scutil --proxy`.
   Check that the `rungic-build` container runs.
   Device packages use this build machine by default.
   Do not use `--host phone`.
   It installs Qt/CMake dependencies on the daily phone, and heavy builds can trigger low-memory process termination.
3. Check `adb devices -l`.
   Identify the phone by its serial number.
   Do not run `kill-server` on the shared ADB server.
   Do not disconnect Wi-Fi.
4. Read `python3 tools/rungic_release.py status --all`.
   Each row shows release, channel, commits behind main, APK, overlay count, and APT protection.
   For one phone, use `python3 tools/rungic_dev.py status` for the baseline and overlays.
   Use `python3 tools/rungic_release.py status` for snapshots and integrity.
5. To learn how far a phone is from main, read `python3 tools/rungic_release.py drift --all`.
   The "commits behind main" count of `status --all` covers only the base release.
   `drift` compares every part with origin/main: each package at its overlay's or release's commit, the APK, and each Android-side file.
   It also runs `rungic-converge check` on the phone (docs/122): an Android setting that does not hold
   (allowlist, overlay, Magisk grant, directories) is a part that differs; what is left to the user
   (runtime permissions, KernelSU's grant) is printed as "reported, not changed" and is not drift.
   It prints "in sync" or the parts that differ, and first the main commit it compares with.
   If GitHub cannot be reached, the fetch stops after 60 s and drift uses the local origin/main, and says so.
   Use `--against COMMIT` to compare with a fixed commit, without a fetch.
   It cannot compare built host programs, such as `rungic-plasma-enter`, and a release does not update them.

## Development overlays

Run deployment in the background without a short client timeout.

```sh
python3 tools/rungic_dev.py deploy <package>...
python3 tools/rungic_dev.py status
python3 tools/rungic_dev.py reset [<package>...]
```

### Select packages and versions

Select packages whose `paths` in `packaging/<name>/package.json` cover the changed files.
`tools/rungic_package.py list` identifies them as stale or uncommitted.

For upstream patch queues under `packages/<name>`, use `deploy <component-name>` directly, for example `plasma-mobile`.
The tool builds through `build_on_device.py` on Mac mini.
The first complete build can take tens of minutes.
It overlays only the component's binary packages registered in the release.
`reset <component-name>` removes all of that component's overlays together.

The version format is `<release-package-version>+dev<UTC-time>.<short-sha>[.dirty]`.
Later deployments retain previous overlays and the original baseline.

### Check the result

- Require `[verify] apt=ok`: every overlay's Installed version equals its Candidate version.
- Optionally check `apt list --upgradable` and `apt-get -s dist-upgrade` inside the container.
  Neither should select Rungic packages.
- Check that `release.dev` lists the overlays.
  If `summary.state` is drift, identify which differences existed before deployment.
- Keep the records in `.work/dev-deploy/<time>-deploy/`.

Restarts follow `user_restart`, `service_restart`, and `session_restart` in `release/packages.json`.
For example, `rungic-design` restarts plasmashell and the voice overlay.
Tell the user about the restart before deployment.

Development deployment does not create a rootfs snapshot.
An uncommitted snapshot from the previous release does not block an overlay.
A later `rollback --snapshot` also removes the overlays.

## Dev release channel: 2026-10-04

Create dev releases only from `origin/main`.
Each version identifies one main commit and includes the APK unless explicitly omitted.
Do not create independent releases from each developer's working tree.

Create a dev release after the required PRs merge, to restore a clean baseline, or to distribute an installation.
Check that main contains the requested changes.
Release deployment removes unmerged overlay experiments.
Ask the user before removing those experiments, unless the current authorization already covers removal.

Use a clean worktree whose HEAD equals `origin/main`, for example one created with `git worktree add … origin/main`.

```sh
python3 tools/rungic_release.py dev
python3 tools/rungic_release.py deploy <version> --all
python3 tools/rungic_release.py status --all
python3 tools/rungic_release.py drift --all
```

`dev` builds missing packages, components, and the APK, then creates a `YYYYMMDD.N` release bundle.
Run `deploy --all` in the background without a client timeout.

### Build inputs

- `dev` rejects dirty worktrees or HEAD values other than `origin/main`.
  It also rejects changed patch queues without changelog entries.
  Mac mini is the default build machine.
- Link a new worktree's `.work/apt` to the shared local APT repository.
  Use `ln -s <main-worktree>/.work/apt .work/apt`.
  Otherwise, the empty repository triggers complete package rebuilds.
- Coupled package versions come from the default phone selected by `RUNGIC_SERIAL`, or from `--coupled-json`.
- APK builds require the local Android SDK, NDK, and other native libraries.
  `build_native_libs` rebuilds host libraries from the current commit each time.
  Stop on build failure instead of publishing old libraries.
  Use `--apk FILE` for a supplied APK or `--no-apk` for a release without one.
- Increase versionCode when the APK changes.
  Phones with the same versionCode do not receive the replacement APK.

### Bundles and deployment

Bundles are `rungic-<version>.tar` in `.work/release-bundles/` by default.
Select another output with `--out` or `RUNGIC_RELEASE_OUT`.
On another machine, use `deploy --all --from rungic-<version>.tar`.
Do not recreate the same release version there.

The phone downloads missing deb packages directly from Mac mini's release pool.
The deployment machine sends only the index.
Transfer through the local machine only when build-machine or phone keys are unavailable, as docs/109 describes.
Do not route phone package downloads through K8 by default.

`deploy --all` deploys sequentially and continues after a phone fails.
It prints a final summary table.
Per-phone records are in `.work/deploy/<time>-<version>-<serial>/`.
Each deployment appends to `release/history.json` for the next commit.

A phone with an uncommitted previous snapshot reports aborted.
Follow formal release step 4 below.

APK installation follows container installation.
When the installed versionCode is lower, the tool runs `adb install -r` and reopens the app.
`--restart never` skips APK installation.
Rollback does not downgrade the APK.

### GitHub prereleases

1. Run `python3 tools/rungic_release.py publish <version>`, or `dev --publish`.
   This prints the `gh release create dev-<version> … --prerelease` command and description file without accessing GitHub.
2. Show the command and description to the user.
3. After explicit approval, run `publish <version> --yes`.

Do not create or push `dev-*` tags independently.

After deployment, check the APT column in `status --all`.
`ok` means release packages have exact version pins, the metapackage carries Protected status, and the unattended-upgrades list exists.

## Formal releases

1. Commit changes for main in logical groups.
   Add attribution at the end of each commit message.
   `rungic_package.py` and `rungic_release.py build` require clean commits.
2. Find stale packages with `python3 tools/rungic_package.py list`.
   Build them with `build <package>... --host macmini`.
   Package versions are `0.<commit-count>`.
3. Run `python3 tools/rungic_release.py build --note "…"`.
   It creates a `YYYYMMDD.N` metapackage using the phone's coupled package versions.
   Development overlays do not prevent this step.
4. Resolve the previous snapshot before deployment.
   An existing snapshot stops deployment.
   Ask whether to commit the previous release or deploy with `--snapshot never`.
   Do not choose for the user.
5. Run `python3 tools/rungic_release.py deploy <version>` in the background without a client timeout.
   Wi-Fi deployment can exceed 10 minutes.
   A 590-second timeout stopped a deployment on 2026-09-30, as docs/96 records.
   The sequence is snapshot, installation, pins, overlay removal, Android files, Android settings (`converge`), restart, integrity check, then smoke acceptance.
   A refused setting is recorded in the `converge` step, not a failed deploy.
   The log identifies overlay removal as `dev-overlay`.
   Failed acceptance automatically restores the snapshot.
6. Check `result` in `.work/deploy/<time>-<version>/deploy.json` and `rungic_dev.py status`.
   Overlays must be empty and the baseline must identify the new release.
   Run `rungic_release.py commit` for the new snapshot only after the user accepts it.
7. Document version, commit, package count, step results, and acceptance limits.
   Distinguish research, offline checks, and device results.

## UI and design-system verification

For an offline local gallery:

```sh
python3 tools/design_gallery.py local <directory> [--section A,B] [--rev <commit>]
```

First run `sh tools/dev-setup.sh` for PySide6.
The gallery renders states from `desktop/design/qml`.
`--rev` selects a commit for before/after comparison.

For the phone:

```sh
python3 tools/design_gallery.py phone <directory>
```

The installed `rungic-design-gallery` runs as the desktop user with the offscreen platform.
It does not open a window on the user's screen.
It produces one image per section and theme, plus `sheet.png`.

The software backend does not render `MultiEffect`.
Thumbnail and LivePicture examples are therefore empty.
Check those controls separately in a real session.
Store screenshots and records in `.work/verify/<date>-<topic>/`.

## When changing these tools

Offline tests must not access phones.
`tools/conftest.py` blocks `rungic_device._run`, so an ADB call fails the test.

Tests must also avoid GitHub and the shared APT repository.
Replace `gh` and `adb_install` with test doubles.
Point `POOL`, `RELEASES`, `APKS`, and `RELEASE_HISTORY` to temporary directories.
See `tools/test_rungic_release_channel.py`.

Route new device operations through the caller module's `run`, for example `rungic_release.run`, or an injected runner.
This allows test doubles to intercept them.
A test bypassed this boundary and removed real phone overlays on 2026-09-30, as docs/97 records.

## Report

State the workflow, overlay or release version, and actual restarts.
Report APT and integrity results, with pre-existing drift identified separately.
Give screenshot locations and the checks that remain incomplete.
