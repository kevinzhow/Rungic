// SPDX-License-Identifier: MIT
package com.rungic.plasma;

import java.io.File;

/** The root provider's su. Our own images preinstall Magisk at /product/bin, while a stock Magisk
 * or KernelSU install keeps su at /system/bin; KernelSU also accepts --mount-master. */
final class RootShell {
    private RootShell() {}

    private static final String[] CANDIDATES = {"/product/bin/su", "/debug_ramdisk/su", "/system/bin/su"};

    static String su() {
        for (String path : CANDIDATES) if (new File(path).canExecute()) return path;
        return "/system/bin/su";
    }

    static final String PATH = "/product/bin:/debug_ramdisk:/system/bin:/system/xbin:/vendor/bin";
}
