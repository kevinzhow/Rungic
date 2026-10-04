// SPDX-License-Identifier: MIT
// A decoder session of the codec broker (CodecBridge) in native code: the channel protocol of
// quality/contracts/codec.json on the channel socket, Android's hardware decoder through the NDK
// (AMediaCodec in asynchronous mode; with OPTION_BUFFERS the pictures through an AImageReader as
// DMA-BUFs). The Java session spent about 15% of a core per 1080p60 stream on stream reads and
// writes, JNI calls and objects; here the per-frame work is the codec's own (docs/108). Encoders
// stay in Java (CodecBridge.Session): runSession hands their configuration back.
#include <jni.h>
#include <android/api-level.h>
#include <android/hardware_buffer.h>
#include <android/log.h>
#include <android/sharedmem_jni.h>
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <media/NdkImage.h>
#include <media/NdkImageReader.h>
#include <media/NdkMediaCodec.h>
#include <media/NdkMediaFormat.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/system_properties.h>
#include <time.h>
#include <unistd.h>

#include "buffers.h"

#define TAG "RungicCodec"
#define HALF (16 * 1024 * 1024)
#define MAGIC 0x4d434231
#define MAGIC2 0x4d434232
enum { FRAME = 1, DRAIN = 2, FLUSH = 3, CLOSE = 4, ACK = 0xac };
enum { DONE = 0, DECODED = 2, CONFIG = 3, EOS = 4, ERROR = -1 };
enum { OPTION_BUFFERS = 1, OPTION_TEN_BIT = 2 };
#define QUEUE 512
#define SLOTS 48
#define TIMES 256
#define IMAGES 8

typedef struct { int type; int index; AMediaCodecBufferInfo info; } Event;   /* 1 input, 2 output, 3 error */
typedef struct { int64_t pts; int id; } Time;
typedef struct { uint64_t id; long slot[14]; } Slot;   /* AHardwareBuffer id; layout with [0] the slot */

typedef struct {
    int fd, kind, width, height, version2, ten_bit, buffers, ended;
    uint8_t *shared;
    AMediaCodec *codec;
    AImageReader *reader;
    pthread_mutex_t lock;
    pthread_cond_t ready;
    Event events[QUEUE]; int head, count;
    int free_inputs[QUEUE], n_free;
    AImage *images[IMAGES]; int n_images;
    Time times[TIMES]; int n_times;
    Slot slots[SLOTS]; int n_slots, next_slot;
    uint8_t params[4096]; int params_length;
    unsigned inputs, outputs;
    char name[64], error[256];
} Session;

/* ---- channel words ------------------------------------------------------------------------- */
static int io(int fd, void *data, size_t length, int sending) {
    uint8_t *p = data;
    while (length) {
        ssize_t n = sending ? send(fd, p, length, MSG_NOSIGNAL) : recv(fd, p, length, 0);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { if (!n) errno = EPIPE; return -1; }
        p += n; length -= n;
    }
    return 0;
}
static int get32(int fd, int32_t *v) { uint32_t w; if (io(fd, &w, 4, 0)) return -1; *v = (int32_t)ntohl(w); return 0; }
static int put32(int fd, int32_t v) { uint32_t w = htonl((uint32_t)v); return io(fd, &w, 4, 1); }
static void word(uint8_t **p, int32_t v) { uint32_t w = htonl((uint32_t)v); memcpy(*p, &w, 4); *p += 4; }
static int fail(Session *s, const char *what) { if (!s->error[0]) snprintf(s->error, sizeof s->error, "%s", what); return -1; }

