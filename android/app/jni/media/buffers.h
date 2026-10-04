// SPDX-License-Identifier: MIT
// A decoded AHardwareBuffer's DMA-BUF and plane layout (buffers.c), for the Java bridge and the
// native session (codec_session.c).
#ifndef RUNGIC_MEDIA_BUFFERS_H
#define RUNGIC_MEDIA_BUFFERS_H
#include <android/hardware_buffer.h>
#include <stddef.h>
/* out: {fd, format, width, height, size, y offset, y stride, cb offset, cb stride, cb step,
 * cr offset, cr stride, cr step}, fd a new descriptor the caller closes; -1 with message. */
int rungic_describe_buffer(AHardwareBuffer *buffer, int sample, long out[13], char *message, size_t n);
#endif
