"""Paths owned by standalone installation and removal. No device-side deletion glob."""
PATHS = {
    'RUNGIC_TERMUX_STAGE': '/data/data/com.termux/files/.rungic-stage',
    'RUNGIC_TERMUX_AUDIO': '/data/data/com.termux/files/usr/tmp/rungic-plasma-audio',
    'RUNGIC_PAYLOAD': '/data/adb/rungic-install',
    'RUNGIC_LXC': '/data/adb/rungic-lxc',
    'RUNGIC_CONTROLLER': '/data/adb/rungic-plasma',
    'RUNGIC_CAST': '/data/adb/rungic-wfd',
    'RUNGIC_LEGACY': '/data/adb/rungic-install-legacy',
    'RUNGIC_HOST_STAGE': '/data/adb/.rungic-host-stage',
    'RUNGIC_CAST_STAGE': '/data/adb/.rungic-wfd-stage',
    'RUNGIC_PROVISION': '/data/adb/.rungic-rootfs-provision',
    'RUNGIC_COMPLETE': '/data/adb/rungic-firstboot.complete',
    'RUNGIC_BOOT_LOG': '/data/adb/rungic-firstboot.log',
    'RUNGIC_LAUNCH_LOG': '/data/adb/rungic-install-launch.log',
    'RUNGIC_BOOT_SERVICE': '/data/adb/service.d/00-rungic-firstboot.sh',
    'RUNGIC_RUNTIME_SERVICE': '/data/adb/service.d/rungic-runtime.sh',
    'RUNGIC_RUNTIME_SERVICE_TMP': '/data/adb/service.d/rungic-runtime.sh.tmp',
    'RUNGIC_CAST_SERVICE': '/data/adb/service.d/rungic-cast-watch.sh',
    'RUNGIC_CAST_SERVICE_TMP': '/data/adb/service.d/rungic-cast-watch.sh.new',
    'RUNGIC_CAST_POLICY_TMP': '/data/adb/service.d/rungic-wfd-sepolicy.sh.new',
    'RUNGIC_CAST_POLICY': '/data/adb/service.d/rungic-wfd-sepolicy.sh',
}
APP = 'com.rungic.plasma'
APP_DATA = '/data/user/0/' + APP
HOME = PATHS['RUNGIC_LXC'] + '/runtime/var/lib/lxc/plasma/state/home'
PRESERVED = '/data/adb/rungic-preserved'
COMPAT = '/data/adb/modules/rungic-install-compat'
UNINSTALLED = '/data/adb/rungic-uninstalled'
PENDING = '/data/adb/rungic-uninstalling'
MAINTENANCE_LOCK = '/data/adb/rungic-maintenance.lock'
FIRSTBOOT_LOCK = '/data/adb/rungic-firstboot.lock'
# Lock inodes remain: removing a held lock permits a second owner to create a new inode.
RETAINED = {
    PENDING: '卸载未完成时阻止重新安装。完成并独立读回后清除此标记。',
    '/data/adb/magisk': '多个应用共用的 Magisk 工具，保留。',
    '/data/adb/magisk/busybox': '多个工具共用的 Magisk BusyBox，保留。',
    '/data/adb/service.d': '共享服务目录，保留。只删除 Rungic 的服务文件。',
    COMPAT: '阻止只读系统分区中的旧种子重新安装。新安装可替换此拦截。',
    UNINSTALLED: '新安装发布前阻止回退到旧种子。',
    MAINTENANCE_LOCK: '协调安装与卸载，保留锁文件。',
    FIRSTBOOT_LOCK: '协调卸载与首启进程，保留锁文件。',
    '/data/adb/rungic-install.lock': '保留安装锁的原文件，避免出现两个锁持有者。',
    '/data/adb/rungic-cast-install.lock': '保留投屏安装锁的原文件，避免出现两个锁持有者。',
    PRESERVED: '保留以前保存的家目录及其保存记录。',
    '/storage/emulated/0/Plasma': 'Android 用户文件不在卸载范围内，保留。',
    '/product/app/Rungic/Rungic.apk': 'Android 只读系统分区中的底座 APK，保留。',
    '/product/etc/rungic': 'Android 只读系统分区中的底座种子，保留。',
    '/data/data/com.termux': '保留 Termux、前缀和无关用户文件。只删除计划内两个 Rungic 私有路径。',
}


def staging_path(release):
    # Caller validates release before it reaches a shell.
    return '/data/local/tmp/rungic-' + release
