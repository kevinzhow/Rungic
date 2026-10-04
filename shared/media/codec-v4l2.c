/* SPDX-License-Identifier: MIT
 * Hardware decoding and encoding straight through Qualcomm's msm_vidc V4L2 devices (docs/108),
 * for codec-client.c: no Android app, Codec2 or Binder in the way. The decoder is a stateful
 * decoder with three rules learned on the G100 S, each of which is kept here:
 *   - buffers are DMA-BUFs from /dev/dma_heap/system (MMAP and USERPTR are refused);
 *   - OUTPUT is set up and streaming, and only one access unit is in the driver, until
 *     V4L2_EVENT_SOURCE_CHANGE; only then CAPTURE (NV12, or P010 for 10-bit; the default
 *     is UBWC) is set up and started. Queueing before STREAMON made the firmware assert and
 *     reset the video core, Android's decoding with it;
 *   - a flush reopens the device rather than restarting streams mid-session.
 * The encoder has one more: OUTPUT (the pictures) streams before CAPTURE and no
 * buffer is queued before both stream; capture buffers queued first made the firmware assert
 * at the first picture. Its input is the consumers' I420, written into NV12 here.
 * Anything unexpected is an error for the consumer, never a retry with another order.
 */
#define _GNU_SOURCE
#include "codec-v4l2.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <linux/dma-buf.h>
#include <linux/dma-heap.h>
#include <linux/videodev2.h>

#define OUT V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE
#define CAP V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE
#define OUT_BUFFERS 8
#define CAP_EXTRA 4
#define MAX_CAP 32
#define PENDING 64
#define PARAMS 4096
#define TIMES 256

typedef struct {int fd;uint8_t *map;size_t size;int queued;} Buffer;
typedef struct {int64_t pts;int id;} Time;
struct V4l2Codec {
 int fd,kind,width,height,ten_bit,encoder;uint32_t fourcc;
 /* encoder: settings, the picture layout and the parameter sets last given to the consumer */
 int fps_num,fps_den,bitrate,key_interval;uint32_t in_bpl,in_scanlines;
 uint8_t headers[PARAMS];int headers_length;
 Buffer out[OUT_BUFFERS],cap[MAX_CAP];int n_out,n_cap,cap_on,stop_sent;
 /* Access units that came before CAPTURE was set up: only the first is in the driver. */
 struct {uint8_t *data;int length;int64_t pts;} pending[PENDING];int n_pending,first_queued;
 Time times[TIMES];int n_times;
 uint8_t params[PARAMS];int params_length,replay;
 uint32_t bpl,scanlines,size;struct v4l2_rect crop;
};

static int xioctl(int fd,unsigned long request,void *arg) {
 int r;do r=ioctl(fd,request,arg);while(r<0 && errno==EINTR);return r;
}
static int failed(char *error,size_t n,const char *what) {
 snprintf(error,n,"V4L2 %s: %s",what,strerror(errno));return -1;
}
static uint32_t fourcc_of(int kind) {
 return kind==0?V4L2_PIX_FMT_H264:kind==1?V4L2_PIX_FMT_HEVC:kind==2?V4L2_PIX_FMT_VP9:0;
}

/* /dev/videoN whose driver calls itself msm_vidc_decoder (msm_vidc_encoder) and takes (gives)
 * this codec, or -1. */
static int find_node(uint32_t fourcc,int encoder) {
 const char *forced=getenv(encoder?"RUNGIC_CODEC_V4L2_ENCODER":"RUNGIC_CODEC_V4L2_DEVICE");
 DIR *dir=forced?NULL:opendir("/dev");
 char path[64];struct dirent *entry=NULL;
 for(;;) {
  if(forced)snprintf(path,sizeof(path),"%s",forced);
  else {
   if(!dir || !(entry=readdir(dir)))break;
   if(strncmp(entry->d_name,"video",5))continue;
   snprintf(path,sizeof(path),"/dev/%s",entry->d_name);
  }
  int fd=open(path,O_RDWR|O_NONBLOCK|O_CLOEXEC);
  if(fd>=0) {
   struct v4l2_capability cap;int ok=0;
   if(!xioctl(fd,VIDIOC_QUERYCAP,&cap) && !strcmp((char *)cap.card,encoder?"msm_vidc_encoder":"msm_vidc_decoder")) {
    struct v4l2_fmtdesc d={.type=encoder?CAP:OUT};
    for(d.index=0;!xioctl(fd,VIDIOC_ENUM_FMT,&d);d.index++)if(d.pixelformat==fourcc){ok=1;break;}
   }
   if(ok){if(dir)closedir(dir);return fd;}
   close(fd);
  }
  if(forced)break;
 }
 if(dir)closedir(dir);
 return -1;
}

