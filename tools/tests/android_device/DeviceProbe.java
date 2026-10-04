// SPDX-License-Identifier: MIT
package com.rungic.plasma;

import android.app.Activity;
import android.app.PendingIntent;
import android.content.*;
import android.os.Looper;
import org.json.*;
import java.lang.reflect.Method;
import java.util.UUID;
import java.util.concurrent.*;

/** Temporary, read-only device check: no SMS submission, Wi-Fi change or APK lifecycle changes.
 * Build with the candidate classes, run as root in a temporary directory, never install as an app.
 * Synthetic PendingIntents verify dispatch/extra transport, not real modem callbacks. */
public final class DeviceProbe {
    private static JSONObject report=new JSONObject();
    private static void check(String name,Callable<Object> operation) {
        try { report.put(name,operation.call()); }
        catch(Throwable e) {
            Throwable cause=e instanceof java.lang.reflect.InvocationTargetException?e.getCause():e;
            try { report.put(name,new JSONObject().put("error",cause.getClass().getSimpleName()).put("reason",String.valueOf(cause.getMessage()))); }
            catch(Exception ignored) {}
        }
    }
    // covers[provider]: iface:network iface:bluetooth iface:telephony iface:device-backend
    public static void main(String[] args) throws Exception {
        if(android.os.Process.myUid()!=0 || args.length!=1)throw new IllegalArgumentException();
        Looper.prepareMainLooper();
        Method initialize=DeviceDaemon.class.getDeclaredMethod("initialize",String.class,String.class,String.class,String.class);
        initialize.setAccessible(true);
        check("telephonyInitializer",()->{initialize.invoke(null,"android.telephony.TelephonyFrameworkInitializer","android.os.TelephonyServiceManager","getTelephonyServiceManager","setTelephonyServiceManager");return true;});
        check("bluetoothInitializer",()->{initialize.invoke(null,"android.bluetooth.BluetoothFrameworkInitializer","android.os.BluetoothServiceManager","getBluetoothServiceManager","setBluetoothServiceManager");return true;});
        Context context=DeviceContext.create();
        AndroidNetworkBridge network=new AndroidNetworkBridge(context,args[0]);
        AndroidTelephonyBridge telephony=new AndroidTelephonyBridge(context,network);
        AndroidBluetoothBridge bluetooth=new AndroidBluetoothBridge(context,network);
        AndroidSmsBridge sms=new AndroidSmsBridge(context,network);
        report.put("uid",android.os.Process.myUid()).put("package",context.getPackageName());
        new Thread(()->{
            check("networkCallbacks",()->{network.start();return true;});
            check("networkRead",()->network.snapshot().has("networks"));
            check("telephonyRead",()->telephony.handle(new JSONObject().put("action","state")).has("modem"));
            check("bluetoothRead",()->bluetooth.handle(new JSONObject().put("action","state")).has("devices"));
            check("smsRead",()->sms.handle(new JSONObject().put("action","list").put("from","10000")
                .put("since",System.currentTimeMillis()).put("limit",1)).has("messages"));
            try(SmsCallbacks callbacks=new SmsCallbacks(1)) {
                check("sentDispatch",()-> {
                    callbacks.sentIntents.get(0).send(context,Activity.RESULT_OK,new Intent());
                    return callbacks.awaitSent(4000) && callbacks.state.sentParts()==1;
                });
                check("deliveredDispatch",()-> {
                    // Synthetic GSM status report, not an SMS send or a real carrier receipt.
                    byte[] pdu=new byte[]{0,2,0,2,(byte)0x81,0x21,0x42,0x10,0x10,0,0,0,0,0x42,0x10,0x10,0,0,0,0,0};
                    callbacks.deliveredIntents.get(0).send(context,Activity.RESULT_OK,
                        new Intent().putExtra("pdu",pdu).putExtra("format","3gpp"));
                    return callbacks.awaitDelivered(4000) && "delivered".equals(callbacks.state.delivery());
                });
            }
            check("boundPartsAndDuplicate",()-> {
                try(SmsCallbacks callbacks=new SmsCallbacks(2)) {
                    callbacks.sentIntents.get(0).send(context,Activity.RESULT_OK,new Intent().putExtra("part",1));
                    callbacks.sentIntents.get(0).send(context,2,new Intent());
                    Thread.sleep(100);
                    if(callbacks.state.sentParts()!=1 || !"pending".equals(callbacks.state.status()))return false;
                    android.os.Parcel parcel=android.os.Parcel.obtain();
                    PendingIntent second;
                    try {callbacks.sentIntents.get(1).writeToParcel(parcel,0);parcel.setDataPosition(0);second=PendingIntent.CREATOR.createFromParcel(parcel);}
                    finally {parcel.recycle();}
                    second.send(context,Activity.RESULT_OK,new Intent().putExtra("part",0));
                    return callbacks.awaitSent(4000) && "sent".equals(callbacks.state.status());
                }
            });
            check("lateCallbackIgnored",()-> {
                SmsCallbacks callbacks=new SmsCallbacks(1);callbacks.close();
                callbacks.sentIntents.get(0).send(context,Activity.RESULT_OK,new Intent());
                Thread.sleep(100);return callbacks.state.sentParts()==0;
            });
            check("invalidReceiptIgnored",()-> {
                try(SmsCallbacks callbacks=new SmsCallbacks(1)) {
                    callbacks.deliveredIntents.get(0).send(context,Activity.RESULT_OK,new Intent().putExtra("pdu",new byte[]{0}).putExtra("format","3gpp"));
                    return !callbacks.awaitDelivered(100) && !"delivered".equals(callbacks.state.delivery());
                }
            });
            System.out.println(report.toString());System.exit(0);
        },"device-probe").start();
        Looper.loop();
    }
}
