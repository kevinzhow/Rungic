# One-time migrations of the container's system

docs/122. `YYYYMMDD-what.sh` here goes into rungic-plasma-config as
`/usr/lib/rungic/migrations/system/`; its postinst runs the ones not yet in `/var/lib/rungic/migrations`
with `/usr/libexec/rungic-migrate`, in name order, each once, as root in the container.

- Only what cannot be done again and again: a default for existing systems, data moved, a file an older
  version left behind. What should always hold belongs in a package (files) or in `rungic-converge`
  (Android settings).
- Safe to run again if interrupted: the ledger is written only after the script succeeded.
- Forward only, and the previous release must still work afterwards: a rollback does not undo it.
- A failure is logged; that migration and the later ones run at the next configure.

Android-side migrations are `system/migrations/android/YYYYMMDD-what.sh`, listed in both Android file
lists at `/data/adb/rungic-plasma/migrations/`, and run first by `rungic-converge apply`. User settings
keep using kconf_update (`system/config/usr/share/kconf_update/rungic.upd`).
