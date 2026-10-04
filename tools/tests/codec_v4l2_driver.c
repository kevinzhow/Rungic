/* SPDX-License-Identifier: MIT
 * shared/media/codec-v4l2.c against a stand-in of Qualcomm's msm_vidc V4L2 decoder, for
 * tools/tests/test_codec_v4l2.py. Linked with -Wl,--wrap=open,--wrap=ioctl,--wrap=poll,--wrap=close,--wrap=dup3:
 * $RUNGIC_CODEC_V4L2_DEVICE and /dev/dma_heap/system lead here, everything else to the real calls.
 *
 * The stand-in keeps the rules the real driver enforces, and records breaking one as a violation
 * (the real one asserted in its firmware and reset the video core): only DMA-BUF memory; no
 * access unit before OUTPUT streams; one access unit at most before CAPTURE is set up; CAPTURE
 * only after the source change. Each access unit becomes a picture whose luma is the unit's last
 * byte (P010: that value << 2, in the high bits), chroma neutral. The source change shows only
 * at the third look for it ($FAKE_VIDC_EVENT_DELAY): the real one takes milliseconds, during which
 * the consumer keeps sending.
 *
 *   codec_v4l2_driver decode|decode10|flush KIND FRAMES
 * Prints one JSON object: what the client reported, the pictures and the stand-in's log.
 */
#define _GNU_SOURCE
#include "codec-client.h"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <linux/dma-buf.h>
#include <linux/dma-heap.h>
#include <linux/videodev2.h>

/* ---- the stand-in device ------------------------------------------------------------------- */
#define OUT V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE
#define CAP V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE
int __real_open(const char *, int, ...);
int __real_ioctl(int, unsigned long, ...);
int __real_poll(struct pollfd *, nfds_t, int);
int __real_close(int);
int __real_dup3(int,int,int);

typedef struct {int fd;int queued;int done;int bytes;struct timeval ts;} Slot;
static struct {
 int fd,heap,opened,sessions;
 uint32_t fourcc,width,height,cap_format;int cap_set,out_on,cap_on,source_change,event_pending,event_delay,stop,last_sent;
 Slot out[32],cap[32];int n_out,n_cap,queued_before_capture;
 /* produced pictures waiting to be dequeued (capture indices) and finished inputs */
 int ready[64],n_ready;
 char log[8192];int log_length;int violations;
 int first_after_open_has_params,au_after_open;
} dev={.fd=-1,.heap=-1};

static void note(const char *format,...) {
 va_list a;va_start(a,format);
 int n=vsnprintf(dev.log+dev.log_length,sizeof(dev.log)-dev.log_length,format,a);
 va_end(a);
 if(n>0 && dev.log_length+n<(int)sizeof(dev.log)-2){dev.log_length+=n;dev.log[dev.log_length++]=';';dev.log[dev.log_length]=0;}
}
static int violation(const char *what){dev.violations++;note("VIOLATION %s",what);errno=EIO;return -1;}

int __wrap_open(const char *path,int flags,...) {
 va_list a;va_start(a,flags);int mode=va_arg(a,int);va_end(a);
 const char *video=getenv("RUNGIC_CODEC_V4L2_DEVICE");
 if(video && !strcmp(path,video) && getenv("FAKE_VIDC")) {
  int fd=memfd_create("fake-vidc",MFD_CLOEXEC);
  memset(dev.out,0,sizeof(dev.out));memset(dev.cap,0,sizeof(dev.cap));
  dev.fd=fd;dev.out_on=dev.cap_on=dev.cap_set=dev.source_change=dev.event_pending=dev.stop=dev.last_sent=0;
  dev.n_out=dev.n_cap=dev.queued_before_capture=dev.n_ready=0;dev.au_after_open=0;dev.sessions++;
  note("open %d",dev.sessions);
  return fd;
 }
 if(!strcmp(path,"/dev/dma_heap/system") && getenv("FAKE_VIDC"))return dev.heap=memfd_create("fake-heap",MFD_CLOEXEC);
 return __real_open(path,flags,mode);
}
/* A reopen moves the new device onto the descriptor number the consumer holds. */
int __wrap_dup3(int old,int target,int flags) {
 int r=__real_dup3(old,target,flags);
 if(r>=0 && old==dev.fd)dev.fd=target;
 return r;
}
int __wrap_close(int fd) {
 if(fd==dev.fd && fd>=0){note("close");dev.fd=-1;}
 return __real_close(fd);
}

