package com.rungic.plasma;

import android.Manifest;
import android.content.Context;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothClass;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.os.SystemClock;
import org.json.*;
import java.io.IOException;
import java.util.*;

/** Bluetooth for the Linux side's BlueZ service (docs/73). The state the service polls every few
 * seconds is read in this process (BLUETOOTH_CONNECT): the adapter, bonded devices, discovery results,
 * and which devices are connected from the ACL broadcasts. Only what an app cannot do or see goes to
 * com.rungic.bluetooth.RootBluetooth, run as root (a whole app_process VM, so never per poll): unpair,
 * connect, disconnect, name, and a calibration of the adapter address and connected devices at the
 * start, after those operations and every 10 minutes. The on/off switch is Android's cmd
 * bluetooth_manager. Bonding is started here and confirmed by the user in Android's pairing dialog. */
final class AndroidBluetoothBridge {
    private final Context context;
    private final AndroidNetworkBridge root;
    private final Map<String,JSONObject> found=new HashMap<>();
    private boolean receiving;
    private final Set<String> connected=new HashSet<>();
    private String address;
    private long calibrated;               // elapsedRealtime of the last root calibration, 0: needed
    private boolean watching;
    AndroidBluetoothBridge(Context context,AndroidNetworkBridge root) { this.context=context;this.root=root; }

