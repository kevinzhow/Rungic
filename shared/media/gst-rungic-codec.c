/* SPDX-License-Identifier: MIT */
#include <gst/gst.h>
#include <gst/video/video.h>
#include <gst/video/gstvideodecoder.h>
#include <gst/video/gstvideoencoder.h>
#include "codec-client.h"
#ifndef PACKAGE
#define PACKAGE "rungic-codec"
#endif
GST_DEBUG_CATEGORY_STATIC(rungic_debug);
#define GST_CAT_DEFAULT rungic_debug
#define RAW_CAPS "video/x-raw,format=I420,width=(int)[16,2560],height=(int)[16,2560]"
/* Decoded pictures: NV12 as the hardware gives them (I420 when downstream wants it), P010 for 10-bit. */
#define DECODED_CAPS "video/x-raw,format=(string){NV12,I420,P010_10LE},width=(int)[16,2560],height=(int)[16,2560]"
static const char *compressed_caps[]={
 "video/x-h264,stream-format=byte-stream,alignment=au,profile=(string){constrained-baseline,baseline,main,high},width=(int)[16,2560],height=(int)[16,2560]",
 "video/x-h265,stream-format=byte-stream,alignment=au,profile=(string){main,main-10},width=(int)[16,2560],height=(int)[16,2560]",
 "video/x-vp9,profile=(string){0,2},width=(int)[16,2560],height=(int)[16,2560]"};