static int heap_buffer(Buffer *b,size_t size) {
 int heap=open("/dev/dma_heap/system",O_RDONLY|O_CLOEXEC);
 if(heap<0)return -1;
 struct dma_heap_allocation_data a={.len=size,.fd_flags=O_RDWR|O_CLOEXEC};
 int r=xioctl(heap,DMA_HEAP_IOCTL_ALLOC,&a);close(heap);
 if(r<0)return -1;
 b->fd=a.fd;b->size=size;b->queued=0;
 b->map=mmap(NULL,size,PROT_READ|PROT_WRITE,MAP_SHARED,b->fd,0);
 if(b->map==MAP_FAILED){b->map=NULL;close(b->fd);b->fd=-1;return -1;}
 return 0;
}
static void free_buffer(Buffer *b) {
 if(b->map)munmap(b->map,b->size);if(b->fd>=0)close(b->fd);
 b->map=NULL;b->fd=-1;b->size=0;b->queued=0;
}
static void sync_buffer(Buffer *b,int end,int write) {
 struct dma_buf_sync s={.flags=(end?DMA_BUF_SYNC_END:DMA_BUF_SYNC_START)|(write?DMA_BUF_SYNC_WRITE:DMA_BUF_SYNC_READ)};
 xioctl(b->fd,DMA_BUF_IOCTL_SYNC,&s);
}

static int queue(V4l2Codec *d,int type,int index,int bytes,int64_t pts) {
 Buffer *b=type==OUT?&d->out[index]:&d->cap[index];
 struct v4l2_plane plane={.bytesused=bytes,.length=b->size,.m.fd=b->fd};
 struct v4l2_buffer q={.type=type,.memory=V4L2_MEMORY_DMABUF,.index=index,.m.planes=&plane,.length=1};
 q.timestamp.tv_sec=pts/1000000;q.timestamp.tv_usec=pts%1000000;
 if(xioctl(d->fd,VIDIOC_QBUF,&q))return -1;
 b->queued=1;return 0;
}

/* Starts the OUTPUT side; CAPTURE waits for the source change. */
static int start(V4l2Codec *d,char *error,size_t n) {
 for(int i=0;i<OUT_BUFFERS;i++)d->out[i].fd=-1;
 for(int i=0;i<MAX_CAP;i++)d->cap[i].fd=-1;
 struct v4l2_format f={.type=OUT};
 f.fmt.pix_mp.pixelformat=d->fourcc;f.fmt.pix_mp.width=d->width;f.fmt.pix_mp.height=d->height;f.fmt.pix_mp.num_planes=1;
 if(xioctl(d->fd,VIDIOC_S_FMT,&f))return failed(error,n,"output format");
 size_t size=f.fmt.pix_mp.plane_fmt[0].sizeimage;
 if(size<(1u<<20))size=1u<<20;
 struct v4l2_event_subscription sub={.type=V4L2_EVENT_SOURCE_CHANGE};
 if(xioctl(d->fd,VIDIOC_SUBSCRIBE_EVENT,&sub))return failed(error,n,"source change events");
 struct v4l2_requestbuffers rb={.count=OUT_BUFFERS,.type=OUT,.memory=V4L2_MEMORY_DMABUF};
 if(xioctl(d->fd,VIDIOC_REQBUFS,&rb) || rb.count<1)return failed(error,n,"output buffers");
 d->n_out=rb.count>OUT_BUFFERS?OUT_BUFFERS:rb.count;
 for(int i=0;i<d->n_out;i++)if(heap_buffer(&d->out[i],size))return failed(error,n,"DMA heap");
 int type=OUT;
 if(xioctl(d->fd,VIDIOC_STREAMON,&type))return failed(error,n,"output stream");
 return 0;
}

