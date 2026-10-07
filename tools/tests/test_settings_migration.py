# SPDX-License-Identifier: MIT
"""The one-time user migrations of rungic.upd (system/config/usr/share/kconf_update, docs/61): the
real scripts run as kconf_update runs them (sh, the user's HOME), on a user's configuration in a
temporary home. KDE's kreadconfig6/kwriteconfig6 are not on a development machine: stand-ins on PATH
read and write the same KConfig files (groups and keys, other lines kept), and systemctl records
its calls."""
import configparser
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UPDATE = ROOT / 'system/config/usr/share/kconf_update'
SESSION = ROOT / 'desktop/session'

CAST = 'com.rungic.quicksetting.cast'
SCREEN = 'com.rungic.quicksetting.agentscreen'
BLUETOOTH = 'org.kde.plasma.quicksetting.bluetooth'
WIFI = 'org.kde.plasma.quicksetting.wifi'
OLD_RECORD = 'org.kde.plasma.quicksetting.record'
NEW_RECORD = 'com.rungic.quicksetting.record'

# kreadconfig6 / kwriteconfig6 for one group and key, as the scripts call them: --file NAME (in
# $XDG_CONFIG_HOME, else ~/.config), --group G, --key K; kwriteconfig6 VALUE or --delete.
KCONFIG = r'''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
opt = {}
value = None
delete = False
while args:
    a = args.pop(0)
    if a in ('--file', '--group', '--key'):
        opt[a[2:]] = args.pop(0)
    elif a == '--delete':
        delete = True
    else:
        value = a
base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.environ['HOME'], '.config')
path = opt['file'] if opt['file'].startswith('/') else os.path.join(base, opt['file'])
lines = open(path).read().splitlines() if os.path.exists(path) else []
header = '[%s]' % opt['group']
group = None
found = None
for i, line in enumerate(lines):
    if line.startswith('['):
        group = line
    elif group == header and line.split('=', 1)[0] == opt['key']:
        found = i
if os.path.basename(sys.argv[0]) == 'kreadconfig6':
    if found is not None:
        print(lines[found].split('=', 1)[1])
    sys.exit(0)
if delete:
    if found is not None:
        del lines[found]
elif found is not None:
    lines[found] = '%s=%s' % (opt['key'], value)
elif header in lines:
    lines.insert(lines.index(header) + 1, '%s=%s' % (opt['key'], value))
else:
    lines += ([''] if lines else []) + [header, '%s=%s' % (opt['key'], value)]
os.makedirs(os.path.dirname(path), exist_ok=True)
open(path, 'w').write('\n'.join(lines) + '\n')
'''


@pytest.fixture
def home(tmp_path):
    tools = tmp_path / 'bin'
    tools.mkdir()
    for name in ('kreadconfig6', 'kwriteconfig6'):
        (tools / name).write_text(KCONFIG)
        (tools / name).chmod(0o755)
    (tools / 'systemctl').write_text('#!/bin/sh\necho "$*" >> "$HOME/systemctl.log"\n')
    (tools / 'systemctl').chmod(0o755)
    user = tmp_path / 'home'
    (user / '.config').mkdir(parents=True)
    return user


def migrate(home, script):
    """Runs one migration as kconf_update does (rungic.upd: Script=NAME,sh)."""
    env = {'HOME': str(home), 'PATH': f'{home.parent / "bin"}:/usr/bin:/bin', 'LANG': 'C.UTF-8'}
    subprocess.run(['sh', str(UPDATE / script)], env=env, check=True, cwd=home)


def config(home, name):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    path = home / '.config' / name
    if path.exists():
        parser.read_string(path.read_text())
    return parser


def quick_settings(home, key='enabledQuickSettings'):
    c = config(home, 'plasmamobilerc')
    return c['QuickSettings'][key].split(',') if c.has_option('QuickSettings', key) else None


def write(home, name, text):
    path = home / '.config' / name
    path.write_text(text)
    return path


# covers: desktop.settings-migration/E1
def test_cast_goes_after_bluetooth_and_the_assistant_screen_after_cast(home):
    write(home, 'plasmamobilerc', f'[QuickSettings]\nenabledQuickSettings={WIFI},{BLUETOOTH},{OLD_RECORD}\n')
    migrate(home, 'rungic-quicksettings.sh')
    assert quick_settings(home) == [WIFI, BLUETOOTH, CAST, SCREEN, OLD_RECORD]


