#!/system/bin/sh
# The root provider's service.d invokes this after Android boot completion.
# Sources are either a verified root-owned independent payload or legacy product.
set -eu
set -o pipefail
[ ! -e /data/adb/rungic-uninstalling ] || { echo 'Rungic 卸载未完成，请重新运行卸载。' >&2; exit 1; }
umask 077
# Magisk and KernelSU both supply a BusyBox and run the scripts in /data/adb/service.d; the active provider is
# the one whose runtime is up (the rule of system/root-provider, which is not installed yet when
# this runs): the Magisk-only steps below are skipped under KernelSU, which keeps its own root
# allowlist (the user grants the app root in its manager instead of a SQLite row).
if [ -x /debug_ramdisk/magisk ]; then
    RUNGIC_BUSYBOX=/data/adb/magisk/busybox
    RUNGIC_MAGISK=/debug_ramdisk/magisk
elif [ -x /data/adb/ksud ]; then
    RUNGIC_BUSYBOX=/data/adb/ksu/bin/busybox
    RUNGIC_MAGISK=
else
    echo 'No active root provider: neither Magisk (/debug_ramdisk/magisk) nor KernelSU (/data/adb/ksud)' >&2
    exit 1
fi
export RUNGIC_BUSYBOX RUNGIC_MAGISK
export PATH=/data/adb/magisk:/data/adb/ksu/bin:/system/bin:/system/xbin
seed=${1:-/product/etc/rungic}
# A managed standalone installation owns the runtime. Never replay an old ROM seed.
if [ "$seed" = /product/etc/rungic ] && [ -f /data/adb/rungic-install/active.env ]; then
    exit 0
fi
. "$seed/seed.env"
mkdir -p /data/adb
# Android's shell closes high-numbered descriptors when executing toybox flock.
# The provider's BusyBox accepts a lock path and keeps the descriptor in its child.
if [ "${RUNGIC_FIRSTBOOT_LOCKED:-}" != 1 ]; then
    export RUNGIC_FIRSTBOOT_LOCKED=1
    exec "$RUNGIC_BUSYBOX" flock -n /data/adb/rungic-firstboot.lock /system/bin/sh "$0" "$seed"
fi
exec >>/data/adb/rungic-firstboot.log 2>&1
echo "$(date -Iseconds) starting Rungic seed $RELEASE_ID"
marker=/data/adb/rungic-firstboot.complete
uid_of() {
    uid=$(stat -c %u "/data/user/0/$1") || exit 1
    case "$uid" in ''|*[!0-9]*) exit 1 ;; esac
    echo "$uid"
}
rungic_uid=$(uid_of com.rungic.plasma)
rungic_files=/data/user/0/com.rungic.plasma/files
mkdir -p "$rungic_files"
chown "$rungic_uid:$rungic_uid" "$rungic_files"
rungic_label=$(ls -dZ /data/user/0/com.rungic.plasma | cut -d ' ' -f1)
chcon "$rungic_label" "$rungic_files"
# App-private, atomic, credential-free status; reading it needs no su prompt.
# This is a UI signal only. The root controller also checks the release marker.
publish() {
    status_tmp=$rungic_files/.rungic-install.properties.tmp
    printf 'schema=2\nrelease=%s\nstate=%s\nphase=%s\nupdated=%s\nerror=%s\n' "$RELEASE_ID" "$1" "$2" "$(date +%s)" "${failure_code:-none}" > "$status_tmp"
    chmod 0600 "$status_tmp"
    chown "$rungic_uid:$rungic_uid" "$status_tmp"
    chcon "$rungic_label" "$status_tmp"
    mv "$status_tmp" "$rungic_files/rungic-install.properties"
}
# Which release the APK waits for. App data can be cleared at any time, so every run
# republishes it (docs/95); a legacy product run removes a descriptor it does not own.
source_file=$rungic_files/rungic-install-source.properties
if [ "$seed" = /product/etc/rungic ]; then
    rm -f "$source_file"
else
    printf 'RELEASE_ID=%s\n' "$RELEASE_ID" > "$rungic_files/.rungic-install-source.properties.tmp"
    chmod 0600 "$rungic_files/.rungic-install-source.properties.tmp"
    chown "$rungic_uid:$rungic_uid" "$rungic_files/.rungic-install-source.properties.tmp"
    chcon "$rungic_label" "$rungic_files/.rungic-install-source.properties.tmp"
    mv "$rungic_files/.rungic-install-source.properties.tmp" "$source_file"
