// SPDX-License-Identifier: MIT
// JNI for com.rungic.plasma.MediaBuffers: a decoded HardwareBuffer's DMA-BUF and plane layout for
// the Linux side, and a channel write that carries such a descriptor (SCM_RIGHTS). The codec
// bridge (CodecBridge) sends each buffer's descriptor once; Linux maps it and copies frames out
// in place of the shared-memory copy (docs/108).
#include <jni.h>
#include <android/hardware_buffer.h>
#include <android/hardware_buffer_jni.h>
#include "buffers.h"
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

// libnativewindow exports it (the GPU allocator uses it too), the NDK stub does not declare it.
typedef struct { int version, num_fds, num_ints; int data[]; } NativeHandle;
typedef const NativeHandle *(*GetNativeHandle)(const AHardwareBuffer *);

static GetNativeHandle native_handle(void) {
    static GetNativeHandle get;
    if (!get) {
        void *lib = dlopen("libnativewindow.so", RTLD_NOW | RTLD_NOLOAD);
        if (!lib) lib = dlopen("libnativewindow.so", RTLD_NOW);
        if (lib) get = (GetNativeHandle)dlsym(lib, "AHardwareBuffer_getNativeHandle");
    }
    return get;
}

static void throw_io(JNIEnv *env, const char *message) {
    (*env)->ThrowNew(env, (*env)->FindClass(env, "java/io/IOException"), message);
}

/** The AHardwareBuffer behind a HardwareBuffer: the same for every Image of one buffer. */
JNIEXPORT jlong JNICALL Java_com_rungic_plasma_MediaBuffers_id(JNIEnv *env, jclass cls, jobject buffer) {
    (void)cls;
    return (jlong)(intptr_t)AHardwareBuffer_fromHardwareBuffer(env, buffer);
}

// Qualcomm gralloc's linear decoder formats (msm_media_info.h): Y of stride x scanlines (scanlines
// aligned to 32), then CbCr interleaved with the same stride. The NDK sees them as one plane.
#define QTI_NV12_VENUS 0x7fa30c04
#define QTI_P010_VENUS 0x7fa30c0a

int rungic_describe_buffer(AHardwareBuffer *buffer, int sample, long out[13], char *message, size_t n) {
    GetNativeHandle get = native_handle();
    if (!buffer || !get) { snprintf(message, n, "No AHardwareBuffer native handle"); return -1; }
    const NativeHandle *handle = get(buffer);
    if (!handle || handle->num_fds < 1) { snprintf(message, n, "HardwareBuffer without a DMA-BUF"); return -1; }
    AHardwareBuffer_Desc desc;
    AHardwareBuffer_describe(buffer, &desc);
    long head[5] = {-1, desc.format, desc.width, desc.height, 0};
    memcpy(out, head, sizeof head);
    if ((desc.format == QTI_NV12_VENUS || desc.format == QTI_P010_VENUS) && desc.stride > 0) {
        // Known layout: no CPU lock, so the buffers need no CPU usage (gralloc then maps nothing
        // when Codec2 hands them over, frame after frame).
        long stride = (long)desc.stride * sample;
        long chroma = stride * ((desc.height + 31) / 32 * 32);
        long layout[8] = {0, stride, chroma, stride, 2 * sample, chroma + sample, stride, 2 * sample};
        memcpy(out + 5, layout, sizeof layout);
    } else {
        AHardwareBuffer_Planes planes;
        int locked = AHardwareBuffer_lockPlanes(buffer, AHARDWAREBUFFER_USAGE_CPU_READ_RARELY, -1, NULL, &planes);
        if (locked != 0 || planes.planeCount != 3 || !planes.planes[0].data || !planes.planes[1].data || !planes.planes[2].data) {
            if (locked == 0) AHardwareBuffer_unlock(buffer, NULL);
            snprintf(message, n, "Decoded buffer is not linear YUV (format 0x%x, usage 0x%llx, lock %d, planes %u)",
                     desc.format, (unsigned long long)desc.usage, locked, locked == 0 ? planes.planeCount : 0);
            return -1;
        }
        // The lock maps the whole buffer: the lowest plane address is its start (offset 0 of the
        // DMA-BUF for gralloc's linear YUV, Y first).
        uintptr_t at[3], base;
        for (int i = 0; i < 3; i++) at[i] = (uintptr_t)planes.planes[i].data;
        base = at[0];
        for (int i = 1; i < 3; i++) if (at[i] < base) base = at[i];
        long layout[8] = {at[0] - base, planes.planes[0].rowStride,
                          at[1] - base, planes.planes[1].rowStride, planes.planes[1].pixelStride,
                          at[2] - base, planes.planes[2].rowStride, planes.planes[2].pixelStride};
        memcpy(out + 5, layout, sizeof layout);
        AHardwareBuffer_unlock(buffer, NULL);
    }
    int fd = fcntl(handle->data[0], F_DUPFD_CLOEXEC, 3);
    if (fd < 0) { snprintf(message, n, "%s", strerror(errno)); return -1; }
    off_t size = lseek(fd, 0, SEEK_END);
    lseek(fd, 0, SEEK_SET);
    out[0] = fd; out[4] = size;
    // The chroma rows must end inside the buffer (a wrong layout guess fails here, not in a copy).
    if (size <= 0 || out[10] + out[11] * ((desc.height + 1) / 2 - 1) + out[12] * ((desc.width + 1) / 2 - 1) >= size) {
        snprintf(message, n, "Decoded planes outside the DMA-BUF (format 0x%x, %ux%u, stride %ld, chroma at %ld, %lld bytes)",
                 desc.format, desc.width, desc.height, out[6], out[7], (long long)size);
        close(fd);
        return -1;
    }
    return 0;
}