# covers: desktop.settings-migration/E1
def test_without_bluetooth_cast_goes_last_and_a_placed_cast_stays_where_the_user_put_it(home):
    write(home, 'plasmamobilerc', f'[QuickSettings]\nenabledQuickSettings={WIFI},{OLD_RECORD}\n')
    migrate(home, 'rungic-quicksettings.sh')
    assert quick_settings(home) == [WIFI, OLD_RECORD, CAST, SCREEN]

    write(home, 'plasmamobilerc', f'[QuickSettings]\nenabledQuickSettings={CAST},{WIFI},{BLUETOOTH}\n')
    migrate(home, 'rungic-quicksettings.sh')
    assert quick_settings(home) == [CAST, SCREEN, WIFI, BLUETOOTH]


# covers: desktop.settings-migration/E1
def test_the_default_list_is_left_alone(home):
    write(home, 'plasmamobilerc', '[General]\nsomething=1\n')
    migrate(home, 'rungic-quicksettings.sh')
    assert quick_settings(home) is None, 'Plasma Mobile shows new tiles in its default list by itself'


# covers: desktop.settings-migration/E2
def test_the_recording_tile_takes_the_old_ones_place(home):
    write(home, 'plasmamobilerc', f'[QuickSettings]\nenabledQuickSettings={WIFI},{OLD_RECORD},{BLUETOOTH}\n')
    migrate(home, 'rungic-recording-quicksetting.sh')
    assert quick_settings(home) == [WIFI, NEW_RECORD, BLUETOOTH]
    assert quick_settings(home, 'disabledQuickSettings') == [OLD_RECORD]


# covers: desktop.settings-migration/E2
def test_a_user_who_disabled_recording_gets_the_new_tile_disabled(home):
    write(home, 'plasmamobilerc', f'[QuickSettings]\nenabledQuickSettings={WIFI},{BLUETOOTH}\n'
                                  f'disabledQuickSettings={OLD_RECORD}\n')
    migrate(home, 'rungic-recording-quicksetting.sh')
    assert quick_settings(home) == [WIFI, BLUETOOTH]
    assert quick_settings(home, 'disabledQuickSettings') == [OLD_RECORD, NEW_RECORD]


# covers: desktop.settings-migration/E2
def test_with_the_default_list_the_new_tile_is_where_recording_was(home):
    migrate(home, 'rungic-recording-quicksetting.sh')
    script = (UPDATE / 'rungic-recording-quicksetting.sh').read_text()
    default = re.search(r'^DEFAULT=(.*)$', script, re.M).group(1).split(',')
    assert quick_settings(home) == [NEW_RECORD if t == OLD_RECORD else t for t in default]
    assert quick_settings(home, 'disabledQuickSettings') == [OLD_RECORD]


# covers: desktop.settings-migration/E1 desktop.settings-migration/E2
def test_running_again_changes_nothing(home):
    write(home, 'plasmamobilerc', f'[QuickSettings]\nenabledQuickSettings={WIFI},{BLUETOOTH},{OLD_RECORD}\n')
    for _ in range(2):
        migrate(home, 'rungic-quicksettings.sh')
        migrate(home, 'rungic-recording-quicksetting.sh')
    assert quick_settings(home) == [WIFI, BLUETOOTH, CAST, SCREEN, NEW_RECORD]
    assert quick_settings(home, 'disabledQuickSettings') == [OLD_RECORD]


OLD_IM = '/usr/local/share/applications/moto-plasma-rime.desktop'
NEW_IM = '/usr/share/applications/rungic-plasma-rime.desktop'