static uint32_t align(uint32_t v,uint32_t a){return (v+a-1)/a*a;}
static uint32_t bytes_per_line(void){return align(dev.width,128)*(dev.cap_format==v4l2_fourcc('P','0','1','0')?2:1);}
static uint32_t scanlines(void){return align(dev.height,32);}
static uint32_t picture_size(void){return bytes_per_line()*scanlines()*3/2+4096;}

/* A queued access unit becomes a picture in a free capture buffer (decode order = output order). */
static void decode(void) {
 if(!dev.cap_on)return;
 for(int i=0;i<dev.n_out;i++) {
  Slot *o=&dev.out[i];
  if(!o->queued || o->done)continue;
  int c=-1;for(int k=0;k<dev.n_cap;k++)if(dev.cap[k].queued){c=k;break;}
  if(c<0)return;
  uint8_t au[65536];int n=o->bytes<(int)sizeof(au)?o->bytes:(int)sizeof(au);
  if(pread(o->fd,au,n,0)<0)n=0;
  if(dev.au_after_open++==0)dev.first_after_open_has_params=n>4 && au[4]==0x67;
  uint8_t value=n?au[n-1]:0;
  int ten=dev.cap_format==v4l2_fourcc('P','0','1','0');
  uint32_t bpl=bytes_per_line(),rows=scanlines(),size=picture_size();
  uint8_t *p=mmap(NULL,size,PROT_READ|PROT_WRITE,MAP_SHARED,dev.cap[c].fd,0);
  if(p!=MAP_FAILED) {
   for(uint32_t r=0;r<rows;r++)for(uint32_t x=0;x<dev.width;x++) {
    if(ten){uint16_t s=(uint16_t)((value<<2)<<6);memcpy(p+r*bpl+x*2,&s,2);}else p[r*bpl+x]=value;
   }
   for(uint32_t r=0;r<rows/2;r++)for(uint32_t x=0;x<dev.width;x++) {
    if(ten){uint16_t s=(uint16_t)(512<<6);memcpy(p+bpl*rows+r*bpl+x*2,&s,2);}else p[bpl*rows+r*bpl+x]=0x80;
   }
   munmap(p,size);
  }
  dev.cap[c].queued=0;dev.cap[c].bytes=size;dev.cap[c].ts=o->ts;dev.cap[c].done=0;
  dev.ready[dev.n_ready++]=c;o->done=1;
 }
 int pending=0;for(int i=0;i<dev.n_out;i++)if(dev.out[i].queued && !dev.out[i].done)pending=1;
 if(dev.stop && !pending && !dev.last_sent) {
  for(int k=0;k<dev.n_cap;k++)if(dev.cap[k].queued){dev.cap[k].queued=0;dev.cap[k].bytes=0;dev.cap[k].done=1;dev.ready[dev.n_ready++]=k;dev.last_sent=1;break;}
 }
}