/* ---- callbacks: MediaCodec's and the image reader's threads --------------------------------- */
static void push(Session *s, Event e) {
    pthread_mutex_lock(&s->lock);
    if (s->count < QUEUE) { s->events[(s->head + s->count) % QUEUE] = e; s->count++; }
    pthread_cond_broadcast(&s->ready);
    pthread_mutex_unlock(&s->lock);
}
static void on_input(AMediaCodec *c, void *user, int32_t index) { (void)c; push(user, (Event){.type = 1, .index = index}); }
static void on_output(AMediaCodec *c, void *user, int32_t index, AMediaCodecBufferInfo *info) {
    (void)c; push(user, (Event){.type = 2, .index = index, .info = *info});
}
static void on_format(AMediaCodec *c, void *user, AMediaFormat *format) {
    (void)c; (void)user;
    __android_log_print(ANDROID_LOG_INFO, TAG, "FORMAT %s", AMediaFormat_toString(format));
}
static void on_error(AMediaCodec *c, void *user, media_status_t error, int32_t code, const char *detail) {
    (void)c; (void)code;
    __android_log_print(ANDROID_LOG_WARN, TAG, "Codec error %d: %s", error, detail ? detail : "");
    push(user, (Event){.type = 3, .index = error});
}
static void on_image(void *user, AImageReader *reader) {
    Session *s = user;
    AImage *image = NULL;
    if (AImageReader_acquireNextImage(reader, &image) != AMEDIA_OK || !image) return;
    pthread_mutex_lock(&s->lock);
    if (s->n_images < IMAGES) s->images[s->n_images++] = image;
    else AImage_delete(image);           /* never more than one is outstanding: a stray frame */
    pthread_cond_broadcast(&s->ready);
    pthread_mutex_unlock(&s->lock);
}

/* Waits up to ms for an event; 0 and the event, or -1 on timeout. */
static int next_event(Session *s, Event *e, int ms) {
    struct timespec until;
    clock_gettime(CLOCK_REALTIME, &until);
    until.tv_sec += ms / 1000; until.tv_nsec += (long)(ms % 1000) * 1000000;
    if (until.tv_nsec >= 1000000000) { until.tv_sec++; until.tv_nsec -= 1000000000; }
    pthread_mutex_lock(&s->lock);
    while (!s->count && ms > 0)
        if (pthread_cond_timedwait(&s->ready, &s->lock, &until) == ETIMEDOUT) break;
    int got = s->count > 0;
    if (got) { *e = s->events[s->head]; s->head = (s->head + 1) % QUEUE; s->count--; }
    pthread_mutex_unlock(&s->lock);
    return got ? 0 : -1;
}

/* The rendered picture with this presentation time (they come in output order). */
static AImage *rendered(Session *s, int64_t pts) {
    struct timespec until;
    clock_gettime(CLOCK_REALTIME, &until);
    until.tv_sec += 2;
    pthread_mutex_lock(&s->lock);
    for (;;) {
        while (s->n_images) {
            AImage *image = s->images[0];
            memmove(s->images, s->images + 1, sizeof(AImage *) * (--s->n_images));
            int64_t ns = 0;
            AImage_getTimestamp(image, &ns);
            if (ns / 1000 == pts) { pthread_mutex_unlock(&s->lock); return image; }
            AImage_delete(image);
        }
        if (pthread_cond_timedwait(&s->ready, &s->lock, &until) == ETIMEDOUT) break;
    }
    pthread_mutex_unlock(&s->lock);
    return NULL;
}

static void remember(Session *s, int64_t pts, int id) {
    if (s->n_times == TIMES) { memmove(s->times, s->times + 1, sizeof(Time) * (TIMES - 1)); s->n_times--; }
    s->times[s->n_times++] = (Time){pts, id};
}
static int known(Session *s, int64_t pts) {
    for (int i = 0; i < s->n_times; i++) if (s->times[i].pts == pts) return 1;
    return 0;
}
static int recall(Session *s, int64_t pts) {
    for (int i = 0; i < s->n_times; i++) if (s->times[i].pts == pts) {
        int id = s->times[i].id;
        memmove(s->times + i, s->times + i + 1, sizeof(Time) * (s->n_times - i - 1));
        s->n_times--;
        return id;
    }
    return -1;
}

/* H.264/HEVC parameter sets ahead of the first slice of an access unit, kept for a flush. */
static void save_params(Session *s, const uint8_t *p, int length) {
    if (s->kind == 2) return;
    int start = -1, used = 0;
    uint8_t sets[sizeof s->params];
    for (int i = 0; i + 3 <= length; i++) {
        if (!(p[i] == 0 && p[i + 1] == 0 && p[i + 2] == 1)) continue;
        int begin = i > 0 && p[i - 1] == 0 ? i - 1 : i;
        if (start >= 0 && used + (begin - start) <= (int)sizeof sets) { memcpy(sets + used, p + start, begin - start); used += begin - start; }
        start = -1;
        if (i + 3 >= length) break;
        int t = s->kind == 0 ? p[i + 3] & 31 : (p[i + 3] >> 1) & 63;
        if (s->kind == 0 ? (t >= 1 && t <= 5) : t < 32) break;
        if (s->kind == 0 ? (t == 7 || t == 8) : (t >= 32 && t <= 34)) start = begin;
        i += 2;
    }
    if (used) { memcpy(s->params, sets, used); s->params_length = used; }
}