/** {fd, format, width, height, size, y offset, y stride, cb offset, cb stride, cb step, cr offset,
 *  cr stride, cr step}: fd a new descriptor of the buffer's DMA-BUF (the caller closes it). Offsets
 *  are from the start of the DMA-BUF, read once. A buffer the CPU cannot read as three planes
 *  (UBWC, compressed) is an IOException, never a crash. sample: bytes per sample, 1 or 2 (P010),
 *  for the layouts the NDK reports as one plane. */
JNIEXPORT jlongArray JNICALL Java_com_rungic_plasma_MediaBuffers_describe(JNIEnv *env, jclass cls, jobject object, jint sample) {
    (void)cls;
    long out[13];
    char message[200];
    if (rungic_describe_buffer(AHardwareBuffer_fromHardwareBuffer(env, object), sample, out, message, sizeof message)) {
        throw_io(env, message);
        return NULL;
    }
    jlong values[13];
    for (int i = 0; i < 13; i++) values[i] = out[i];
    jlongArray result = (*env)->NewLongArray(env, 13);
    if (result) (*env)->SetLongArrayRegion(env, result, 0, 13, values);
    else close((int)out[0]);
    return result;
}

/** Write length bytes to a connected socket in one message, with descriptor attach (>= 0) as
 *  SCM_RIGHTS on its first byte. */
JNIEXPORT void JNICALL Java_com_rungic_plasma_MediaBuffers_send(JNIEnv *env, jclass cls, jint socket, jbyteArray data, jint length, jint attach) {
    (void)cls;
    jbyte *bytes = (*env)->GetByteArrayElements(env, data, NULL);
    if (!bytes) return;
    size_t done = 0;
    int failed = 0;
    while (done < (size_t)length) {
        struct iovec vec = {.iov_base = bytes + done, .iov_len = (size_t)length - done};
        char control[CMSG_SPACE(sizeof(int))];
        struct msghdr msg = {.msg_iov = &vec, .msg_iovlen = 1};
        if (attach >= 0 && done == 0) {
            memset(control, 0, sizeof(control));
            msg.msg_control = control; msg.msg_controllen = sizeof(control);
            struct cmsghdr *h = CMSG_FIRSTHDR(&msg);
            h->cmsg_level = SOL_SOCKET; h->cmsg_type = SCM_RIGHTS; h->cmsg_len = CMSG_LEN(sizeof(int));
            memcpy(CMSG_DATA(h), &attach, sizeof(int));
        }
        ssize_t n = sendmsg(socket, &msg, MSG_NOSIGNAL);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { failed = errno ? errno : EPIPE; break; }
        done += n;
    }
    (*env)->ReleaseByteArrayElements(env, data, bytes, JNI_ABORT);
    if (failed) throw_io(env, strerror(failed));
}
