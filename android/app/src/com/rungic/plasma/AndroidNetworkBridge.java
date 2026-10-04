package com.rungic.plasma;

import android.content.Context;
import android.net.*;
import android.net.wifi.*;
import android.os.SystemClock;
import org.json.*;
import java.io.*;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;
import java.util.UUID;
import java.util.regex.*;

/** Android owns all networking. No network configuration or credentials are stored here. */
final class AndroidNetworkBridge implements Closeable {
    private final Context context;
    final String apkPath;
    private final ConnectivityManager cm;
    private final WifiManager wm;
    private final ScheduledExecutorService worker=Executors.newSingleThreadScheduledExecutor();
    private volatile String identity="{}";
    private java.lang.Process rootProcess;
    private BufferedWriter rootInput;
    private BlockingQueue<String> rootOutput;
    AndroidNetworkBridge(Context context,String apkPath) {
        this.apkPath=apkPath;
        this.context=context;
        cm=context.getSystemService(ConnectivityManager.class);
        wm=context.getApplicationContext().getSystemService(WifiManager.class);
    }
    // Android's own callbacks tell the Linux side (HostEvents) and refresh the Wi-Fi identity (a root
    // `cmd wifi status`) when something changed; the slow schedule only catches what they miss.
    // Every 5 s it cost a root command and a wakeup on both sides at idle.
    private final ConnectivityManager.NetworkCallback changes=new ConnectivityManager.NetworkCallback() {
        @Override public void onAvailable(Network n) { changed(true); }
        @Override public void onLost(Network n) { synchronized(seen) { seen.remove(n); } changed(true); }
        // Capabilities carry the Wi-Fi signal and change every few seconds: only what the Linux side
        // shows counts (the kind, validation, captive portal, metering, the signal's bars).
        @Override public void onCapabilitiesChanged(Network n,NetworkCapabilities c) {
            String summary=summary(c);
            String before;
            synchronized(seen) { before=seen.put(n,summary); }
            if(!summary.equals(before))changed(false);
        }
        @Override public void onLinkPropertiesChanged(Network n,LinkProperties p) { changed(true); }
    };
    private final java.util.Map<Network,String> seen=new java.util.HashMap<>();
    private String summary(NetworkCapabilities c) {
        StringBuilder b=new StringBuilder();
        for(int t:new int[]{NetworkCapabilities.TRANSPORT_WIFI,NetworkCapabilities.TRANSPORT_CELLULAR,NetworkCapabilities.TRANSPORT_ETHERNET,
                NetworkCapabilities.TRANSPORT_VPN,NetworkCapabilities.TRANSPORT_BLUETOOTH,NetworkCapabilities.TRANSPORT_USB})
            b.append(c.hasTransport(t)?'1':'0');
        b.append(c.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)).append(c.hasCapability(NetworkCapabilities.NET_CAPABILITY_CAPTIVE_PORTAL))
            .append(c.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED));
        if(c.getTransportInfo() instanceof WifiInfo)b.append(wm.calculateSignalLevel(((WifiInfo)c.getTransportInfo()).getRssi()));
        return b.toString();
    }
    private final android.content.BroadcastReceiver wifiState=new android.content.BroadcastReceiver() {
        @Override public void onReceive(android.content.Context context,android.content.Intent intent) { changed(true); }
    };
    private ScheduledFuture<?> pendingRefresh;
    private boolean watching;
    void start() {
        worker.scheduleWithFixedDelay(this::refreshIdentity,0,60,TimeUnit.SECONDS);
        if(watching)return;
        watching=true;
        cm.registerNetworkCallback(new NetworkRequest.Builder().clearCapabilities().build(),changes);
        context.getApplicationContext().registerReceiver(wifiState,new android.content.IntentFilter(WifiManager.WIFI_STATE_CHANGED_ACTION),
            android.content.Context.RECEIVER_NOT_EXPORTED);
    }
    /** `identity`: the Wi-Fi network may be another one, so its SSID is read again. */
    private synchronized void changed(boolean identity) {
        HostEvents.bump(HostEvents.NETWORK);
        if(!identity)return;
        // One refresh for a burst of callbacks (a new network brings several at once).
        if(pendingRefresh!=null)pendingRefresh.cancel(false);
        pendingRefresh=worker.schedule(this::refreshIdentity,500,TimeUnit.MILLISECONDS);
    }
    private Network wifiNetwork() {
        for(Network n:cm.getAllNetworks()) {
            NetworkCapabilities c=cm.getNetworkCapabilities(n);
            if(c!=null && c.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) && !c.hasTransport(NetworkCapabilities.TRANSPORT_VPN))return n;
        }
        return null;
    }
    private static String field(String text,String expression) {
        Matcher match=Pattern.compile(expression).matcher(text);
        return match.find()?match.group(1):"";
    }
    private void refreshIdentity() {
        String before=identity;
        updateIdentity();
        // Its time field changes with every refresh; only the network's identity matters.
        if(!before.replaceAll("\"time\":\\d+","").equals(identity.replaceAll("\"time\":\\d+","")))
            HostEvents.bump(HostEvents.NETWORK);
    }
    private void updateIdentity() {
        try {
            Network before=wifiNetwork();
            if(before==null) { identity="{}";return; }
            String output=wifiCommand("status");
            Network after=wifiNetwork();
            if(!before.equals(after)) { identity="{}";return; }
            // Parse only the primary client on this Android 16 build. Fail closed
            // on ambiguous output (e.g. concurrent STA), never invent an SSID.
            int begin=output.indexOf("WifiInfo: ");
            if(begin<0 || output.indexOf("WifiInfo: ",begin+1)>=0) { identity="{}";return; }
            String line=output.substring(begin).split("\n",2)[0];
            String ssid=field(line,"^WifiInfo: SSID: (.*?), BSSID: [0-9a-fA-F:]{17}, MAC: ");
            if(ssid.startsWith("\"") && ssid.endsWith("\""))ssid=ssid.substring(1,ssid.length()-1);
            JSONObject value=new JSONObject().put("handle",before.getNetworkHandle()).put("time",SystemClock.elapsedRealtime());
            if(!ssid.isEmpty() && !ssid.equals("<unknown ssid>"))value.put("ssid",ssid);
            value.put("bssid",field(line,", BSSID: ([0-9a-fA-F:]{17}),"));
            value.put("mac",field(line,", MAC: ([0-9a-fA-F:]{17}),"));
            identity=value.toString();
        } catch(Exception e) { identity="{}"; }
    }
    private String wifiCommand(String operation) throws Exception { return wifiCommand(operation,2000); }
    // Only commands built in this class reach Magisk: constants, and the validated, single-quoted
    // arguments of wifi(). No other caller text enters a shell.
    private String wifiCommand(String operation,long timeoutMs) throws Exception {
        return rootShell("/system/bin/cmd wifi "+operation,timeoutMs);
    }
    // One bounded command session for the device backend, also used by Bluetooth.
    synchronized String rootShell(String command,long timeoutMs) throws Exception {
        // One private root session avoids a Magisk grant toast/process launch
        // on every status refresh.
        try {
            if(rootProcess==null || !rootProcess.isAlive()) {
                closeRoot();
                ProcessBuilder builder=android.os.Process.myUid()==0
                    ?new ProcessBuilder("/system/bin/sh")
                    :new ProcessBuilder("/product/bin/su","--mount-master","-c","/system/bin/sh");
                builder.environment().remove("LD_PRELOAD");builder.environment().remove("LD_LIBRARY_PATH");
                rootProcess=builder.redirectErrorStream(true).start();
                rootInput=new BufferedWriter(new OutputStreamWriter(rootProcess.getOutputStream(),StandardCharsets.UTF_8));
                rootOutput=new ArrayBlockingQueue<>(256);
                final java.lang.Process process=rootProcess;
                final BlockingQueue<String> queue=rootOutput;
                Thread reader=new Thread(() -> {
                    try(BufferedReader in=new BufferedReader(new InputStreamReader(process.getInputStream(),StandardCharsets.UTF_8))) {
                        String line;while((line=in.readLine())!=null)if(!queue.offer(line))break;
                    } catch(IOException ignored) {}
                },"wifi-command-output");reader.setDaemon(true);reader.start();
            }
            String marker="RUNGIC_"+UUID.randomUUID().toString().replace("-","");
            rootInput.write(command+"; printf '\\n"+marker+":%s\\n' \"$?\"\n");rootInput.flush();
            long deadline=SystemClock.elapsedRealtime()+timeoutMs;
            StringBuilder output=new StringBuilder();
            while(SystemClock.elapsedRealtime()<deadline) {
                String line=rootOutput.poll(Math.max(1,deadline-SystemClock.elapsedRealtime()),TimeUnit.MILLISECONDS);
                if(line==null)break;
                if(line.startsWith(marker+":")) {
                    if(!line.equals(marker+":0"))throw new IOException("Android Wi-Fi command failed");
                    return output.toString();
                }
                if(output.length()+line.length()>65536)throw new IOException("Android Wi-Fi response too large");
                output.append(line).append('\n');
            }
            throw new IOException("Android Wi-Fi service timed out");
        } catch(Exception e) { closeRoot();throw e; }
    }
    private synchronized void closeRoot() {
        if(rootInput!=null)try { rootInput.close(); } catch(IOException ignored) {}
        if(rootProcess!=null)rootProcess.destroy();
        rootProcess=null;rootInput=null;rootOutput=null;
    }
    JSONObject setEnabled(boolean enabled) throws Exception {
        // Invoked on the private socket thread, never Android's UI thread.
        wifiCommand(enabled?"set-wifi-enabled enabled":"set-wifi-enabled disabled");
        identity="{}";
        return new JSONObject().put("accepted",true);
    }
    private static String quote(String value) { return "'"+value.replace("'","'\\''")+"'"; }
    /** Wi-Fi for the Linux side's NetworkManager service (docs/73): scan, scan results, saved
     * networks, connect, forget, through Android's own `cmd wifi`. Requests are authorized by the private device socket, independent of any
     * foreground window. Android keeps the credentials; none are stored here. */
    JSONObject wifi(JSONObject request) throws Exception {
        String action=request.optString("action");
        switch(action) {
            case "scan": wifiCommand("start-scan",3000); return new JSONObject().put("accepted",true);
            case "scan-results": return new JSONObject().put("text",wifiCommand("list-scan-results",3000));
            case "saved": return new JSONObject().put("text",wifiCommand("list-networks",3000));
            case "connect": {
                String ssid=request.getString("ssid"),security=request.getString("security"),passphrase=request.optString("passphrase","");
                byte[] raw=ssid.getBytes(StandardCharsets.UTF_8);
                if(raw.length<1 || raw.length>32 || !ssid.matches("[^\\p{Cntrl}]+"))throw new IllegalArgumentException("Invalid SSID");
                boolean open=security.equals("open") || security.equals("owe");
                if(!open && !security.equals("wpa2") && !security.equals("wpa3"))throw new IllegalArgumentException("Unsupported security");
                if(open ? !passphrase.isEmpty() : !passphrase.matches("[\\x20-\\x7e]{8,63}"))throw new IllegalArgumentException("Invalid passphrase");
                        String reply=wifiCommand("connect-network "+quote(ssid)+" "+security+(open?"":" "+quote(passphrase)),20000);
                identity="{}";
                return new JSONObject().put("accepted",true).put("text",reply);
            }
            case "activate": {
                // A saved network by its Android id (cmd wifi has no way to join one without its
                // passphrase): com.rungic.wifi.RootWifi from this APK, run as root.
                int id=request.getInt("id");
                if(id<0)throw new IllegalArgumentException("Invalid network id");
                        String reply=rootWifi("connect "+id);
                identity="{}";
                return new JSONObject().put("accepted",true).put("text",reply);
            }
            case "disconnect": {
                        String reply=rootWifi("disconnect");
                identity="{}";
                return new JSONObject().put("accepted",true).put("text",reply);
            }
            case "forget": {
                int id=request.getInt("id");
                if(id<0)throw new IllegalArgumentException("Invalid network id");
                        wifiCommand("forget-network "+id,5000);
                identity="{}";
                return new JSONObject().put("accepted",true);
            }
            default: throw new IllegalArgumentException("Unknown Wi-Fi action");
        }
    }
    private String rootWifi(String arguments) throws Exception {
        String apk=apkPath;
        String reply=rootShell("CLASSPATH="+quote(apk)+" /system/bin/app_process /system/bin com.rungic.wifi.RootWifi "+arguments,20000);
        if(!reply.trim().endsWith("success"))throw new IOException("Android Wi-Fi: "+reply.trim());
        return reply;
    }
    /** Android's screen-off timeout for the Linux power settings (docs/73): read it, or set it (root:
     * `settings put`, as WRITE_SETTINGS is not this app's). ms 0 is "never". */
    JSONObject screenTimeout(JSONObject request) throws Exception {
        if(request.has("ms")) {
            long ms=request.getLong("ms");
            if(ms<0 || ms>Integer.MAX_VALUE)throw new IllegalArgumentException("Invalid timeout");
            rootShell("/system/bin/settings put system screen_off_timeout "+(ms==0?Integer.MAX_VALUE:ms),3000);
        }
        long current=android.provider.Settings.System.getLong(context.getContentResolver(),
            android.provider.Settings.System.SCREEN_OFF_TIMEOUT,60000);
        return new JSONObject().put("ms",current>=Integer.MAX_VALUE?0:current);
    }
    /** The Linux container's RAM ceiling (docs/61): "limit" one of the launcher's presets (MiB or
     * "unlimited") sets it; the reply is the launcher's status (choice, limit, use, peak, total). A
     * memory cgroup of Android's, so only root (the launcher's memory-limit action) can change it. */
    JSONObject containerMemory(JSONObject request) throws Exception {
        String limit=request.optString("limit","");
        if(!limit.isEmpty() && !limit.matches("2048|3072|4096|5120|6144|unlimited"))throw new IllegalArgumentException("Invalid memory limit");
        String reply=rootShell("/data/adb/rungic-plasma/rungic-plasma "+(limit.isEmpty()?"memory-status":"memory-limit "+limit)+" 2>&1",30000).trim();
        String[] lines=reply.split("\n");
        String last=lines[lines.length-1];
        if(!last.startsWith("{"))throw new java.io.IOException(reply.isEmpty()?"No reply from the launcher":reply);
        JSONObject result=new JSONObject(last);
        if(lines.length>1)result.put("warning",lines[0]);
        return result;
    }
    JSONObject snapshot() throws Exception {
        Network active=cm.getActiveNetwork();
        JSONArray networks=new JSONArray();
        JSONObject cached=new JSONObject(identity);
        for(Network network:cm.getAllNetworks()) {
            NetworkCapabilities caps=cm.getNetworkCapabilities(network);
            LinkProperties lp=cm.getLinkProperties(network);
            if(caps==null || lp==null || lp.getInterfaceName()==null)continue;
            boolean vpn=caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN);
            boolean wifi=!vpn && caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI);
            String kind=vpn?"vpn":wifi?"wifi":caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)?"ethernet":caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)?"cellular":"other";
            JSONObject row=new JSONObject().put("handle",network.getNetworkHandle()).put("interface",lp.getInterfaceName()).put("kind",kind)
                .put("default",network.equals(active)).put("validated",caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED))
                .put("captive",caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_CAPTIVE_PORTAL))
                .put("metered",!caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)).put("mtu",lp.getMtu());
            JSONArray addresses=new JSONArray(),dns=new JSONArray(),routes=new JSONArray();
            for(LinkAddress a:lp.getLinkAddresses())addresses.put(a.toString());
            for(InetAddress a:lp.getDnsServers())dns.put(a.getHostAddress());
            for(RouteInfo r:lp.getRoutes()) {
                JSONObject route=new JSONObject().put("destination",r.getDestination().toString()).put("default",r.isDefaultRoute());
                if(r.hasGateway())route.put("gateway",r.getGateway().getHostAddress());
                routes.put(route);
            }
            row.put("addresses",addresses).put("dns",dns).put("routes",routes);
            if(wifi) {
                WifiInfo info=caps.getTransportInfo() instanceof WifiInfo?(WifiInfo)caps.getTransportInfo():wm.getConnectionInfo();
                if(info!=null)row.put("rssi",info.getRssi()).put("frequency",info.getFrequency()).put("linkMbps",info.getLinkSpeed()).put("security",info.getCurrentSecurityType());
                // Read again whenever the Wi-Fi network changes (changed(true)); the handle ties it to this one.
                if(cached.optLong("handle",-1)==network.getNetworkHandle()) {
                    for(String key:new String[]{"ssid","bssid","mac"})if(cached.has(key))row.put(key,cached.getString(key));
                }
            }
            networks.put(row);
        }
        return new JSONObject().put("version",1).put("wifiEnabled",wm.isWifiEnabled()).put("wifi5GHz",wm.is5GHzBandSupported())
            .put("wifi6GHz",wm.is6GHzBandSupported()).put("networks",networks);
    }
    @Override public void close() {
        if(watching) {
            try { cm.unregisterNetworkCallback(changes); } catch(IllegalArgumentException ignored) {}
            try { context.getApplicationContext().unregisterReceiver(wifiState); } catch(IllegalArgumentException ignored) {}
            watching=false;
        }
        worker.shutdownNow();closeRoot();
    }
}
