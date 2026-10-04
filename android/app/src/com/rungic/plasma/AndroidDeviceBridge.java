// SPDX-License-Identifier: MIT
package com.rungic.plasma;

import android.net.LocalSocket;
import android.net.LocalSocketAddress;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;

/** Compatibility forwarding from the presentation process. Stopping it never stops the backend. */
final class AndroidDeviceBridge implements Closeable {
    private volatile boolean running;
    private volatile LocalSocket watching;
    private Thread watcher;
    static JSONObject request(JSONObject request) throws Exception {
        try(LocalSocket c=new LocalSocket()) {
            c.setSoTimeout(75000);
            c.connect(new LocalSocketAddress(DeviceDaemon.SOCKET,LocalSocketAddress.Namespace.ABSTRACT));
            if(c.getPeerCredentials().getUid()!=0)throw new SecurityException("Unexpected device backend identity");
            c.getOutputStream().write((request.toString()+"\n").getBytes(StandardCharsets.UTF_8));
            return read(c);
        }
    }
    private static JSONObject read(LocalSocket c) throws Exception {
        ByteArrayOutputStream data=new ByteArrayOutputStream();int b;
        while((b=c.getInputStream().read())!=-1 && b!='\n') {
            if(data.size()>=524288)throw new IOException("Device response too large");data.write(b);
        }
        if(b!='\n')throw new IOException("Device backend disconnected; outcome unknown, do not resend automatically");
        return new JSONObject(data.toString("UTF-8"));
    }
    void start() {
        running=true;
        watcher=new Thread(()->{
            String epoch="";JSONObject seen=new JSONObject();
            while(running) {
                try(LocalSocket c=new LocalSocket()) {
                    watching=c;c.setSoTimeout(65000);
                    c.connect(new LocalSocketAddress(DeviceDaemon.SOCKET,LocalSocketAddress.Namespace.ABSTRACT));
                    if(c.getPeerCredentials().getUid()!=0)throw new SecurityException();
                    JSONObject request=new JSONObject().put("op","watch")
                        .put("topics",new JSONArray(new String[]{"network","telephony","bluetooth"}))
                        .put("epoch",epoch).put("seen",seen).put("timeout",60000);
                    c.getOutputStream().write((request.toString()+"\n").getBytes(StandardCharsets.UTF_8));
                    JSONObject reply=read(c),now=reply.getJSONObject("versions");
                    String next=reply.getString("epoch");
                    for(String topic:new String[]{"network","telephony","bluetooth"})
                        if(!next.equals(epoch) || now.optLong(topic)!=seen.optLong(topic,-1))HostEvents.bump(topic);
                    epoch=next;seen=now;
                } catch(Exception ignored) {
                    if(running)try { Thread.sleep(3000); } catch(InterruptedException e) { break; }
                } finally { watching=null; }
            }
        },"device-events");watcher.setDaemon(true);watcher.start();
    }
    @Override public void close() {
        running=false;
        LocalSocket c=watching;if(c!=null)try { c.close(); } catch(IOException ignored) {}
        if(watcher!=null)watcher.interrupt();
    }
}
