package com.rungic.plasma;

import java.io.IOException;

/** Unknown root responses must not enter installation or startup. */
public final class RemovalStateTest {
    // covers: install.removal-app-status/E1 install.removal-app-status/E2
    public static void main(String[] args) throws Exception {
        if (!RemovalState.isPending("RUNGIC_REMOVAL_PENDING\n")) throw new AssertionError();
        if (RemovalState.isPending("RUNGIC_REMOVAL_ABSENT\n")) throw new AssertionError();
        for (String output : new String[]{"", "denied", "RUNGIC_REMOVAL_PENDING noise", "RUNGIC_REMOVAL_ABSENT\nRUNGIC_REMOVAL_PENDING"}) {
            try { RemovalState.isPending(output); throw new AssertionError("Unknown response accepted"); }
            catch (IOException expected) { }
        }
        System.out.println("PASS pending, absent and unknown removal responses");
    }
}
