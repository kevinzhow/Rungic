/* SPDX-License-Identifier: MIT
 * IPC transport shared by the GStreamer and FFmpeg adapters. The optional
 * preload constructor grants Firefox only a connection to the codec broker,
 * before its normal sandbox is installed. It does not change sandbox policy.
 */
#define _GNU_SOURCE
#include "codec-client.h"
#include "codec-v4l2.h"
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <sys/mman.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <poll.h>
#include <pthread.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <arpa/inet.h>
#include <linux/dma-buf.h>
#define MAGIC 0x4d434231u
#define MAGIC2 0x4d434232u
#define OPEN 0x4f50454eu
#define CHANNEL 0x4d434631u
static pthread_mutex_t broker_lock=PTHREAD_MUTEX_INITIALIZER;
static int broker=-1;static pid_t broker_pid;
/* Channel version 2 state, kept here by channel descriptor: RungicCodec keeps its first layout for
 * consumers built against it (the private FFmpeg). A slot is one of the app's decoder buffers. */
#define SLOTS 48
typedef struct {int id,fd;uint8_t *map;size_t size;} Slot;
/* version 3: no channel, the msm_vidc V4L2 decoder itself (codec-v4l2.c); fd is its device. */
typedef struct {int fd,version;Slot slot[SLOTS];V4l2Decoder *v4l2;} Channel;
static Channel channels[32];
static pthread_mutex_t channel_lock=PTHREAD_MUTEX_INITIALIZER;
static int app_version1; /* the app refused channel version 2 once: an older Rungic APK */
static int buffers_unusable; /* this phone's decoder buffers could not be read: shared memory from now on */
static Channel *channel_of(int fd) {
 Channel *found=NULL;pthread_mutex_lock(&channel_lock);
 for(size_t i=0;i<sizeof(channels)/sizeof(channels[0]);i++)if(channels[i].version && channels[i].fd==fd){found=&channels[i];break;}
 pthread_mutex_unlock(&channel_lock);return found;
}
static Channel *channel_add(int fd,int version) {
 Channel *added=NULL;pthread_mutex_lock(&channel_lock);
 for(size_t i=0;i<sizeof(channels)/sizeof(channels[0]);i++)if(!channels[i].version) {
  added=&channels[i];memset(added,0,sizeof(*added));added->fd=fd;added->version=version;
  for(int s=0;s<SLOTS;s++){added->slot[s].fd=-1;added->slot[s].id=-1;}
  break;
 }
 pthread_mutex_unlock(&channel_lock);return added;
}
static void slot_clear(Slot *s) {
 if(s->map)munmap(s->map,s->size);if(s->fd>=0)close(s->fd);
 s->map=NULL;s->size=0;s->fd=-1;s->id=-1;
}
/* 1 when the descriptor was a V4L2 decoder's (closed here with it). */
static int channel_remove(int fd) {
 Channel *c=channel_of(fd);if(!c)return 0;
 int v4l2=c->v4l2!=NULL;
 for(int s=0;s<SLOTS;s++)slot_clear(&c->slot[s]);
 if(c->v4l2)v4l2_close(c->v4l2);
 pthread_mutex_lock(&channel_lock);c->version=0;c->fd=-1;c->v4l2=NULL;pthread_mutex_unlock(&channel_lock);
 return v4l2;
}
static int io(int fd,void *data,size_t length,int sending) {
 uint8_t *p=data;
 while(length) {
  struct pollfd f={.fd=fd,.events=sending?POLLOUT:POLLIN};
  int r;do {r=poll(&f,1,12000);}while(r<0 && errno==EINTR);
  if(r<=0){if(!r)errno=ETIMEDOUT;return -1;}
  ssize_t n=sending?send(fd,p,length,MSG_NOSIGNAL):recv(fd,p,length,0);
  if(n<0 && errno==EINTR)continue;
  if(n<=0){if(!n)errno=EPIPE;return -1;}p+=n;length-=n;
 }
 return 0;
}
static int put32(int fd,uint32_t v){v=htonl(v);return io(fd,&v,4,1);}
static int get32(int fd,uint32_t *v){int r=io(fd,v,4,0);if(!r)*v=ntohl(*v);return r;}
/* A record's first word, with the descriptor the app may attach to it (SCM_RIGHTS), else -1. */
static int get32_fd(int fd,uint32_t *v,int *received) {
 uint8_t *p=(uint8_t *)v;size_t length=4;*received=-1;
 while(length) {
  struct pollfd f={.fd=fd,.events=POLLIN};
  int r;do {r=poll(&f,1,12000);}while(r<0 && errno==EINTR);
  if(r<=0){if(!r)errno=ETIMEDOUT;goto fail;}
  char control[CMSG_SPACE(sizeof(int)*4)];
  struct iovec vec={.iov_base=p,.iov_len=length};
  struct msghdr msg={.msg_iov=&vec,.msg_iovlen=1,.msg_control=control,.msg_controllen=sizeof(control)};
  ssize_t n=recvmsg(fd,&msg,MSG_CMSG_CLOEXEC);
  if(n<0 && errno==EINTR)continue;
  if(n<=0){if(!n)errno=EPIPE;goto fail;}
  for(struct cmsghdr *h=CMSG_FIRSTHDR(&msg);h;h=CMSG_NXTHDR(&msg,h))
   if(h->cmsg_level==SOL_SOCKET && h->cmsg_type==SCM_RIGHTS)
    for(size_t i=0;i<(h->cmsg_len-CMSG_LEN(0))/sizeof(int);i++) {
     int got;memcpy(&got,(char *)CMSG_DATA(h)+i*sizeof(int),sizeof(int));
     if(*received<0)*received=got;else close(got);
    }
  if(msg.msg_flags&MSG_CTRUNC){errno=EPROTO;goto fail;}
  p+=n;length-=n;
 }
 *v=ntohl(*v);return 0;
fail:if(*received>=0)close(*received);*received=-1;return -1;
}
static int connect_broker(void) {
 if(broker>=0 && broker_pid==getpid())return 0;
 if(broker>=0)close(broker);broker=-1;broker_pid=getpid();
 int fd=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);if(fd<0)return -1;
 struct sockaddr_un addr={.sun_family=AF_UNIX};
 /* The app's codec broker; another one only for a stand-in (tools/system/tests). */
 const char *path=getenv("RUNGIC_CODEC_SOCKET");
 if(!path || !*path)path="/mnt/android-wayland/codec.sock";
 if(strlen(path)>=sizeof(addr.sun_path)){close(fd);errno=ENAMETOOLONG;return -1;}
 strcpy(addr.sun_path,path);
 /* A frozen Rungic app (the phone asleep, docs/research/97) stops accepting: once its backlog is
  * full a blocking connect waited forever. A Unix connect waits for SO_SNDTIMEO at most. */
 struct timeval wait={.tv_sec=2},none={0};
 setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&wait,sizeof(wait));
 if(connect(fd,(struct sockaddr *)&addr,sizeof(addr)) || put32(fd,MAGIC)) {int e=errno;close(fd);errno=e;return -1;}
 setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&none,sizeof(none));
 broker=fd;return 0;
}
static void before_fork(void){pthread_mutex_lock(&broker_lock);}
static void after_fork_parent(void){pthread_mutex_unlock(&broker_lock);}
static void after_fork_child(void) {
 /* Firefox's fork server does not exec its children. Establish a distinct
  * broker in the child before it installs the content/RDD sandbox. */
 int saved=errno;
 if(broker>=0)close(broker);
 broker=-1;broker_pid=0;
 pthread_mutex_unlock(&broker_lock);
 connect_broker();errno=saved;
}
__attribute__((constructor)) static void preload_broker(void) {
 if(getenv("RUNGIC_CODEC_PRECONNECT")) {
  int saved=errno;connect_broker();
  pthread_atfork(before_fork,after_fork_parent,after_fork_child);
  errno=saved;
 }
}
void rungic_codec_init(RungicCodec *c){memset(c,0,sizeof(*c));c->fd=-1;}
static int fail(RungicCodec *c,const char *what) {
 snprintf(c->error,sizeof(c->error),"%s: %s",what,strerror(errno));return -1;
}
static int remote_error(RungicCodec *c) {
 uint32_t n;if(get32(c->fd,&n) || n>=sizeof(c->error)) {errno=EPROTO;return fail(c,"Backend error");}
 if(io(c->fd,c->error,n,0))return fail(c,"Read error");c->error[n]=0;return -1;
}
/* A channel and shared memory from the broker (c->fd, c->memory). */
static int open_channel(RungicCodec *c) {
 int fdlist[4]={-1,-1,-1,-1},count=0,result=-1;
 pthread_mutex_lock(&broker_lock);
 if(connect_broker() || put32(broker,OPEN))goto broker_error;
 uint32_t word=0;
 char control[CMSG_SPACE(sizeof(fdlist))]={0};
 struct iovec vec={.iov_base=&word,.iov_len=4};
 struct msghdr msg={.msg_iov=&vec,.msg_iovlen=1,.msg_control=control,.msg_controllen=sizeof(control)};
 struct pollfd pollfd={.fd=broker,.events=POLLIN};
 int ready;do{ready=poll(&pollfd,1,12000);}while(ready<0 && errno==EINTR);
 if(ready<=0){if(!ready)errno=ETIMEDOUT;goto broker_error;}
 ssize_t n;do {n=recvmsg(broker,&msg,MSG_CMSG_CLOEXEC);}while(n<0 && errno==EINTR);
 if(n<=0)goto broker_error;
 for(struct cmsghdr *h=CMSG_FIRSTHDR(&msg);h;h=CMSG_NXTHDR(&msg,h)) {
  if(h->cmsg_level==SOL_SOCKET && h->cmsg_type==SCM_RIGHTS) {
   size_t bytes=h->cmsg_len-CMSG_LEN(0);
   for(size_t i=0;i<bytes/sizeof(int);i++) {int fd;memcpy(&fd,(char *)CMSG_DATA(h)+i*sizeof(int),sizeof(int));if(count<4)fdlist[count++]=fd;else close(fd);}
  }
 }
 if(n<4 && io(broker,(char *)&word+n,4-n,0))goto broker_error;
 if(ntohl(word)!=CHANNEL || count!=2 || (msg.msg_flags&MSG_CTRUNC)){errno=EBUSY;goto out;}
 c->fd=fdlist[0];fdlist[0]=-1;
 struct stat statbuf;
 /* Android SharedMemory can be an ashmem character device (st_size=0),
  * or a regular memfd. The trusted broker creates exactly two 16 MiB halves. */
 if(fstat(fdlist[1],&statbuf) ||
    !(statbuf.st_size==RUNGIC_CODEC_HALF*2 || (S_ISCHR(statbuf.st_mode) && statbuf.st_size==0))){errno=EPROTO;goto out;}
 c->memory=mmap(NULL,RUNGIC_CODEC_HALF*2,PROT_READ|PROT_WRITE,MAP_SHARED,fdlist[1],0);
 if(c->memory==MAP_FAILED){c->memory=NULL;goto out;}
 result=0;goto out;
broker_error:
 if(broker>=0)close(broker);broker=-1;
out:
 {int error=errno;for(int i=0;i<count;i++)if(fdlist[i]>=0)close(fdlist[i]);pthread_mutex_unlock(&broker_lock);errno=error;}
 return result;
}
/* 0 configured, -1 failed (c->error), 1 the app does not know channel version 2. */
static int configure(RungicCodec *c,const RungicCodecConfig *config,int version,int options) {
 const int values[]={version==2?(int)MAGIC2:(int)MAGIC,config->encoder,config->kind,config->width,config->height,config->fps_num,config->fps_den,config->bitrate,config->key_interval,config->color_standard,config->color_range,config->color_transfer,options};
 uint32_t word;
 for(size_t i=0;i<sizeof(values)/sizeof(values[0])-(version==2?0:1);i++)if(put32(c->fd,values[i]))goto config_error;
 if(get32(c->fd,&word)) {
  /* An app before version 2 rejects the magic; a minimal broker may just close the channel. */
  if(version==2 && (errno==EPIPE || errno==ECONNRESET))return 1;
  goto config_error;
 }
 if(word==(uint32_t)-1){remote_error(c);return version==2 && strstr(c->error,"Channel version")?1:-1;}
 if(word!=RUNGIC_DONE || get32(c->fd,&word) || word>=sizeof(c->name)){errno=EPROTO;goto config_error;}
 if(io(c->fd,c->name,word,0))goto config_error;c->name[word]=0;
 if(version==2 && !channel_add(c->fd,2)){errno=EMFILE;goto config_error;}
 return 0;
config_error:fail(c,"Configure codec");return -1;
}
static int default_options(void) {
 const char *buffers=getenv("RUNGIC_CODEC_BUFFERS");
 if(buffers && *buffers)return !strcmp(buffers,"1")?RUNGIC_OPTION_BUFFERS:0;
 /* Firefox's RDD sandbox: DMA-BUF sync ioctls there are not verified yet. */
 return getenv("RUNGIC_CODEC_PRECONNECT") || buffers_unusable?0:RUNGIC_OPTION_BUFFERS;
}
int rungic_codec_open(RungicCodec *c,const RungicCodecConfig *config) {
 return rungic_codec_open_options(c,config,RUNGIC_OPTIONS_DEFAULT);
}
/* Decoders go straight to the msm_vidc V4L2 decoder when there is one (RUNGIC_CODEC_V4L2=0
 * turns that off; Firefox's preload, whose sandbox cannot open devices, only with =1). */
