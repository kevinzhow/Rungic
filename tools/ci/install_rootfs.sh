#!/bin/bash
# Run from a file, with stdin closed. A package must never read the next command.
set -eux
export DEBIAN_FRONTEND=noninteractive
source_commit=$1
firefox_version=$2
state=/var/lib/rungic-apt
mapfile -t release_packages < "$state/exact-packages.txt"
mapfile -t runtime_packages < "$state/runtime-packages.txt"
config=$(grep '^rungic-plasma-config=' "$state/exact-packages.txt")
# Bootstrap source selection stays under /var. The owning package supplies all
# persistent APT configuration, including the Mozilla source, key and pin.
apt-get -o Dir::Etc::sourcelist="$state/bootstrap.list" -o Dir::Etc::sourceparts=- update </dev/null
apt-get -o Dir::Etc::sourcelist="$state/bootstrap.list" -o Dir::Etc::sourceparts=- install -y --no-install-recommends "$config" </dev/null
apt-get update </dev/null
apt-get install -y --no-install-recommends "${release_packages[@]}" "firefox=$firefox_version" "${runtime_packages[@]}" </dev/null
useradd --create-home --uid 1000 --user-group --shell /bin/bash rungic
for group in sudo audio video input render; do
    getent group "$group" >/dev/null || groupadd --system "$group"
    usermod -aG "$group" rungic
done
passwd -l root
passwd -l rungic
localedef -i en_US -f UTF-8 en_US.UTF-8
localedef -i zh_CN -f UTF-8 zh_CN.UTF-8
apt-get check </dev/null
audit=$(dpkg --audit </dev/null)
if [ -n "$audit" ]; then printf '%s\n' "$audit" >&2; exit 1; fi
/usr/lib/rungic-clicker/venv/bin/python -m pip check
/usr/lib/rungic-clicker/venv/bin/python -c 'from rapidocr import RapidOCR; import openai, anthropic, typesafe_sdk'
dpkg-query -W -f '${Package}\t${Version}\t${Architecture}\n' > "$state/installed.tsv" </dev/null
test -s "$state/installed.tsv"
getent passwd 1000
# Retain logs, package inputs and failed roots, but omit this run's downloaded
# cache from the prepared tree. The image builder excludes APT working state.
apt-get clean </dev/null
python3 - "$source_commit" <<'PY'
import hashlib,json,sys
from pathlib import Path
state=Path('/var/lib/rungic-apt')
release=state/'release.json'
manifest=json.loads(release.read_text())
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
receipt={'schema':1, 'source_commit':sys.argv[1], 'release':manifest['version'],
         'release_sha256':digest(release), 'dpkg_status_sha256':digest(Path('/var/lib/dpkg/status')),
         'install_script_sha256':digest(state/'install-rootfs.sh'),
         'all_installation_steps_completed':True}
(state/'root-install.complete').write_text(json.dumps(receipt,indent=2)+'\n')
PY
