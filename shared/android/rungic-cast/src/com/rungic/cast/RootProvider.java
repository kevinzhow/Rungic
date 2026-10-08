// SPDX-License-Identifier: MIT
package com.rungic.cast;

import java.io.File;

/** The root provider's BusyBox, resolved at use. Magisk and KernelSU install it at different
 * paths. The rule of system/root-provider: the active provider is the one whose runtime is up,
 * Magisk's /debug_ramdisk/magisk exists only while Magisk runs, so a phone that moved to KernelSU
 * with Magisk's /data/adb/magisk left behind is KernelSU. Casting is optional, so a missing
 * provider must not crash the caller: the path then names no program and starting it fails. */
final class RootProvider {
    private RootProvider() {}

    static String busybox() {
        if (new File("/debug_ramdisk/magisk").canExecute()) return "/data/adb/magisk/busybox";
        if (new File("/data/adb/ksud").canExecute()) return "/data/adb/ksu/bin/busybox";
        return "/nonexistent/rungic-no-root-provider/busybox";
    }
}
