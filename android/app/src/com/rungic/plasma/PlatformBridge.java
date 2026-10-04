package com.rungic.plasma;

import com.winland.server.NativeBridge;

import android.app.Activity;
import android.app.KeyguardManager;
import android.content.*;
import android.net.*;
import android.net.wifi.*;
import android.os.*;
import android.provider.Settings;
import android.view.WindowManager;
import android.util.Log;
import org.json.*;
import java.io.*;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.util.TimeZone;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;

/** Small private host interface. Android retains ownership of hardware services. */
final class PlatformBridge implements Closeable {
    private final Activity activity;
    private final File path;
    private LocalSocket bound;
    private LocalServerSocket server;
    private volatile boolean running;
    private final Handler awakeHandler = new Handler(Looper.getMainLooper());
    private final Runnable expireAwake = this::clearAwake;
    private void clearAwake() { ((MainActivity)activity).setKeepAwake(MainActivity.AWAKE_LINUX,false); }
    private final java.util.concurrent.ExecutorService boostWorker=java.util.concurrent.Executors.newSingleThreadExecutor();
    /** The desktop in front of the other apps or not (docs/49): its session runs on the big cores in
     * front (rungic-plasma boost), through the app's root session, in order. */
    void desktopBoost(boolean front) {
        boostWorker.execute(() -> {
            try { network.rootShell("/data/adb/rungic-plasma/rungic-plasma boost "+(front?"on":"off"),10000); }
            catch(Exception e) { Log.w("RungicPlatform","desktop boost: "+e.getMessage()); }
        });
    }
    private final AndroidClipboardBridge clipboard=new AndroidClipboardBridge();
    private final AndroidNetworkBridge network;
    private final CaptureBridge capture;
    private final OcrBridge ocr;
    private final AndroidBluetoothBridge bluetooth;
    private final AndroidTelephonyBridge telephony;
    private final AndroidSmsBridge sms;
    PlatformBridge(Activity activity,CaptureBridge capture) { this.activity=activity;this.capture=capture;path=new File(activity.getFilesDir(),"tmp/platform.sock");network=new AndroidNetworkBridge(activity);bluetooth=new AndroidBluetoothBridge(activity,network);telephony=new AndroidTelephonyBridge(activity,network);sms=new AndroidSmsBridge(activity,network);ocr=new OcrBridge(activity); }
    void start() throws IOException {
        if(running)return;
        path.delete();
        bound=new LocalSocket();
        bound.bind(new LocalSocketAddress(path.getAbsolutePath(),LocalSocketAddress.Namespace.FILESYSTEM));
        server=new LocalServerSocket(bound.getFileDescriptor());
        try { android.system.Os.chmod(path.getAbsolutePath(),0666); } catch(Exception e) { throw new IOException(e); }
        running=true;
        network.start();
        clipboard.start();
        Thread thread=new Thread(() -> {
            while(running) {
                LocalSocket client=null;
                try {
                    client=server.accept();
                    int uid=client.getPeerCredentials().getUid();
                    if(uid!=1000 && uid!=0)continue;
                    client.setSoTimeout(3000);
                    ByteArrayOutputStream bytes=new ByteArrayOutputStream();
                    int b;
                    while((b=client.getInputStream().read())!=-1 && b!='\n') { if(bytes.size()>=524288)throw new IOException("Request too large");bytes.write(b); }
                    JSONObject request=new JSONObject(bytes.toString("UTF-8"));
                    if(request.optString("op").startsWith("clipboard-")) {
                        JSONObject result=AndroidClipboardBridge.request(request,3000);
                        client.getOutputStream().write((result.toString()+"\n").getBytes(StandardCharsets.UTF_8));
                        continue;
                    }
                    if(request.optString("op").equals("lock")) {
                        JSONObject result;
                        if(!activity.hasWindowFocus()) result=new JSONObject().put("error","请先返回 Plasma Mobile");
                        else {
                            java.lang.Process process=new ProcessBuilder("su","-c","/data/adb/rungic-plasma/rungic-plasma lock").redirectErrorStream(true).start();
                            if(!process.waitFor(8,TimeUnit.SECONDS)) { process.destroy();result=new JSONObject().put("error","锁屏请求超时"); }
                            else result=new JSONObject().put(process.exitValue()==0?"ok":"error",process.exitValue()==0?true:"Android 锁屏失败");
                        }
                        client.getOutputStream().write((result.toString()+"\n").getBytes(StandardCharsets.UTF_8));
                        continue;
                    }
                    if(request.optString("op").equals("cast")) {
                        // Connecting to a TV takes seconds to a minute: answer on its own thread.
                        LocalSocket owned=client;client=null;
                        Thread cast=new Thread(() -> answerCast(owned,request),"rungic-cast");cast.setDaemon(true);cast.start();
                        continue;
                    }
                    if(request.optString("op").equals("watch")) {
                        // Waits for a change of the Android state the caller follows (HostEvents): on its
                        // own thread, for up to a minute.
                        LocalSocket owned=client;client=null;
                        Thread watch=new Thread(() -> answerWatch(owned,request),"rungic-watch");watch.setDaemon(true);watch.start();
                        continue;
                    }
                    if(request.optString("op").equals("ocr")) {
                        // The pixels follow the request line; recognition runs on the OCR thread (docs/64).
                        LocalSocket owned=client;client=null;
                        ocr.answer(owned,request);
                        continue;
                    }
                    if(request.optString("op").equals("bluetooth")) {
                        JSONObject result;
                        try { result=bluetooth.handle(request); }
                        catch(Exception e) { result=new JSONObject().put("error",e.getMessage()==null?"Bluetooth request failed":e.getMessage()); }
                        client.getOutputStream().write((result.toString()+"\n").getBytes(StandardCharsets.UTF_8));
                        continue;
                    }
                    if(request.optString("op").equals("telephony")) {
                        JSONObject result;
                        try { result=telephony.handle(request); }
                        catch(Exception e) { result=new JSONObject().put("error",e.getMessage()==null?"Telephony request failed":e.getMessage()); }
                        client.getOutputStream().write((result.toString()+"\n").getBytes(StandardCharsets.UTF_8));
                        continue;
                    }
                    if(request.optString("op").equals("sms")) {
                        // A send waits up to a minute for the radio: answer on its own thread.
                        LocalSocket owned=client;client=null;
                        Thread text=new Thread(() -> answerSms(owned,request),"rungic-sms");text.setDaemon(true);text.start();
                        continue;
                    }
                    if(request.optString("op").equals("container-memory")) {
                        JSONObject result;
                        try { result=network.containerMemory(request); }
                        catch(Exception e) { result=new JSONObject().put("error",e.getMessage()==null?"memory limit request failed":e.getMessage()); }
                        client.getOutputStream().write((result.toString()+"\n").getBytes(StandardCharsets.UTF_8));
                        continue;
                    }
                    if(request.optString("op").equals("screen-timeout")) {
                        JSONObject result;
                        try { result=network.screenTimeout(request); }
                        catch(Exception e) { result=new JSONObject().put("error",e.getMessage()==null?"screen timeout request failed":e.getMessage()); }
                        client.getOutputStream().write((result.toString()+"\n").getBytes(StandardCharsets.UTF_8));
                        continue;
                    }
                    if(request.optString("op").equals("wifi")) {
                        // Root commands with timeouts: on this socket thread, not the UI thread.
                        JSONObject result;
                        try { result=network.wifi(request); }
                        catch(Exception e) { result=new JSONObject().put("error",e.getMessage()==null?"Wi-Fi request failed":e.getMessage()); }
                        client.getOutputStream().write((result.toString()+"\n").getBytes(StandardCharsets.UTF_8));
                        continue;
                    }
                    if(request.optString("op").equals("network-wifi")) {
                        JSONObject result;
                        try { result=network.setEnabled(request.getBoolean("enabled")); }
                        catch(Exception e) { result=new JSONObject().put("error",e.getMessage()==null?"Wi-Fi request failed":e.getMessage()); }
                        client.getOutputStream().write((result.toString()+"\n").getBytes(StandardCharsets.UTF_8));
                        continue;
                    }
                    FutureTask<JSONObject> task=new FutureTask<>(() -> handle(request));
                    activity.runOnUiThread(task);
                    JSONObject result;
                    try { result=task.get(3,TimeUnit.SECONDS); }
                    catch(Exception e) { task.cancel(false);result=new JSONObject().put("error",e.getMessage()); }
                    client.getOutputStream().write((result.toString()+"\n").getBytes(StandardCharsets.UTF_8));
                } catch(Exception e) { if(running)Log.w("RungicPlatform","Request failed: "+e.getClass().getSimpleName()); }
                finally { if(client!=null)try { client.close(); } catch(IOException ignored) {} }
            }
        },"rungic-platform");thread.setDaemon(true);thread.start();
    }
    /**
     * TV casting through the root tool rungic-cast (docs/58): {"op":"cast","args":["status"|"scan"[,"<seconds>"]|
     * "connect"[,"<name|address>"]|"disconnect"]}. The tool prints one JSON object.
     */
    private static void answerCast(LocalSocket client,JSONObject request) {
        try(LocalSocket c=client) {
            JSONObject result;
            try {
                JSONArray args=request.optJSONArray("args");
                String command=args==null||args.length()==0?"status":args.getString(0);
                if(args!=null && args.length()>2)throw new IllegalArgumentException("Too many cast arguments");
                if(!command.matches("status|capabilities|settings|scan|connect|disconnect|modes|resolution"))throw new IllegalArgumentException("Unsupported cast command");
                StringBuilder line=new StringBuilder("if [ ! -x /data/adb/rungic-wfd/rungic-cast ] || [ ! -s /data/adb/rungic-wfd/rungic-cast.jar ]; then echo '{\"error\":\"Casting component is not installed\",\"code\":\"component-missing\"}'; exit 2; fi; /data/adb/rungic-wfd/rungic-cast ").append(command);
                if(args!=null && args.length()>1) {
                    if(command.equals("scan")) { if(!args.getString(1).matches("[0-9]{1,2}"))throw new IllegalArgumentException("scan takes seconds"); }
                    else if(command.equals("resolution")) { if(!args.getString(1).matches("(?i)[0-9a-f:]{17}/(auto|[0-9]+x[0-9]+@[0-9]+)"))throw new IllegalArgumentException("Invalid resolution selection"); }
                    else if(!command.equals("connect"))throw new IllegalArgumentException("Only connect takes a TV");
                    line.append(" '").append(args.getString(1).replace("'","'\\''")).append("'");
                }
                java.lang.Process process=new ProcessBuilder("su","-c",line.toString()).redirectErrorStream(true).start();
                if(!process.waitFor(command.equals("resolution") ? 330 : command.equals("connect") ? 180 : 60,TimeUnit.SECONDS)) { process.destroy();throw new IOException("投屏命令超时"); }
                String out=new String(process.getInputStream().readAllBytes(),StandardCharsets.UTF_8).trim();
                String last=out.substring(out.lastIndexOf('\n')+1);
                result=last.startsWith("{")?new JSONObject(last):new JSONObject().put("error",out.isEmpty()?"rungic-cast failed":out);
            } catch(Exception e) { result=new JSONObject().put("error",e.getMessage()==null?"投屏请求失败":e.getMessage()); }
            c.getOutputStream().write((result.toString()+"\n").getBytes(StandardCharsets.UTF_8));
        } catch(Exception e) { Log.w("RungicPlatform","Cast request failed: "+e.getClass().getSimpleName()); }
    }
    private void answerSms(LocalSocket c,JSONObject request) {
        try(LocalSocket client=c) {
            JSONObject result;
            try { result=sms.handle(request); }
            catch(Exception e) { result=new JSONObject().put("error",e.getMessage()==null?"Text message request failed":e.getMessage()); }
            client.getOutputStream().write((result.toString()+"\n").getBytes(StandardCharsets.UTF_8));
        } catch(Exception e) { Log.w("RungicPlatform","SMS request failed: "+e.getClass().getSimpleName()); }
    }
    private void answerWatch(LocalSocket c,JSONObject request) {
        try(LocalSocket client=c) {
            long timeout=Math.max(1,Math.min(60000,request.optLong("timeout",30000)));
            JSONObject result;
            try { result=HostEvents.await(request,timeout); }
            catch(Exception e) { result=new JSONObject().put("error",e.getMessage()==null?"watch failed":e.getMessage()); }
            client.getOutputStream().write((result.toString()+"\n").getBytes(StandardCharsets.UTF_8));
        } catch(Exception e) { Log.w("RungicPlatform","Watch request failed: "+e.getClass().getSimpleName()); }
    }
    private JSONObject handle(JSONObject request) throws Exception {
        String op=request.optString("op");
        if(op.equals("status"))return status();
        if(op.equals("display-get"))return ((MainActivity)activity).displayInfo();
        if(op.equals("network-get"))return network.snapshot();
        if(op.equals("capture-info"))return capture.info();
        if(op.equals("brightness-get"))return brightness();
        if(op.equals("native-stats")) {
            // Read-only compositor counters for diagnostics (tools/rungic_agent.py).
            String error=NativeBridge.getLastNativeError();
            return new JSONObject().put("stats",NativeBridge.getWaylandRuntimeStats())
                .put("presentedFrames",NativeBridge.getPresentedFrames())
                .put("lastError",error==null?JSONObject.NULL:error);
        }
        if(op.equals("keep-awake")) {
            awakeHandler.removeCallbacks(expireAwake);
            boolean enabled=request.getBoolean("enabled");
            if(enabled) {
                ((MainActivity)activity).setKeepAwake(MainActivity.AWAKE_LINUX,true);
                // A failed Linux bridge must not leave a permanently bright screen.
                awakeHandler.postDelayed(expireAwake,12000);
            } else expireAwake.run();
            return new JSONObject().put("ok",true).put("foreground",activity.hasWindowFocus()).put("locked",activity.getSystemService(KeyguardManager.class).isKeyguardLocked());
        }
        // The assistant's screen is the Linux desktop's own output (docs/65): the assistant turns it
        // on for a task whether or not Plasma Mobile is in front.
        if(op.equals("agent-screen"))return ((MainActivity)activity).agentScreen(request);
        if(op.equals("desktop-mode"))return ((MainActivity)activity).desktopMode(request);
        if(op.equals("tv"))return ((MainActivity)activity).tv(request);
        if(op.equals("director"))return ((MainActivity)activity).directorRequest(request);
        if(!activity.hasWindowFocus())return new JSONObject().put("error","请先返回 Plasma Mobile");
        if(op.equals("display-set"))return ((MainActivity)activity).setDisplayInfo(request);
        if(op.equals("cast-desktop"))return ((MainActivity)activity).castDesktop(request);
        if(op.equals("cast-controls"))return ((MainActivity)activity).castControls(request);
        if(op.equals("text-commit")) {
            // Same path as the Android keyboard's commitText (for tests and tools).
            com.winland.server.NativeBridge.sendTextInput(request.getString("text"));
            return new JSONObject().put("ok",true);
        }
        if(op.equals("brightness")) {
            double value=request.getDouble("value");
            if(!Double.isFinite(value) || (value!=-1 && (value<0.02 || value>1)))throw new IllegalArgumentException("brightness");
            WindowManager.LayoutParams p=activity.getWindow().getAttributes();p.screenBrightness=(float)value;activity.getWindow().setAttributes(p);
            return new JSONObject().put("ok",true);
        }
        if(op.equals("settings")) {
            String target=request.getString("target"), action;
            switch(target) {
                case "network":action=Settings.Panel.ACTION_INTERNET_CONNECTIVITY;break;
                case "bluetooth":action=Settings.ACTION_BLUETOOTH_SETTINGS;break;
                case "display":action=Settings.ACTION_DISPLAY_SETTINGS;break;
                case "sound":action=Settings.Panel.ACTION_VOLUME;break;
                case "datetime":action=Settings.ACTION_DATE_SETTINGS;break;
                case "location":action=Settings.ACTION_LOCATION_SOURCE_SETTINGS;break;
                default:throw new IllegalArgumentException("Unknown setting");
            }
            activity.startActivity(new Intent(action));return new JSONObject().put("ok",true);
        }
        if(op.equals("orientation")) {
            // Without "mode": the current setting, so a temporary change can be undone (docs/65).
            if(!request.has("mode"))return new JSONObject().put("mode",activity.getPreferences(Activity.MODE_PRIVATE).getString("orientation","portrait"));
            String mode=request.getString("mode");
            applyOrientation(activity,mode);
            activity.getPreferences(Activity.MODE_PRIVATE).edit().putString("orientation",mode).apply();
            return new JSONObject().put("ok",true);
        }
        if(op.equals("vibrate")) {
            activity.getSystemService(Vibrator.class).vibrate(VibrationEffect.createOneShot(35,VibrationEffect.DEFAULT_AMPLITUDE));
            return new JSONObject().put("ok",true);
        }
        return new JSONObject().put("error","Unsupported operation");
    }
    static void applyOrientation(Activity activity,String mode) {
        int value;
        switch(mode) {
            case "system":value=android.content.pm.ActivityInfo.SCREEN_ORIENTATION_FULL_USER;break;
            case "portrait":value=android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT;break;
            case "landscape":value=android.content.pm.ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE;break;
            default:throw new IllegalArgumentException("orientation");
        }
        activity.setRequestedOrientation(value);
    }
    private JSONObject brightness() throws Exception {
        float window=activity.getWindow().getAttributes().screenBrightness;
        // The Android setting is the user's requested level, not measured nits
        // or the current automatic-brightness/thermal-limited panel output.
        float setting=Settings.System.getInt(activity.getContentResolver(),Settings.System.SCREEN_BRIGHTNESS,128)/255f;
        int level=Math.round(Math.max(0.02f,Math.min(1f,window<0?setting:window))*100);
        return new JSONObject().put("level",level).put("followAndroid",window<0);
    }
    private JSONObject status() throws Exception {
        JSONObject out=new JSONObject().put("version",1).put("model",Build.MODEL).put("manufacturer",Build.MANUFACTURER)
            .put("android",Build.VERSION.RELEASE).put("sdk",Build.VERSION.SDK_INT).put("timezone",TimeZone.getDefault().getID())
            .put("orientation",activity.getPreferences(Activity.MODE_PRIVATE).getString("orientation","portrait"))
            .put("foreground",activity.hasWindowFocus()).put("keepAwake",(activity.getWindow().getAttributes().flags & WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)!=0).put("windowBrightness",activity.getWindow().getAttributes().screenBrightness);
        ConnectivityManager cm=activity.getSystemService(ConnectivityManager.class);
        Network network=cm.getActiveNetwork(); NetworkCapabilities nc=network==null?null:cm.getNetworkCapabilities(network);
        JSONObject net=new JSONObject().put("connected",network!=null);
        if(nc!=null) {
            net.put("transport",nc.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)?"Wi-Fi":nc.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)?"移动网络":nc.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)?"以太网":"其他")
                .put("validated",nc.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)).put("metered",cm.isActiveNetworkMetered());
            LinkProperties lp=cm.getLinkProperties(network);
            if(lp!=null) { JSONArray ips=new JSONArray(),dns=new JSONArray();for(LinkAddress a:lp.getLinkAddresses())ips.put(a.toString());for(InetAddress a:lp.getDnsServers())dns.put(a.getHostAddress());net.put("addresses",ips).put("dns",dns).put("interface",lp.getInterfaceName()); }
            if(nc.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) {
                WifiInfo wifi=activity.getApplicationContext().getSystemService(WifiManager.class).getConnectionInfo();
                if(wifi!=null)net.put("ssid",wifi.getSSID()).put("rssi",wifi.getRssi()).put("frequency",wifi.getFrequency()).put("linkMbps",wifi.getLinkSpeed());
            }
        }
        out.put("network",net);
        Intent battery=activity.registerReceiver(null,new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        if(battery!=null)out.put("battery",new JSONObject().put("percent",100*battery.getIntExtra(BatteryManager.EXTRA_LEVEL,0)/Math.max(1,battery.getIntExtra(BatteryManager.EXTRA_SCALE,100)))
            .put("temperature",battery.getIntExtra(BatteryManager.EXTRA_TEMPERATURE,0)/10.0).put("status",battery.getIntExtra(BatteryManager.EXTRA_STATUS,1)).put("plugged",battery.getIntExtra(BatteryManager.EXTRA_PLUGGED,0)));
        return out;
    }
    @Override public void close() throws IOException {
        running=false;
        // A queued "boost off" still needs the root session.
        boostWorker.shutdown();
        try { boostWorker.awaitTermination(3,TimeUnit.SECONDS); } catch(InterruptedException ignored) {}
        clipboard.close();network.close();if(server!=null)server.close();if(bound!=null)bound.close();path.delete();
    }
}
