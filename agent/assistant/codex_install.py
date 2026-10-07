# SPDX-License-Identifier: GPL-2.0-or-later
"""Codex as OpenAI ships it: the official standalone installation in the user's home, installed and
updated from Settings with the official script (docs/99). The system has no Codex of its own; the
rungic-codex package is only /usr/bin/codex, which runs this installation behind the user's proxy.

Codex's server serves each client version its own model catalog (a newer model reaches only newer
clients, docs/98), so the agent follows OpenAI's stable releases: a newer one is found the way
Codex itself looks (GitHub's latest release, which is never a pre-release) and shown in Settings;
the user starts the update, which is the command `codex update` runs for a standalone install.
"""
import json
import os
import re
import stat
import time
import urllib.request
from pathlib import Path

LATEST_RELEASE_URL = 'https://api.github.com/repos/openai/codex/releases/latest'
INSTALL_URL = 'https://chatgpt.com/codex/install.sh'
LAUNCHER = '/usr/bin/codex'                  # rungic-codex: the proxy, then the standalone Codex
CHECK_EVERY_S = 6 * 3600


def codex_home():
    return Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')


def binary():
    return codex_home() / 'packages/standalone/current/bin/codex'


def standalone():
    """The installed standalone Codex (its `current` release), or None."""
    path = binary()
    return path if os.access(path, os.X_OK) else None


def installed():
    """Check the same standalone binary independently of RPC; unreadable state stays unknown."""
    path = binary()
    try:
        return True if stat.S_ISREG(path.stat().st_mode) else None
    except FileNotFoundError:
        return False
    except OSError:
        return None


def command():
    """What runs Codex: the launcher (it adds the proxy) when it is there, else the standalone binary."""
    if not standalone():
        return None
    return LAUNCHER if os.access(LAUNCHER, os.X_OK) else str(standalone())


def install_command():
    """The official script, as `codex update` runs it for a standalone install (codex-rs
    tui/src/update_action.rs: StandaloneUnix), behind the user's proxy. The latest stable release."""
    return ['sh', '-c', '[ ! -r /etc/profile.d/proxy.sh ] || . /etc/profile.d/proxy.sh; '
                        f'curl -fsSL {INSTALL_URL} | CODEX_NON_INTERACTIVE=1 sh']


def parse_version(text):
    """'codex-cli 0.159.2' or 'rust-v0.159.2' -> (0, 159, 2); None for anything else (pre-releases too)."""
    m = re.search(r'(\d+)\.(\d+)\.(\d+)(-[0-9A-Za-z.]+)?\s*$', (text or '').strip())
    if not m or m.group(4):
        return None
    return tuple(int(x) for x in m.groups()[:3])


def version_text(parts):
    return '.'.join(map(str, parts)) if parts else ''


def latest_release(opener=urllib.request.urlopen, timeout=15):
    """OpenAI's latest stable Codex release: GitHub's /releases/latest, which leaves out drafts and
    pre-releases (the endpoint Codex's own update check reads, codex-rs tui/src/updates.rs)."""
    request = urllib.request.Request(LATEST_RELEASE_URL, headers={'Accept': 'application/vnd.github+json',
                                                                  'User-Agent': 'rungic-voice-agent'})
    with opener(request, timeout=timeout) as reply:
        data = json.loads(reply.read())
    if data.get('draft') or data.get('prerelease'):
        raise ValueError('not a stable release')
    parts = parse_version(data.get('tag_name', ''))
    if not parts:
        raise ValueError(f"unexpected tag {data.get('tag_name')!r}")
    return parts


class UpdateCheck:
    """The installed version against the latest stable one, read at most every CHECK_EVERY_S."""

    def __init__(self, installed, latest=latest_release, clock=time.time):
        self.installed, self.latest, self.clock = installed, latest, clock
        self.result = {'installed': '', 'latest': '', 'available': False, 'checked': 0, 'error': ''}

    def check(self, force=False):
        installed = self.installed()
        now = self.clock()
        if force or not self.result['checked'] or now - self.result['checked'] >= CHECK_EVERY_S:
            try:
                self.result.update(latest=version_text(self.latest()), error='')
            except Exception as error:  # noqa: BLE001  (offline: keep the last answer)
                self.result['error'] = str(error) or type(error).__name__
            self.result['checked'] = now
        self.result['installed'] = version_text(installed)
        latest = parse_version(self.result['latest'])
        self.result['available'] = bool(installed and latest and latest > installed)
        return dict(self.result)