fi
# Copy only a launcher from the installed runtime, never an old product payload.
install_runtime_boot() {
    [ -x /data/adb/rungic-plasma/runtime-boot.sh ] || return 0
    mkdir -p /data/adb/service.d
    cp /data/adb/rungic-plasma/runtime-boot.sh /data/adb/service.d/rungic-runtime.sh.tmp
    chmod 700 /data/adb/service.d/rungic-runtime.sh.tmp
    mv /data/adb/service.d/rungic-runtime.sh.tmp /data/adb/service.d/rungic-runtime.sh
}
install_runtime_boot
if [ -f "$marker" ] && [ "$(cat "$marker")" = "$RELEASE_ID" ]; then
    publish ready complete
    echo 'already installed'
    [ ! -x /data/adb/rungic-plasma/rungic-runtime ] || /data/adb/rungic-plasma/rungic-runtime boot
    exit 0
fi
failure_code=unknown
phase=verify
provision_mounted=0
provision_bind_mounts=
finished=0
finish_exit() {
    code=$?
    trap - EXIT
    for entry in $provision_bind_mounts; do umount "$provision/$entry" 2>/dev/null || true; done
    if [ "$provision_mounted" = 1 ]; then
        umount "$provision" 2>/dev/null || true
        /data/adb/rungic-plasma/rootfs-image detach >/dev/null 2>&1 || true
    fi
    if [ "$finished" != 1 ]; then publish failed "$phase" || true; fi
    exit "$code"
}
trap finish_exit EXIT
publish installing "$phase"
die() { echo "Rungic seed failed: $*" >&2; exit 1; }
digest() { sha256sum "$1" | cut -d ' ' -f1; }
check() { [ "$(digest "$1")" = "$2" ] || { failure_code=checksum; die "SHA-256 mismatch: $1"; }; }
check "$seed/host-seed.tar.gz" "$HOST_SEED_SHA256"
check "$seed/rootfs.img.gz" "$ROOTFS_GZ_SHA256"
check "$seed/termux.apk" "$TERMUX_APK_SHA256"
check "$seed/termux-prefix.tar.gz" "$TERMUX_PREFIX_SHA256"
check "$seed/rungic.apk" "$RUNGIC_APK_SHA256"
check "$seed/rungic-sparse-write" "$SPARSE_WRITE_SHA256"

pm path com.termux >/dev/null 2>&1 || die 'Termux system app missing'
pm path com.rungic.plasma >/dev/null 2>&1 || die 'Rungic system app missing'
phase=runtime; publish installing "$phase"
termux_uid=$(uid_of com.termux)
termux_data=/data/user/0/com.termux/files
mkdir -p "$termux_data"
chown "$termux_uid:$termux_uid" "$termux_data"
if [ ! -x "$termux_data/usr/bin/pulseaudio" ]; then
    # Termux can create an empty prefix before the image seed runs. Remove only
    # an empty directory; preserve and reject any existing nonempty prefix.
    if [ -e "$termux_data/usr" ]; then
        [ ! -L "$termux_data/usr" ] && rmdir "$termux_data/usr" ||
            die 'incomplete nonempty Termux prefix already exists'
    fi
    [ ! -e "$termux_data/.rungic-stage" ] || rm -rf "$termux_data/.rungic-stage"
    mkdir "$termux_data/.rungic-stage"
    tar -xzf "$seed/termux-prefix.tar.gz" -C "$termux_data/.rungic-stage" || die 'Termux prefix extract'
    [ -x "$termux_data/.rungic-stage/usr/bin/pulseaudio" ] || die 'Termux PulseAudio absent'
    mv "$termux_data/.rungic-stage/usr" "$termux_data/usr"
    rmdir "$termux_data/.rungic-stage"
    chown -R "$termux_uid:$termux_uid" "$termux_data/usr"
    label=$(ls -dZ "/data/user/0/com.termux" | cut -d ' ' -f1)
    chcon -R "$label" "$termux_data/usr"
fi
mkdir -p "$termux_data/home"
chown "$termux_uid:$termux_uid" "$termux_data/home"
label=$(ls -dZ "/data/user/0/com.termux" | cut -d ' ' -f1)
chcon "$label" "$termux_data" "$termux_data/home"

