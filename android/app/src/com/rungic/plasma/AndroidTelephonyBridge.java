package com.rungic.plasma;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Build;
import android.telephony.ServiceState;
import android.telephony.SignalStrength;
import android.telephony.SubscriptionManager;
import android.telephony.TelephonyCallback;
import android.telephony.TelephonyManager;
import org.json.JSONObject;

/** Mobile network for the Linux side's ModemManager and NetworkManager services (docs/73): the SIM,
 * the network operator, the signal and the mobile data switch of Android's data subscription.
 * READ_PHONE_STATE and the data switch (svc data) come from root, like the Bluetooth bridge's
 * privileges. */
final class AndroidTelephonyBridge {
    private final Context context;
    private final AndroidNetworkBridge root;
    AndroidTelephonyBridge(Context context,AndroidNetworkBridge root) { this.context=context;this.root=root; }

    /** Tells the Linux side (HostEvents) when the service, the signal level or mobile data changed, so
     * it asks for the state then rather than every few seconds. Registered once the permission is there. */
    private final class Changes extends TelephonyCallback implements TelephonyCallback.ServiceStateListener,
            TelephonyCallback.SignalStrengthsListener, TelephonyCallback.DataConnectionStateListener,
            TelephonyCallback.UserMobileDataStateListener {
        private int level=-1;
        @Override public void onServiceStateChanged(ServiceState state) { HostEvents.bump(HostEvents.TELEPHONY); }
        @Override public void onSignalStrengthsChanged(SignalStrength signal) {
            if(signal.getLevel()!=level) { level=signal.getLevel();HostEvents.bump(HostEvents.TELEPHONY); }
        }
        @Override public void onDataConnectionStateChanged(int state,int type) { HostEvents.bump(HostEvents.TELEPHONY); }
        @Override public void onUserMobileDataStateChanged(boolean enabled) { HostEvents.bump(HostEvents.TELEPHONY); }
    }
    private TelephonyManager watched;
    private int watchedSubscription=Integer.MIN_VALUE;
    private void watch(TelephonyManager tm) {
        // telephony() makes a new manager each time; one registration per data subscription.
        int subscription=SubscriptionManager.getDefaultDataSubscriptionId();
        if(tm==null || subscription==watchedSubscription)return;
        try {
            if(watched!=null)watched.unregisterTelephonyCallback(changes);
            tm.registerTelephonyCallback(context.getMainExecutor(),changes);
            watched=tm;watchedSubscription=subscription;
        } catch(SecurityException e) { watched=null;watchedSubscription=Integer.MIN_VALUE; }
    }
    private final Changes changes=new Changes();

    private TelephonyManager telephony() {
        TelephonyManager tm=context.getSystemService(TelephonyManager.class);
        int sub=SubscriptionManager.getDefaultDataSubscriptionId();
        return tm!=null && sub!=SubscriptionManager.INVALID_SUBSCRIPTION_ID?tm.createForSubscriptionId(sub):tm;
    }

    private static String sim(int state) {
        switch(state) {
            case TelephonyManager.SIM_STATE_ABSENT: return "absent";
            case TelephonyManager.SIM_STATE_PIN_REQUIRED: return "pin";
            case TelephonyManager.SIM_STATE_PUK_REQUIRED: return "puk";
            case TelephonyManager.SIM_STATE_NETWORK_LOCKED: return "network-locked";
            case TelephonyManager.SIM_STATE_READY: return "ready";
            case TelephonyManager.SIM_STATE_NOT_READY: return "not-ready";
            case TelephonyManager.SIM_STATE_PERM_DISABLED: return "disabled";
            case TelephonyManager.SIM_STATE_CARD_IO_ERROR: case TelephonyManager.SIM_STATE_CARD_RESTRICTED: return "error";
            default: return "unknown";
        }
    }

    JSONObject handle(JSONObject request) throws Exception {
        String action=request.optString("action");
        if(action.equals("data")) {
            root.rootShell("/system/bin/svc data "+(request.getBoolean("on")?"enable":"disable"),10000);
            return new JSONObject().put("accepted",true);
        }
        if(!action.equals("state"))throw new IllegalArgumentException("Unknown telephony action");
        if(context.checkSelfPermission(Manifest.permission.READ_PHONE_STATE)!=PackageManager.PERMISSION_GRANTED)
            root.rootShell("/system/bin/pm grant "+context.getPackageName()+" "+Manifest.permission.READ_PHONE_STATE,10000);
        TelephonyManager tm=telephony();
        watch(tm);
        boolean modem=tm!=null && tm.getPhoneType()!=TelephonyManager.PHONE_TYPE_NONE
                && context.getPackageManager().hasSystemFeature(PackageManager.FEATURE_TELEPHONY_RADIO_ACCESS);
        JSONObject result=new JSONObject().put("modem",modem).put("manufacturer",Build.MANUFACTURER).put("model",Build.MODEL);
        if(!modem)return result;
        ServiceState service=tm.getServiceState();
        SignalStrength signal=tm.getSignalStrength();
        return result.put("sim",sim(tm.getSimState())).put("slots",tm.getActiveModemCount())
                .put("simOperator",tm.getSimOperator()).put("simOperatorName",tm.getSimOperatorName())
                .put("operator",tm.getNetworkOperator()).put("operatorName",tm.getNetworkOperatorName())
                .put("service",service==null?ServiceState.STATE_OUT_OF_SERVICE:service.getState())
                .put("roaming",service!=null && service.getRoaming())
                .put("level",signal==null?0:signal.getLevel()).put("dataType",tm.getDataNetworkType())
                .put("dataEnabled",tm.isDataEnabled()).put("dataConnected",tm.getDataState()==TelephonyManager.DATA_CONNECTED)
                .put("dataRoaming",tm.isDataRoamingEnabled());
    }
}