# covers: desktop.settings-migration/E3
def test_old_usr_local_paths_move_to_the_packages(home):
    write(home, 'kwinrc', f'[Wayland]\nInputMethod={OLD_IM}\n')
    portal = home / '.config/systemd/user/plasma-xdg-desktop-portal-kde.service.d/moto.conf'
    portal.parent.mkdir(parents=True)
    portal.write_text('[Service]\nExecStart=\nExecStart=/usr/local/libexec/xdg-desktop-portal-kde\n')
    codex = home / '.codex'
    (codex / 'skills').mkdir(parents=True)
    (codex / 'config.toml').write_text('[mcp_servers.desktop]\ncommand = "/usr/local/bin/moto-cua"\n'
                                       '[mcp_servers.mine]\ncommand = "/usr/local/bin/my-tool"\n')
    (codex / 'skills/moto-phone-desktop').symlink_to('/usr/local/share/moto-voice-agent/skills/moto-phone-desktop')
    wants = home / '.config/systemd/user/plasma-workspace.target.wants'
    wants.mkdir(parents=True)
    (wants / 'moto-plasma-display.service').symlink_to('/etc/systemd/user/moto-plasma-display.service')
    migrate(home, 'rungic-usr-paths.sh')
    assert config(home, 'kwinrc')['Wayland']['InputMethod'] == NEW_IM
    assert not portal.parent.exists(), 'the old per-user portal override is gone with its directory'
    toml = (codex / 'config.toml').read_text()
    assert 'command = "/usr/bin/rungic-cua"' in toml and 'command = "/usr/local/bin/my-tool"' in toml
    assert os.readlink(codex / 'skills/moto-phone-desktop') == '/usr/share/rungic-voice-agent/skills/rungic-phone-desktop'
    assert not (wants / 'moto-plasma-display.service').is_symlink()
    assert (home / 'systemctl.log').read_text().split('\n')[0] == '--user daemon-reload'


# covers: desktop.settings-migration/E3
def test_values_the_user_chose_are_left_alone(home):
    write(home, 'kwinrc', '[Wayland]\nInputMethod=/usr/share/applications/org.kde.plasma.keyboard.desktop\n')
    portal = home / '.config/systemd/user/plasma-xdg-desktop-portal-kde.service.d/moto.conf'
    portal.parent.mkdir(parents=True)
    portal.write_text('[Service]\nEnvironment=MINE=1\n')
    codex = home / '.codex'
    (codex / 'skills').mkdir(parents=True)
    (codex / 'skills/moto-phone-desktop').symlink_to('/home/me/my-skill')
    wants = home / '.config/systemd/user/plasma-workspace.target.wants'
    wants.mkdir(parents=True)
    (wants / 'moto-plasma-display.service').symlink_to('/home/me/my-unit.service')
    migrate(home, 'rungic-usr-paths.sh')
    assert config(home, 'kwinrc')['Wayland']['InputMethod'] == '/usr/share/applications/org.kde.plasma.keyboard.desktop'
    assert portal.read_text() == '[Service]\nEnvironment=MINE=1\n'
    assert os.readlink(codex / 'skills/moto-phone-desktop') == '/home/me/my-skill'
    assert (wants / 'moto-plasma-display.service').is_symlink()
    assert not (home / 'systemctl.log').exists(), 'nothing removed: no reload'


# covers: desktop.settings-migration/E3
def test_the_old_firefox_launcher_goes_and_the_users_own_stays(home):
    apps = home / '.local/share/applications'
    apps.mkdir(parents=True)
    (apps / 'firefox.desktop').write_text('# Moto: all desktop actions use the private-codec launch wrapper\n[Desktop Entry]\n')
    migrate(home, 'rungic-firefox-launcher.sh')
    assert not (apps / 'firefox.desktop').exists()

    (apps / 'firefox.desktop').write_text('# Moto: all desktop actions use the private-codec launch wrapper\n[Desktop Entry]\n')
    (apps / 'firefox.desktop.pre-moto-launcher').write_text('[Desktop Entry]\nName=Mine\n')
    migrate(home, 'rungic-firefox-launcher.sh')
    assert (apps / 'firefox.desktop').read_text() == '[Desktop Entry]\nName=Mine\n', 'the backup is restored'

    migrate(home, 'rungic-firefox-launcher.sh')
    assert (apps / 'firefox.desktop').read_text() == '[Desktop Entry]\nName=Mine\n', "the user's own launcher stays"


