// SPDX-License-Identifier: MIT
package com.rungic.cast;

import java.io.File;

/** The root provider's BusyBox, resolved at use. Magisk and KernelSU install it at different
 * paths, and casting is optional, so a missing provider must not crash the caller. */
final class RootProvider {
    private RootProvider() {}

    static String busybox() {
        for (String path : new String[]{"/data/adb/magisk/busybox", "/data/adb/ksu/bin/busybox"}) {
            if (new File(path).canExecute()) return path;
        }
        return "/data/adb/magisk/busybox";
    }
}
