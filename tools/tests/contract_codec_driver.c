/* SPDX-License-Identifier: MIT
 * Drives shared/media/codec-client.c as the GStreamer and FFmpeg adapters do (open, a frame, a drain,
 * a flush, close), for tools/tests/test_contract_codec.py. Linked with -Wl,--wrap=connect: the broker's
 * fixed path leads to $RUNGIC_CODEC_TEST_SOCKET, the contract's stand-in; the client is unchanged.
 * Prints one JSON object: what the client reported and the pictures it copied.
 *   contract_codec_driver decode|decode10|encode WIDTH HEIGHT      (decode10: 10-bit output asked for)
 */
#define _GNU_SOURCE
#include "codec-client.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>

int __real_connect(int fd, const struct sockaddr *address, socklen_t length);
int __wrap_connect(int fd, const struct sockaddr *address, socklen_t length) {
    const char *target = getenv("RUNGIC_CODEC_TEST_SOCKET");
    const struct sockaddr_un *unix_address = (const struct sockaddr_un *)address;
    if (target && address->sa_family == AF_UNIX && !strcmp(unix_address->sun_path, "/mnt/android-wayland/codec.sock")) {
        struct sockaddr_un moved = {.sun_family = AF_UNIX};
        strncpy(moved.sun_path, target, sizeof(moved.sun_path) - 1);
        return __real_connect(fd, (const struct sockaddr *)&moved, sizeof(moved));
    }
    return __real_connect(fd, address, length);
}

static void hex(const char *name, const uint8_t *p, size_t n) {
    printf(",\"%s\":\"", name);
    for (size_t i = 0; i < n; i++) printf("%02x", p[i]);
    printf("\"");
}

static void text(const char *name, const char *value) {
    printf(",\"%s\":\"", name);
    for (; *value; value++) putchar(*value == '"' || *value == '\\' ? '\'' : *value);
    printf("\"");
}

static int records;

static int output(void *user, const RungicCodecFrame *f) {
    (void)user;
    printf("%s{\"type\":%d,\"id\":%d,\"flags\":%d,\"size\":%d,\"pts\":%lld,\"width\":%d,\"height\":%d",
           records++ ? "," : "", f->type, f->id, f->flags, f->size, (long long)f->pts, f->width, f->height);
    if (f->type == RUNGIC_DECODED) {
        int w = f->width, h = f->height, cw = (w + 1) / 2, ch = (h + 1) / 2;
        printf(",\"depth\":%d", f->depth);
        uint8_t *planes[3] = {calloc(w, h), calloc(cw, ch), calloc(cw, ch)};
        int stride[3] = {w, cw, cw};
        int copied = rungic_codec_copy_i420(f, planes, stride);
        printf(",\"copied\":%s", copied ? "false" : "true");
        hex("y", planes[0], w * h);
        hex("u", planes[1], cw * ch);
        hex("v", planes[2], cw * ch);
        for (int i = 0; i < 3; i++) free(planes[i]);
        /* NV12 (8-bit) or P010 (10-bit): Y, then CbCr interleaved */
        int bytes = f->depth == 10 ? 2 : 1;
        uint8_t *semi[2] = {calloc(w * bytes, h), calloc(cw * 2 * bytes, ch)};
        int semi_stride[2] = {w * bytes, cw * 2 * bytes};
        copied = f->depth == 10 ? rungic_codec_copy_p010(f, semi, semi_stride) : rungic_codec_copy_nv12(f, semi, semi_stride);
        printf(",\"semiplanar\":%s", copied ? "false" : "true");
        hex("semi_y", semi[0], (size_t)w * bytes * h);
        hex("semi_uv", semi[1], (size_t)cw * 2 * bytes * ch);
        free(semi[0]);
        free(semi[1]);
    } else {
        hex("data", f->data, f->size);
    }
    printf("}");
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 4) return 2;
    int encode = !strcmp(argv[1], "encode"), ten = !strcmp(argv[1], "decode10"), width = atoi(argv[2]), height = atoi(argv[3]);
    RungicCodec codec;
    rungic_codec_init(&codec);
    RungicCodecConfig config = {.encoder = encode, .kind = 0, .width = width, .height = height, .fps_num = 30,
                                .fps_den = 1, .bitrate = 2000000, .key_interval = 1};
    int opened = ten ? rungic_codec_open_options(&codec, &config, RUNGIC_OPTION_BUFFERS | RUNGIC_OPTION_TEN_BIT)
                     : rungic_codec_open(&codec, &config);
    printf("{\"open\":%s", opened ? "false" : "true");
    if (codec.fd < 0) {
        text("error", codec.error);
        printf("}\n");
        return 0;
    }
    text("name", codec.name);
    int length;
    if (encode) {                         /* an I420 picture */
        length = width * height * 3 / 2;
        for (int i = 0; i < length; i++) codec.memory[i] = (uint8_t)(i * 7);
    } else {                              /* an H.264 access unit: SPS, then an IDR slice */
        static const uint8_t au[] = {0, 0, 0, 1, 0x67, 0x42, 0, 0x1e, 0, 0, 0, 1, 0x65, 0x88, 0x84};
        length = sizeof(au);
        memcpy(codec.memory, au, sizeof(au));
    }
    printf(",\"frame_records\":[");
    int result = rungic_codec_exchange(&codec, RUNGIC_FRAME, 7, 1234567890123LL, encode, length, output, NULL);
    printf("],\"frame\":%d", result);
    records = 0;
    printf(",\"drain_records\":[");
    result = rungic_codec_exchange(&codec, RUNGIC_DRAIN, 0, 0, 0, 0, output, NULL);
    printf("],\"drain\":%d,\"ended\":%d,\"inputs\":%u,\"outputs\":%u", result, codec.ended, codec.input_count, codec.output_count);
    result = rungic_codec_exchange(&codec, RUNGIC_FLUSH, 0, 0, 0, 0, NULL, NULL);
    printf(",\"flush\":%d,\"ended_after_flush\":%d", result, codec.ended);
    rungic_codec_close(&codec);
    printf("}\n");
    return 0;
}
