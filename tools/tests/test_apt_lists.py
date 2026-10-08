"""Package lists on a fresh install (docs/122): a rootfs image ships none."""
import fnmatch
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / 'system/config/usr/lib/systemd/system/rungic-apt-lists.service'


# covers: delivery.packaging/E6
def test_missing_ubuntu_lists_are_fetched_once_the_network_is_up():
    text = UNIT.read_text()
    condition = next(line.split('=', 1)[1] for line in text.splitlines() if line.startswith('ConditionPathExistsGlob='))
    assert condition.startswith('!')
    pattern = condition[1:]
    # The lists apt writes for Ubuntu's sources, by either host; not this project's own repository.
    for present in ('/var/lib/apt/lists/ports.ubuntu.com_ubuntu-ports_dists_resolute_InRelease',
                    '/var/lib/apt/lists/archive.ubuntu.com_ubuntu_dists_resolute-updates_InRelease'):
        assert fnmatch.fnmatch(present, pattern), present
    assert not fnmatch.fnmatch('/var/lib/apt/lists/_var_lib_rungic-apt_._Release', pattern)
    assert 'After=network-online.target' in text and 'ExecStart=/usr/bin/apt-get -q update' in text
    assert 'Restart=on-failure' in text and 'WantedBy=multi-user.target' in text
    package = json.loads((ROOT / 'packaging/rungic-plasma-config/package.json').read_text())
    assert 'rungic-apt-lists.service' in package['units']['system']
