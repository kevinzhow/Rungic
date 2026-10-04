// SPDX-License-Identifier: MIT
package com.rungic.plasma;

import android.content.Context;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Looper;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;

/** Android hardware backend, supervised by the root controller, not an application component.
 * The desktop APK is only a compatibility client. No UI objects or automatic SMS replay. */
public final class DeviceDaemon {
    static final String SOCKET="com.rungic.device.v1";
    private final int appUid;
    private final AndroidNetworkBridge network;
    private final AndroidBluetoothBridge bluetooth;
    private final AndroidTelephonyBridge telephony;
    private final AndroidSmsBridge sms;
    private final ExecutorService clients=pool(8), watches=pool(4);
    private static ExecutorService pool(int size) {
        return new ThreadPoolExecutor(0,size,30,TimeUnit.SECONDS,new SynchronousQueue<Runnable>(),
                new ThreadPoolExecutor.AbortPolicy());
    }
    private DeviceDaemon(Context context,int appUid,String apkPath) {
        this.appUid=appUid;
        network=new AndroidNetworkBridge(context,apkPath);
        bluetooth=new AndroidBluetoothBridge(context,network);
        telephony=new AndroidTelephonyBridge(context,network);
        sms=new AndroidSmsBridge(context,network);
    }
    private JSONObject handle(JSONObject request) throws Exception {
        String op=request.optString("op");
        switch(op) {
            case "network-get": return network.snapshot();
            case "wifi": return network.wifi(request);
            case "network-wifi": return network.setEnabled(request.getBoolean("enabled"));
            case "bluetooth": return bluetooth.handle(request);
            case "telephony": return telephony.handle(request);
            case "sms": return sms.handle(request);
            case "container-memory": return network.containerMemory(request);
            case "screen-timeout": return network.screenTimeout(request);
            case "desktop-boost":
                network.rootShell("/data/adb/rungic-plasma/rungic-plasma boost "+(request.getBoolean("enabled")?"on":"off"),10000);
                return new JSONObject().put("ok",true);
            case "device-status":
                return new JSONObject().put("protocol",1).put("uid",android.os.Process.myUid())
                        .put("pid",android.os.Process.myPid()).put("epoch",HostEvents.EPOCH)
                        .put("independent",true);
            case "watch":
                JSONArray topics=request.getJSONArray("topics");
                if(topics.length()<1 || topics.length()>3)throw new IllegalArgumentException("Invalid device topics");
                for(int i=0;i<topics.length();i++)if(!DeviceOperations.topic(topics.getString(i)))
                    throw new IllegalArgumentException("UI topic belongs to the presentation bridge");
                return HostEvents.await(request,Math.max(1,Math.min(60000,request.optLong("timeout",30000))));
            default: throw new IllegalArgumentException("Unsupported device operation");
        }
    }
    private void write(LocalSocket socket,JSONObject reply) throws IOException {
        socket.getOutputStream().write((reply.toString()+"\n").getBytes(StandardCharsets.UTF_8));
    }
    private void answer(LocalSocket socket,JSONObject request) {
        try(LocalSocket client=socket) {
            JSONObject result;
            try { result=handle(request); }
            catch(Exception e) { result=new JSONObject().put("error",e.getMessage()==null?e.getClass().getSimpleName():e.getMessage()); }
            write(client,result);
        } catch(Exception ignored) {} // Never log message text, Wi-Fi credentials or provider contents.
    }
    private void accept(LocalSocket socket) {
        boolean handed=false;
        try {
            int uid=socket.getPeerCredentials().getUid();
            if(uid!=0 && uid!=1000 && uid!=appUid)return;
            socket.setSoTimeout(3000);
            ByteArrayOutputStream data=new ByteArrayOutputStream();int b;
            while((b=socket.getInputStream().read())!=-1 && b!='\n') {
                if(data.size()>=524288)throw new IOException("Request too large");data.write(b);
            }
            if(b!='\n')throw new IOException("Incomplete request");
            JSONObject request=new JSONObject(data.toString("UTF-8"));
            ExecutorService pool=request.optString("op").equals("watch")?watches:clients;
            try { pool.execute(()->answer(socket,request));handed=true; }
            catch(RejectedExecutionException e) { write(socket,new JSONObject().put("error","Device backend busy; request not submitted")); }
        } catch(Exception ignored) {}
        finally { if(!handed)try { socket.close(); } catch(IOException ignored) {} }
    }
    public static void main(String[] args) {
        try {
            if(android.os.Process.myUid()!=0 || args.length!=2)throw new IllegalArgumentException();
            int appUid=Integer.parseInt(args[0]);
            if(appUid<10000)throw new IllegalArgumentException();
            Looper.prepareMainLooper();
            // app_process does not run the telephony/Bluetooth Zygote initializers.
            initialize("android.telephony.TelephonyFrameworkInitializer","android.os.TelephonyServiceManager",
                    "getTelephonyServiceManager","setTelephonyServiceManager");
            initialize("android.bluetooth.BluetoothFrameworkInitializer","android.os.BluetoothServiceManager",
                    "getBluetoothServiceManager","setBluetoothServiceManager");
            Context context=DeviceContext.create();
            DeviceDaemon daemon=new DeviceDaemon(context,appUid,args[1]);
            LocalServerSocket server=new LocalServerSocket(SOCKET);
            // Publish ownership only after binding; a second instance cannot replace a live PID.
            try(FileWriter pid=new FileWriter("pid")) { pid.write(Integer.toString(android.os.Process.myPid())); }
            daemon.network.start();
            new Thread(()->{
                for(;;)try { daemon.accept(server.accept()); }
                catch(IOException e) { System.exit(1); }
            },"device-socket").start();
            Looper.loop();
        } catch(Throwable e) {
            System.err.println("device backend unavailable: "+e.getClass().getSimpleName());System.exit(1);
        }
    }
    private static void initialize(String initializer,String manager,String getter,String setter) throws Exception {
        Class<?> init=Class.forName(initializer),type=Class.forName(manager);
        // Bluetooth's getter is hidden on some framework versions.
        java.lang.reflect.Method get=init.getDeclaredMethod(getter);get.setAccessible(true);
        if(get.invoke(null)==null) {
            java.lang.reflect.Method set=init.getDeclaredMethod(setter,type);set.setAccessible(true);
            set.invoke(null,type.getConstructor().newInstance());
        }
    }
}