if [ ! -x /data/adb/rungic-lxc/rungic-lxc-enter ] ||
   [ ! -x /data/adb/rungic-plasma/rungic-plasma-enter ]; then
    stage=/data/adb/.rungic-host-stage
    [ ! -e "$stage" ] || rm -rf "$stage"
    mkdir "$stage"
    tar -xzf "$seed/host-seed.tar.gz" -C "$stage" || die 'host seed extract'
    [ -x "$stage/rungic-lxc/rungic-lxc-enter" ] || die 'host seed LXC entry absent'
    [ -x "$stage/rungic-plasma/rungic-plasma-enter" ] || die 'host seed Plasma entry absent'
    for name in rungic-lxc rungic-plasma; do
        entry=$name/$name-enter
        if [ -e "/data/adb/$name" ] && [ ! -x "/data/adb/$entry" ]; then
            die "incomplete existing host component: $name"
        fi
        [ -e "/data/adb/$name" ] || mv "$stage/$name" "/data/adb/$name"
    done
    rm -rf "$stage"
    restorecon -RF /data/adb/rungic-lxc /data/adb/rungic-plasma >/dev/null 2>&1 || true
fi

# Casting (docs/58) is optional (docs/75): a failure is logged and does not stop
# the desktop install. Its boot scripts also start now, for this boot.
install_cast() {
    stage=/data/adb/.rungic-wfd-stage
    [ ! -e "$stage" ] || rm -rf "$stage"
    mkdir "$stage"
    tar -xzf "$seed/host-seed.tar.gz" -C "$stage" rungic-wfd || return 1
    /system/bin/sh "$stage/rungic-wfd/install.sh" "$stage/rungic-wfd" || return 1
    rm -rf "$stage"
    # The rule names Qualcomm WFD domains (verified on SM6435); another vendor
    # policy may lack them, which leaves the rest of casting in place.
    /system/bin/sh /data/adb/service.d/rungic-wfd-sepolicy.sh || echo 'WFD policy not applied'
    # Close the install lock (BusyBox flock's descriptor) in the long-lived watcher.
    "$RUNGIC_BUSYBOX" setsid /system/bin/sh -c \
        'exec 3>&- 4>&- 5>&- 6>&- 7>&- 8>&- 9>&-; exec /system/bin/sh /data/adb/service.d/rungic-cast-watch.sh' \
        </dev/null >/dev/null 2>&1 &
}
if ! /system/bin/sh /data/adb/rungic-wfd/install.sh --check >/dev/null 2>&1; then
    if install_cast; then echo 'casting installed'; else echo 'casting install failed (optional)'; fi
fi

images=/data/adb/rungic-lxc/images
phase=rootfs; publish installing "$phase"
mkdir -p "$images"
image=$images/rootfs.img
image_marker=$images/rootfs.seeded
if [ -e "$image" ]; then
    if [ ! -f "$image_marker" ] || [ "$(cat "$image_marker")" != "$ROOTFS_SHA256" ]; then
        check "$image" "$ROOTFS_SHA256"
    fi
else
    # Conservative reservation for a complete image; no reliance on sparse support.
    free_kib=$(df -k "$images" | tail -n 1 | awk '{print $4}')
    case "$free_kib" in ''|*[!0-9]*) die 'free space check unavailable' ;; esac
    awk -v free="$free_kib" -v bytes="$ROOTFS_BYTES" 'BEGIN { exit !(free >= bytes / 1024 + 524288) }' || { failure_code=space; die 'insufficient image reserve'; }
    rm -f "$image.part"
    gzip -dc "$seed/rootfs.img.gz" |
        "$seed/rungic-sparse-write" "$image.part" "$ROOTFS_BYTES" || die 'rootfs decompression'
    check "$image.part" "$ROOTFS_SHA256"
    mv "$image.part" "$image"
    sync
fi
echo "$ROOTFS_SHA256" > "$image_marker.tmp"
mv "$image_marker.tmp" "$image_marker"

# Device-local network settings belong to the device spec, not the shared ARM64 rootfs.
provision=/data/adb/.rungic-rootfs-provision
phase=configure; publish installing "$phase"
mkdir -p "$provision"
root_device=$(/data/adb/rungic-plasma/rootfs-image attach) || die 'rootfs mapper attach'
mount -t ext4 -o noatime "$root_device" "$provision" || die 'rootfs provision mount'
provision_mounted=1
for entry in dev proc sys; do
    mount --bind "/$entry" "$provision/$entry" || die "rootfs provision $entry"
    provision_bind_mounts="$entry $provision_bind_mounts"