/* ---- output records --------------------------------------------------------------------------- */
typedef struct { int type, id, flags, size; int64_t pts; int meta[13]; int offsets[3]; int slot, depth; } Record;

/* One record in one message (with a slot's DMA-BUF the first time), then the consumer's ACK. */
static int write_record(Session *s, const Record *r, int attach) {
    uint8_t data[24 * 4], *p = data;
    word(&p, r->type); word(&p, r->id); word(&p, r->flags); word(&p, r->size);
    word(&p, (int32_t)((uint64_t)r->pts >> 32)); word(&p, (int32_t)(uint32_t)r->pts);
    for (int i = 0; i < 13; i++) word(&p, r->meta[i]);
    if (s->version2) { for (int i = 0; i < 3; i++) word(&p, r->offsets[i]); word(&p, r->slot); word(&p, r->depth); }
    struct iovec vec = {.iov_base = data, .iov_len = (size_t)(p - data)};
    char control[CMSG_SPACE(sizeof(int))];
    struct msghdr msg = {.msg_iov = &vec, .msg_iovlen = 1};
    if (attach >= 0) {
        memset(control, 0, sizeof control);
        msg.msg_control = control; msg.msg_controllen = sizeof control;
        struct cmsghdr *h = CMSG_FIRSTHDR(&msg);
        h->cmsg_level = SOL_SOCKET; h->cmsg_type = SCM_RIGHTS; h->cmsg_len = CMSG_LEN(sizeof(int));
        memcpy(CMSG_DATA(h), &attach, sizeof(int));
    }
    ssize_t n;
    do n = sendmsg(s->fd, &msg, MSG_NOSIGNAL); while (n < 0 && errno == EINTR);
    if (n < 0) return fail(s, "Write output record");
    if ((size_t)n < vec.iov_len && io(s->fd, data + n, vec.iov_len - n, 1)) return fail(s, "Write output record");
    int32_t ack;
    if (get32(s->fd, &ack) || ack != ACK) return fail(s, "Frame acknowledgment");
    return 0;
}

/* A rendered picture: its buffer's slot (the DMA-BUF and layout the first time). */
static int send_buffer(Session *s, AImage *image, int id, const AMediaCodecBufferInfo *info) {
    AHardwareBuffer *buffer = NULL;
    if (AImage_getHardwareBuffer(image, &buffer) != AMEDIA_OK || !buffer) return fail(s, "Rendered frame without a HardwareBuffer");
    uint64_t key = (uint64_t)(uintptr_t)buffer;
    Slot *slot = NULL;
    for (int i = 0; i < s->n_slots; i++) if (s->slots[i].id == key) slot = &s->slots[i];
    int attach = -1;
    if (!slot) {
        if (s->n_slots >= SLOTS) return fail(s, "Too many decoder buffers");
        long out[13];
        char message[200];
        if (rungic_describe_buffer(buffer, s->ten_bit ? 2 : 1, out, message, sizeof message)) return fail(s, message);
        slot = &s->slots[s->n_slots++];
        slot->id = key;
        memcpy(slot->slot, out, sizeof out);
        attach = (int)out[0];
        slot->slot[0] = s->next_slot++;
        slot->slot[13] = s->ten_bit ? 10 : 8;
    }
    AImageCropRect crop = {0};
    AImage_getCropRect(image, &crop);
    long *l = slot->slot, rows = l[3];
    int sample = s->ten_bit ? 2 : 1;
    long luma = (long)l[6] * rows;
    Record r = {.type = DECODED, .id = id, .flags = (int)info->flags, .size = (int)l[4], .pts = info->presentationTimeUs,
                .meta = {crop.right - crop.left, crop.bottom - crop.top, crop.left, crop.top,
                         (int)l[6], sample, (int)(l[4] - l[5] < luma ? l[4] - l[5] : luma),
                         (int)l[8], (int)l[9], (int)(l[4] - l[7]),
                         (int)l[11], (int)l[12], (int)(l[4] - l[10])},
                .offsets = {(int)l[5], (int)l[7], (int)l[10]}, .slot = (int)l[0], .depth = (int)l[13]};
    int result = write_record(s, &r, attach);
    if (attach >= 0) close(attach);
    return result;
}

