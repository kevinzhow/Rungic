// SPDX-License-Identifier: MIT
package com.rungic.plasma;

import android.content.AttributionSource;
import android.content.Context;
import android.content.ContextWrapper;

/** A real framework package context for a root process, unrelated to the desktop app's UID.
 * Framework/AppOps attribution names an existing framework package and the actual UID 0. */
final class DeviceContext {
    private DeviceContext() {}
    static Context create() throws Exception {
        Class<?> at=Class.forName("android.app.ActivityThread");
        Context system=(Context)at.getMethod("getSystemContext").invoke(at.getMethod("systemMain").invoke(null));
        Context shell=system.createPackageContext("com.android.shell",Context.CONTEXT_IGNORE_SECURITY);
        return new ContextWrapper(shell) {
            @Override public String getOpPackageName() { return "com.android.shell"; }
            @Override public Context getApplicationContext() { return this; }
            @Override public AttributionSource getAttributionSource() {
                return new AttributionSource.Builder(android.os.Process.myUid()).setPackageName("com.android.shell").build();
            }
        };
    }
}
