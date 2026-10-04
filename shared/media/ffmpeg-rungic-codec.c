/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Hardware codec adapters for FFmpeg 8.x (the private 8.1.2 and Ubuntu's 8.0.1), through the
 * shared codec client: Qualcomm's V4L2 devices, else the Android app's MediaCodec. Decoder uses
 * AV_CODEC_CAP_HYBRID: hardware for supported SDR streams, the original FFmpeg codec when opening
 * the hardware path is impossible. The encoder is explicitly hardware-only.
 */
#include "avcodec.h"
#include "codec_internal.h"
#include "decode.h"
#include "encode.h"
#include "bsf.h"
#include "libavutil/imgutils.h"
#include "libavutil/mem.h"
#include "libavutil/opt.h"
#include "libavutil/pixdesc.h"
#include "rungic-codec-client.h"

typedef struct Stamp {
 int valid,id,flags;int64_t pts,dts,duration;void *opaque;AVBufferRef *opaque_ref;
} Stamp;
typedef struct Output {
 AVFrame *frame;AVPacket *packet;struct Output *next;
} Output;
typedef struct RungicContext {
 const AVClass *class;
 RungicCodec codec;RungicCodecConfig config;
 AVCodecContext *avctx,*software;AVBSFContext *bsf;
 AVPacket *packet,*filtered;AVFrame *frame;
 Output *head,*tail;int queued,sequence,ended,warmup,error;
 uint8_t *headers;size_t headers_size;
 Stamp stamps[256];
} RungicContext;
static const AVOption options[]={{NULL}};
static const AVClass rungic_class={.class_name="rungic_mediacodec",.item_name=av_default_item_name,.option=options,.version=LIBAVUTIL_VERSION_INT};
static void free_stamp(Stamp *stamp){av_buffer_unref(&stamp->opaque_ref);memset(stamp,0,sizeof(*stamp));}
static void clear_queue(RungicContext *s) {
 while(s->head){Output *p=s->head;s->head=p->next;av_frame_free(&p->frame);av_packet_free(&p->packet);av_free(p);}
 s->tail=NULL;s->queued=0;for(int i=0;i<256;i++)free_stamp(&s->stamps[i]);
}
static int close_codec(AVCodecContext *ctx) {
 RungicContext *s=ctx->priv_data;
 av_log(ctx,AV_LOG_INFO,"rungic: close %s input=%u output=%u\n",s->codec.name,s->codec.input_count,s->codec.output_count);
 rungic_codec_close(&s->codec);avcodec_free_context(&s->software);av_bsf_free(&s->bsf);av_packet_free(&s->packet);av_packet_free(&s->filtered);av_frame_free(&s->frame);
 clear_queue(s);av_freep(&s->headers);return 0;
}
static int setup_common(AVCodecContext *ctx,int encoder) {
 RungicContext *s=ctx->priv_data;s->avctx=ctx;rungic_codec_init(&s->codec);
 s->packet=av_packet_alloc();s->filtered=av_packet_alloc();s->frame=av_frame_alloc();
 if(!s->packet || !s->filtered || !s->frame)return AVERROR(ENOMEM);
 int kind=ctx->codec_id==AV_CODEC_ID_H264?0:ctx->codec_id==AV_CODEC_ID_HEVC?1:2;
 AVRational fps=ctx->framerate;if(fps.num<=0 || fps.den<=0)fps=(AVRational){30,1};
 s->config=(RungicCodecConfig){.encoder=encoder,.kind=kind,.width=ctx->width,.height=ctx->height,
  .fps_num=fps.num,.fps_den=fps.den,.bitrate=ctx->bit_rate>0?ctx->bit_rate:4000000,
  .key_interval=ctx->gop_size>0?FFMAX(1,(int)(ctx->gop_size/av_q2d(fps))):2,
  .color_standard=ctx->colorspace==AVCOL_SPC_BT709?1:ctx->colorspace==AVCOL_SPC_SMPTE170M?2:0,
  .color_range=ctx->color_range==AVCOL_RANGE_JPEG?1:2,.color_transfer=3};
 return 0;
}
static void software_parameters(AVCodecContext *dst,const AVCodecContext *src) {
 dst->width=src->width;dst->height=src->height;
 dst->coded_width=src->coded_width;dst->coded_height=src->coded_height;
 dst->pix_fmt=src->pix_fmt;dst->colorspace=src->colorspace;
 dst->color_range=src->color_range;dst->color_primaries=src->color_primaries;
 dst->color_trc=src->color_trc;dst->chroma_sample_location=src->chroma_sample_location;
 dst->sample_aspect_ratio=src->sample_aspect_ratio;dst->profile=src->profile;
}
static int software_buffer(AVCodecContext *inner,AVFrame *frame,int flags) {
 RungicContext *s=inner->opaque;software_parameters(s->avctx,inner);
 return s->avctx->get_buffer2(s->avctx,frame,flags);
}
static enum AVPixelFormat software_format(AVCodecContext *inner,const enum AVPixelFormat *formats) {
 RungicContext *s=inner->opaque;software_parameters(s->avctx,inner);
 return s->avctx->get_format(s->avctx,formats);
}
static int init_software(AVCodecContext *ctx) {
 RungicContext *s=ctx->priv_data;const char *name=ctx->codec_id==AV_CODEC_ID_H264?"h264":ctx->codec_id==AV_CODEC_ID_HEVC?"hevc":"vp9";
 const AVCodec *codec=avcodec_find_decoder_by_name(name);if(!codec)return AVERROR_DECODER_NOT_FOUND;
 s->software=avcodec_alloc_context3(codec);AVCodecParameters *par=avcodec_parameters_alloc();
 if(!s->software || !par){avcodec_parameters_free(&par);return AVERROR(ENOMEM);}
 int ret=avcodec_parameters_from_context(par,ctx);if(ret>=0)ret=avcodec_parameters_to_context(s->software,par);avcodec_parameters_free(&par);if(ret<0)return ret;
 s->software->pkt_timebase=ctx->pkt_timebase;s->software->time_base=ctx->time_base;
 /* Callbacks must see the public context, as Firefox's allocator stores that
  * context and consults its current pixel format. Serialize this fallback. */
 s->software->thread_count=1;s->software->thread_type=0;
 s->software->flags=ctx->flags;s->software->flags2=ctx->flags2;
 s->software->get_format=software_format;s->software->get_buffer2=software_buffer;s->software->opaque=s;
 ret=avcodec_open2(s->software,codec,NULL);
 if(ret>=0)av_log(ctx,AV_LOG_INFO,"rungic: software fallback %s (%s)\n",name,s->codec.error[0]?s->codec.error:"unsupported hardware format");
 return ret;
}
static int init_decoder(AVCodecContext *ctx) {
 RungicContext *s=ctx->priv_data;int ret=setup_common(ctx,0);if(ret<0)return ret;
 int high_depth=ctx->bits_per_raw_sample>8;
 if(ctx->codec_id==AV_CODEC_ID_H264 && ctx->extradata_size>=4 && ctx->extradata[0]==1) {
  int p=ctx->extradata[1];if(p!=66 && p!=77 && p!=100)high_depth=1;
 }
 if(ctx->codec_id==AV_CODEC_ID_HEVC && ctx->extradata_size>=2 && ctx->extradata[0]==1 && (ctx->extradata[1]&31)!=1)high_depth=1;
 if(ctx->codec_id==AV_CODEC_ID_VP9 && ctx->profile>0)high_depth=1;
 if(ctx->width<16 || ctx->height<16 || ctx->width>2560 || ctx->height>2560 || high_depth || rungic_codec_open(&s->codec,&s->config))return init_software(ctx);
 ctx->pix_fmt=AV_PIX_FMT_YUV420P;
 const char *filter=ctx->codec_id==AV_CODEC_ID_H264?"h264_mp4toannexb":ctx->codec_id==AV_CODEC_ID_HEVC?"hevc_mp4toannexb":NULL;
 if(filter) {
  const AVBitStreamFilter *f=av_bsf_get_by_name(filter);if(!f)return AVERROR_BSF_NOT_FOUND;
  if((ret=av_bsf_alloc(f,&s->bsf))<0)return ret;
  if((ret=avcodec_parameters_from_context(s->bsf->par_in,ctx))<0)return ret;
  s->bsf->time_base_in=ctx->pkt_timebase;if((ret=av_bsf_init(s->bsf))<0)return ret;
 }
 av_log(ctx,AV_LOG_INFO,"rungic: hardware decoder %s %dx%d\n",s->codec.name,ctx->width,ctx->height);return 0;
}
static int callback(void *opaque,const RungicCodecFrame *frame) {
 RungicContext *s=opaque;AVCodecContext *ctx=s->avctx;
 if(frame->type==RUNGIC_CONFIG) {
  uint8_t *p=av_mallocz(frame->size+AV_INPUT_BUFFER_PADDING_SIZE);if(!p)return -1;
  memcpy(p,frame->data,frame->size);av_free(s->headers);s->headers=p;s->headers_size=frame->size;
  if(s->warmup || !ctx->extradata_size) {
   av_freep(&ctx->extradata);ctx->extradata=av_memdup(p,frame->size+AV_INPUT_BUFFER_PADDING_SIZE);
   if(!ctx->extradata)return -1;ctx->extradata_size=frame->size;
  }
  return 0;
 }
 if(s->warmup)return 0;
 /* A picture whose packet is unknown (a decoder that dropped its time) is skipped, not fatal. */
 if(frame->id<0)return 0;
 Stamp *stamp=&s->stamps[(unsigned)frame->id%256];
 if(!stamp->valid || stamp->id!=frame->id)return 0;
 if(s->queued>=128){free_stamp(stamp);return -1;}
 Output *o=av_mallocz(sizeof(*o));if(!o)return -1;
 int ret=0;
 if(frame->type==RUNGIC_DECODED) {
  AVFrame *f=o->frame=av_frame_alloc();if(!f){ret=AVERROR(ENOMEM);goto fail;}
  if((ret=ff_set_dimensions(ctx,frame->width,frame->height))<0)goto fail;
  ctx->pix_fmt=AV_PIX_FMT_YUV420P;
  f->format=ctx->pix_fmt;f->width=ctx->width;f->height=ctx->height;
  if((ret=ff_get_buffer(ctx,f,0))<0)goto fail;
  if(rungic_codec_copy_i420(frame,f->data,f->linesize)){ret=AVERROR_INVALIDDATA;goto fail;}
  f->pts=stamp->pts;f->best_effort_timestamp=stamp->pts;f->pkt_dts=stamp->dts;f->duration=stamp->duration;
  f->color_range=ctx->color_range;f->colorspace=ctx->colorspace;f->color_primaries=ctx->color_primaries;f->color_trc=ctx->color_trc;
  f->sample_aspect_ratio=ctx->sample_aspect_ratio;f->chroma_location=ctx->chroma_sample_location;
  if(stamp->flags&AV_PKT_FLAG_KEY)f->flags|=AV_FRAME_FLAG_KEY;
  if(ctx->flags&AV_CODEC_FLAG_COPY_OPAQUE){f->opaque=stamp->opaque;f->opaque_ref=stamp->opaque_ref?av_buffer_ref(stamp->opaque_ref):NULL;}
 } else if(frame->type==RUNGIC_ENCODED) {
  AVPacket *p=o->packet=av_packet_alloc();if(!p){ret=AVERROR(ENOMEM);goto fail;}
  int key=(frame->flags&1)!=0;size_t headers=key?s->headers_size:0;
  if((ret=ff_get_encode_buffer(ctx,p,headers+frame->size,0))<0)goto fail;
  if(headers)memcpy(p->data,s->headers,headers);memcpy(p->data+headers,frame->data,frame->size);
  p->pts=p->dts=stamp->pts;p->duration=stamp->duration;if(key)p->flags|=AV_PKT_FLAG_KEY;
  if(ctx->flags&AV_CODEC_FLAG_COPY_OPAQUE){p->opaque=stamp->opaque;p->opaque_ref=stamp->opaque_ref?av_buffer_ref(stamp->opaque_ref):NULL;}
 } else {ret=AVERROR_INVALIDDATA;goto fail;}
 free_stamp(stamp);if(s->tail)s->tail->next=o;else s->head=o;s->tail=o;s->queued++;return 0;
/* The frame is lost (an application's get_buffer2 refused it, as mpv's before it turns direct
 * rendering off): its time is freed so later packets still find room. */
fail:av_frame_free(&o->frame);av_packet_free(&o->packet);av_free(o);free_stamp(stamp);s->error=ret;return -1;
}
static int exchange(RungicContext *s,int cmd,int id,int64_t pts,int flags,int length) {
 if(rungic_codec_exchange(&s->codec,cmd,id,pts,flags,length,callback,s)) {
  av_log(s->avctx,AV_LOG_ERROR,"rungic: %s\n",s->codec.error);return s->error?s->error:AVERROR_EXTERNAL;
 }
 return 0;
}
static int pop_frame(RungicContext *s,AVFrame *f) {
 if(!s->head)return AVERROR(EAGAIN);Output *o=s->head;s->head=o->next;if(!s->head)s->tail=NULL;s->queued--;
 av_frame_move_ref(f,o->frame);av_frame_free(&o->frame);av_free(o);return 0;
}
static int pop_packet(RungicContext *s,AVPacket *p) {
 if(!s->head)return AVERROR(EAGAIN);Output *o=s->head;s->head=o->next;if(!s->head)s->tail=NULL;s->queued--;
 av_packet_move_ref(p,o->packet);av_packet_free(&o->packet);av_free(o);return 0;
}
static int receive_software(AVCodecContext *ctx,AVFrame *frame) {
 RungicContext *s=ctx->priv_data;
 for(;;) {
  int ret=avcodec_receive_frame(s->software,frame);
  if(ret!=AVERROR(EAGAIN)) {
   if(ret==0) {
    software_parameters(ctx,s->software);
    /* The public receive API removes the inner decoder's private metadata.
     * Restore the outer decoder's bookkeeping without copying pixel data. */
    ret=ff_attach_decode_data(frame);
   }
   return ret;
  }
  ret=ff_decode_get_packet(ctx,s->packet);
  if(ret==AVERROR_EOF) {if(s->ended)return AVERROR_EOF;s->ended=1;ret=avcodec_send_packet(s->software,NULL);}
  else if(ret>=0){ret=avcodec_send_packet(s->software,s->packet);av_packet_unref(s->packet);}
  if(ret<0)return ret;
 }
}
static int send_compressed(RungicContext *s,AVPacket *pkt) {
 if(pkt->size<=0 || (unsigned)pkt->size>RUNGIC_CODEC_HALF)return AVERROR_INVALIDDATA;
 /* A stamp still set 256 packets later belongs to a picture that was lost (dropped after a
  * refused buffer): reuse it rather than fail every packet from here on. */
 int id=s->sequence++;Stamp *t=&s->stamps[(unsigned)id%256];if(t->valid)free_stamp(t);
 *t=(Stamp){.valid=1,.id=id,.pts=pkt->pts,.dts=pkt->dts,.duration=pkt->duration,.flags=pkt->flags,.opaque=pkt->opaque,.opaque_ref=pkt->opaque_ref?av_buffer_ref(pkt->opaque_ref):NULL};
 AVRational tb=s->avctx->pkt_timebase;if(!tb.num || !tb.den)tb=AV_TIME_BASE_Q;
 int64_t pts=pkt->pts==AV_NOPTS_VALUE?(int64_t)id*33333:av_rescale_q(pkt->pts,tb,AV_TIME_BASE_Q);
 memcpy(s->codec.memory,pkt->data,pkt->size);return exchange(s,RUNGIC_FRAME,id,pts,0,pkt->size);
}
static int receive_frame(AVCodecContext *ctx,AVFrame *frame) {
 RungicContext *s=ctx->priv_data;if(s->software)return receive_software(ctx,frame);
 for(;;) {
  if(s->head)return pop_frame(s,frame);if(s->ended)return AVERROR_EOF;
  int ret=ff_decode_get_packet(ctx,s->packet);
  if(ret==AVERROR_EOF) {s->ended=1;if((ret=exchange(s,RUNGIC_DRAIN,0,0,0,0))<0)return ret;continue;}
  if(ret<0)return ret;
  if(s->bsf) {
   if((ret=av_bsf_send_packet(s->bsf,s->packet))<0)return ret;
   while((ret=av_bsf_receive_packet(s->bsf,s->filtered))>=0) {
    ret=send_compressed(s,s->filtered);av_packet_unref(s->filtered);if(ret<0)return ret;
   }
   if(ret!=AVERROR(EAGAIN) && ret!=AVERROR_EOF)return ret;
  } else {ret=send_compressed(s,s->packet);av_packet_unref(s->packet);if(ret<0)return ret;}
 }
}
static void flush_decoder(AVCodecContext *ctx) {
 RungicContext *s=ctx->priv_data;clear_queue(s);av_packet_unref(s->packet);av_packet_unref(s->filtered);s->ended=0;s->error=0;
 if(s->software)avcodec_flush_buffers(s->software);
 else {if(s->bsf)av_bsf_flush(s->bsf);if(exchange(s,RUNGIC_FLUSH,0,0,0,0)<0)s->ended=1;}
}
static int init_hardware_encoder(AVCodecContext *ctx) {
 RungicContext *s=ctx->priv_data;int ret=setup_common(ctx,1);if(ret<0)return ret;
 if(ctx->pix_fmt!=AV_PIX_FMT_YUV420P || ((ctx->width|ctx->height)&1))return AVERROR(EINVAL);
 if(rungic_codec_open(&s->codec,&s->config)){av_log(ctx,AV_LOG_WARNING,"rungic: %s\n",s->codec.error);return AVERROR_EXTERNAL;}
 ctx->max_b_frames=0;ctx->has_b_frames=0;
 if(ctx->flags&AV_CODEC_FLAG_GLOBAL_HEADER) {
  /* Like upstream mediacodecenc: obtain the actual encoder headers using a
   * disposable black frame. Reopen instead of relying on encoder flush. */
  int pixels=ctx->width*ctx->height;memset(s->codec.memory,16,pixels);memset(s->codec.memory+pixels,128,pixels/2);s->warmup=1;
  ret=exchange(s,RUNGIC_FRAME,-1,0,0,pixels*3/2);if(ret>=0)ret=exchange(s,RUNGIC_DRAIN,0,0,0,0);s->warmup=0;if(ret<0)return ret;
  if(!ctx->extradata_size)return AVERROR_INVALIDDATA;
  if(rungic_codec_open(&s->codec,&s->config))return AVERROR_EXTERNAL;
 }
 av_log(ctx,AV_LOG_INFO,"rungic: hardware encoder %s %dx%d\n",s->codec.name,ctx->width,ctx->height);return 0;
}
static int init_encoder(AVCodecContext *ctx) {
 RungicContext *s=ctx->priv_data;
 int ret=init_hardware_encoder(ctx);
 if(ret>=0 || !(ctx->codec->capabilities&AV_CODEC_CAP_HYBRID))return ret;
 if(!s->packet || !s->filtered || !s->frame)return ret;
 rungic_codec_close(&s->codec);clear_queue(s);
 const char *name=ctx->codec_id==AV_CODEC_ID_H264?"libx264_sw":"libx265";
 const AVCodec *codec=avcodec_find_encoder_by_name(name);
 if(!codec)return AVERROR_ENCODER_NOT_FOUND;
 s->software=avcodec_alloc_context3(codec);
 AVCodecParameters *par=avcodec_parameters_alloc();
 if(!s->software || !par){avcodec_parameters_free(&par);return AVERROR(ENOMEM);}
 ret=avcodec_parameters_from_context(par,ctx);
 if(ret>=0)ret=avcodec_parameters_to_context(s->software,par);
 avcodec_parameters_free(&par);if(ret<0)return ret;
 s->software->time_base=ctx->time_base;s->software->framerate=ctx->framerate;
 s->software->flags=ctx->flags;s->software->flags2=ctx->flags2;
 s->software->max_b_frames=0;s->software->gop_size=ctx->gop_size;
 s->software->thread_count=2;
 av_opt_set(s->software->priv_data,"preset","ultrafast",0);
 av_opt_set(s->software->priv_data,"tune","zerolatency",0);
 if((ret=avcodec_open2(s->software,codec,NULL))<0)return ret;
 av_freep(&ctx->extradata);ctx->extradata_size=s->software->extradata_size;
 if(ctx->extradata_size) {
  ctx->extradata=av_mallocz(ctx->extradata_size+AV_INPUT_BUFFER_PADDING_SIZE);
  if(!ctx->extradata)return AVERROR(ENOMEM);
  memcpy(ctx->extradata,s->software->extradata,ctx->extradata_size);
 }
 av_log(ctx,AV_LOG_INFO,"rungic: encoder software fallback %s\n",name);return 0;
}
static int receive_software_packet(AVCodecContext *ctx,AVPacket *pkt) {
 RungicContext *s=ctx->priv_data;
 for(;;) {
  int ret=avcodec_receive_packet(s->software,pkt);
  if(ret!=AVERROR(EAGAIN))return ret;
  ret=ff_encode_get_frame(ctx,s->frame);
  if(ret==AVERROR_EOF) {
   if(s->ended)return AVERROR_EOF;
   s->ended=1;ret=avcodec_send_frame(s->software,NULL);
  } else if(ret>=0) {
   ret=avcodec_send_frame(s->software,s->frame);av_frame_unref(s->frame);
  }
  if(ret<0)return ret;
 }
}
static int receive_packet(AVCodecContext *ctx,AVPacket *pkt) {
 RungicContext *s=ctx->priv_data;
 if(s->software)return receive_software_packet(ctx,pkt);
 for(;;) {
  if(s->head)return pop_packet(s,pkt);if(s->ended)return AVERROR_EOF;
  int ret=ff_encode_get_frame(ctx,s->frame);
  if(ret==AVERROR_EOF){s->ended=1;if((ret=exchange(s,RUNGIC_DRAIN,0,0,0,0))<0)return ret;continue;}
  if(ret<0)return ret;AVFrame *f=s->frame;
  if(f->format!=AV_PIX_FMT_YUV420P || f->width!=ctx->width || f->height!=ctx->height)return AVERROR(EINVAL);
  int id=s->sequence++,offset=0;Stamp *t=&s->stamps[(unsigned)id%256];if(t->valid)return AVERROR(ENOBUFS);
  *t=(Stamp){.valid=1,.id=id,.pts=f->pts,.duration=f->duration,.opaque=f->opaque,.opaque_ref=f->opaque_ref?av_buffer_ref(f->opaque_ref):NULL};
  for(int p=0;p<3;p++){int w=p?ctx->width/2:ctx->width,h=p?ctx->height/2:ctx->height;for(int y=0;y<h;y++)memcpy(s->codec.memory+offset+y*w,f->data[p]+y*f->linesize[p],w);offset+=w*h;}
  int64_t pts=f->pts==AV_NOPTS_VALUE?(int64_t)id*33333:av_rescale_q(f->pts,ctx->time_base,AV_TIME_BASE_Q);
  int force=f->pict_type==AV_PICTURE_TYPE_I;av_frame_unref(f);
  if((ret=exchange(s,RUNGIC_FRAME,id,pts,force,offset))<0)return ret;
 }
}
#define DECODER(NAME,ID) \
const FFCodec ff_##NAME##_rungic_decoder={ \
 .p.name=#NAME "_rungic",CODEC_LONG_NAME("Qualcomm V4L2 or Android MediaCodec, software fallback"), \
 .p.type=AVMEDIA_TYPE_VIDEO,.p.id=ID,.priv_data_size=sizeof(RungicContext),.p.priv_class=&rungic_class, \
 .init=init_decoder,FF_CODEC_RECEIVE_FRAME_CB(receive_frame),.close=close_codec,.flush=flush_decoder, \
 .p.capabilities=AV_CODEC_CAP_DELAY|AV_CODEC_CAP_DR1|AV_CODEC_CAP_HYBRID, \
 .caps_internal=FF_CODEC_CAP_INIT_CLEANUP|FF_CODEC_CAP_SETS_FRAME_PROPS, .p.wrapper_name="rungic" };
