// SPDX-License-Identifier: MIT
package com.rungic.plasma;

import android.hardware.HardwareBuffer;
import android.util.Log;

/** A decoded HardwareBuffer's DMA-BUF and plane layout, and channel writes carrying it (jni/media). */
final class MediaBuffers {
    static final boolean AVAILABLE;
    static {
        boolean loaded=false;
        try { System.loadLibrary("rungicmedia");loaded=true; }
        catch(UnsatisfiedLinkError e) { Log.w("RungicCodec","No librungicmedia: decoded frames go through shared memory"); }
        AVAILABLE=loaded;
    }
    private MediaBuffers() {}
    /** The AHardwareBuffer behind buffer: one value for every Image of the same buffer. */
    static native long id(HardwareBuffer buffer);
    /** {fd, format, width, height, size, y offset, y stride, cb offset, cb stride, cb step, cr offset,
     * cr stride, cr step}; the caller closes fd. IOException for a buffer the CPU cannot read as
     * three planes. sample: bytes per sample (1, or 2 for P010). Never Image.getPlanes() here: a
     * compressed buffer aborts inside the framework. */
    static native long[] describe(HardwareBuffer buffer,int sample) throws java.io.IOException;
    /** length bytes of data in one message on socket, with descriptor attach (when >= 0). */
    static native void send(int socket,byte[] data,int length,int attach);
}