static int open_v4l2(RungicCodec *c,const RungicCodecConfig *config,int options) {
 const char *wanted=getenv("RUNGIC_CODEC_V4L2");
 if(config->encoder || (wanted && !strcmp(wanted,"0")) || (getenv("RUNGIC_CODEC_PRECONNECT") && !(wanted && !strcmp(wanted,"1"))))return -1;
 char error[sizeof(c->error)];
 V4l2Decoder *d=v4l2_open(config,(options&RUNGIC_OPTION_TEN_BIT)!=0,error,sizeof(error));
 if(!d)return -1;
 void *memory=mmap(NULL,RUNGIC_CODEC_HALF*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
 Channel *channel=memory==MAP_FAILED?NULL:channel_add(v4l2_fd(d),3);
 if(!channel){if(memory!=MAP_FAILED)munmap(memory,RUNGIC_CODEC_HALF*2);v4l2_close(d);return -1;}
 channel->v4l2=d;c->fd=v4l2_fd(d);c->memory=memory;
 snprintf(c->name,sizeof(c->name),"msm_vidc_decoder (V4L2)");
 return 0;
}
int rungic_codec_open_options(RungicCodec *c,const RungicCodecConfig *config,int options) {
 rungic_codec_close(c);c->ended=0;c->input_count=c->output_count=0;c->error[0]=0;
 const char *disabled=getenv("RUNGIC_CODEC_DISABLE");
 if(disabled && !strcmp(disabled,"1")){errno=ENODEV;return fail(c,"Hardware disabled by environment");}
 if(options==RUNGIC_OPTIONS_DEFAULT)options=default_options();
 if(!open_v4l2(c,config,options))return 0;
 for(int version=app_version1?1:2;;version=1) {
  if(version==1 && (options&RUNGIC_OPTION_TEN_BIT)){errno=ENOTSUP;fail(c,"10-bit output needs a newer Rungic app");return -1;}
  if(open_channel(c)){fail(c,"Open codec channel");rungic_codec_close(c);return -1;}
  int r=configure(c,config,version,options);
  if(!r){if(version==1 && !app_version1)app_version1=1;return 0;}
  rungic_codec_close(c);
  if(r<0 || version==1)return -1;
 }
}
/* The slot a decoded picture is in: mapped from the descriptor that came with its first picture. */
static Slot *install_slot(Channel *channel,int id,int received,int size) {
 Slot *slot=NULL,*free_slot=NULL,*oldest=NULL;
 for(int s=0;s<SLOTS;s++) {
  Slot *t=&channel->slot[s];
  if(t->id==id)slot=t;
  else if(t->id<0){if(!free_slot)free_slot=t;}
  else if(!oldest || t->id<oldest->id)oldest=t;
 }
 if(received<0) {
  if(!slot || (size_t)size!=slot->size){errno=EPROTO;return NULL;}
  return slot;
 }
 /* Slot numbers only grow: the lowest belongs to a decoder buffer set the app has replaced. */
 if(!slot)slot=free_slot?free_slot:oldest;
 slot_clear(slot);
 if(size<=0 || size>(256<<20)){close(received);errno=EPROTO;return NULL;}
 void *map=mmap(NULL,size,PROT_READ,MAP_SHARED,received,0);
 if(map==MAP_FAILED){close(received);return NULL;}
 slot->id=id;slot->fd=received;slot->map=map;slot->size=size;
 return slot;
}
int rungic_codec_exchange(RungicCodec *c,int cmd,int id,int64_t pts,int flags,int length,RungicCodecOutput callback,void *user) {
 int consumer_failed=0;
 if(c->fd<0){errno=ENOTCONN;return fail(c,"Codec closed");}
 Channel *direct=channel_of(c->fd);
 if(direct && direct->v4l2) {
  if(cmd==RUNGIC_FRAME && (length<=0 || (unsigned)length>RUNGIC_CODEC_HALF)){errno=EINVAL;return fail(c,"Codec exchange");}
  if(cmd==RUNGIC_FRAME)c->input_count++;
  return v4l2_exchange(direct->v4l2,cmd,id,pts,c->memory,length,callback,user,&c->ended,&c->output_count,c->error,sizeof(c->error));
 }
 if(put32(c->fd,cmd))goto error;
 if(cmd==RUNGIC_FRAME) {
  if(length<=0 || (unsigned)length>RUNGIC_CODEC_HALF){errno=EINVAL;goto error;}
  const uint32_t data[]={id,(uint64_t)pts>>32,(uint32_t)pts,flags,length};
  for(size_t i=0;i<5;i++)if(put32(c->fd,data[i]))goto error;
  c->input_count++;
 }
 for(;;) {
  uint32_t type;int received=-1;Channel *channel=channel_of(c->fd);
  if(channel?get32_fd(c->fd,&type,&received):get32(c->fd,&type))goto error;
  if(received>=0 && type!=RUNGIC_DECODED){close(received);errno=EPROTO;goto error;}
  if(type==RUNGIC_DONE){if(cmd==RUNGIC_FLUSH)c->ended=0;return consumer_failed?-1:0;}
  if(type==(uint32_t)-1) {remote_error(c);if(strstr(c->error,"not linear YUV"))buffers_unusable=1;return -1;}
  if(type==RUNGIC_EOS){c->ended=1;continue;}
  if(type<RUNGIC_ENCODED || type>RUNGIC_CONFIG){if(received>=0)close(received);errno=EPROTO;goto error;}
  /* Version 2 adds three plane offsets, the buffer's slot (-1: shared memory) and the depth. */
  uint32_t h[23]={0};int words=channel?23:18;
  for(int i=0;i<words;i++)if(get32(c->fd,&h[i])){if(received>=0)close(received);goto error;}
  RungicCodecFrame frame={.type=type,.id=h[0],.flags=h[1],.size=h[2],.pts=(int64_t)(((uint64_t)h[3]<<32)|h[4]),.width=h[5],.height=h[6],.crop_x=h[7],.crop_y=h[8],.data=c->memory+RUNGIC_CODEC_HALF,.depth=8};
  for(int p=0;p<3;p++){frame.plane[p].stride=h[9+p*3];frame.plane[p].step=h[10+p*3];frame.plane[p].length=h[11+p*3];}
  Slot *slot=NULL;
  if(channel) {
   for(int p=0;p<3;p++)frame.offset[p]=h[18+p];
   frame.depth=h[22];
   int id=(int)h[21];
   if(id>=0)slot=install_slot(channel,id,received,frame.size);
   else if(received>=0){close(received);errno=EPROTO;goto error;}
   if(id>=0 && !slot)goto error;
   if(slot)frame.data=slot->map;
  } else for(int p=1;p<3;p++)frame.offset[p]=frame.offset[p-1]+frame.plane[p-1].length;
  if(frame.size<0 || (!slot && (unsigned)frame.size>RUNGIC_CODEC_HALF) || (frame.depth!=8 && frame.depth!=10)){errno=EPROTO;goto error;}
  for(int p=0;p<3;p++)if(frame.offset[p]<0 || frame.plane[p].length<0 || (int64_t)frame.offset[p]+frame.plane[p].length>frame.size){errno=EPROTO;goto error;}
  if(slot){struct dma_buf_sync sync={.flags=DMA_BUF_SYNC_START|DMA_BUF_SYNC_READ};ioctl(slot->fd,DMA_BUF_IOCTL_SYNC,&sync);}
  int r=callback && !consumer_failed?callback(user,&frame):0;
  if(slot){struct dma_buf_sync sync={.flags=DMA_BUF_SYNC_END|DMA_BUF_SYNC_READ};ioctl(slot->fd,DMA_BUF_IOCTL_SYNC,&sync);}
  if(put32(c->fd,0xac))goto error;
  if(type!=RUNGIC_CONFIG)c->output_count++;
  /* A downstream FLUSHING result must not leave response records unread.
   * Consume/ack the rest of this exchange before the next FLUSH command. */
  if(r){snprintf(c->error,sizeof(c->error),"Output consumer stopped");consumer_failed=1;}
 }
error:return fail(c,"Codec exchange");
}
void rungic_codec_close(RungicCodec *c) {
 if(c->fd>=0){if(!channel_remove(c->fd)){shutdown(c->fd,SHUT_RDWR);close(c->fd);}c->fd=-1;}
 if(c->memory){munmap(c->memory,RUNGIC_CODEC_HALF*2);c->memory=NULL;}
}
/* Plane p's first visible sample, or NULL when w x h samples of size bytes (step apart) do not
 * fit its length. */
static const uint8_t *plane_at(const RungicCodecFrame *f,int p,int w,int h,int bytes) {
 int x=p?f->crop_x/2:f->crop_x,y=p?f->crop_y/2:f->crop_y;
 int step=f->plane[p].step,row=f->plane[p].stride,len=f->plane[p].length;
 if(step<bytes || step>4*bytes || row<1 || len<1 || f->offset[p]<0 || (int64_t)f->offset[p]+len>f->size)return NULL;
 size_t base=(size_t)y*row+(size_t)x*step,last=base+(size_t)(h-1)*row+(size_t)(w-1)*step+bytes-1;
 if(last>=(unsigned)len)return NULL;
 return f->data+f->offset[p]+base;
}
static int picture(const RungicCodecFrame *f,int depth) {
 return f->type==RUNGIC_DECODED && f->depth==depth && f->width>=1 && f->height>=1 && f->width<=2560 && f->height<=2560 && f->crop_x>=0 && f->crop_y>=0;
}
int rungic_codec_copy_i420(const RungicCodecFrame *f,uint8_t *const dst[3],const int stride[3]) {
 if(!picture(f,8))return -1;
 for(int p=0;p<3;p++) {
  int w=p?(f->width+1)/2:f->width,h=p?(f->height+1)/2:f->height,step=f->plane[p].step,row=f->plane[p].stride;
  const uint8_t *s=plane_at(f,p,w,h,1);
  if(!s || stride[p]<w || step>2)return -1;
  for(int r=0;r<h;r++) {
   const uint8_t *src=s+(size_t)r*row;uint8_t *d=dst[p]+r*stride[p];
   if(step==1)memcpy(d,src,w);else for(int col=0;col<w;col++)d[col]=src[col*step];
  }
 }
 return 0;
}
/* Y, then CbCr interleaved: rows copied as they are when the picture is semi-planar already. */
static int copy_semiplanar(const RungicCodecFrame *f,uint8_t *const dst[2],const int stride[2],int bytes) {
 int w=f->width,h=f->height,cw=(w+1)/2,ch=(h+1)/2;
 const uint8_t *y=plane_at(f,0,w,h,bytes),*u=plane_at(f,1,cw,ch,bytes),*v=plane_at(f,2,cw,ch,bytes);
 if(!y || !u || !v || f->plane[0].step!=bytes || stride[0]<w*bytes || stride[1]<cw*2*bytes)return -1;
 for(int r=0;r<h;r++)memcpy(dst[0]+(size_t)r*stride[0],y+(size_t)r*f->plane[0].stride,(size_t)w*bytes);
 int interleaved=f->plane[1].step==2*bytes && f->plane[2].step==2*bytes && v==u+bytes && f->plane[1].stride==f->plane[2].stride;
 for(int r=0;r<ch;r++) {
  uint8_t *d=dst[1]+(size_t)r*stride[1];
  if(interleaved){memcpy(d,u+(size_t)r*f->plane[1].stride,(size_t)cw*2*bytes);continue;}
  const uint8_t *su=u+(size_t)r*f->plane[1].stride,*sv=v+(size_t)r*f->plane[2].stride;
  for(int col=0;col<cw;col++){memcpy(d+col*2*bytes,su+(size_t)col*f->plane[1].step,bytes);memcpy(d+col*2*bytes+bytes,sv+(size_t)col*f->plane[2].step,bytes);}
 }
 return 0;
}
int rungic_codec_copy_nv12(const RungicCodecFrame *f,uint8_t *const dst[2],const int stride[2]) {
 return picture(f,8)?copy_semiplanar(f,dst,stride,1):-1;
}
int rungic_codec_copy_p010(const RungicCodecFrame *f,uint8_t *const dst[2],const int stride[2]) {
 return picture(f,10)?copy_semiplanar(f,dst,stride,2):-1;
}