done
mkdir -p "$provision/var/log/plasma" "$provision/etc/profile.d"
# Host keys are device identity: image builds remove them, installation generates them.
# Do this before systemd/socket activation can accept connections.
chroot "$provision" /usr/bin/ssh-keygen -A || die 'SSH host key generation'
chroot "$provision" /usr/bin/systemctl unmask ssh.socket ssh.service || die 'SSH unmask'
chroot "$provision" /usr/bin/systemctl enable ssh.socket || die 'SSH automatic access'
if [ -n "$PHONE_HTTP_PROXY" ]; then
    cat > "$provision/etc/profile.d/proxy.sh" <<EOF
export http_proxy=$PHONE_HTTP_PROXY
export https_proxy=$PHONE_HTTP_PROXY
export HTTP_PROXY=$PHONE_HTTP_PROXY
export HTTPS_PROXY=$PHONE_HTTP_PROXY
EOF
    chmod 0644 "$provision/etc/profile.d/proxy.sh"
fi
sync
for entry in $provision_bind_mounts; do
    umount "$provision/$entry" || die "rootfs provision $entry unmount"
done
provision_bind_mounts=
umount "$provision" || die 'rootfs provision unmount'
provision_mounted=0
/data/adb/rungic-plasma/rootfs-image detach || die 'rootfs mapper detach'

phase=storage; publish installing "$phase"
# Boot-complete precedes unlock/storage readiness on some devices. Wait for the
# real Android storage instead of creating a hidden folder under an empty mount.
storage_attempt=0
while [ ! -d /storage/emulated/0/Android ]; do
    publish waiting "$phase"
    storage_attempt=$((storage_attempt + 1))
    [ "$storage_attempt" -lt 300 ] || { failure_code=storage; die 'shared storage wait expired'; }
    sleep 2
done
publish installing "$phase"
mkdir -p "$rungic_files/tmp" /storage/emulated/0/Plasma
chown "$rungic_uid:$rungic_uid" "$rungic_files" "$rungic_files/tmp"
label=$(ls -dZ /data/user/0/com.rungic.plasma | cut -d ' ' -f1)
chcon "$label" "$rungic_files" "$rungic_files/tmp"
/data/adb/rungic-plasma/android-audio prepare || die 'audio directory preparation'
/data/adb/rungic-plasma/rungic-plasma-enter /bin/true || die 'shared mount preflight'
phase=finish; publish installing "$phase"
install_runtime_boot
# Casting shows the Linux desktop on the TV in an overlay window (docs/58), which needs this
# app op; default-permissions cannot grant it. Some first boots refused appops from Magisk's
# root context (docs/79), so the shell identity is the fallback, and the app asks again
# itself when a TV appears. Optional (docs/75): a refusal does not stop the install.
overlay='appops set com.rungic.plasma SYSTEM_ALERT_WINDOW allow'
if sh -c "$overlay" || { [ -n "$RUNGIC_MAGISK" ] && "$RUNGIC_MAGISK" su 2000 -c "$overlay"; }; then
    echo 'overlay allowed'
else
    echo 'overlay not allowed; the app asks when casting (optional)'
fi
if [ -n "$RUNGIC_MAGISK" ]; then
    # Fixed Magisk 31.0 schema; INSERT returns no SQL NULL (docs/39, docs/70).
    "$RUNGIC_MAGISK" --sqlite "INSERT OR REPLACE INTO policies (uid,policy,until,logging,notification) VALUES($rungic_uid,2,0,1,1)" || die 'Magisk policy'
else
    # KernelSU keeps its own allowlist; there is no equivalent SQLite write. The install does
    # not depend on it: the user grants com.rungic.plasma root in the KernelSU manager.
    echo 'KernelSU root provider: grant com.rungic.plasma root in its manager'
fi
echo "$RELEASE_ID" > "$marker.tmp"
mv "$marker.tmp" "$marker"
sync
failure_code=none
publish ready complete
finished=1
echo "$(date -Iseconds) Rungic seed complete"
[ ! -x /data/adb/rungic-plasma/rungic-runtime ] || /data/adb/rungic-plasma/rungic-runtime boot
