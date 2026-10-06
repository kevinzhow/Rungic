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
        int[] queries={0};
        RemovalState.Query query=() -> { queries[0]++; return "RUNGIC_REMOVAL_PENDING"; };
        if(RemovalState.afterFailure(new IOException("native failure"),query)!=RemovalState.Result.OTHER)throw new AssertionError();
        if(RemovalState.afterFailure(new ControlException("start",1,"ordinary failure"),query)!=RemovalState.Result.OTHER)throw new AssertionError();
        if(RemovalState.afterFailure(new ControlException("start",1,"noise\nRungic 卸载未完成，请重新运行卸载。\n"),query)!=RemovalState.Result.PENDING)throw new AssertionError();
        if(queries[0]!=0)throw new AssertionError("An existing controller error made a root query");
        ControlException missing=new ControlException("start",127,"/data/adb/rungic-plasma/rungic-plasma: not found");
        if(RemovalState.afterFailure(missing,query)!=RemovalState.Result.PENDING || queries[0]!=1)throw new AssertionError();
        if(RemovalState.afterFailure(missing,()->"RUNGIC_REMOVAL_ABSENT")!=RemovalState.Result.OTHER)throw new AssertionError();
        if(RemovalState.afterFailure(missing,()->"unknown")!=RemovalState.Result.UNKNOWN)throw new AssertionError();
        if(RemovalState.afterFailure(missing,()->{throw new IOException("timeout");})!=RemovalState.Result.UNKNOWN)throw new AssertionError();
        // A different missing command does not prove that the root controller disappeared.
        if(RemovalState.afterFailure(new ControlException("start",127,"another command: not found"),query)!=RemovalState.Result.OTHER)throw new AssertionError();
        System.out.println("PASS pending, absent, unknown and failure-only removal queries");
    }
}
