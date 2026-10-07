#!/bin/sh
# Shared startup gate: GLib watches NameOwnerChanged before querying the current owner.
# Do not activate the dependency. Keep the overlay's 120 s bound, exit code and message.
name=${1:?requires a session bus name}
seconds=${2:-120}
case "$seconds" in ''|*[!0-9]*) echo "requires a timeout of 1–120 seconds" >&2; exit 2;; esac
if ! [ "$seconds" -gt 0 ] || ! [ "$seconds" -le 120 ]; then
    echo "requires a timeout of 1–120 seconds" >&2
    exit 2
fi
if gdbus wait --session --timeout="$seconds" "$name"; then
    exit 0
fi
case "$name" in org.kde.plasmashell) label=plasmashell;; *) label=$name;; esac
echo "$label not on the session bus after $seconds s" >&2
exit 1
