/* SPDX-License-Identifier: MIT
 * Internal to librungiccodec: decoding through the msm_vidc V4L2 decoder (codec-v4l2.c). */
#ifndef RUNGIC_CODEC_V4L2_H
#define RUNGIC_CODEC_V4L2_H
#include "codec-client.h"
typedef struct V4l2Decoder V4l2Decoder;
/* NULL (error set) when this device has no such decoder or it cannot start. */
V4l2Decoder *v4l2_open(const RungicCodecConfig *config,int ten_bit,char *error,size_t n);
int v4l2_fd(V4l2Decoder *);
/* As rungic_codec_exchange; data is the access unit of a FRAME. */
int v4l2_exchange(V4l2Decoder *,int cmd,int id,int64_t pts,const uint8_t *data,int length,
                  RungicCodecOutput callback,void *user,int *ended,unsigned *outputs,char *error,size_t n);
void v4l2_close(V4l2Decoder *);
#endif
