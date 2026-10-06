package com.rungic.plasma;

import java.io.IOException;

/** Read the removal marker even when its controller no longer exists. */
final class RemovalState {
    static final String ROOT_COMMAND = "uid=$(id -u) || exit 1; [ \"$uid\" = 0 ] || exit 1; entries=$(ls -A /data/adb) || exit 1; if printf '%s\\n' \"$entries\" | grep -Fxq rungic-uninstalling; then echo RUNGIC_REMOVAL_PENDING; else code=$?; [ \"$code\" = 1 ] || exit 1; echo RUNGIC_REMOVAL_ABSENT; fi";

    static boolean isPending(String output) throws IOException {
        String state = output.trim();
        if (state.equals("RUNGIC_REMOVAL_PENDING")) return true;
        if (state.equals("RUNGIC_REMOVAL_ABSENT")) return false;
        throw new IOException("Cannot read the Rungic removal state.");
    }
}