#define ENCODER(NAME,ID) \
const FFCodec ff_##NAME##_rungic_encoder={ \
 .p.name=#NAME "_rungic",CODEC_LONG_NAME("Qualcomm V4L2 or Android MediaCodec encoder"), \
 .p.type=AVMEDIA_TYPE_VIDEO,.p.id=ID,.priv_data_size=sizeof(RungicContext),.p.priv_class=&rungic_class, \
 .init=init_encoder,FF_CODEC_RECEIVE_PACKET_CB(receive_packet),.close=close_codec, \
 .p.capabilities=AV_CODEC_CAP_DELAY|AV_CODEC_CAP_HARDWARE, \
 .caps_internal=FF_CODEC_CAP_INIT_CLEANUP, CODEC_PIXFMTS(AV_PIX_FMT_YUV420P),.p.wrapper_name="rungic" };
DECODER(h264,AV_CODEC_ID_H264)
DECODER(hevc,AV_CODEC_ID_HEVC)
DECODER(vp9,AV_CODEC_ID_VP9)
ENCODER(h264,AV_CODEC_ID_H264)
ENCODER(hevc,AV_CODEC_ID_HEVC)
#define AUTO_ENCODER(NAME,ID) \
const FFCodec ff_##NAME##_rungic_auto_encoder={ \
 .p.name=#NAME "_rungic_auto",CODEC_LONG_NAME("Qualcomm V4L2 or Android MediaCodec encoder, software fallback"), \
 .p.type=AVMEDIA_TYPE_VIDEO,.p.id=ID,.priv_data_size=sizeof(RungicContext),.p.priv_class=&rungic_class, \
 .init=init_encoder,FF_CODEC_RECEIVE_PACKET_CB(receive_packet),.close=close_codec, \
 .p.capabilities=AV_CODEC_CAP_DELAY|AV_CODEC_CAP_HYBRID, \
 .caps_internal=FF_CODEC_CAP_INIT_CLEANUP, CODEC_PIXFMTS(AV_PIX_FMT_YUV420P),.p.wrapper_name="rungic" };
AUTO_ENCODER(h264,AV_CODEC_ID_H264)
AUTO_ENCODER(hevc,AV_CODEC_ID_HEVC)