static int setup_capture(V4l2Codec *d,char *error,size_t n) {
 struct v4l2_format f={.type=CAP};
 if(xioctl(d->fd,VIDIOC_G_FMT,&f))return failed(error,n,"capture format");
 f.fmt.pix_mp.pixelformat=d->ten_bit?v4l2_fourcc('P','0','1','0'):V4L2_PIX_FMT_NV12;
 if(xioctl(d->fd,VIDIOC_S_FMT,&f))return failed(error,n,d->ten_bit?"P010 capture":"NV12 capture");
 if(f.fmt.pix_mp.num_planes!=1){errno=EPROTO;return failed(error,n,"capture planes");}
 d->bpl=f.fmt.pix_mp.plane_fmt[0].bytesperline;d->size=f.fmt.pix_mp.plane_fmt[0].sizeimage;
 /* The driver's luma rows: the coded height aligned to 32 (msm_vidc); sizeimage includes them. */
 d->scanlines=(f.fmt.pix_mp.height+31)&~31u;
 if(!d->bpl || (uint64_t)d->bpl*d->scanlines*3/2>d->size){errno=EPROTO;return failed(error,n,"capture layout");}
 struct v4l2_selection sel={.type=V4L2_BUF_TYPE_VIDEO_CAPTURE,.target=V4L2_SEL_TGT_COMPOSE};
 if(xioctl(d->fd,VIDIOC_G_SELECTION,&sel))sel.r=(struct v4l2_rect){0,0,f.fmt.pix_mp.width,f.fmt.pix_mp.height};
 d->crop=sel.r;
 struct v4l2_control minimum={.id=V4L2_CID_MIN_BUFFERS_FOR_CAPTURE};
 int count=(xioctl(d->fd,VIDIOC_G_CTRL,&minimum)?4:minimum.value)+CAP_EXTRA;
 if(count>MAX_CAP)count=MAX_CAP;
 struct v4l2_requestbuffers rb={.count=count,.type=CAP,.memory=V4L2_MEMORY_DMABUF};
 if(xioctl(d->fd,VIDIOC_REQBUFS,&rb) || rb.count<1)return failed(error,n,"capture buffers");
 d->n_cap=rb.count>MAX_CAP?MAX_CAP:rb.count;
 for(int i=0;i<d->n_cap;i++) {
  if(heap_buffer(&d->cap[i],d->size))return failed(error,n,"DMA heap");
  if(queue(d,CAP,i,0,0))return failed(error,n,"queue capture");
 }
 int type=CAP;
 if(xioctl(d->fd,VIDIOC_STREAMON,&type))return failed(error,n,"capture stream");
 d->cap_on=1;return 0;
}

static void remember(V4l2Codec *d,int64_t pts,int id) {
 if(d->n_times==TIMES){memmove(d->times,d->times+1,sizeof(Time)*(TIMES-1));d->n_times--;}
 d->times[d->n_times++]=(Time){pts,id};
}
static int recall(V4l2Codec *d,int64_t pts) {
 for(int i=0;i<d->n_times;i++)if(d->times[i].pts==pts) {
  int id=d->times[i].id;memmove(d->times+i,d->times+i+1,sizeof(Time)*(d->n_times-i-1));d->n_times--;return id;
 }
 return -1;
}

/* H.264/HEVC parameter sets of an access unit, kept to start again after a flush. */
static void save_params(V4l2Codec *d,const uint8_t *p,int length) {
 if(d->kind==2)return;
 int start=-1;uint8_t sets[PARAMS];int used=0;
 for(int i=0;i+3<=length;i++) {
  if(!(p[i]==0 && p[i+1]==0 && p[i+2]==1))continue;
  if(start>=0 && used+(i-start)<=PARAMS){memcpy(sets+used,p+start,i-start);used+=i-start;start=-1;}
  int t=d->kind==0?p[i+3]&31:(p[i+3]>>1)&63;
  int param=d->kind==0?(t==7 || t==8):(t>=32 && t<=34);
  int picture=d->kind==0?(t>=1 && t<=5):t<32;
  if(picture)break;
  if(param)start=i>0 && p[i-1]==0?i-1:i;
  i+=2;
 }
 if(used){memcpy(d->params,sets,used);d->params_length=used;}
}

