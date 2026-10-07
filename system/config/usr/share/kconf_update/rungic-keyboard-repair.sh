#!/bin/sh
# kconf_update script (rungic.upd, Id=rungic-keyboard-repair-v1): undoes what agent/screen's floating
# keyboard did to plasmakeyboardrc while it read the file with QML's Settings (QSettings): a
# [%General] group with enabledLocales=@Invalid(), and the list rewritten as "zh_CN, en_US", which
# KConfig reads with a leading space (" en_US"), so the phone keyboard had one language left
# (G100, 2026-10-08, docs/121). The user's choice of languages is kept; only the spaces go.
set -eu
file=${XDG_CONFIG_HOME:-$HOME/.config}/plasmakeyboardrc
[ -f "$file" ] || exit 0
locales=$(kreadconfig6 --file "$file" --group General --key enabledLocales)
case $locales in
  *' '*) kwriteconfig6 --file "$file" --group General --key enabledLocales "$(printf '%s' "$locales" | tr -d ' ')" ;;
esac
if [ -n "$(kreadconfig6 --file "$file" --group %General --key enabledLocales)" ]; then
    kwriteconfig6 --file "$file" --group %General --key enabledLocales --delete
fi
