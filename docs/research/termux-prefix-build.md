# Building the Termux prefix seed

`tools/ci/build_termux_prefix.py` builds a seed from the official ARM64 Termux
0.118.3 APK (fixed SHA-256 in the script) and the authenticated Termux main
repository. It reproduces the official installer's executable permissions and
`SYMLINKS.txt` links, then resolves and downloads PulseAudio's dependencies with
host apt using isolated package state. It does not install Android packages on
the build host.

Run on Linux with Python 3, apt-get, dpkg-deb and GNU tar. Supply the official APK
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