/* A picture in the codec's own buffer (no OPTION_BUFFERS): Y, then the chroma as Android's
 * semi-planar Image gives it (U and V views one byte apart), into the output half. */
static int send_copy(Session *s, int index, int id, const AMediaCodecBufferInfo *info) {
    size_t capacity = 0;
    uint8_t *data = AMediaCodec_getOutputBuffer(s->codec, index, &capacity);
    AMediaFormat *format = AMediaCodec_getOutputFormat(s->codec);
    int32_t width = s->width, height = s->height, stride = 0, slice = 0, color = 0, left = 0, top = 0, right = -1, bottom = -1;
    AMediaFormat_getInt32(format, AMEDIAFORMAT_KEY_WIDTH, &width);
    AMediaFormat_getInt32(format, AMEDIAFORMAT_KEY_HEIGHT, &height);
    AMediaFormat_getInt32(format, AMEDIAFORMAT_KEY_STRIDE, &stride);
    AMediaFormat_getInt32(format, AMEDIAFORMAT_KEY_SLICE_HEIGHT, &slice);
    AMediaFormat_getInt32(format, AMEDIAFORMAT_KEY_COLOR_FORMAT, &color);
    AMediaFormat_getRect(format, AMEDIAFORMAT_KEY_DISPLAY_CROP, &left, &top, &right, &bottom);
    AMediaFormat_delete(format);
    if (stride <= 0) stride = width;
    if (slice <= 0) slice = height;
    if (right < 0) { right = width - 1; bottom = height - 1; }
    size_t luma = (size_t)stride * slice, chroma = luma / 2;
    if (!data || info->offset + luma + chroma > capacity || luma + 2 * chroma > HALF) return fail(s, "Output image too large");
    const uint8_t *src = data + info->offset;
    uint8_t *dst = s->shared + HALF;
    Record r = {.type = DECODED, .id = id, .flags = (int)info->flags, .pts = info->presentationTimeUs, .slot = -1, .depth = 8};
    int planar = color == 19;   /* COLOR_FormatYUV420Planar; else semi-planar NV12 (21, Qualcomm's own) */
    memcpy(dst, src, luma);
    if (planar) {
        memcpy(dst + luma, src + luma, chroma);
        r.size = (int)(luma + chroma);
        int meta[13] = {right - left + 1, bottom - top + 1, left, top, stride, 1, (int)luma,
                        stride / 2, 1, (int)chroma / 2, stride / 2, 1, (int)chroma / 2};
        memcpy(r.meta, meta, sizeof meta);
        r.offsets[1] = (int)luma; r.offsets[2] = (int)(luma + chroma / 2);
    } else {
        memcpy(dst + luma, src + luma, chroma);
        memcpy(dst + luma + chroma, src + luma + 1, chroma - 1);
        r.size = (int)(luma + 2 * chroma - 1);
        int meta[13] = {right - left + 1, bottom - top + 1, left, top, stride, 1, (int)luma,
                        stride, 2, (int)chroma, stride, 2, (int)chroma - 1};
        memcpy(r.meta, meta, sizeof meta);
        r.offsets[1] = (int)luma; r.offsets[2] = (int)(luma + chroma);
    }
    AMediaCodec_releaseOutputBuffer(s->codec, index, false);
    return write_record(s, &r, -1);
}

static int output(Session *s, int index, const AMediaCodecBufferInfo *info) {
    int eos = (info->flags & AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM) != 0;
    int config = (info->flags & AMEDIACODEC_BUFFER_FLAG_CODEC_CONFIG) != 0;
    /* A rendered (Surface) output may report no size: its time identifies a picture. */
    if (!config && (info->size > 0 || (s->reader && known(s, info->presentationTimeUs)))) {
        int id = recall(s, info->presentationTimeUs);
        if (id < 0) { AMediaCodec_releaseOutputBuffer(s->codec, index, false); return fail(s, "Unmatched output PTS"); }
        s->outputs++;
        if (s->reader) {
            AMediaCodec_releaseOutputBuffer(s->codec, index, true);
            AImage *image = rendered(s, info->presentationTimeUs);
            if (!image) return fail(s, "Rendered frame timeout");
            int r = send_buffer(s, image, id, info);
            AImage_delete(image);
            if (r) return -1;
        } else if (send_copy(s, index, id, info)) return -1;
    } else AMediaCodec_releaseOutputBuffer(s->codec, index, false);
    if (eos) { s->ended = 1; if (put32(s->fd, EOS)) return fail(s, "Write EOS"); }
    return 0;
}