/* Hands every decoded picture ready now to the consumer and gives its buffer back. */
static int collect(V4l2Codec *d,RungicCodecOutput callback,void *user,unsigned *outputs,int *last,int *consumer_failed,char *error,size_t n) {
 while(d->cap_on) {
  struct v4l2_plane plane={0};
  struct v4l2_buffer q={.type=CAP,.memory=V4L2_MEMORY_DMABUF,.m.planes=&plane,.length=1};
  if(xioctl(d->fd,VIDIOC_DQBUF,&q)) {
   if(errno==EAGAIN)return 0;
   if(errno==EPIPE){*last=1;return 0;}
   return failed(error,n,"dequeue picture");
  }
  if(q.index>=(unsigned)d->n_cap){errno=EPROTO;return failed(error,n,"picture index");}
  Buffer *b=&d->cap[q.index];b->queued=0;
  if(plane.bytesused && !(q.flags&V4L2_BUF_FLAG_ERROR)) {
   int64_t pts=(int64_t)q.timestamp.tv_sec*1000000+q.timestamp.tv_usec;
   int sample=d->ten_bit?2:1;uint32_t chroma=d->bpl*d->scanlines;
   RungicCodecFrame frame={.type=RUNGIC_DECODED,.id=recall(d,pts),.size=b->size,.pts=pts,
    .width=d->crop.width,.height=d->crop.height,.crop_x=d->crop.left,.crop_y=d->crop.top,.data=b->map,
    .offset={0,chroma,chroma+sample},.depth=d->ten_bit?10:8};
   frame.plane[0].stride=d->bpl;frame.plane[0].step=sample;frame.plane[0].length=chroma;
   for(int p=1;p<3;p++){frame.plane[p].stride=d->bpl;frame.plane[p].step=2*sample;frame.plane[p].length=b->size-frame.offset[p];}
   sync_buffer(b,0,0);
   int r=callback && !*consumer_failed?callback(user,&frame):0;
   sync_buffer(b,1,0);
   (*outputs)++;
   if(r){snprintf(error,n,"Output consumer stopped");*consumer_failed=1;}
  }
  if(q.flags&V4L2_BUF_FLAG_LAST){*last=1;return 0;}
  if(queue(d,CAP,q.index,0,0))return failed(error,n,"queue capture");
 }
 return 0;
}

static void reclaim_inputs(V4l2Codec *d) {
 for(;;) {
  struct v4l2_plane plane={0};
  struct v4l2_buffer q={.type=OUT,.memory=V4L2_MEMORY_DMABUF,.m.planes=&plane,.length=1};
  if(xioctl(d->fd,VIDIOC_DQBUF,&q) || q.index>=(unsigned)d->n_out)return;
  d->out[q.index].queued=0;
 }
}

/* Waits (up to ms) for the driver: an event, a free input or a picture. */
static int wait_driver(V4l2Codec *d,int ms) {
 struct pollfd p={.fd=d->fd,.events=POLLIN|POLLOUT|POLLPRI};
 int r;do r=poll(&p,1,ms);while(r<0 && errno==EINTR);
 return r;
}

static int events(V4l2Codec *d,char *error,size_t n) {
 struct v4l2_event ev;
 while(!xioctl(d->fd,VIDIOC_DQEVENT,&ev)) {
  if(ev.type!=V4L2_EVENT_SOURCE_CHANGE)continue;
  /* A new resolution in the middle of a stream: the consumer reopens with new caps. */
  if(d->cap_on){errno=ENOTSUP;return failed(error,n,"resolution change");}
  if(setup_capture(d,error,n))return -1;
 }
 return 0;
}

static int put(V4l2Codec *d,const uint8_t *data,int length,int64_t pts,RungicCodecOutput callback,void *user,unsigned *outputs,int *last,int *consumer_failed,char *error,size_t n) {
 int index=-1;
 for(long waited=0;;) {
  reclaim_inputs(d);
  for(int i=0;i<d->n_out;i++)if(!d->out[i].queued){index=i;break;}
  if(index>=0)break;
  /* All inputs busy: the decoder may need its pictures taken first. */
  if(collect(d,callback,user,outputs,last,consumer_failed,error,n))return -1;
  if(waited>=5000){errno=ETIMEDOUT;return failed(error,n,"free input");}
  wait_driver(d,10);waited+=10;
 }
 Buffer *b=&d->out[index];
 int replay=d->replay?d->params_length:0;
 if((size_t)(replay+length)>b->size){errno=E2BIG;return failed(error,n,"access unit size");}
 sync_buffer(b,0,1);
 if(replay)memcpy(b->map,d->params,replay);
 memcpy(b->map+replay,data,length);
 sync_buffer(b,1,1);
 d->replay=0;
 if(queue(d,OUT,index,replay+length,pts))return failed(error,n,"queue access unit");
 return 0;
}

static void control(V4l2Codec *d,uint32_t id,int value) {
 struct v4l2_control c={.id=id,.value=value};
 xioctl(d->fd,VIDIOC_S_CTRL,&c);   /* a control this firmware lacks keeps its default */
}

/* The encoder: formats, rate control, buffers, then OUTPUT streaming before CAPTURE, and only
 * then the capture buffers. */