int __wrap_ioctl(int fd,unsigned long request,...) {
 va_list a;va_start(a,request);void *arg=va_arg(a,void *);va_end(a);
 if(fd==dev.heap && fd>=0 && request==DMA_HEAP_IOCTL_ALLOC) {
  struct dma_heap_allocation_data *h=arg;
  int b=memfd_create("fake-dmabuf",MFD_CLOEXEC);
  if(b<0 || ftruncate(b,h->len))return -1;
  h->fd=b;return 0;
 }
 if(request==DMA_BUF_IOCTL_SYNC && fd!=dev.fd)return 0;
 if(fd!=dev.fd || fd<0)return __real_ioctl(fd,request,arg);
 switch(request) {
 case VIDIOC_QUERYCAP:{struct v4l2_capability *c=arg;memset(c,0,sizeof(*c));strcpy((char *)c->card,"msm_vidc_decoder");strcpy((char *)c->driver,"msm_vidc_driver");return 0;}
 case VIDIOC_ENUM_FMT:{struct v4l2_fmtdesc *d=arg;static const uint32_t f[]={V4L2_PIX_FMT_H264,V4L2_PIX_FMT_HEVC,V4L2_PIX_FMT_VP9};
  if(d->type!=OUT || d->index>=3){errno=EINVAL;return -1;}d->pixelformat=f[d->index];return 0;}
 case VIDIOC_S_FMT:{struct v4l2_format *f=arg;
  if(f->type==OUT){dev.fourcc=f->fmt.pix_mp.pixelformat;dev.width=f->fmt.pix_mp.width;dev.height=f->fmt.pix_mp.height;f->fmt.pix_mp.plane_fmt[0].sizeimage=2<<20;note("output %ux%u",dev.width,dev.height);return 0;}
  if(!dev.source_change)return violation("capture format before the source change");
  dev.cap_format=f->fmt.pix_mp.pixelformat;
  if(dev.cap_format!=V4L2_PIX_FMT_NV12 && dev.cap_format!=v4l2_fourcc('P','0','1','0')){errno=EINVAL;return -1;}
  f->fmt.pix_mp.width=align(dev.width,16);f->fmt.pix_mp.height=align(dev.height,16);f->fmt.pix_mp.num_planes=1;
  f->fmt.pix_mp.plane_fmt[0].bytesperline=bytes_per_line();f->fmt.pix_mp.plane_fmt[0].sizeimage=picture_size();
  dev.cap_set=1;note("capture %s",dev.cap_format==V4L2_PIX_FMT_NV12?"NV12":"P010");return 0;}
 case VIDIOC_G_FMT:{struct v4l2_format *f=arg;if(f->type!=CAP){errno=EINVAL;return -1;}
  f->fmt.pix_mp.pixelformat=v4l2_fourcc('Q','1','2','C');f->fmt.pix_mp.width=align(dev.width,16);f->fmt.pix_mp.height=align(dev.height,16);
  f->fmt.pix_mp.num_planes=1;return 0;}
 case VIDIOC_G_SELECTION:{struct v4l2_selection *s=arg;s->r=(struct v4l2_rect){0,0,dev.width,dev.height};return 0;}
 case VIDIOC_G_CTRL:{struct v4l2_control *c=arg;c->value=4;return 0;}
 case VIDIOC_SUBSCRIBE_EVENT:return 0;
 case VIDIOC_REQBUFS:{struct v4l2_requestbuffers *r=arg;
  if(r->memory!=V4L2_MEMORY_DMABUF){errno=EINVAL;return -1;}
  if(r->count>32)r->count=32;
  if(r->type==OUT)dev.n_out=r->count;else{if(r->count && !dev.cap_set)return violation("capture buffers before the capture format");dev.n_cap=r->count;}
  return 0;}
 case VIDIOC_STREAMON:{int t=*(int *)arg;
  if(t==OUT){dev.out_on=1;note("stream output");}
  else{if(!dev.cap_set)return violation("capture stream before its format");dev.cap_on=1;note("stream capture");decode();}
  return 0;}
 case VIDIOC_STREAMOFF:{int t=*(int *)arg;if(t==OUT)dev.out_on=0;else dev.cap_on=0;return 0;}
 case VIDIOC_QBUF:{struct v4l2_buffer *b=arg;
  if(b->memory!=V4L2_MEMORY_DMABUF || !b->m.planes){errno=EINVAL;return -1;}
  if(b->type==OUT) {
   if(!dev.out_on)return violation("access unit before the output stream");
   if(!dev.cap_on && ++dev.queued_before_capture>1)return violation("a second access unit before capture is set up");
   Slot *s=&dev.out[b->index];s->fd=b->m.planes[0].m.fd;s->bytes=b->m.planes[0].bytesused;s->queued=1;s->done=0;s->ts=b->timestamp;
   if(!dev.cap_on && !dev.source_change) {
    const char *delay=getenv("FAKE_VIDC_EVENT_DELAY");
    dev.source_change=1;dev.event_pending=1;dev.event_delay=delay?atoi(delay):3;
   }
   decode();return 0;
  }
  Slot *s=&dev.cap[b->index];s->fd=b->m.planes[0].m.fd;s->queued=1;decode();return 0;}
 case VIDIOC_DQBUF:{struct v4l2_buffer *b=arg;
  if(b->type==OUT) {
   for(int i=0;i<dev.n_out;i++)if(dev.out[i].queued && dev.out[i].done){dev.out[i].queued=0;b->index=i;return 0;}
   errno=EAGAIN;return -1;
  }
  if(!dev.n_ready){errno=dev.last_sent && dev.stop==2?EPIPE:EAGAIN;return -1;}
  int c=dev.ready[0];memmove(dev.ready,dev.ready+1,sizeof(int)*(--dev.n_ready));
  b->index=c;b->m.planes[0].bytesused=dev.cap[c].bytes;b->timestamp=dev.cap[c].ts;b->flags=0;
  if(dev.cap[c].done && dev.cap[c].bytes==0){b->flags=V4L2_BUF_FLAG_LAST;dev.stop=2;}
  return 0;}
 case VIDIOC_DQEVENT:{struct v4l2_event *e=arg;
  if(!dev.event_pending || dev.event_delay-->0){errno=ENOENT;return -1;}
  memset(e,0,sizeof(*e));e->type=V4L2_EVENT_SOURCE_CHANGE;e->u.src_change.changes=V4L2_EVENT_SRC_CH_RESOLUTION;dev.event_pending=0;return 0;}
 case VIDIOC_DECODER_CMD:{struct v4l2_decoder_cmd *c=arg;if(c->cmd!=V4L2_DEC_CMD_STOP){errno=EINVAL;return -1;}
  if(!dev.cap_on)return violation("drain before capture streams");dev.stop=1;note("stop");decode();return 0;}
 }
 errno=ENOTTY;return -1;
}

