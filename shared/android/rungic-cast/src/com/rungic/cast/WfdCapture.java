// SPDX-License-Identifier: MIT
package com.rungic.cast;
import android.util.AtomicFile;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.concurrent.TimeUnit;
import org.json.*;

/** Optional, bounded passive capability observation of the source's RTSP handshake.
 * No RTP/audio/video capture. Lack of tcpdump/capture data means unknown capabilities.
 */
final class WfdCapture implements AutoCloseable {
    private static final File DIR=new File("/data/adb/rungic-wfd/run");
    private final File capture=new File(DIR,"negotiation-"+android.os.Process.myPid()+".pcap");
    private Process process;
    WfdCapture() {
        try {
            DIR.mkdirs();
            process=new ProcessBuilder(RootProvider.busybox(),"timeout","-s","INT","150",
                    "/system/bin/tcpdump","-i","any","-s","4096","-U","-w",capture.toString(),"tcp port 7236")
                    .redirectErrorStream(true).redirectOutput(new File(DIR,"capture.log")).start();
        }catch(Exception ignored){}
    }
    void record(String address) {
        try {
            if(!capture.exists()||capture.length()>1024*1024)return;
            WfdPackets.Video video=WfdPackets.read(Files.readAllBytes(capture.toPath()));
            if(video==null||WfdFormats.parse(video.offered,video.r2).isEmpty())return;
            JSONObject data=new JSONObject().put("source","receiver-rtsp-response").put("address",address)
                    .put("r2",video.r2).put("offered",video.offered).put("selected",video.selected)
                    .put("boot_id",bootId()).put("observed_at",System.currentTimeMillis()).put("display_id",Main.connectedDisplayId());
            File dir=new File(DIR,"receivers");dir.mkdirs();
            AtomicFile file=new AtomicFile(new File(dir,address.replace(":", "")+".json"));
            FileOutputStream out=file.startWrite();
            try {out.write(data.toString().getBytes(StandardCharsets.UTF_8));file.finishWrite(out);}
            catch(Exception e){file.failWrite(out);throw e;}
        } catch(Exception e){System.err.println("WFD capability observation: "+e.getMessage());}
    }
    static JSONObject read(String address)throws Exception {
        if(address==null||!address.matches("(?i)[0-9a-f:]{17}"))return null;
        File file=new File(DIR,"receivers/"+address.replace(":","")+".json");
        if(!file.exists())return null;
        JSONObject value=new JSONObject(new String(new AtomicFile(file).readFully(),StandardCharsets.UTF_8));
        // Volatile run dir is not necessarily wiped at boot; display+time prevent stale claims.
        long age=System.currentTimeMillis()-value.getLong("observed_at");
        if(!bootId().equals(value.optString("boot_id"))||age<0||age>86400000||value.optInt("display_id",-2)!=Main.connectedDisplayId())return null;
        return value;
    }
    private static String bootId()throws IOException {
        return new String(Files.readAllBytes(new File("/proc/sys/kernel/random/boot_id").toPath()),StandardCharsets.US_ASCII).trim();
    }
    private static void stop(File capture) {
        try {
            for(File entry:new File("/proc").listFiles()) {
                if(!entry.getName().matches("[0-9]+"))continue;
                try {
                    String command=new String(Files.readAllBytes(new File(entry,"cmdline").toPath()),StandardCharsets.UTF_8);
                    if(command.startsWith("/system/bin/tcpdump\0") && command.contains("\0"+capture+"\0"))
                        android.os.Process.sendSignal(Integer.parseInt(entry.getName()),2);
                }catch(IOException vanished){}
            }
        }catch(Exception ignored){}
        capture.delete();
    }
    /** SIGTERM can skip Java finally. The resident observer reaps only dead owners' captures. */
    static void reap() {
        File[] files=DIR.listFiles();
        if(files==null)return;
        for(File file:files) {
            if(!file.getName().matches("negotiation-[0-9]+\\.pcap"))continue;
            String pid=file.getName().substring(12,file.getName().length()-5);
            try {
                String command=new String(Files.readAllBytes(new File("/proc/"+pid+"/cmdline").toPath()),StandardCharsets.UTF_8);
                if(command.contains("com.rungic.cast.Main"))continue;
            }catch(IOException exited){}
            stop(file);
        }
    }
    public void close() {
        stop(capture);
        if(process!=null)try { if(!process.waitFor(2,TimeUnit.SECONDS))process.destroy(); }catch(Exception ignored){}
    }
}