static int start_encoder(V4l2Codec *d,char *error,size_t n) {
 for(int i=0;i<OUT_BUFFERS;i++)d->out[i].fd=-1;
 for(int i=0;i<MAX_CAP;i++)d->cap[i].fd=-1;
 struct v4l2_format coded={.type=CAP};
 coded.fmt.pix_mp.pixelformat=d->fourcc;coded.fmt.pix_mp.width=d->width;coded.fmt.pix_mp.height=d->height;coded.fmt.pix_mp.num_planes=1;
 if(xioctl(d->fd,VIDIOC_S_FMT,&coded))return failed(error,n,"coded format");
 struct v4l2_format raw={.type=OUT};
 raw.fmt.pix_mp.pixelformat=V4L2_PIX_FMT_NV12;raw.fmt.pix_mp.width=d->width;raw.fmt.pix_mp.height=d->height;raw.fmt.pix_mp.num_planes=1;
 if(xioctl(d->fd,VIDIOC_S_FMT,&raw))return failed(error,n,"picture format");
 if(raw.fmt.pix_mp.num_planes!=1){errno=EPROTO;return failed(error,n,"picture planes");}
 /* The driver reports the luma rows it reserves (height aligned) and their stride. */
 d->in_bpl=raw.fmt.pix_mp.plane_fmt[0].bytesperline;d->in_scanlines=raw.fmt.pix_mp.height;
 size_t in_size=raw.fmt.pix_mp.plane_fmt[0].sizeimage;
 if(d->in_bpl<(uint32_t)d->width || d->in_scanlines<(uint32_t)d->height || (uint64_t)d->in_bpl*d->in_scanlines*3/2>in_size)
  {errno=EPROTO;return failed(error,n,"picture layout");}
 /* The coded size follows the picture format. */
 if(xioctl(d->fd,VIDIOC_G_FMT,&coded))return failed(error,n,"coded format");
 size_t out_size=coded.fmt.pix_mp.plane_fmt[0].sizeimage;
 if(out_size<(64u<<10))out_size=(size_t)d->width*d->height*3/2;
 int fps_num=d->fps_num>0?d->fps_num:30,fps_den=d->fps_den>0?d->fps_den:1;
 struct v4l2_streamparm rate={.type=OUT};
 rate.parm.output.timeperframe.numerator=fps_den;rate.parm.output.timeperframe.denominator=fps_num;
 xioctl(d->fd,VIDIOC_S_PARM,&rate);
 control(d,V4L2_CID_MPEG_VIDEO_BITRATE_MODE,V4L2_MPEG_VIDEO_BITRATE_MODE_VBR);
 control(d,V4L2_CID_MPEG_VIDEO_BITRATE,d->bitrate>0?d->bitrate:4000000);
 int key=d->key_interval>0?d->key_interval:2;
 control(d,V4L2_CID_MPEG_VIDEO_GOP_SIZE,key*fps_num/fps_den);
 control(d,V4L2_CID_MPEG_VIDEO_B_FRAMES,0);
 /* As the bridge: Baseline H.264 (WebRTC peers expect it), Main HEVC. */
 if(d->kind==0)control(d,V4L2_CID_MPEG_VIDEO_H264_PROFILE,V4L2_MPEG_VIDEO_H264_PROFILE_CONSTRAINED_BASELINE);
 else control(d,V4L2_CID_MPEG_VIDEO_HEVC_PROFILE,V4L2_MPEG_VIDEO_HEVC_PROFILE_MAIN);
 control(d,V4L2_CID_MPEG_VIDEO_PREPEND_SPSPPS_TO_IDR,1);
 struct v4l2_requestbuffers rb={.count=OUT_BUFFERS,.type=OUT,.memory=V4L2_MEMORY_DMABUF};
 if(xioctl(d->fd,VIDIOC_REQBUFS,&rb) || rb.count<1)return failed(error,n,"picture buffers");
 d->n_out=rb.count>OUT_BUFFERS?OUT_BUFFERS:rb.count;
 struct v4l2_requestbuffers cb={.count=6,.type=CAP,.memory=V4L2_MEMORY_DMABUF};
 if(xioctl(d->fd,VIDIOC_REQBUFS,&cb) || cb.count<1)return failed(error,n,"coded buffers");
 d->n_cap=cb.count>MAX_CAP?MAX_CAP:cb.count;
 for(int i=0;i<d->n_out;i++)if(heap_buffer(&d->out[i],in_size))return failed(error,n,"DMA heap");
 for(int i=0;i<d->n_cap;i++)if(heap_buffer(&d->cap[i],out_size))return failed(error,n,"DMA heap");
 int type=OUT;
 if(xioctl(d->fd,VIDIOC_STREAMON,&type))return failed(error,n,"picture stream");
 type=CAP;
 if(xioctl(d->fd,VIDIOC_STREAMON,&type))return failed(error,n,"coded stream");
 d->cap_on=1;
 for(int i=0;i<d->n_cap;i++)if(queue(d,CAP,i,0,0))return failed(error,n,"queue coded buffer");
 return 0;
}

