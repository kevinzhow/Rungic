// SPDX-License-Identifier: MIT
package com.rungic.plasma;

import android.content.Context;
import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.hardware.HardwareBuffer;
import android.media.*;
import android.net.*;
import android.os.*;
import android.system.*;
import android.util.Log;
import android.view.Surface;
import java.io.*;
import java.nio.*;
import java.util.*;
import java.util.concurrent.*;

/** Restricted codec broker. No file paths, network operations, camera or microphone API. */
final class CodecBridge implements Closeable {
    static final int HALF=16*1024*1024, MAGIC=0x4d434231, MAGIC2=0x4d434232, OPEN=0x4f50454e, CHANNEL=0x4d434631;
    /** Channel version 2 options: decoded frames as DMA-BUFs (Linux maps them, no copy here), 10-bit (P010) output. */
    static final int OPTION_BUFFERS=1, OPTION_TEN_BIT=2;
    static final int FRAME=1,DRAIN=2,FLUSH=3,CLOSE=4,ACK=0xac;
    static final int DONE=0,ENCODED=1,DECODED=2,CONFIG=3,EOS=4,ERROR=-1;
    private final File path;
    private final Semaphore brokerSlots=new Semaphore(64),codecSlots=new Semaphore(6);
    private final Set<LocalSocket> brokers=ConcurrentHashMap.newKeySet();
    private final Set<FileDescriptor> channels=ConcurrentHashMap.newKeySet();
    private final ExecutorService workers=Executors.newCachedThreadPool();
    private volatile boolean running;
    private LocalSocket bound;
    private LocalServerSocket listener;
    CodecBridge(Context context) { path=new File(context.getFilesDir(),"tmp/codec.sock"); }
    synchronized void start() throws IOException {
        if(running)return;
        path.delete();bound=new LocalSocket();
        bound.bind(new LocalSocketAddress(path.getAbsolutePath(),LocalSocketAddress.Namespace.FILESYSTEM));
        listener=new LocalServerSocket(bound.getFileDescriptor());
        try { Os.chmod(path.getAbsolutePath(),0666); } catch(ErrnoException e) { throw new IOException(e); }
        running=true;
        Thread thread=new Thread(() -> {
            while(running)try {
                LocalSocket s=listener.accept();int uid=s.getPeerCredentials().getUid();
                if((uid!=0 && uid!=1000) || !brokerSlots.tryAcquire()) { s.close();continue; }
                brokers.add(s);
                workers.execute(() -> {try(LocalSocket socket=s){broker(socket);}catch(Exception e){if(!(e instanceof EOFException))Log.d("RungicCodec","Broker closed: "+e);}
                    finally{brokers.remove(s);brokerSlots.release();}});
            } catch(Exception e) { if(running)Log.w("RungicCodec","Accept",e); }
        },"rungic-codec-accept");thread.setDaemon(true);thread.start();
    }
    private void broker(LocalSocket socket) throws Exception {
        socket.setSoTimeout(5000);
        DataInputStream input=new DataInputStream(socket.getInputStream());
        DataOutputStream output=new DataOutputStream(socket.getOutputStream());
        if(input.readInt()!=MAGIC)throw new IOException("Broker version");
        socket.setSoTimeout(0);
        while(running) {
            if(input.readInt()!=OPEN)throw new IOException("Broker operation");
            if(!codecSlots.tryAcquire()) {output.writeInt(ERROR);output.flush();continue;}
            FileDescriptor local=new FileDescriptor(),remote=new FileDescriptor();SharedMemory memory=null;
            boolean transferred=false;
            try {
                Os.socketpair(OsConstants.AF_UNIX,OsConstants.SOCK_STREAM|OsConstants.SOCK_CLOEXEC,0,local,remote);
                memory=SharedMemory.create("rungic-codec-frames",HALF*2);
                Parcel parcel=Parcel.obtain();ParcelFileDescriptor memoryFd=null;
                try {
                    memory.writeToParcel(parcel,0);parcel.setDataPosition(0);memoryFd=parcel.readFileDescriptor();
                    socket.setFileDescriptorsForSend(new FileDescriptor[]{remote,memoryFd.getFileDescriptor()});
                    output.writeInt(CHANNEL);output.flush();
                    socket.setFileDescriptorsForSend(null);
                } finally {if(memoryFd!=null)memoryFd.close();parcel.recycle();}
                Os.close(remote);
                final SharedMemory shared=memory;channels.add(local);
                workers.execute(() -> {
                    try {
                        // Decoders run in native code (jni/media/codec_session.c); an encoder's configuration comes back.
                        int[] header=null;
                        if(MediaBuffers.AVAILABLE)try(ParcelFileDescriptor channel=ParcelFileDescriptor.dup(local)) {
                            header=MediaBuffers.runSession(channel.getFd(),shared);
                            if(header==null)return;
                        }
                        new Session(local,shared,header).run();
                    }catch(Exception e){Log.w("RungicCodec","Session: "+e);}
                    finally {channels.remove(local);try{Os.close(local);}catch(Exception ignored){}shared.close();codecSlots.release();}
                });
                transferred=true;
            } finally {
                if(!transferred) {try{Os.close(local);}catch(Exception ignored){}try{Os.close(remote);}catch(Exception ignored){}
                    if(memory!=null)memory.close();codecSlots.release();}
            }
        }
    }
    static final class Output {
        final int index;final MediaCodec.BufferInfo info=new MediaCodec.BufferInfo();
        Output(int index,MediaCodec.BufferInfo from){this.index=index;info.set(from.offset,from.size,from.presentationTimeUs,from.flags);}
    }
    static final class Session {
        final FileDescriptor fd;final SharedMemory memory;
        MediaCodec codec;ByteBuffer shared;DataInputStream in;DataOutputStream out;
        boolean encoder,ended,version2,tenBit;int kind,width,height,inputCount,outputCount;
        String name;
        // Version 2 with OPTION_BUFFERS: the decoder renders into an ImageReader; each buffer's DMA-BUF
        // goes to Linux once (a slot), then only its slot number.
        ImageReader reader;HandlerThread imageThread;ParcelFileDescriptor channel;
        final LinkedBlockingQueue<Image> images=new LinkedBlockingQueue<>();
        final Map<Long,long[]> slots=new HashMap<>();int nextSlot;
        // Asynchronous MediaCodec: its callbacks queue free input indices (Integer), outputs (Output)
        // and errors (Exception); the session thread waits on this queue instead of polling.
        final LinkedBlockingQueue<Object> events=new LinkedBlockingQueue<>();
        final ArrayDeque<Integer> freeInputs=new ArrayDeque<>();HandlerThread callbackThread;
        final Map<Long,ArrayDeque<Integer>> frames=new HashMap<>();
        final Map<Integer,byte[]> parameters=new TreeMap<>();
        final int[] header;int headerRead;
        Session(FileDescriptor fd,SharedMemory memory,int[] header){this.fd=fd;this.memory=memory;this.header=header;}
        /** A configuration word: from the header the native session already read, else the channel. */
        int config() throws IOException {return header!=null?header[headerRead++]:in.readInt();}
        void run() throws Exception {
            try(FileInputStream input=new FileInputStream(Os.dup(fd));FileOutputStream output=new FileOutputStream(Os.dup(fd))) {
                in=new DataInputStream(new BufferedInputStream(input,4096));out=new DataOutputStream(new BufferedOutputStream(output,4096));
                Os.setsockoptTimeval(fd,OsConstants.SOL_SOCKET,OsConstants.SO_RCVTIMEO,StructTimeval.fromMillis(10000));
                Os.setsockoptTimeval(fd,OsConstants.SOL_SOCKET,OsConstants.SO_SNDTIMEO,StructTimeval.fromMillis(10000));
                try {
                    configure();
                    // A paused pipeline may keep a channel idle. Closing its FD cancels all waits.
                    Os.setsockoptTimeval(fd,OsConstants.SOL_SOCKET,OsConstants.SO_RCVTIMEO,StructTimeval.fromMillis(0));
                    while(true) {
                        int cmd=in.readInt();if(cmd==CLOSE)break;
                        if(cmd==FLUSH) {
                            // Asynchronous mode: flush drops every index the callbacks gave; start() resumes them.
                            codec.flush();frames.clear();ended=false;events.clear();freeInputs.clear();
                            for(Image image;(image=images.poll())!=null;)image.close();
                            codec.start();
                            if(!encoder && !parameters.isEmpty()) {
                                ByteArrayOutputStream csd=new ByteArrayOutputStream();for(byte[] p:parameters.values())csd.write(p);
                                int index=nextInput();
                                byte[] bytes=csd.toByteArray();codec.getInputBuffer(index).put(bytes);
                                codec.queueInputBuffer(index,0,bytes.length,0,MediaCodec.BUFFER_FLAG_CODEC_CONFIG);
                            }
                            out.writeInt(DONE);out.flush();continue;
                        }
                        if(ended)throw new IOException("Input after EOS");
                        if(cmd!=FRAME && cmd!=DRAIN)throw new IOException("Codec operation");
                        int id=-1,flags=0,length=0;long pts=0;
                        if(cmd==FRAME) {id=in.readInt();pts=in.readLong();flags=in.readInt();length=in.readInt();
                            if(length<=0 || length>HALF)throw new IOException("Frame size");
                            if(encoder && length!=width*height*3/2)throw new IOException("I420 size");
                            if(frames.size()>128)throw new IOException("Too many delayed frames");}
                        int index=nextInput();
                        if(cmd==DRAIN)codec.queueInputBuffer(index,0,0,0,MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                        else {
                            if(encoder) {
                                if((flags&1)!=0) {Bundle b=new Bundle();b.putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME,0);codec.setParameters(b);}
                                try(Image image=codec.getInputImage(index)) { if(image==null)throw new IOException("No input image");fill(image); }
                            } else {
                                ByteBuffer b=codec.getInputBuffer(index);if(b.capacity()<length)throw new IOException("Compressed AU too large");
                                ByteBuffer src=shared.duplicate();src.position(0);src.limit(length);b.put(src);saveParameters(length);
                            }
                            frames.computeIfAbsent(pts,k -> new ArrayDeque<>()).add(id);
                            codec.queueInputBuffer(index,0,length,pts,0);inputCount++;
                        }
                        drain(cmd==DRAIN);out.writeInt(DONE);out.flush();
                    }
                } catch(EOFException ignored) {} catch(Exception e) {
                    try {byte[] message=e.toString().getBytes("UTF-8");out.writeInt(ERROR);out.writeInt(Math.min(message.length,2048));out.write(message,0,Math.min(message.length,2048));out.flush();}catch(Exception ignored){}
                    throw e;
                } finally {
                    if(codec!=null){try{codec.stop();}catch(Exception ignored){}codec.release();}
                    if(callbackThread!=null)callbackThread.quitSafely();
                    for(Image image;(image=images.poll())!=null;)image.close();
                    if(reader!=null)reader.close();
                    if(imageThread!=null)imageThread.quitSafely();
                    if(channel!=null)try{channel.close();}catch(IOException ignored){}
                    if(shared!=null)SharedMemory.unmap(shared);
                    Log.i("RungicCodec","CLOSE "+name+" input="+inputCount+" output="+outputCount);
                }
            }
        }
        void configure() throws Exception {
            int magic=config();if(magic!=MAGIC && magic!=MAGIC2)throw new IOException("Channel version");
            version2=magic==MAGIC2;
            int op=config();if(op<0 || op>1)throw new IOException("Mode");encoder=op==1;
            kind=config();width=config();height=config();int fpsn=config(),fpsd=config();
            int bitrate=config(),interval=config(),standard=config(),range=config(),transfer=config();
            int options=version2?config():0;
            tenBit=!encoder && (options&OPTION_TEN_BIT)!=0 && kind!=0 && Build.VERSION.SDK_INT>=31;
            boolean buffers=!encoder && (options&OPTION_BUFFERS)!=0 && MediaBuffers.AVAILABLE;
            if(tenBit && !buffers)throw new IOException("10-bit output needs DMA-BUF frames");
            if(kind<0 || kind>2 || (encoder && kind==2) || width<16 || height<16 || width>2560 || height>2560 || (encoder && ((width|height)&1)!=0))throw new IOException("Unsupported dimensions/codec");
            String[] kinds={"avc","hevc","vp9"},mimes={"video/avc","video/hevc","video/x-vnd.on2.vp9"};
            name="c2.qti."+kinds[kind]+(encoder?".encoder":".decoder");
            MediaCodecInfo info=null;
            for(MediaCodecInfo c:new MediaCodecList(MediaCodecList.REGULAR_CODECS).getCodecInfos())if(c.getName().equals(name))info=c;
            if(info==null || !info.isHardwareAccelerated() || info.isSoftwareOnly())throw new IOException("Hardware codec unavailable");
            MediaCodecInfo.VideoCapabilities video=info.getCapabilitiesForType(mimes[kind]).getVideoCapabilities();
            if(!video.isSizeSupported(width,height))throw new IOException("Size unsupported by hardware");
            MediaFormat format=MediaFormat.createVideoFormat(mimes[kind],width,height);
            if(!buffers)format.setInteger(MediaFormat.KEY_COLOR_FORMAT,MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible);
            // Rendered into a Surface, Qualcomm's decoders write UBWC (compressed) unless told otherwise.
            else format.setInteger("vendor.qti-ext-dec-forceNonUBWC.value",1);
            if(standard>0)format.setInteger(MediaFormat.KEY_COLOR_STANDARD,standard);
            if(range>0)format.setInteger(MediaFormat.KEY_COLOR_RANGE,range);
            if(transfer>0)format.setInteger(MediaFormat.KEY_COLOR_TRANSFER,transfer);
            if(encoder) {
                double fps=fpsn>0 && fpsd>0?(double)fpsn/fpsd:30;
                if(fps<1 || fps>60 || !video.areSizeAndRateSupported(width,height,fps))throw new IOException("Frame rate unsupported");
                format.setFloat(MediaFormat.KEY_FRAME_RATE,(float)fps);
                format.setInteger(MediaFormat.KEY_BIT_RATE,Math.max(64000,Math.min(40000000,bitrate)));
                format.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL,Math.max(1,Math.min(10,interval)));
                format.setInteger(MediaFormat.KEY_MAX_B_FRAMES,0);
                format.setInteger(MediaFormat.KEY_PROFILE,kind==0?MediaCodecInfo.CodecProfileLevel.AVCProfileBaseline:MediaCodecInfo.CodecProfileLevel.HEVCProfileMain);
            } else {
                // Codec2 maps and unmaps every input block: a 16 MiB block per frame cost more CPU than
                // the decoding itself. An access unit rarely exceeds the raw picture.
                int maxInput=Math.max(1<<20,Math.min(HALF,width*height*3/2));
                format.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE,maxInput);
            }
            Surface surface=null;
            if(buffers) {
                // No CPU usage: gralloc would map every buffer each time Codec2 hands it over. Linux maps
                // each one once; forceNonUBWC (below) keeps them linear.
                imageThread=new HandlerThread("rungic-codec-images");imageThread.start();
                reader=ImageReader.newInstance(width,height,tenBit?ImageFormat.YCBCR_P010:ImageFormat.YUV_420_888,4,HardwareBuffer.USAGE_GPU_SAMPLED_IMAGE);
                reader.setOnImageAvailableListener(r -> {
                    try {Image image=r.acquireNextImage();if(image!=null)images.add(image);}
                    // A listener exception would take the whole app (and the desktop) down.
                    catch(RuntimeException e) {Log.w("RungicCodec","Rendered frame dropped: "+e);}
                },new Handler(imageThread.getLooper()));
                surface=reader.getSurface();channel=ParcelFileDescriptor.dup(fd);
            }
            codec=MediaCodec.createByCodecName(name);
            callbackThread=new HandlerThread("rungic-codec-events");callbackThread.start();
            codec.setCallback(new MediaCodec.Callback() {
                @Override public void onInputBufferAvailable(MediaCodec c,int index){events.add(index);}
                @Override public void onOutputBufferAvailable(MediaCodec c,int index,MediaCodec.BufferInfo info){events.add(new Output(index,info));}
                @Override public void onError(MediaCodec c,MediaCodec.CodecException e){events.add(e);}
                @Override public void onOutputFormatChanged(MediaCodec c,MediaFormat f){Log.i("RungicCodec","FORMAT "+name+" "+f);}
            },new Handler(callbackThread.getLooper()));
            codec.configure(format,surface,null,encoder?MediaCodec.CONFIGURE_FLAG_ENCODE:0);codec.start();
            shared=memory.mapReadWrite();byte[] bytes=name.getBytes("UTF-8");out.writeInt(DONE);out.writeInt(bytes.length);out.write(bytes);out.flush();
            Log.i("RungicCodec","OPEN "+name+" "+width+"x"+height+(buffers?" buffers":"")+(tenBit?" 10-bit":"")+" uid="+android.os.Process.myUid());
        }
        void fill(Image image) throws Exception {
            Image.Plane[] planes=image.getPlanes();int offset=0;
            for(int p=0;p<3;p++) {
                int w=p==0?width:width/2,h=p==0?height:height/2;
                Image.Plane plane=planes[p];int step=plane.getPixelStride(),stride=plane.getRowStride();ByteBuffer dst=plane.getBuffer();int start=dst.position();
                if(step!=1 && step!=2)throw new IOException("Input pixel stride");
                for(int y=0;y<h;y++) {
                    ByteBuffer src=shared.duplicate();src.position(offset+y*w);src.limit(offset+(y+1)*w);dst.position(start+y*stride);
                    if(step==1)dst.put(src);
                    else for(int x=0;x<w;x++)dst.put(start+y*stride+x*step,src.get());
                }
                offset+=w*h;
            }
        }
        void saveParameters(int length) {
            if(kind==2)return;
            int start=-1,prefix=0;
            for(int i=0;i+3<=length;i++) {
                int n=0;if(i+4<=length && shared.get(i)==0 && shared.get(i+1)==0 && shared.get(i+2)==0 && shared.get(i+3)==1)n=4;
                else if(shared.get(i)==0 && shared.get(i+1)==0 && shared.get(i+2)==1)n=3;
                if(n==0)continue;
                if(start>=0)saveParameter(start,prefix,i);
                // Parameter sets precede the pictures: stop at the first slice (an AU is mostly slices).
                if(i+n<length) {int t=kind==0?shared.get(i+n)&31:(shared.get(i+n)>>1)&63;if(kind==0?(t>=1 && t<=5):t<32)return;}
                start=i;prefix=n;i+=n-1;
            }
            if(start>=0)saveParameter(start,prefix,length);
        }
        void saveParameter(int start,int prefix,int end) {
            if(start+prefix>=end || end-start>65536)return;
            int type=kind==0?shared.get(start+prefix)&31:(shared.get(start+prefix)>>1)&63;
            if(kind==0?(type!=7 && type!=8):(type<32 || type>34))return;
            byte[] value=new byte[end-start];ByteBuffer b=shared.duplicate();b.position(start);b.get(value);parameters.put(type,value);
        }
        /** The rendered Image of the output with this presentation time (rendered in output order). */
        Image renderedImage(long pts) throws Exception {
            long deadline=System.nanoTime()+2000000000L;
            while(System.nanoTime()<deadline) {
                Image image=images.poll(100,TimeUnit.MILLISECONDS);
                if(image==null)continue;
                if(image.getTimestamp()/1000==pts)return image;
                Log.w("RungicCodec","Skipping rendered frame "+image.getTimestamp()/1000+", waiting for "+pts);image.close();
            }
            throw new IOException("Rendered frame timeout");
        }
        /** A version 2 output record for a rendered frame: its buffer's slot, with the DMA-BUF the first time. */
        void writeBuffer(Image image,int id,MediaCodec.BufferInfo info) throws Exception {
            int attach=-1;
            try(HardwareBuffer buffer=image.getHardwareBuffer()) {
                if(buffer==null)throw new IOException("Rendered frame without a HardwareBuffer");
                long key=MediaBuffers.id(buffer);long[] slot=slots.get(key);
                if(slot==null) {
                    if(slots.size()>=48)throw new IOException("Too many decoder buffers");
                    long[] d=MediaBuffers.describe(buffer,tenBit?2:1);attach=(int)d[0];
                    slot=Arrays.copyOf(d,14);slot[0]=nextSlot++;slot[13]=tenBit?10:8;slots.put(key,slot);
                }
                Rect crop=image.getCropRect();
                int luma=(int)slot[6],chroma=(int)slot[8];long rows=slot[3];
                ByteBuffer b=ByteBuffer.allocate(24*4);
                b.putInt(DECODED).putInt(id).putInt(info.flags).putInt((int)slot[4]).putLong(info.presentationTimeUs);
                b.putInt(crop.width()).putInt(crop.height()).putInt(crop.left).putInt(crop.top);
                // Lengths reach the last byte each plane's rows can address in the buffer.
                b.putInt(luma).putInt(tenBit?2:1).putInt((int)Math.min(slot[4]-slot[5],(long)luma*rows));
                b.putInt(chroma).putInt((int)slot[9]).putInt((int)(slot[4]-slot[7]));
                b.putInt((int)slot[11]).putInt((int)slot[12]).putInt((int)(slot[4]-slot[10]));
                b.putInt((int)slot[5]).putInt((int)slot[7]).putInt((int)slot[10]).putInt((int)slot[0]).putInt((int)slot[13]);
                out.flush();
                MediaBuffers.send(channel.getFd(),b.array(),b.position(),attach);
            } finally {if(attach>=0)ParcelFileDescriptor.adoptFd(attach).close();}
        }
        /** A free input index: outputs that come meanwhile go to Linux (the decoder may need them back). */
        int nextInput() throws Exception {
            long deadline=System.nanoTime()+5000000000L;
            while(freeInputs.isEmpty()) {
                long left=deadline-System.nanoTime();
                Object event=left>0?events.poll(left,TimeUnit.NANOSECONDS):null;
                if(event==null)throw new IOException("Input buffer timeout");
                handle(event);
            }
            return freeInputs.poll();
        }
        /** The outputs ready now, or (eos) every output up to the end of the stream. */
        void drain(boolean eos) throws Exception {
            if(!eos) {for(Object event;(event=events.poll())!=null;)handle(event);return;}
            while(!ended) {
                Object event=events.poll(10,TimeUnit.SECONDS);
                if(event==null)throw new IOException("Output EOS timeout");
                handle(event);
            }
        }
        void handle(Object event) throws Exception {
            if(event instanceof Integer){freeInputs.add((Integer)event);return;}
            if(event instanceof Exception)throw new IOException("Codec error: "+event);
            output(((Output)event).index,((Output)event).info);
        }
        void output(int index,MediaCodec.BufferInfo info) throws Exception {
            boolean outputEos=(info.flags&MediaCodec.BUFFER_FLAG_END_OF_STREAM)!=0;
            int type=0,id=-1,size=0;int[] meta=new int[13];int[] offsets=new int[3];
            Image image=null;
            try {
                try {
                    // A rendered (Surface) output may report no size: its PTS identifies a picture.
                    boolean config=(info.flags&MediaCodec.BUFFER_FLAG_CODEC_CONFIG)!=0;
                    if(info.size>0 || (reader!=null && !config && frames.containsKey(info.presentationTimeUs))) {
                        if(config)type=CONFIG;
                        else {ArrayDeque<Integer> queue=frames.get(info.presentationTimeUs);
                            if(queue==null || queue.isEmpty())throw new IOException("Unmatched output PTS "+info.presentationTimeUs);
                            id=queue.removeFirst();if(queue.isEmpty())frames.remove(info.presentationTimeUs);outputCount++;
                            type=encoder?ENCODED:DECODED;}
                        if(type==DECODED && reader!=null) {
                            codec.releaseOutputBuffer(index,true);index=-1;
                            image=renderedImage(info.presentationTimeUs);
                        } else if(type==DECODED) {
                            ByteBuffer dst=shared.duplicate();dst.position(HALF);dst.limit(HALF*2);
                            try(Image decoded=codec.getOutputImage(index)) {
                                if(decoded==null)throw new IOException("No decoded image");Rect crop=decoded.getCropRect();
                                meta[0]=crop.width();meta[1]=crop.height();meta[2]=crop.left;meta[3]=crop.top;
                                Image.Plane[] planes=decoded.getPlanes();
                                for(int p=0;p<3;p++) {
                                    ByteBuffer src=planes[p].getBuffer().duplicate();int n=src.remaining();
                                    if(n>dst.remaining())throw new IOException("Output image too large");
                                    meta[4+p*3]=planes[p].getRowStride();meta[5+p*3]=planes[p].getPixelStride();meta[6+p*3]=n;
                                    offsets[p]=size;dst.put(src);size+=n;
                                }
                            }
                        } else {ByteBuffer dst=shared.duplicate();dst.position(HALF);dst.limit(HALF*2);
                            ByteBuffer src=codec.getOutputBuffer(index).duplicate();src.position(info.offset);src.limit(info.offset+info.size);size=info.size;if(size>HALF)throw new IOException("Output AU too large");dst.put(src);}
                    }
                } finally {if(index>=0)codec.releaseOutputBuffer(index,false);}
                // Hardware buffer is released before waiting for a paused Linux sink
                // (a rendered Image is held until Linux has copied it: its ACK).
                if(type!=0) {
                    if(image!=null)writeBuffer(image,id,info);
                    else {
                        out.writeInt(type);out.writeInt(id);out.writeInt(info.flags);out.writeInt(size);out.writeLong(info.presentationTimeUs);
                        for(int v:meta)out.writeInt(v);
                        if(version2) {for(int v:offsets)out.writeInt(v);out.writeInt(-1);out.writeInt(8);}
                        out.flush();
                    }
                    if(in.readInt()!=ACK)throw new IOException("Frame acknowledgment");
                }
            } finally {if(image!=null)image.close();}
            if(outputEos) {ended=true;out.writeInt(EOS);}
        }
    }
    @Override public synchronized void close() throws IOException {
        running=false;
        for(LocalSocket socket:brokers)try{socket.close();}catch(Exception ignored){}
        for(FileDescriptor fd:channels)try{Os.shutdown(fd,OsConstants.SHUT_RDWR);}catch(Exception ignored){}
        if(listener!=null)listener.close();if(bound!=null)bound.close();path.delete();workers.shutdownNow();
    }
}
