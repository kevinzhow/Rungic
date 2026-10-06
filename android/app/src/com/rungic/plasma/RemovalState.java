package com.rungic.plasma;

import java.io.IOException;

/** Read the removal marker even when its controller no longer exists. */
final class RemovalState {
    // Read directory entries so an unreadable parent is an error, not "absent".
    // Matching the name also detects empty markers and dangling symbolic links.
    // Do not replace this with test -e, which cannot preserve those distinctions.
    static final String ROOT_COMMAND = "uid=$(id -u) || exit 1; [ \"$uid\" = 0 ] || exit 1; entries=$(ls -A /data/adb) || exit 1; if printf '%s\\n' \"$entries\" | grep -Fxq rungic-uninstalling; then echo RUNGIC_REMOVAL_PENDING; else code=$?; [ \"$code\" = 1 ] || exit 1; echo RUNGIC_REMOVAL_ABSENT; fi";

    enum Result { OTHER, PENDING, UNKNOWN }
    interface Query { String read() throws Exception; }

    /** Probe only after the shell reports the missing controller. Never called on success. */
    static Result afterFailure(Throwable failure, Query query) {
        if (!(failure instanceof ControlException)) return Result.OTHER;
        ControlException error=(ControlException)failure;
        if (error.lastLine().equals("Rungic 卸载未完成，请重新运行卸载。")) return Result.PENDING;
        if (error.exitCode!=127 || !error.output.contains("/data/adb/rungic-plasma/rungic-plasma")) return Result.OTHER;
        try { return isPending(query.read())?Result.PENDING:Result.OTHER; }
        catch (Exception unknown) { return Result.UNKNOWN; }
    }

    static boolean isPending(String output) throws IOException {
        String state = output.trim();
        if (state.equals("RUNGIC_REMOVAL_PENDING")) return true;
        if (state.equals("RUNGIC_REMOVAL_ABSENT")) return false;
        throw new IOException("Cannot read the Rungic removal state.");
    }
}