static int handle(Session *s, const Event *e) {
    if (e->type == 1) { if (s->n_free < QUEUE) s->free_inputs[s->n_free++] = e->index; return 0; }
    if (e->type == 3) { snprintf(s->error, sizeof s->error, "Codec error %d", e->index); return -1; }
    return output(s, e->index, &e->info);
}

/* A free input index; outputs that come meanwhile go to the consumer (the decoder may need them). */
static int next_input(Session *s) {
    struct timespec start, now;
    clock_gettime(CLOCK_MONOTONIC, &start);
    while (!s->n_free) {
        clock_gettime(CLOCK_MONOTONIC, &now);
        int left = 5000 - (int)((now.tv_sec - start.tv_sec) * 1000 + (now.tv_nsec - start.tv_nsec) / 1000000);
        Event e;
        if (left <= 0 || next_event(s, &e, left)) { fail(s, "Input buffer timeout"); return -1; }
        if (handle(s, &e)) return -1;
    }
    int index = s->free_inputs[0];
    memmove(s->free_inputs, s->free_inputs + 1, sizeof(int) * (--s->n_free));
    return index;
}

/* The outputs ready now, or (eos) every output up to the end of the stream. */
static int drain(Session *s, int eos) {
    Event e;
    if (!eos) { while (!next_event(s, &e, 0)) if (handle(s, &e)) return -1; return 0; }
    while (!s->ended) {
        if (next_event(s, &e, 10000)) return fail(s, "Output EOS timeout");
        if (handle(s, &e)) return -1;
    }
    return 0;
}

/* ---- configuration ----------------------------------------------------------------------------- */
#define HEADER 13   /* magic, mode, kind, width, height, fps n/d, bitrate, interval, standard, range, transfer, options */

static int configure(Session *s, const int32_t *h) {
    s->version2 = h[0] == MAGIC2;
    s->kind = h[2]; s->width = h[3]; s->height = h[4];
    int options = s->version2 ? h[12] : 0;
    s->buffers = (options & OPTION_BUFFERS) != 0;
    s->ten_bit = (options & OPTION_TEN_BIT) && s->kind != 0 && android_get_device_api_level() >= 31;
    if (s->ten_bit && !s->buffers) return fail(s, "10-bit output needs DMA-BUF frames");
    if (s->kind < 0 || s->kind > 2 || s->width < 16 || s->height < 16 || s->width > 2560 || s->height > 2560)
        return fail(s, "Unsupported dimensions/codec");
    static const char *kinds[] = {"avc", "hevc", "vp9"}, *mimes[] = {"video/avc", "video/hevc", "video/x-vnd.on2.vp9"};
    snprintf(s->name, sizeof s->name, "c2.qti.%s.decoder", kinds[s->kind]);
    s->codec = AMediaCodec_createCodecByName(s->name);
    if (!s->codec) return fail(s, "Hardware codec unavailable");
    AMediaFormat *format = AMediaFormat_new();
    AMediaFormat_setString(format, AMEDIAFORMAT_KEY_MIME, mimes[s->kind]);
    AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_WIDTH, s->width);
    AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_HEIGHT, s->height);
    /* Codec2 maps and unmaps every input block: an access unit rarely exceeds the raw picture. */
    int max_input = s->width * s->height * 3 / 2;
    AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_MAX_INPUT_SIZE, max_input < (1 << 20) ? 1 << 20 : max_input > HALF ? HALF : max_input);
    if (h[9] > 0) AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_COLOR_STANDARD, h[9]);
    if (h[10] > 0) AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_COLOR_RANGE, h[10]);
    if (h[11] > 0) AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_COLOR_TRANSFER, h[11]);
    ANativeWindow *window = NULL;
    if (s->buffers) {
        /* Rendered into a Surface, Qualcomm's decoders write UBWC (compressed) unless told otherwise.
         * No CPU usage: gralloc would map every buffer each time Codec2 hands it over. */
        AMediaFormat_setInt32(format, "vendor.qti-ext-dec-forceNonUBWC.value", 1);
        /* 10-bit: the decoder writes Qualcomm's own P010 (0x7fa30c0a), which an AImageReader for
         * YCBCR_P010 refuses (unlike Java's); a private-format reader takes any buffer. */
        media_status_t r = AImageReader_newWithUsage(s->width, s->height, s->ten_bit ? AIMAGE_FORMAT_PRIVATE : AIMAGE_FORMAT_YUV_420_888,
                                                     AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE, 4, &s->reader);
        if (r != AMEDIA_OK) { AMediaFormat_delete(format); return fail(s, "Image reader"); }
        AImageReader_ImageListener listener = {.context = s, .onImageAvailable = on_image};
        AImageReader_setImageListener(s->reader, &listener);
        AImageReader_getWindow(s->reader, &window);
    } else AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_COLOR_FORMAT, 0x7f420888);   /* YUV420Flexible */
    AMediaCodecOnAsyncNotifyCallback callbacks = {.onAsyncInputAvailable = on_input, .onAsyncOutputAvailable = on_output,
                                                  .onAsyncFormatChanged = on_format, .onAsyncError = on_error};
    media_status_t r = AMediaCodec_setAsyncNotifyCallback(s->codec, callbacks, s);
    if (r == AMEDIA_OK) r = AMediaCodec_configure(s->codec, format, window, NULL, 0);
    AMediaFormat_delete(format);
    if (r != AMEDIA_OK) return fail(s, "Codec configuration");
    if (AMediaCodec_start(s->codec) != AMEDIA_OK) return fail(s, "Codec start");
    int length = (int)strlen(s->name);
    if (put32(s->fd, DONE) || put32(s->fd, length) || io(s->fd, s->name, length, 1)) return fail(s, "Write configuration reply");
    __android_log_print(ANDROID_LOG_INFO, TAG, "OPEN %s %dx%d native%s%s", s->name, s->width, s->height,
                        s->buffers ? " buffers" : "", s->ten_bit ? " 10-bit" : "");
    return 0;
}