    private final BroadcastReceiver links=new BroadcastReceiver() {
        @Override public void onReceive(Context context,Intent intent) {
            HostEvents.bump(HostEvents.BLUETOOTH);   // the Linux side asks for the state now (HostEvents)
            BluetoothDevice device=intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE,BluetoothDevice.class);
            synchronized(connected) {
                if(BluetoothAdapter.ACTION_STATE_CHANGED.equals(intent.getAction())) connected.clear();
                else if(device==null) return;
                else if(BluetoothDevice.ACTION_ACL_CONNECTED.equals(intent.getAction())) connected.add(device.getAddress());
                else if(BluetoothDevice.ACTION_ACL_DISCONNECTED.equals(intent.getAction())) connected.remove(device.getAddress());
            }
        }
    };

    private void permissions() throws Exception {
        for(String permission:new String[]{Manifest.permission.BLUETOOTH_SCAN,Manifest.permission.BLUETOOTH_CONNECT})
            if(context.checkSelfPermission(permission)!=PackageManager.PERMISSION_GRANTED)
                root.rootShell("/system/bin/pm grant "+context.getPackageName()+" "+permission,10000);
        if(!watching) {
            IntentFilter filter=new IntentFilter(BluetoothDevice.ACTION_ACL_CONNECTED);
            filter.addAction(BluetoothDevice.ACTION_ACL_DISCONNECTED);
            filter.addAction(BluetoothAdapter.ACTION_STATE_CHANGED);
            filter.addAction(BluetoothDevice.ACTION_BOND_STATE_CHANGED);
            filter.addAction(BluetoothAdapter.ACTION_DISCOVERY_STARTED);
            filter.addAction(BluetoothAdapter.ACTION_DISCOVERY_FINISHED);
            context.getApplicationContext().registerReceiver(links,filter,Context.RECEIVER_EXPORTED);
            watching=true;
        }
    }

    /** Adapter address and connected devices as root sees them; the broadcasts keep them current. */
    private void calibrate() throws Exception {
        if(calibrated!=0 && SystemClock.elapsedRealtime()-calibrated<600000)return;
        JSONObject state=helper("state");
        JSONArray devices=state.optJSONArray("devices");
        synchronized(connected) {
            connected.clear();
            for(int i=0;devices!=null && i<devices.length();i++)
                if(devices.getJSONObject(i).optBoolean("connected"))connected.add(devices.getJSONObject(i).getString("address"));
        }
        if(!state.optString("address").isEmpty())address=state.getString("address");
        calibrated=SystemClock.elapsedRealtime();
    }

    private JSONObject state() throws Exception {
        permissions();
        BluetoothAdapter adapter=adapter();
        if(adapter==null)throw new IOException("No Bluetooth adapter");
        int state=adapter.getState();
        boolean on=state==BluetoothAdapter.STATE_ON;
        if(on)calibrate();
        JSONObject result=new JSONObject().put("state",state).put("enabled",on).put("name",adapter.getName())
                .put("address",address==null?"":address).put("discovering",on && adapter.isDiscovering());
        JSONArray devices=new JSONArray();
        if(on) {
            for(BluetoothDevice d:adapter.getBondedDevices()) {
                JSONObject row=com.rungic.bluetooth.RootBluetooth.device(d);
                synchronized(connected) { row.put("connected",connected.contains(d.getAddress())); }
                devices.put(row);
            }
        }
        return result.put("devices",devices);
    }

    private final BroadcastReceiver receiver=new BroadcastReceiver() {
        @Override public void onReceive(Context context,Intent intent) {
            BluetoothDevice device=intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE,BluetoothDevice.class);
            if(device==null)return;
            HostEvents.bump(HostEvents.BLUETOOTH);
            try {
                BluetoothClass type=intent.getParcelableExtra(BluetoothDevice.EXTRA_CLASS,BluetoothClass.class);
                String name=intent.getStringExtra(BluetoothDevice.EXTRA_NAME);
                synchronized(found) {
                    JSONObject row=found.containsKey(device.getAddress())?found.get(device.getAddress()):new JSONObject().put("address",device.getAddress());
                    if(name!=null)row.put("name",name);
                    if(type!=null)row.put("class",type.hashCode());
                    if(intent.hasExtra(BluetoothDevice.EXTRA_RSSI))row.put("rssi",intent.getShortExtra(BluetoothDevice.EXTRA_RSSI,(short)0));
                    row.put("seen",SystemClock.elapsedRealtime());
                    found.put(device.getAddress(),row);
                }
            } catch(JSONException ignored) {}
        }
    };

    private BluetoothAdapter adapter() {
        BluetoothManager manager=context.getSystemService(BluetoothManager.class);
        return manager==null?null:manager.getAdapter();
    }

    private static final java.util.regex.Pattern ADDRESS=java.util.regex.Pattern.compile("[0-9A-F]{2}(:[0-9A-F]{2}){5}");

    private JSONObject helper(String arguments) throws Exception {
        String apk=root.apkPath;
        String reply=root.rootShell("CLASSPATH='"+apk.replace("'","'\\''")+"' /system/bin/app_process /system/bin com.rungic.bluetooth.RootBluetooth "+arguments+" || true",15000);
        String[] lines=reply.trim().split("\n");
        JSONObject result=new JSONObject(lines[lines.length-1]);
        if(result.has("error"))throw new IOException(result.getString("error"));
        return result;
    }

    JSONObject handle(JSONObject request) throws Exception {
        String action=request.optString("action");
        String address=request.optString("address","").toUpperCase(Locale.ROOT);
        switch(action) {
            case "state": {
                JSONObject state=state();
                JSONArray devices=new JSONArray();
                long now=SystemClock.elapsedRealtime();
                synchronized(found) {
                    for(Iterator<Map.Entry<String,JSONObject>> it=found.entrySet().iterator();it.hasNext();) {
                        JSONObject row=it.next().getValue();
                        if(now-row.optLong("seen")>120000)it.remove();else devices.put(row);
                    }
                }
                return state.put("found",devices);
            }
            case "power": {
                root.rootShell("/system/bin/cmd bluetooth_manager "+(request.getBoolean("on")?"enable":"disable"),10000);
                return new JSONObject().put("accepted",true);
            }
            case "discover": {
                BluetoothAdapter adapter=adapter();
                if(adapter==null)throw new IOException("No Bluetooth adapter");
                if(request.getBoolean("on")) {
                    // Discovery runs in this app; the permission comes from root like the helper's privileges.
                    permissions();
                    if(!receiving) {
                        IntentFilter filter=new IntentFilter(BluetoothDevice.ACTION_FOUND);
                        filter.addAction(BluetoothDevice.ACTION_NAME_CHANGED);
                        context.getApplicationContext().registerReceiver(receiver,filter,Context.RECEIVER_EXPORTED);
                        receiving=true;
                    }
                    if(!adapter.isDiscovering() && !adapter.startDiscovery())throw new IOException("Discovery refused");
                } else if(adapter.isDiscovering()) adapter.cancelDiscovery();
                return new JSONObject().put("accepted",true);
            }
            case "pair": case "unpair": case "connect": case "disconnect":
                if(!ADDRESS.matcher(address).matches())throw new IllegalArgumentException("Invalid address");
                calibrated=0;                  // the connections change: calibrate on the next poll
                return helper(action+" "+address);
            case "name": {
                String name=request.getString("name");
                if(name.isEmpty() || name.length()>248 || !name.matches("[^\\p{Cntrl}]+"))throw new IllegalArgumentException("Invalid name");
                calibrated=0;
                return helper("name '"+name.replace("'","'\\''")+"'");
            }
            default: throw new IllegalArgumentException("Unknown Bluetooth action");
        }
    }
}