V4l2Codec *v4l2_open(const RungicCodecConfig *config,int ten_bit,char *error,size_t n) {
 uint32_t fourcc=fourcc_of(config->kind);
 if(!fourcc || (ten_bit && (config->kind==0 || config->encoder)) || (config->encoder && config->kind==2))
  {snprintf(error,n,"V4L2: not a codec this backend takes");return NULL;}
 int fd=find_node(fourcc,config->encoder);
 if(fd<0){snprintf(error,n,"V4L2: no msm_vidc %s for this codec",config->encoder?"encoder":"decoder");return NULL;}
 V4l2Codec *d=calloc(1,sizeof(*d));
 if(!d){close(fd);return NULL;}
 *d=(V4l2Codec){.fd=fd,.kind=config->kind,.width=config->width,.height=config->height,.ten_bit=ten_bit,.fourcc=fourcc,
  .encoder=config->encoder,.fps_num=config->fps_num,.fps_den=config->fps_den,.bitrate=config->bitrate,.key_interval=config->key_interval};
 if(d->encoder?start_encoder(d,error,n):start(d,error,n)){v4l2_close(d);return NULL;}
 return d;
}

int v4l2_fd(V4l2Codec *d){return d->fd;}

static void stop(V4l2Codec *d) {
 int type=CAP;xioctl(d->fd,VIDIOC_STREAMOFF,&type);
 type=OUT;xioctl(d->fd,VIDIOC_STREAMOFF,&type);
 struct v4l2_requestbuffers none={.count=0,.type=CAP,.memory=V4L2_MEMORY_DMABUF};
 xioctl(d->fd,VIDIOC_REQBUFS,&none);
 none.type=OUT;xioctl(d->fd,VIDIOC_REQBUFS,&none);
 for(int i=0;i<OUT_BUFFERS;i++)free_buffer(&d->out[i]);
 for(int i=0;i<MAX_CAP;i++)free_buffer(&d->cap[i]);
 for(int i=0;i<d->n_pending;i++)free(d->pending[i].data);
 d->n_pending=d->first_queued=d->cap_on=d->stop_sent=d->n_out=d->n_cap=d->n_times=0;
}

void v4l2_close(V4l2Codec *d) {
 if(!d)return;
 stop(d);if(d->fd>=0)close(d->fd);free(d);
}

/* A flush: a new session on the same descriptor number (the consumer holds it), the parameter
 * sets replayed in front of the next access unit. */
static int reopen(V4l2Codec *d,char *error,size_t n) {
 stop(d);
 int fd=find_node(d->fourcc,d->encoder);
 if(fd<0){errno=ENODEV;return failed(error,n,"reopen");}
 if(dup3(fd,d->fd,O_CLOEXEC)<0){close(fd);return failed(error,n,"reopen");}
 close(fd);
 d->replay=d->params_length>0;
 d->headers_length=0;
 return d->encoder?start_encoder(d,error,n):start(d,error,n);
}

