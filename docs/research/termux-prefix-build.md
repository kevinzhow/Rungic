# Building the Termux prefix seed

`tools/ci/build_termux_prefix.py` builds a seed from the official ARM64 Termux
0.118.3 APK (fixed SHA-256 in the script) and the authenticated Termux main
repository. It reproduces the official installer's executable permissions and
`SYMLINKS.txt` links, then resolves and downloads PulseAudio's dependencies with
host apt using isolated package state. It does not install Android packages on
the build host.

Run on Linux with Python 3, apt-get, dpkg-deb and GNU tar and gzip. The archive uses explicit `gzip -n` and sorted entries with zero timestamps and numeric ownership. The CLI fixes its creation mask to `0022`, so generated dpkg
metadata has the same permissions regardless of the caller's umask. Supply the official APK
from the URL in the script; the script verifies it before extraction:

```sh
python3 tools/ci/build_termux_prefix.py --apk termux.apk --output termux-seed
python3 tools/ci/build_termux_prefix.py --apk termux.apk \
  --lock termux-seed/termux-prefix.lock.json --output termux-replay
sha256sum termux-seed/termux-prefix.tar.gz termux-replay/termux-prefix.tar.gz
```

The output directory must not already exist. An interrupted or rejected build
stays available for diagnosis; it is not a completed seed. Initial resolution
produces a package lock with exact versions, architectures, hashes and sizes.
Replay downloads these versions from the authenticated repository and rejects
changed package bytes. If an old version leaves the repository, replay fails;
keep the resolver archives with the build record instead of substituting a newer
package. `apt.log` and the retained signed indexes explain the selected inputs.

The archive contains `usr/`. Firstboot assigns the actual Android Termux UID;
the archive does not contain a previous phone user's identity or D-Bus machine
ID. Check the archive SHA-256 against `termux-prefix-report.json` and record it
in the standalone payload manifest.

This is a composition of official bytes, **not a configured Android runtime**.
The APK's bootstrap second stage and package maintainer scripts require Android
and have not run. Added packages are recorded as unpacked. The report explicitly
sets `android_configuration_complete` to false. `configuration-pending.json`
lists every bootstrap and added package, and `configuration-scripts/` retains
all actual postinst scripts plus the second-stage entry. The Rungic firstboot at
`e93a35f` only extracts the seed; it does not run those scripts. Validate startup,
playback and recording through that product flow without manually repairing the
prefix and reporting the repair as successful first installation.

## Reproducible build check

On 2026-10-07, the builder on K8-Plus downloaded the pinned official APK,
resolved 23 packages against signed Termux indexes, and built a seed. A second
build used its emitted lock and downloaded the same package versions again.
Both archives have SHA-256
`ded598dfe489f77fce453f72bbf9da8d7fd6369860b201d7ccefcb8089b4fa9e`.
Each preserved 1,146 bootstrap symlinks. Raw apt logs, signed indexes, lock and
reports are retained under the developer checkout's `.work/termux-repro/`.
This checks the host composition; neither build executed an Android program.

The historical K8 replay above used caller umask `0002`. Its 23 generated dpkg
package lists had mode `0664`; a Mac build with umask `0022` produced `0644`.
All 10,036 archive entries had identical content, links and order. The fixed
builder now sets umask `0022` before composition. Two fresh K8 replays with
caller umasks `0002` and `0077`, the official APK and the same 23 locked packages,
both produced SHA-256
`42f5ab0af60ced0dc241339b78bae80b2f20b5dea41b7b6d9209d9271f61afbf`.
This also matches the Mac archive built with an explicit `0022` wrapper. Existing
archives remain unchanged. The regression uses a real DEB and archive tools with
an offline download substitute; these official-input replays use authenticated
apt downloads. Neither check executes an Android program.

## Bootstrap compatibility boundary

The pinned input is the official GitHub **debug-signed** ARM64 APK for Termux
0.118.3. Its APK file SHA-256 is an input identity, not a signing certificate
hash. Use this bootstrap with that Termux build; a matching version string alone
does not prove APK identity or certificate compatibility. An F-Droid build or
another Termux version is outside this builder's verified input combination.

Current `rungic-firstboot.sh` checks the seed APK and archive against the payload
manifest and checks that `com.termux` exists. It **does not** compare the installed
Termux version or certificate with the bootstrap source. It also preserves an
existing executable PulseAudio prefix. This is a known limitation, not an
installation compatibility guarantee. Record installed APK identity and prefix
provenance before using the seed. The next candidate's device QA must verify
startup, playback and recording from the ordinary product path.

For the USB G100 candidate on 2026-10-07, mibook independently read
`/product/app/Termux/Termux.apk` and package metadata (0.118.3, versionCode 1002,
DEBUGGABLE). Its APK file SHA-256 matches the pinned input exactly, and both
signing certificates have SHA-256
`b6da01480eefd5fbf2cd3771b8d1021ec791304bdd6c4bf41d3faabad48ee5e1`.
The independent comparison is recorded in QA thread message `9eb81171`.
This confirms the APK/bootstrap input combination for that base; it does not
prove prefix configuration, startup, playback or recording. The runtime matching
check described above remains absent.

The independent device report is
`reports/g100-termux-compatibility-20261007-001/` in mibook's workspace
(51 files; SHA256SUMS digest
`5ac970f8311d1d157874e72c8e899f194120fb48fd4086d1743ec43355d06835`).
It extracted the certificate and compared it with OpenSSL; it did not perform
full APK signature verification, install the APK, or test firstboot. K8 separately
ran SDK 36 `apksigner verify --print-certs` on the byte-identical reference APK.
The phone's boot ID stayed unchanged during the read-only collection.
