/* SPDX-License-Identifier: MIT
 * Internal to librungiccodec: coding through the msm_vidc V4L2 devices (codec-v4l2.c). */
#ifndef RUNGIC_CODEC_V4L2_H
#define RUNGIC_CODEC_V4L2_H
#include "codec-client.h"
typedef struct V4l2Codec V4l2Codec;
/* NULL (error set) when this device has no such decoder or it cannot start. */
V4l2Codec *v4l2_open(const RungicCodecConfig *config,int ten_bit,char *error,size_t n);
int v4l2_fd(V4l2Codec *);
/* As rungic_codec_exchange; data is a FRAME's access unit (decoder) or I420 picture (encoder). */
int v4l2_exchange(V4l2Codec *,int cmd,int id,int64_t pts,int flags,const uint8_t *data,int length,
                  RungicCodecOutput callback,void *user,int *ended,unsigned *outputs,char *error,size_t n);
void v4l2_close(V4l2Codec *);
#endif