/* Coded data ready now: parameter sets (when they change) as a CONFIG record, then the frame. */
static int collect_coded(V4l2Codec *d,RungicCodecOutput callback,void *user,unsigned *outputs,int *last,int *consumer_failed,char *error,size_t n) {
 for(;;) {
  struct v4l2_plane plane={0};
  struct v4l2_buffer q={.type=CAP,.memory=V4L2_MEMORY_DMABUF,.m.planes=&plane,.length=1};
  if(xioctl(d->fd,VIDIOC_DQBUF,&q)) {
   if(errno==EAGAIN)return 0;
   if(errno==EPIPE){*last=1;return 0;}
   return failed(error,n,"dequeue coded data");
  }
  if(q.index>=(unsigned)d->n_cap){errno=EPROTO;return failed(error,n,"coded index");}
  Buffer *b=&d->cap[q.index];b->queued=0;
  if(plane.bytesused>plane.data_offset && plane.bytesused<=b->size) {
   const uint8_t *p=b->map+plane.data_offset;int size=plane.bytesused-plane.data_offset;
   int64_t pts=(int64_t)q.timestamp.tv_sec*1000000+q.timestamp.tv_usec;
   sync_buffer(b,0,0);
   /* Leading parameter sets (H.264 SPS/PPS, HEVC VPS/SPS/PPS) up to the first picture NAL. */
   int split=0;
   for(int i=0;i+3<size;i++) {
    if(!(p[i]==0 && p[i+1]==0 && p[i+2]==1))continue;
    int t=d->kind==0?p[i+3]&31:(p[i+3]>>1)&63;
    int param=d->kind==0?(t==7 || t==8):(t>=32 && t<=34);
    if(!param){split=i>0 && p[i-1]==0?i-1:i;break;}
    i+=2;
   }
   int r=0;
   if(split>0 && split<=PARAMS && (split!=d->headers_length || memcmp(d->headers,p,split))) {
    memcpy(d->headers,p,split);d->headers_length=split;
    RungicCodecFrame config={.type=RUNGIC_CONFIG,.id=0,.flags=2,.size=split,.pts=pts,.data=d->headers,.depth=8};
    r=callback && !*consumer_failed?callback(user,&config):0;
   }
   RungicCodecFrame frame={.type=RUNGIC_ENCODED,.id=recall(d,pts),.flags=(q.flags&V4L2_BUF_FLAG_KEYFRAME)?1:0,
    .size=size-split,.pts=pts,.data=p+split,.depth=8};
   if(!r && frame.size>0)r=callback && !*consumer_failed?callback(user,&frame):0;
   sync_buffer(b,1,0);
   (*outputs)++;
   if(r){snprintf(error,n,"Output consumer stopped");*consumer_failed=1;}
  }
  if(q.flags&V4L2_BUF_FLAG_LAST){*last=1;return 0;}
  if(queue(d,CAP,q.index,0,0))return failed(error,n,"queue coded buffer");
 }
}

/* An I420 picture (the consumers' layout, width*height*3/2) into a free picture buffer as NV12. */
static int put_picture(V4l2Codec *d,const uint8_t *data,int length,int64_t pts,int key,RungicCodecOutput callback,void *user,unsigned *outputs,int *last,int *consumer_failed,char *error,size_t n) {
 int w=d->width,h=d->height,cw=w/2,ch=h/2;
 if(length!=w*h*3/2){errno=EINVAL;return failed(error,n,"I420 size");}
 int index=-1;
 for(long waited=0;;) {
  reclaim_inputs(d);
  for(int i=0;i<d->n_out;i++)if(!d->out[i].queued){index=i;break;}
  if(index>=0)break;
  if(collect_coded(d,callback,user,outputs,last,consumer_failed,error,n))return -1;
  if(waited>=5000){errno=ETIMEDOUT;return failed(error,n,"free picture buffer");}
  wait_driver(d,10);waited+=10;
 }
 Buffer *b=&d->out[index];
 uint8_t *uv=b->map+(size_t)d->in_bpl*d->in_scanlines;
 const uint8_t *u=data+w*h,*v=u+cw*ch;
 sync_buffer(b,0,1);
 for(int r=0;r<h;r++)memcpy(b->map+(size_t)r*d->in_bpl,data+(size_t)r*w,w);
 for(int r=0;r<ch;r++) {
  uint8_t *row=uv+(size_t)r*d->in_bpl;const uint8_t *ur=u+(size_t)r*cw,*vr=v+(size_t)r*cw;
  for(int x=0;x<cw;x++){row[2*x]=ur[x];row[2*x+1]=vr[x];}
 }
 sync_buffer(b,1,1);
 if(key)control(d,V4L2_CID_MPEG_VIDEO_FORCE_KEY_FRAME,1);
 if(queue(d,OUT,index,(size_t)d->in_bpl*d->in_scanlines*3/2,pts))return failed(error,n,"queue picture");
 return 0;
}