/* ---- commands ---------------------------------------------------------------------------------- */
static int flush(Session *s) {
    /* Asynchronous mode: flush drops every index the callbacks gave; start() resumes them. */
    if (AMediaCodec_flush(s->codec) != AMEDIA_OK) return fail(s, "Codec flush");
    pthread_mutex_lock(&s->lock);
    s->count = 0; s->head = 0;
    for (int i = 0; i < s->n_images; i++) AImage_delete(s->images[i]);
    s->n_images = 0;
    pthread_mutex_unlock(&s->lock);
    s->n_free = 0; s->n_times = 0; s->ended = 0;
    if (AMediaCodec_start(s->codec) != AMEDIA_OK) return fail(s, "Codec restart");
    if (s->params_length) {
        int index = next_input(s);
        if (index < 0) return -1;
        size_t capacity = 0;
        uint8_t *buffer = AMediaCodec_getInputBuffer(s->codec, index, &capacity);
        if (!buffer || capacity < (size_t)s->params_length) return fail(s, "Input buffer");
        memcpy(buffer, s->params, s->params_length);
        AMediaCodec_queueInputBuffer(s->codec, index, 0, s->params_length, 0, AMEDIACODEC_BUFFER_FLAG_CODEC_CONFIG);
    }
    return put32(s->fd, DONE) ? fail(s, "Write reply") : 0;
}

static int frame(Session *s, int cmd) {
    int32_t id = -1, flags = 0, length = 0, high = 0, low = 0;
    int64_t pts = 0;
    if (cmd == FRAME) {
        if (get32(s->fd, &id) || get32(s->fd, &high) || get32(s->fd, &low) || get32(s->fd, &flags) || get32(s->fd, &length))
            return fail(s, "Read frame");
        pts = (int64_t)(((uint64_t)(uint32_t)high << 32) | (uint32_t)low);
        if (length <= 0 || length > HALF) return fail(s, "Frame size");
        if (s->n_times >= 128) return fail(s, "Too many delayed frames");
    }
    if (s->ended) return fail(s, "Input after EOS");
    int index = next_input(s);
    if (index < 0) return -1;
    if (cmd == DRAIN) AMediaCodec_queueInputBuffer(s->codec, index, 0, 0, 0, AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM);
    else {
        size_t capacity = 0;
        uint8_t *buffer = AMediaCodec_getInputBuffer(s->codec, index, &capacity);
        if (!buffer || capacity < (size_t)length) return fail(s, "Compressed AU too large");
        memcpy(buffer, s->shared, length);
        save_params(s, s->shared, length);
        remember(s, pts, id);
        if (AMediaCodec_queueInputBuffer(s->codec, index, 0, length, pts, 0) != AMEDIA_OK) return fail(s, "Queue input");
        s->inputs++;
    }
    if (drain(s, cmd == DRAIN)) return -1;
    return put32(s->fd, DONE) ? fail(s, "Write reply") : 0;
}