# covers: desktop.settings-migration/E5
def test_a_new_account_gets_rime_and_chinese_with_english(home):
    # plasma-mobile's own kwinrc lives elsewhere (XDG_CONFIG_DIRS); the user's has no input method.
    write(home, 'kwinrc', '[Windows]\nPlacement=Maximizing\n')
    migrate(home, 'rungic-keyboard-defaults.sh')
    assert config(home, 'kwinrc')['Wayland']['InputMethod'] == NEW_IM
    assert config(home, 'kwinrc')['Windows']['Placement'] == 'Maximizing', 'other settings are kept'
    assert config(home, 'plasmakeyboardrc')['General']['enabledLocales'] == 'zh_CN,en_US'


# covers: desktop.settings-migration/E5
def test_a_keyboard_and_languages_the_user_chose_stay(home):
    write(home, 'kwinrc', '[Wayland]\nInputMethod=/usr/share/applications/org.kde.plasma.keyboard.desktop\n')
    write(home, 'plasmakeyboardrc', '[General]\nenabledLocales=en_US\n')
    migrate(home, 'rungic-keyboard-defaults.sh')
    assert config(home, 'kwinrc')['Wayland']['InputMethod'] == '/usr/share/applications/org.kde.plasma.keyboard.desktop'
    assert config(home, 'plasmakeyboardrc')['General']['enabledLocales'] == 'en_US'


# covers: desktop.settings-migration/E5
def test_a_keyboard_config_the_floating_keyboard_rewrote_gets_its_languages_back(home):
    # What QML's Settings (QSettings) left: the list with spaces (KConfig reads " en_US") and a
    # [%General] group of its own.
    write(home, 'plasmakeyboardrc', '[General]\nenabledLocales=zh_CN, en_US\n\n[%General]\nenabledLocales=@Invalid()\n')
    migrate(home, 'rungic-keyboard-repair.sh')
    c = config(home, 'plasmakeyboardrc')
    assert c['General']['enabledLocales'] == 'zh_CN,en_US'
    assert not c.has_option('%General', 'enabledLocales')


# covers: desktop.settings-migration/E5
def test_the_keyboard_repair_keeps_a_clean_or_missing_config(home):
    migrate(home, 'rungic-keyboard-repair.sh')
    assert not (home / '.config/plasmakeyboardrc').exists(), 'no file is created'
    write(home, 'plasmakeyboardrc', '[General]\nenabledLocales=en_US,ja_JP\n')
    migrate(home, 'rungic-keyboard-repair.sh')
    assert (home / '.config/plasmakeyboardrc').read_text() == '[General]\nenabledLocales=en_US,ja_JP\n'


def upd_entries():
    entries, current = [], None
    for line in (UPDATE / 'rungic.upd').read_text().splitlines():
        if line.startswith('Id='):
            current = {'Id': line[3:]}
            entries.append(current)
        elif line.startswith('Script=') and current is not None:
            current['Script'] = line[7:]
    return entries


# covers: desktop.settings-migration/E4
def test_each_migration_is_one_kconf_update_step_run_once_before_kwin():
    text = (UPDATE / 'rungic.upd').read_text()
    assert re.search(r'^Version=6$', text, re.M), 'KF6 kconf_update runs only Version=6 files'
    entries = upd_entries()
    ids = [e['Id'] for e in entries]
    assert len(ids) == len(set(ids)), 'kconf_update records each Id once in kconf_updaterc'
    scripts = sorted(e['Script'].split(',')[0] for e in entries)
    assert scripts == sorted(p.name for p in UPDATE.glob('*.sh')), 'every migration script is a step, and only they'
    # The session runs kconf_update itself before startplasmamobile starts KWin (kded would run it
    # only after KWin had read its configuration), offscreen because there is no compositor yet.
    session = SESSION.read_text()
    run = session.index('QT_QPA_PLATFORM=offscreen /usr/lib/aarch64-linux-gnu/libexec/kf6/kconf_update')
    assert run < session.index('exec /usr/bin/startplasmamobile')


# covers: desktop.settings-migration/E4
def test_no_migration_guesses_the_display_scale():
    for script in UPDATE.glob('*.sh'):
        text = script.read_text()
        assert 'kwinoutputconfig' not in text and not re.search(r'[Ss]cale', text), script.name