static int exchange_encoder(V4l2Codec *d,int cmd,int id,int64_t pts,int flags,const uint8_t *data,int length,
                            RungicCodecOutput callback,void *user,int *ended,unsigned *outputs,char *error,size_t n) {
 int last=0,consumer_failed=0;
 if(cmd==RUNGIC_FLUSH){*ended=0;return reopen(d,error,n);}
 if(cmd==RUNGIC_FRAME) {
  if(*ended){errno=EINVAL;return failed(error,n,"input after the end");}
  remember(d,pts,id);
  if(put_picture(d,data,length,pts,flags&1,callback,user,outputs,&last,&consumer_failed,error,n))return -1;
 }
 if(collect_coded(d,callback,user,outputs,&last,&consumer_failed,error,n))return -1;
 if(cmd==RUNGIC_DRAIN && !*ended) {
  if(!d->stop_sent) {
   struct v4l2_encoder_cmd stop_cmd={.cmd=V4L2_ENC_CMD_STOP};
   if(xioctl(d->fd,VIDIOC_ENCODER_CMD,&stop_cmd))return failed(error,n,"drain");
   d->stop_sent=1;
  }
  for(int waited=0;!last;) {
   if(collect_coded(d,callback,user,outputs,&last,&consumer_failed,error,n))return -1;
   if(last)break;
   if(waited>=5000){errno=ETIMEDOUT;return failed(error,n,"end of stream");}
   wait_driver(d,10);waited+=10;reclaim_inputs(d);
  }
  *ended=1;
 }
 return consumer_failed?-1:0;
}

int v4l2_exchange(V4l2Codec *d,int cmd,int id,int64_t pts,int flags,const uint8_t *data,int length,
                  RungicCodecOutput callback,void *user,int *ended,unsigned *outputs,char *error,size_t n) {
 if(d->encoder)return exchange_encoder(d,cmd,id,pts,flags,data,length,callback,user,ended,outputs,error,n);
 int last=0,consumer_failed=0;
 if(cmd==RUNGIC_FLUSH){*ended=0;return reopen(d,error,n);}
 if(cmd==RUNGIC_FRAME) {
  if(*ended){errno=EINVAL;return failed(error,n,"input after the end");}
  remember(d,pts,id);save_params(d,data,length);
  if(!d->cap_on) {
   if(!d->first_queued) {
    if(put(d,data,length,pts,callback,user,outputs,&last,&consumer_failed,error,n))return -1;
    d->first_queued=1;
   } else {
    if(d->n_pending==PENDING){errno=EOVERFLOW;return failed(error,n,"no source change yet");}
    uint8_t *copy=malloc(length);if(!copy)return failed(error,n,"memory");
    memcpy(copy,data,length);
    d->pending[d->n_pending].data=copy;d->pending[d->n_pending].length=length;d->pending[d->n_pending++].pts=pts;
   }
   /* The first source change normally comes within milliseconds of the first access unit: wait
    * for it briefly (pictures start sooner; a player does not drop the first ones), longer when
    * many units are waiting. */
   int patience=d->n_pending>=PENDING/2?2000:100;
   for(int waited=0;!d->cap_on && waited<patience;waited+=2)
    if(wait_driver(d,2)>0 && events(d,error,n))return -1;
  } else if(put(d,data,length,pts,callback,user,outputs,&last,&consumer_failed,error,n))return -1;
 }
 if(events(d,error,n))return -1;
 if(d->cap_on && d->n_pending) {
  for(int i=0;i<d->n_pending;i++) {
   int r=put(d,d->pending[i].data,d->pending[i].length,d->pending[i].pts,callback,user,outputs,&last,&consumer_failed,error,n);
   free(d->pending[i].data);
   if(r){for(int j=i+1;j<d->n_pending;j++)free(d->pending[j].data);d->n_pending=0;return -1;}
  }
  d->n_pending=0;
 }
 if(collect(d,callback,user,outputs,&last,&consumer_failed,error,n))return -1;
 if(cmd==RUNGIC_DRAIN && !*ended) {
  if(!d->first_queued){*ended=1;return consumer_failed?-1:0;}
  for(int waited=0;!d->cap_on;waited+=10) {
   if(waited>=2000){errno=ETIMEDOUT;return failed(error,n,"source change");}
   if(wait_driver(d,10)>0 && events(d,error,n))return -1;
  }
  if(d->n_pending)return v4l2_exchange(d,cmd,0,0,0,NULL,0,callback,user,ended,outputs,error,n);
  if(!d->stop_sent) {
   struct v4l2_decoder_cmd stop_cmd={.cmd=V4L2_DEC_CMD_STOP};
   if(xioctl(d->fd,VIDIOC_DECODER_CMD,&stop_cmd))return failed(error,n,"drain");
   d->stop_sent=1;
  }
  for(int waited=0;!last;) {
   if(collect(d,callback,user,outputs,&last,&consumer_failed,error,n))return -1;
   if(last)break;
   if(waited>=5000){errno=ETIMEDOUT;return failed(error,n,"end of stream");}
   wait_driver(d,10);waited+=10;reclaim_inputs(d);
  }
  *ended=1;
 }
 return consumer_failed?-1:0;
}