static const char *mime_caps[]={"video/x-h264","video/x-h265","video/x-vp9"};
static void color_config(RungicCodecConfig *c,const GstVideoInfo *info) {
 c->color_range=info->colorimetry.range==GST_VIDEO_COLOR_RANGE_0_255?1:2;
 c->color_standard=info->colorimetry.matrix==GST_VIDEO_COLOR_MATRIX_BT709?1:
     info->colorimetry.matrix==GST_VIDEO_COLOR_MATRIX_BT601?2:0;
 c->color_transfer=3; /* SDR; 10-bit decoders clear it and follow the stream. */
}
typedef struct {GstVideoDecoder parent;RungicCodec codec;GstVideoCodecState *input;GstVideoInfo output;GstFlowReturn flow;gboolean no_nv12,checked_nv12;} RungicDecoder;
typedef struct {GstVideoDecoderClass parent;int kind;} RungicDecoderClass;
G_DEFINE_ABSTRACT_TYPE(RungicDecoder,rungic_decoder,GST_TYPE_VIDEO_DECODER)
static int decoder_output(void *opaque,const RungicCodecFrame *frame) {
 RungicDecoder *self=opaque;GstVideoDecoder *decoder=GST_VIDEO_DECODER(self);
 if(frame->type==RUNGIC_CONFIG)return 0;
 if(frame->type!=RUNGIC_DECODED)return -1;
 GstVideoCodecFrame *f=gst_video_decoder_get_frame(decoder,frame->id);
 if(!f){GST_WARNING_OBJECT(self,"No pending frame %d",frame->id);return 0;}
 if(frame->depth==8 && !self->checked_nv12) {
  /* NV12 as decoded unless downstream cannot take it (then I420, the chroma copied apart). */
  self->checked_nv12=TRUE;
  GstCaps *want=gst_caps_new_simple("video/x-raw","format",G_TYPE_STRING,"NV12",NULL);
  GstCaps *peer=gst_pad_peer_query_caps(GST_VIDEO_DECODER_SRC_PAD(decoder),want);
  self->no_nv12=gst_caps_is_empty(peer);gst_caps_unref(peer);gst_caps_unref(want);
 }
 GstVideoFormat format=frame->depth==10?GST_VIDEO_FORMAT_P010_10LE:self->no_nv12?GST_VIDEO_FORMAT_I420:GST_VIDEO_FORMAT_NV12;
 if(GST_VIDEO_INFO_WIDTH(&self->output)!=frame->width || GST_VIDEO_INFO_HEIGHT(&self->output)!=frame->height || GST_VIDEO_INFO_FORMAT(&self->output)!=format) {
  for(;;) {
   GstVideoCodecState *state=gst_video_decoder_set_output_state(decoder,format,frame->width,frame->height,self->input);
   self->output=state->info;gst_video_codec_state_unref(state);
   if(gst_video_decoder_negotiate(decoder))break;
   /* Downstream without NV12: copy the chroma apart as before. */
   if(format!=GST_VIDEO_FORMAT_NV12){gst_video_codec_frame_unref(f);self->flow=GST_FLOW_NOT_NEGOTIATED;return -1;}
   self->no_nv12=TRUE;format=GST_VIDEO_FORMAT_I420;
  }
 }
 self->flow=gst_video_decoder_allocate_output_frame(decoder,f);
 if(self->flow!=GST_FLOW_OK){gst_video_codec_frame_unref(f);return -1;}
 GstVideoFrame raw;
 if(!gst_video_frame_map(&raw,&self->output,f->output_buffer,GST_MAP_WRITE)){gst_video_codec_frame_unref(f);self->flow=GST_FLOW_ERROR;return -1;}
 uint8_t *dst[3];int strides[3];
 for(guint i=0;i<GST_VIDEO_FRAME_N_PLANES(&raw);i++){dst[i]=GST_VIDEO_FRAME_PLANE_DATA(&raw,i);strides[i]=GST_VIDEO_FRAME_PLANE_STRIDE(&raw,i);}
 int r=format==GST_VIDEO_FORMAT_I420?rungic_codec_copy_i420(frame,dst,strides):
       format==GST_VIDEO_FORMAT_NV12?rungic_codec_copy_nv12(frame,dst,strides):rungic_codec_copy_p010(frame,dst,strides);
 gst_video_frame_unmap(&raw);
 if(r){gst_video_codec_frame_unref(f);self->flow=GST_FLOW_ERROR;return -1;}
 self->flow=gst_video_decoder_finish_frame(decoder,f);return self->flow==GST_FLOW_OK?0:-1;
}
static gboolean decoder_set_format(GstVideoDecoder *decoder,GstVideoCodecState *state) {
 RungicDecoder *self=(RungicDecoder *)decoder;RungicDecoderClass *klass=(RungicDecoderClass *)G_OBJECT_GET_CLASS(self);
 RungicCodecConfig config={.kind=klass->kind,.width=GST_VIDEO_INFO_WIDTH(&state->info),.height=GST_VIDEO_INFO_HEIGHT(&state->info)};
 color_config(&config,&state->info);
 const gchar *profile=state->caps?gst_structure_get_string(gst_caps_get_structure(state->caps,0),"profile"):NULL;
 gboolean ten_bit=profile && (!strcmp(profile,"main-10") || !strcmp(profile,"2"));
 if(ten_bit)config.color_transfer=0; /* HDR or not: what the stream says */
 if(rungic_codec_open_options(&self->codec,&config,ten_bit?RUNGIC_OPTION_BUFFERS|RUNGIC_OPTION_TEN_BIT:RUNGIC_OPTIONS_DEFAULT)) {GST_WARNING_OBJECT(self,"%s",self->codec.error);return FALSE;}
 if(self->input)gst_video_codec_state_unref(self->input);self->input=gst_video_codec_state_ref(state);
 gst_video_info_init(&self->output);self->flow=GST_FLOW_OK;
 GST_INFO_OBJECT(self,"Hardware decoder %s",self->codec.name);return TRUE;
}
static GstFlowReturn decoder_handle(GstVideoDecoder *decoder,GstVideoCodecFrame *frame) {
 RungicDecoder *self=(RungicDecoder *)decoder;GstMapInfo map;
 self->flow=GST_FLOW_OK;
 if(!gst_buffer_map(frame->input_buffer,&map,GST_MAP_READ)){gst_video_codec_frame_unref(frame);return GST_FLOW_ERROR;}
 if(map.size>RUNGIC_CODEC_HALF || !self->codec.memory) {gst_buffer_unmap(frame->input_buffer,&map);gst_video_codec_frame_unref(frame);return GST_FLOW_ERROR;}
 memcpy(self->codec.memory,map.data,map.size);int size=map.size;gst_buffer_unmap(frame->input_buffer,&map);
 int id=frame->system_frame_number;int64_t pts=GST_CLOCK_TIME_IS_VALID(frame->pts)?frame->pts/GST_USECOND:(int64_t)id*33333;
 gst_video_codec_frame_unref(frame);
 int r=rungic_codec_exchange(&self->codec,RUNGIC_FRAME,id,pts,0,size,decoder_output,self);
 if(r && self->flow==GST_FLOW_OK){GST_ELEMENT_ERROR(self,STREAM,DECODE,("Android hardware decoder failed"),("%s",self->codec.error));return GST_FLOW_ERROR;}
 return self->flow;
}
static GstFlowReturn decoder_finish(GstVideoDecoder *decoder) {
 RungicDecoder *self=(RungicDecoder *)decoder;self->flow=GST_FLOW_OK;
 if(self->codec.fd>=0 && !self->codec.ended && rungic_codec_exchange(&self->codec,RUNGIC_DRAIN,0,0,0,0,decoder_output,self)) {
  if(self->flow!=GST_FLOW_OK)return self->flow;
  GST_ELEMENT_ERROR(self,STREAM,DECODE,("Hardware decoder drain failed"),("%s",self->codec.error));return GST_FLOW_ERROR;
 }
 return self->flow;
}
static gboolean decoder_flush(GstVideoDecoder *decoder) {
 RungicDecoder *self=(RungicDecoder *)decoder;
 if(self->codec.fd<0)return TRUE;
 int r=rungic_codec_exchange(&self->codec,RUNGIC_FLUSH,0,0,0,0,NULL,NULL);
 if(r)GST_WARNING_OBJECT(self,"Flush: %s",self->codec.error);return r==0;
}
static GstFlowReturn decoder_drain(GstVideoDecoder *decoder) {
 GstFlowReturn r=decoder_finish(decoder);if(r==GST_FLOW_OK && !decoder_flush(decoder))return GST_FLOW_ERROR;return r;
}
static gboolean decoder_stop(GstVideoDecoder *decoder) {
 RungicDecoder *self=(RungicDecoder *)decoder;
 GST_INFO_OBJECT(self,"Close %s input=%u output=%u",self->codec.name,self->codec.input_count,self->codec.output_count);
 rungic_codec_close(&self->codec);if(self->input){gst_video_codec_state_unref(self->input);self->input=NULL;}
 self->checked_nv12=self->no_nv12=FALSE;return TRUE;
}
static void rungic_decoder_init(RungicDecoder *self) {
 rungic_codec_init(&self->codec);gst_video_decoder_set_packetized(GST_VIDEO_DECODER(self),TRUE);
 gst_video_decoder_set_needs_format(GST_VIDEO_DECODER(self),TRUE);gst_video_info_init(&self->output);
}
static void rungic_decoder_class_init(RungicDecoderClass *klass) {
 GstVideoDecoderClass *video=GST_VIDEO_DECODER_CLASS(klass);
 video->set_format=decoder_set_format;video->handle_frame=decoder_handle;video->finish=decoder_finish;
 video->drain=decoder_drain;video->flush=decoder_flush;video->stop=decoder_stop;
}
static void decoder_codec_class_init(gpointer klass,gpointer data) {
 RungicDecoderClass *c=klass;c->kind=GPOINTER_TO_INT(data);GstElementClass *element=GST_ELEMENT_CLASS(c);
 gst_element_class_set_metadata(element,"Android hardware video decoder","Codec/Decoder/Video/Hardware","MediaCodec via private shared-memory IPC","Rungic project");
 GstCaps *sink=gst_caps_from_string(compressed_caps[c->kind]),*src=gst_caps_from_string(DECODED_CAPS);
 gst_element_class_add_pad_template(element,gst_pad_template_new("sink",GST_PAD_SINK,GST_PAD_ALWAYS,sink));
 gst_element_class_add_pad_template(element,gst_pad_template_new("src",GST_PAD_SRC,GST_PAD_ALWAYS,src));gst_caps_unref(sink);gst_caps_unref(src);
}
typedef struct {GstVideoEncoder parent;RungicCodec codec;GstVideoCodecState *input;GstFlowReturn flow;GByteArray *headers;guint bitrate,key_interval;} RungicEncoder;
typedef struct {GstVideoEncoderClass parent;int kind;} RungicEncoderClass;
G_DEFINE_ABSTRACT_TYPE(RungicEncoder,rungic_encoder,GST_TYPE_VIDEO_ENCODER)
enum { PROP_0,PROP_BITRATE,PROP_KEY_INTERVAL };
static int encoder_output(void *opaque,const RungicCodecFrame *frame) {
 RungicEncoder *self=opaque;GstVideoEncoder *encoder=GST_VIDEO_ENCODER(self);
 if(frame->type==RUNGIC_CONFIG){g_byte_array_set_size(self->headers,0);g_byte_array_append(self->headers,frame->data,frame->size);return 0;}
 if(frame->type!=RUNGIC_ENCODED)return -1;
 GstVideoCodecFrame *f=gst_video_encoder_get_frame(encoder,frame->id);if(!f)return -1;
 gboolean key=(frame->flags&1)!=0;size_t headers=key?self->headers->len:0;
 f->output_buffer=gst_buffer_new_allocate(NULL,headers+frame->size,NULL);
 if(headers)gst_buffer_fill(f->output_buffer,0,self->headers->data,headers);
 gst_buffer_fill(f->output_buffer,headers,frame->data,frame->size);
 if(key)GST_VIDEO_CODEC_FRAME_SET_SYNC_POINT(f);
 f->dts=f->pts;self->flow=gst_video_encoder_finish_frame(encoder,f);return self->flow==GST_FLOW_OK?0:-1;
}
static gboolean encoder_set_format(GstVideoEncoder *encoder,GstVideoCodecState *state) {
 RungicEncoder *self=(RungicEncoder *)encoder;RungicEncoderClass *klass=(RungicEncoderClass *)G_OBJECT_GET_CLASS(self);
 RungicCodecConfig config={.encoder=1,.kind=klass->kind,.width=GST_VIDEO_INFO_WIDTH(&state->info),.height=GST_VIDEO_INFO_HEIGHT(&state->info),
 .fps_num=GST_VIDEO_INFO_FPS_N(&state->info),.fps_den=GST_VIDEO_INFO_FPS_D(&state->info),.bitrate=self->bitrate*1000,.key_interval=self->key_interval};
 color_config(&config,&state->info);
 if(rungic_codec_open(&self->codec,&config)){GST_WARNING_OBJECT(self,"%s",self->codec.error);return FALSE;}
 if(self->input)gst_video_codec_state_unref(self->input);self->input=gst_video_codec_state_ref(state);g_byte_array_set_size(self->headers,0);
 GstCaps *caps=gst_caps_new_simple(mime_caps[klass->kind],"stream-format",G_TYPE_STRING,"byte-stream","alignment",G_TYPE_STRING,"au",NULL);
 GstVideoCodecState *output=gst_video_encoder_set_output_state(encoder,caps,state);gst_video_codec_state_unref(output);
 GstClockTime latency=config.fps_num>0?gst_util_uint64_scale(GST_SECOND,config.fps_den,config.fps_num):GST_SECOND/30;
 gst_video_encoder_set_latency(encoder,latency,latency*4);
 GST_INFO_OBJECT(self,"Hardware encoder %s",self->codec.name);return gst_video_encoder_negotiate(encoder);
}
static GstFlowReturn encoder_handle(GstVideoEncoder *encoder,GstVideoCodecFrame *frame) {
 RungicEncoder *self=(RungicEncoder *)encoder;self->flow=GST_FLOW_OK;GstVideoFrame raw;
 if(!self->input || !self->codec.memory || !gst_video_frame_map(&raw,&self->input->info,frame->input_buffer,GST_MAP_READ)) {gst_video_codec_frame_unref(frame);return GST_FLOW_ERROR;}
 int width=GST_VIDEO_FRAME_WIDTH(&raw),height=GST_VIDEO_FRAME_HEIGHT(&raw),offset=0;
 for(int p=0;p<3;p++) {int w=p?width/2:width,h=p?height/2:height;const uint8_t *src=GST_VIDEO_FRAME_PLANE_DATA(&raw,p);int stride=GST_VIDEO_FRAME_PLANE_STRIDE(&raw,p);
  for(int y=0;y<h;y++)memcpy(self->codec.memory+offset+y*w,src+y*stride,w);offset+=w*h;}
 gst_video_frame_unmap(&raw);
 int id=frame->system_frame_number;int64_t pts=GST_CLOCK_TIME_IS_VALID(frame->pts)?frame->pts/GST_USECOND:(int64_t)id*33333;
 int flags=GST_VIDEO_CODEC_FRAME_IS_FORCE_KEYFRAME(frame)?1:0;gst_video_codec_frame_unref(frame);
 int r=rungic_codec_exchange(&self->codec,RUNGIC_FRAME,id,pts,flags,offset,encoder_output,self);
 if(r && self->flow==GST_FLOW_OK){GST_ELEMENT_ERROR(self,STREAM,ENCODE,("Android hardware encoder failed"),("%s",self->codec.error));return GST_FLOW_ERROR;}return self->flow;
}
static GstFlowReturn encoder_finish(GstVideoEncoder *encoder) {
 RungicEncoder *self=(RungicEncoder *)encoder;self->flow=GST_FLOW_OK;
 if(self->codec.fd>=0 && !self->codec.ended && rungic_codec_exchange(&self->codec,RUNGIC_DRAIN,0,0,0,0,encoder_output,self)) {
  if(self->flow!=GST_FLOW_OK)return self->flow;
  GST_ELEMENT_ERROR(self,STREAM,ENCODE,("Hardware encoder drain failed"),("%s",self->codec.error));return GST_FLOW_ERROR;}
 return self->flow;
}
static gboolean encoder_flush(GstVideoEncoder *encoder) {
 RungicEncoder *self=(RungicEncoder *)encoder;if(!self->input)return TRUE;
 GstVideoCodecState *state=gst_video_codec_state_ref(self->input);gboolean ok=encoder_set_format(encoder,state);gst_video_codec_state_unref(state);return ok;
}
static gboolean encoder_stop(GstVideoEncoder *encoder) {
 RungicEncoder *self=(RungicEncoder *)encoder;GST_INFO_OBJECT(self,"Close %s input=%u output=%u",self->codec.name,self->codec.input_count,self->codec.output_count);
 rungic_codec_close(&self->codec);if(self->input){gst_video_codec_state_unref(self->input);self->input=NULL;}g_byte_array_set_size(self->headers,0);return TRUE;
}
static void encoder_get_property(GObject *object,guint id,GValue *v,GParamSpec *pspec) {
 RungicEncoder *self=(RungicEncoder *)object;if(id==PROP_BITRATE)g_value_set_uint(v,self->bitrate);else if(id==PROP_KEY_INTERVAL)g_value_set_uint(v,self->key_interval);else G_OBJECT_WARN_INVALID_PROPERTY_ID(object,id,pspec);
}
static void encoder_set_property(GObject *object,guint id,const GValue *v,GParamSpec *pspec) {
 RungicEncoder *self=(RungicEncoder *)object;if(id==PROP_BITRATE)self->bitrate=g_value_get_uint(v);else if(id==PROP_KEY_INTERVAL)self->key_interval=g_value_get_uint(v);else G_OBJECT_WARN_INVALID_PROPERTY_ID(object,id,pspec);
}
static void encoder_finalize(GObject *object) {
 RungicEncoder *self=(RungicEncoder *)object;rungic_codec_close(&self->codec);g_byte_array_unref(self->headers);G_OBJECT_CLASS(rungic_encoder_parent_class)->finalize(object);
}
static void rungic_encoder_init(RungicEncoder *self) {rungic_codec_init(&self->codec);self->headers=g_byte_array_new();self->bitrate=4000;self->key_interval=2;}
static void rungic_encoder_class_init(RungicEncoderClass *klass) {
 GstVideoEncoderClass *v=GST_VIDEO_ENCODER_CLASS(klass);v->set_format=encoder_set_format;v->handle_frame=encoder_handle;v->finish=encoder_finish;v->flush=encoder_flush;v->stop=encoder_stop;
 GObjectClass *o=G_OBJECT_CLASS(klass);o->get_property=encoder_get_property;o->set_property=encoder_set_property;o->finalize=encoder_finalize;
 g_object_class_install_property(o,PROP_BITRATE,g_param_spec_uint("bitrate","Bitrate","Target bitrate in kbit/s",64,40000,4000,G_PARAM_READWRITE|G_PARAM_STATIC_STRINGS|GST_PARAM_MUTABLE_READY));
 g_object_class_install_property(o,PROP_KEY_INTERVAL,g_param_spec_uint("key-int-seconds","Key frame interval","Maximum key frame interval in seconds",1,10,2,G_PARAM_READWRITE|G_PARAM_STATIC_STRINGS|GST_PARAM_MUTABLE_READY));
}
static void encoder_codec_class_init(gpointer klass,gpointer data) {
 RungicEncoderClass *c=klass;c->kind=GPOINTER_TO_INT(data);GstElementClass *element=GST_ELEMENT_CLASS(c);
 gst_element_class_set_metadata(element,"Android hardware video encoder","Codec/Encoder/Video/Hardware","MediaCodec via private shared-memory IPC","Rungic project");
 GstCaps *sink=gst_caps_from_string(RAW_CAPS),*src=gst_caps_new_simple(mime_caps[c->kind],"stream-format",G_TYPE_STRING,"byte-stream","alignment",G_TYPE_STRING,"au",NULL);
 gst_element_class_add_pad_template(element,gst_pad_template_new("sink",GST_PAD_SINK,GST_PAD_ALWAYS,sink));
 gst_element_class_add_pad_template(element,gst_pad_template_new("src",GST_PAD_SRC,GST_PAD_ALWAYS,src));gst_caps_unref(sink);gst_caps_unref(src);
}
static gboolean plugin_init(GstPlugin *plugin) {
 GST_DEBUG_CATEGORY_INIT(rungic_debug,"rungiccodec",0,"Android hardware codec bridge");
 const char *decoders[]={"rungich264dec","rungich265dec","rungicvp9dec"},*encoders[]={"rungich264enc","rungich265enc"};
 for(int i=0;i<3;i++) {
  GTypeInfo info={.class_size=sizeof(RungicDecoderClass),.class_init=decoder_codec_class_init,.class_data=GINT_TO_POINTER(i),.instance_size=sizeof(RungicDecoder)};
  char name[64];g_snprintf(name,sizeof(name),"RungicVideoDecoder%d",i);
  GType type=g_type_register_static(rungic_decoder_get_type(),name,&info,0);
  if(!gst_element_register(plugin,decoders[i],GST_RANK_PRIMARY+32,type))return FALSE;
 }
 for(int i=0;i<2;i++) {
  GTypeInfo info={.class_size=sizeof(RungicEncoderClass),.class_init=encoder_codec_class_init,.class_data=GINT_TO_POINTER(i),.instance_size=sizeof(RungicEncoder)};
  char name[64];g_snprintf(name,sizeof(name),"RungicVideoEncoder%d",i);
  GType type=g_type_register_static(rungic_encoder_get_type(),name,&info,0);
  if(!gst_element_register(plugin,encoders[i],GST_RANK_PRIMARY+32,type))return FALSE;
 }
 return TRUE;
}
GST_PLUGIN_DEFINE(GST_VERSION_MAJOR,GST_VERSION_MINOR,rungiccodec,"Android hardware codec bridge",plugin_init,"1.0.0","MIT/X11","Rungic","https://gstreamer.freedesktop.org/")