static void session_close(Session *s) {
    if (s->codec) { AMediaCodec_stop(s->codec); AMediaCodec_delete(s->codec); }
    pthread_mutex_lock(&s->lock);
    for (int i = 0; i < s->n_images; i++) AImage_delete(s->images[i]);
    s->n_images = 0;
    pthread_mutex_unlock(&s->lock);
    if (s->reader) AImageReader_delete(s->reader);
    if (s->shared) munmap(s->shared, 2 * (size_t)HALF);
    pthread_cond_destroy(&s->ready);
    pthread_mutex_destroy(&s->lock);
    __android_log_print(ANDROID_LOG_INFO, TAG, "CLOSE %s input=%u output=%u%s%s", s->name, s->inputs, s->outputs,
                        s->error[0] ? " error=" : "", s->error);
}

/* Runs a decoder session on channel (the Linux end's socket) with the session's shared memory.
 * Returns null when the session ran here (to its end or an error, reported on the channel), or for
 * an encoder (or with debug.rungic.codec.native=0) its configuration words: the session continues in Java. */
JNIEXPORT jintArray JNICALL Java_com_rungic_plasma_MediaBuffers_runSession(JNIEnv *env, jclass cls, jint channel, jobject memory) {
    (void)cls;
    struct timeval timeout = {.tv_sec = 10};
    setsockopt(channel, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof timeout);
    setsockopt(channel, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof timeout);
    int32_t h[HEADER] = {0};
    for (int i = 0; i < HEADER - 1; i++) if (get32(channel, &h[i])) return NULL;
    if (h[0] != MAGIC && h[0] != MAGIC2) return NULL;
    if (h[0] == MAGIC2 && get32(channel, &h[HEADER - 1])) return NULL;
    /* debug.rungic.codec.native=0 sends decoders to the Java session too (comparisons). */
    char value[PROP_VALUE_MAX] = "";
    __system_property_get("debug.rungic.codec.native", value);
    if (h[1] == 1 || !strcmp(value, "0")) {
        jintArray header = (*env)->NewIntArray(env, HEADER);
        if (header) (*env)->SetIntArrayRegion(env, header, 0, HEADER, h);
        return header;
    }
    Session *s = calloc(1, sizeof *s);
    if (!s) return NULL;
    s->fd = channel;
    pthread_mutex_init(&s->lock, NULL);
    pthread_condattr_t attr;
    pthread_condattr_init(&attr);
    pthread_cond_init(&s->ready, &attr);
    pthread_condattr_destroy(&attr);
    snprintf(s->name, sizeof s->name, "decoder");
    int memory_fd = ASharedMemory_dupFromJava(env, memory);
    if (memory_fd >= 0) {
        void *map = mmap(NULL, 2 * (size_t)HALF, PROT_READ | PROT_WRITE, MAP_SHARED, memory_fd, 0);
        close(memory_fd);
        if (map != MAP_FAILED) s->shared = map;
    }
    int failed = h[1] != 0 ? fail(s, "Mode") : !s->shared ? fail(s, "Shared memory") : configure(s, h);
    if (!failed) {
        /* A paused pipeline may keep a channel idle. Closing the channel ends every wait. */
        timeout.tv_sec = 0;
        setsockopt(channel, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof timeout);
        for (;;) {
            int32_t cmd;
            if (get32(channel, &cmd) || cmd == CLOSE) break;
            int r = cmd == FLUSH ? flush(s) : cmd == FRAME || cmd == DRAIN ? frame(s, cmd) : fail(s, "Codec operation");
            if (r) { failed = 1; break; }
        }
    }
    if (failed) {
        int length = (int)strlen(s->error);
        if (!put32(channel, ERROR) && !put32(channel, length)) io(channel, s->error, length, 1);
    }
    session_close(s);
    free(s);
    return NULL;
}
