#!/bin/sh
# kconf_update script (rungic.upd, Id=rungic-keyboard-defaults-v1): a new account types Chinese with
# Rime and can switch to English (docs/41). plasma-mobile writes its own kwinrc (in XDG_CONFIG_DIRS)
# naming the upstream keyboard, and plasma-keyboard enables only the system language when the user
# chose none: without this the first account got neither Rime nor English (G100, 2026-10-07).
# Only the user's own files are read (an absolute path: no cascade), so a choice the user made stays.
set -eu
config=${XDG_CONFIG_HOME:-$HOME/.config}
mkdir -p "$config"
if [ -z "$(kreadconfig6 --file "$config/kwinrc" --group Wayland --key InputMethod)" ]; then
    kwriteconfig6 --file "$config/kwinrc" --group Wayland --key InputMethod /usr/share/applications/rungic-plasma-rime.desktop
fi
# The same languages agent/screen's floating keyboard assumes when none are set.
if [ -z "$(kreadconfig6 --file "$config/plasmakeyboardrc" --group General --key enabledLocales)" ]; then
    kwriteconfig6 --file "$config/plasmakeyboardrc" --group General --key enabledLocales zh_CN,en_US
fi