int __wrap_poll(struct pollfd *fds,nfds_t n,int timeout) {
 for(nfds_t i=0;i<n;i++)if(fds[i].fd==dev.fd && dev.fd>=0) {
  fds[i].revents=(dev.event_pending && dev.event_delay<=0?POLLPRI:0)|(dev.n_ready?POLLIN:0)|POLLOUT;return 1;
 }
 return __real_poll(fds,n,timeout);
}

/* ---- the consumer, as the GStreamer element uses librungiccodec ---------------------------- */
static int records;
static int output(void *user,const RungicCodecFrame *f) {
 (void)user;
 int w=f->width,h=f->height,cw=(w+1)/2,ch=(h+1)/2,bytes=f->depth==10?2:1;
 uint8_t *semi[2]={calloc(w*bytes,h),calloc(cw*2*bytes,ch)};int stride[2]={w*bytes,cw*2*bytes};
 int r=f->depth==10?rungic_codec_copy_p010(f,semi,stride):rungic_codec_copy_nv12(f,semi,stride);
 unsigned luma=bytes==2?(unsigned)(semi[0][0]|semi[0][1]<<8)>>6:semi[0][0];
 unsigned chroma=bytes==2?(unsigned)(semi[1][0]|semi[1][1]<<8)>>6:semi[1][0];
 printf("%s{\"id\":%d,\"pts\":%lld,\"width\":%d,\"height\":%d,\"depth\":%d,\"copied\":%s,\"luma\":%u,\"chroma\":%u}",
        records++?",":"",f->id,(long long)f->pts,w,h,f->depth,r?"false":"true",luma,chroma);
 free(semi[0]);free(semi[1]);
 return 0;
}

static int frame(RungicCodec *c,int id,int params) {
 /* SPS (type 7) first when asked, then an IDR slice (type 5) ending in the picture's value. */
 static const uint8_t sps[]={0,0,0,1,0x67,0x42,0,0x1e,0,0,0,1,0x68,0xce};
 int n=0;
 if(params){memcpy(c->memory,sps,sizeof(sps));n=sizeof(sps);}
 const uint8_t slice[]={0,0,0,1,0x65,0x88,0x84,(uint8_t)(16+id)};
 memcpy(c->memory+n,slice,sizeof(slice));n+=sizeof(slice);
 return rungic_codec_exchange(c,RUNGIC_FRAME,id,id*33333LL,0,n,output,NULL);
}

int main(int argc,char **argv) {
 if(argc!=4)return 2;
 int ten=!strcmp(argv[1],"decode10"),flush=!strcmp(argv[1],"flush"),kind=atoi(argv[2]),frames=atoi(argv[3]);
 RungicCodec codec;rungic_codec_init(&codec);
 RungicCodecConfig config={.kind=kind,.width=176,.height=144};
 int opened=ten?rungic_codec_open_options(&codec,&config,RUNGIC_OPTION_BUFFERS|RUNGIC_OPTION_TEN_BIT):rungic_codec_open(&codec,&config);
 printf("{\"open\":%s,\"name\":\"%s\",\"error\":\"%s\",\"records\":[",opened?"false":"true",codec.name,opened?codec.error:"");
 int failures=0;
 if(!opened) {
  for(int i=0;i<frames;i++)failures+=frame(&codec,i,i==0)!=0;
  if(flush) {
   failures+=rungic_codec_exchange(&codec,RUNGIC_FLUSH,0,0,0,0,NULL,NULL)!=0;
   for(int i=0;i<frames;i++)failures+=frame(&codec,100+i,0)!=0;     /* no parameter sets resent */
  }
  failures+=rungic_codec_exchange(&codec,RUNGIC_DRAIN,0,0,0,0,output,NULL)!=0;
 }
 printf("],\"failures\":%d,\"ended\":%d,\"inputs\":%u,\"outputs\":%u,\"last_error\":\"%s\"",failures,codec.ended,codec.input_count,codec.output_count,failures?codec.error:"");
 rungic_codec_close(&codec);
 printf(",\"sessions\":%d,\"violations\":%d,\"replayed_params\":%s,\"log\":\"%s\"}\n",dev.sessions,dev.violations,
        dev.first_after_open_has_params?"true":"false",dev.log);
 return 0;
}
