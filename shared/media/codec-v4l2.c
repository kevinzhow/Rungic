/* SPDX-License-Identifier: MIT
 * Hardware decoding straight through Qualcomm's msm_vidc V4L2 decoder (docs/108), for
 * codec-client.c: no Android app, Codec2 or Binder in the way. The driver is a stateful
 * decoder with three rules learned on the G100 S, each of which is kept here:
 *   - buffers are DMA-BUFs from /dev/dma_heap/system (MMAP and USERPTR are refused);
 *   - OUTPUT is set up and streaming, and only one access unit is in the driver, until
 *     V4L2_EVENT_SOURCE_CHANGE; only then CAPTURE (NV12, or P010 for 10-bit; the default
 *     is UBWC) is set up and started. Queueing before STREAMON made the firmware assert and
 *     reset the video core, Android's decoding with it;
 *   - a flush reopens the device rather than restarting streams mid-session.
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
struct V4l2Decoder {
 int fd,kind,width,height,ten_bit;uint32_t fourcc;
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

/* /dev/videoN whose driver calls itself msm_vidc_decoder and takes this codec, or -1. */
static int find_decoder(uint32_t fourcc) {
 const char *forced=getenv("RUNGIC_CODEC_V4L2_DEVICE");
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
   if(!xioctl(fd,VIDIOC_QUERYCAP,&cap) && !strcmp((char *)cap.card,"msm_vidc_decoder")) {
    struct v4l2_fmtdesc d={.type=OUT};
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

static int queue(V4l2Decoder *d,int type,int index,int bytes,int64_t pts) {
 Buffer *b=type==OUT?&d->out[index]:&d->cap[index];
 struct v4l2_plane plane={.bytesused=bytes,.length=b->size,.m.fd=b->fd};
 struct v4l2_buffer q={.type=type,.memory=V4L2_MEMORY_DMABUF,.index=index,.m.planes=&plane,.length=1};
 q.timestamp.tv_sec=pts/1000000;q.timestamp.tv_usec=pts%1000000;
 if(xioctl(d->fd,VIDIOC_QBUF,&q))return -1;
 b->queued=1;return 0;
}

/* Starts the OUTPUT side; CAPTURE waits for the source change. */
static int start(V4l2Decoder *d,char *error,size_t n) {
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

static int setup_capture(V4l2Decoder *d,char *error,size_t n) {
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

static void remember(V4l2Decoder *d,int64_t pts,int id) {
 if(d->n_times==TIMES){memmove(d->times,d->times+1,sizeof(Time)*(TIMES-1));d->n_times--;}
 d->times[d->n_times++]=(Time){pts,id};
}
static int recall(V4l2Decoder *d,int64_t pts) {
 for(int i=0;i<d->n_times;i++)if(d->times[i].pts==pts) {
  int id=d->times[i].id;memmove(d->times+i,d->times+i+1,sizeof(Time)*(d->n_times-i-1));d->n_times--;return id;
 }
 return -1;
}

/* H.264/HEVC parameter sets of an access unit, kept to start again after a flush. */
static void save_params(V4l2Decoder *d,const uint8_t *p,int length) {
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
static int collect(V4l2Decoder *d,RungicCodecOutput callback,void *user,unsigned *outputs,int *last,int *consumer_failed,char *error,size_t n) {
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

static void reclaim_inputs(V4l2Decoder *d) {
 for(;;) {
  struct v4l2_plane plane={0};
  struct v4l2_buffer q={.type=OUT,.memory=V4L2_MEMORY_DMABUF,.m.planes=&plane,.length=1};
  if(xioctl(d->fd,VIDIOC_DQBUF,&q) || q.index>=(unsigned)d->n_out)return;
  d->out[q.index].queued=0;
 }
}

/* Waits (up to ms) for the driver: an event, a free input or a picture. */
static int wait_driver(V4l2Decoder *d,int ms) {
 struct pollfd p={.fd=d->fd,.events=POLLIN|POLLOUT|POLLPRI};
 int r;do r=poll(&p,1,ms);while(r<0 && errno==EINTR);
 return r;
}

static int events(V4l2Decoder *d,char *error,size_t n) {
 struct v4l2_event ev;
 while(!xioctl(d->fd,VIDIOC_DQEVENT,&ev)) {
  if(ev.type!=V4L2_EVENT_SOURCE_CHANGE)continue;
  /* A new resolution in the middle of a stream: the consumer reopens with new caps. */
  if(d->cap_on){errno=ENOTSUP;return failed(error,n,"resolution change");}
  if(setup_capture(d,error,n))return -1;
 }
 return 0;
}

static int put(V4l2Decoder *d,const uint8_t *data,int length,int64_t pts,RungicCodecOutput callback,void *user,unsigned *outputs,int *last,int *consumer_failed,char *error,size_t n) {
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

V4l2Decoder *v4l2_open(const RungicCodecConfig *config,int ten_bit,char *error,size_t n) {
 uint32_t fourcc=fourcc_of(config->kind);
 if(config->encoder || !fourcc || (ten_bit && config->kind==0)){snprintf(error,n,"V4L2: not a decoder this backend takes");return NULL;}
 int fd=find_decoder(fourcc);
 if(fd<0){snprintf(error,n,"V4L2: no msm_vidc decoder for this codec");return NULL;}
 V4l2Decoder *d=calloc(1,sizeof(*d));
 if(!d){close(fd);return NULL;}
 *d=(V4l2Decoder){.fd=fd,.kind=config->kind,.width=config->width,.height=config->height,.ten_bit=ten_bit,.fourcc=fourcc};
 if(start(d,error,n)){v4l2_close(d);return NULL;}
 return d;
}

int v4l2_fd(V4l2Decoder *d){return d->fd;}

static void stop(V4l2Decoder *d) {
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

void v4l2_close(V4l2Decoder *d) {
 if(!d)return;
 stop(d);if(d->fd>=0)close(d->fd);free(d);
}

/* A flush: a new session on the same descriptor number (the consumer holds it), the parameter
 * sets replayed in front of the next access unit. */
static int reopen(V4l2Decoder *d,char *error,size_t n) {
 stop(d);
 int fd=find_decoder(d->fourcc);
 if(fd<0){errno=ENODEV;return failed(error,n,"reopen");}
 if(dup3(fd,d->fd,O_CLOEXEC)<0){close(fd);return failed(error,n,"reopen");}
 close(fd);
 d->replay=d->params_length>0;
 return start(d,error,n);
}

int v4l2_exchange(V4l2Decoder *d,int cmd,int id,int64_t pts,const uint8_t *data,int length,
                  RungicCodecOutput callback,void *user,int *ended,unsigned *outputs,char *error,size_t n) {
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
    memcpy(copy,data,length);d->pending[d->n_pending++]=(typeof(d->pending[0])){copy,length,pts};
   }
   /* The first source change normally comes within milliseconds of the first access unit. */
   for(int waited=0;!d->cap_on && waited<(d->n_pending>=PENDING/2?2000:0);waited+=10)
    if(wait_driver(d,10)>0 && events(d,error,n))return -1;
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
  if(d->n_pending)return v4l2_exchange(d,cmd,0,0,NULL,0,callback,user,ended,outputs,error,n);
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
