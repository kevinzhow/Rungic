/* SPDX-License-Identifier: MIT */
#ifndef RUNGIC_CODEC_CLIENT_H
#define RUNGIC_CODEC_CLIENT_H
#include <stdint.h>
#include <stddef.h>
#define RUNGIC_CODEC_HALF (16u*1024u*1024u)
enum { RUNGIC_FRAME=1, RUNGIC_DRAIN=2, RUNGIC_FLUSH=3, RUNGIC_CLOSE=4 };
enum { RUNGIC_DONE=0, RUNGIC_ENCODED=1, RUNGIC_DECODED=2, RUNGIC_CONFIG=3, RUNGIC_EOS=4 };
/* rungic_codec_open_options: decoded pictures in the app's buffers (DMA-BUF, mapped here; no copy in
 * the app), and 10-bit output (P010, needs buffers). rungic_codec_open uses RUNGIC_OPTIONS_DEFAULT:
 * buffers, unless RUNGIC_CODEC_BUFFERS=0 or the Firefox preload (RUNGIC_CODEC_PRECONNECT) without
 * RUNGIC_CODEC_BUFFERS=1. An app without them (channel version 1) gets shared memory.
 * RUNGIC_OPTION_NV12_INPUT: an encoder that takes NV12 pictures (width*height*3/2, CbCr interleaved)
 * where it can, the V4L2 encoder; rungic_codec_input_nv12 says whether the opened one does, else
 * it takes I420 as always. */
enum { RUNGIC_OPTION_BUFFERS=1, RUNGIC_OPTION_TEN_BIT=2, RUNGIC_OPTION_NV12_INPUT=4, RUNGIC_OPTIONS_DEFAULT=-1 };
typedef struct {
 int encoder,kind,width,height,fps_num,fps_den,bitrate,key_interval;
 int color_standard,color_range,color_transfer;
} RungicCodecConfig;
typedef struct {
 int type,id,flags,size;int64_t pts;
 int width,height,crop_x,crop_y;
 struct {int stride,step,length;} plane[3];
 const uint8_t *data;
 /* After `data` so that consumers built against the first layout keep working. Each plane starts
  * at data+offset[p]; depth 8, or 10 for P010 (samples in the high bits of 16-bit words). */
 int offset[3],depth;
} RungicCodecFrame;
typedef struct {
 int fd;uint8_t *memory;char name[128],error[256];
 unsigned input_count,output_count;int ended;
} RungicCodec;
typedef int (*RungicCodecOutput)(void *,const RungicCodecFrame *);
void rungic_codec_init(RungicCodec *);
int rungic_codec_open(RungicCodec *,const RungicCodecConfig *);
int rungic_codec_open_options(RungicCodec *,const RungicCodecConfig *,int options);
int rungic_codec_input_nv12(const RungicCodec *);
int rungic_codec_exchange(RungicCodec *,int cmd,int id,int64_t pts,int flags,int length,RungicCodecOutput,void *);
/* The V4L2 encoder's own picture buffers, for a caller that fills them itself (a GPU drawing into
 * the DMA-BUF, no copy here): rungic_codec_picture gives a free one, NV12 with this layout (-1 when
 * the codec has none: give pictures through memory as always); rungic_codec_encode_picture encodes
 * it as RUNGIC_FRAME would. The buffer and its fd stay the codec's. */
typedef struct {int index,fd,stride,scanlines;size_t size,uv_offset;} RungicCodecPicture;
int rungic_codec_picture(RungicCodec *,RungicCodecPicture *,RungicCodecOutput,void *);
int rungic_codec_encode_picture(RungicCodec *,int index,int id,int64_t pts,int flags,RungicCodecOutput,void *);
void rungic_codec_close(RungicCodec *);
int rungic_codec_copy_i420(const RungicCodecFrame *,uint8_t *const dst[3],const int stride[3]);
/* Y and interleaved CbCr: NV12 from an 8-bit picture, P010 from a 10-bit one. */
int rungic_codec_copy_nv12(const RungicCodecFrame *,uint8_t *const dst[2],const int stride[2]);
int rungic_codec_copy_p010(const RungicCodecFrame *,uint8_t *const dst[2],const int stride[2]);
#endif
