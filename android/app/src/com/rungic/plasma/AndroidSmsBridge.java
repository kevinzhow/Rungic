package com.rungic.plasma;

import android.Manifest;
import android.app.Activity;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.provider.Telephony;
import android.telephony.SmsManager;
import android.telephony.SubscriptionManager;
import android.telephony.SubscriptionInfo;
import android.telephony.SmsMessage;
import android.os.Build;
import java.util.List;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Text messages through Android's SIM for the Linux side (op sms): send and list. Android keeps
 * the messages (the system SMS provider; a message sent here is saved to its sent box). SEND_SMS
 * and READ_SMS come from root (pm grant), like the telephony bridge's READ_PHONE_STATE. Answered on
 * its own thread: a send waits for the radio's result. */
final class AndroidSmsBridge {
    static final int MAX_TEXT=SmsState.MAX_TEXT;
    private static final long SENT_WAIT_MS=45000, DELIVERED_WAIT_MS=15000;
    private final Activity activity;
    private final AndroidNetworkBridge root;
    AndroidSmsBridge(Activity activity,AndroidNetworkBridge root) { this.activity=activity;this.root=root; }

    private void grant(String permission) throws Exception {
        if(activity.checkSelfPermission(permission)!=PackageManager.PERMISSION_GRANTED)
            root.rootShell("/system/bin/pm grant "+activity.getPackageName()+" "+permission,10000);
        if(activity.checkSelfPermission(permission)!=PackageManager.PERMISSION_GRANTED)
            throw new SecurityException("Android did not grant "+permission);
    }

    JSONObject handle(JSONObject request) throws Exception {
        String action=request.optString("action");
        if(action.equals("send"))return send(request);
        if(action.equals("list"))return list(request);
        throw new IllegalArgumentException("Unknown sms action");
    }

    private static String result(int code) {
        switch(code) {
            case Activity.RESULT_OK: return "sent";
            case SmsManager.RESULT_ERROR_NO_SERVICE: return "no-service";
            case SmsManager.RESULT_ERROR_RADIO_OFF: return "radio-off";
            case SmsManager.RESULT_ERROR_NULL_PDU: return "null-pdu";
            case SmsManager.RESULT_ERROR_LIMIT_EXCEEDED: return "limit-exceeded";
            case SmsManager.RESULT_ERROR_SHORT_CODE_NOT_ALLOWED: return "short-code-not-allowed";
            case SmsManager.RESULT_ERROR_SHORT_CODE_NEVER_ALLOWED: return "short-code-never-allowed";
            default: return "error-"+code;
        }
    }

    private JSONObject send(JSONObject request) throws Exception {
        String to=SmsState.number(request.getString("to"));
        String text=request.getString("text");
        SmsState.text(text);
        grant(Manifest.permission.SEND_SMS);
        grant(Manifest.permission.READ_PHONE_STATE);
        List<SubscriptionInfo> active=activity.getSystemService(SubscriptionManager.class).getActiveSubscriptionInfoList();
        int subscription=request.optInt("subscription",SubscriptionManager.getDefaultSmsSubscriptionId());
        if(!request.has("subscription") && subscription<0 && active!=null && active.size()==1)
            subscription=active.get(0).getSubscriptionId();
        boolean found=false;
        if(active!=null)for(SubscriptionInfo sim:active)if(sim.getSubscriptionId()==subscription)found=true;
        if(!found)throw new IllegalStateException("No active default SMS SIM; select an active subscription");
        SmsManager sms=SmsManager.getSmsManagerForSubscriptionId(subscription);
        ArrayList<String> parts=sms.divideMessage(text);
        String id=UUID.randomUUID().toString();
        String sentAction=activity.getPackageName()+".SMS_SENT."+id, deliveredAction=activity.getPackageName()+".SMS_DELIVERED."+id;
        CountDownLatch sent=new CountDownLatch(parts.size()), delivered=new CountDownLatch(parts.size());
        SmsState state=new SmsState(parts.size());
        BroadcastReceiver receiver=new BroadcastReceiver() {
            @Override public void onReceive(Context context,Intent intent) {
                if(sentAction.equals(intent.getAction())) {
                    if(state.sent(intent.getIntExtra("part",-1),getResultCode()))sent.countDown();
                } else if(deliveredAction.equals(intent.getAction())) {
                    byte[] pdu=intent.getByteArrayExtra("pdu");
                    if(pdu!=null) {
                        String format=intent.getStringExtra("format");
                        SmsMessage report=format==null?SmsMessage.createFromPdu(pdu):SmsMessage.createFromPdu(pdu,format);
                        if(report!=null && report.isStatusReportMessage()
                                && state.receipt(intent.getIntExtra("part",-1),format,report.getStatus()))
                            delivered.countDown();
                    }
                }
            }
        };
        IntentFilter filter=SmsIntents.filter(sentAction,deliveredAction);
        if(Build.VERSION.SDK_INT>=33)activity.registerReceiver(receiver,filter,Context.RECEIVER_NOT_EXPORTED);
        else activity.registerReceiver(receiver,filter);
        try {
            ArrayList<PendingIntent> sentIntents=new ArrayList<>(), deliveredIntents=new ArrayList<>();
            for(int i=0;i<parts.size();i++) {
                Intent sentIntent=SmsIntents.callback(activity.getPackageName(),id,"sent",i);
                Intent deliveredIntent=SmsIntents.callback(activity.getPackageName(),id,"delivered",i);
                sentIntents.add(PendingIntent.getBroadcast(activity,i,sentIntent,
                        PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_ONE_SHOT));
                // Explicitly package-scoped; mutable only so the telephony service can attach
                // the delivery PDU. A broadcast alone never proves successful delivery.
                int deliveryFlags=PendingIntent.FLAG_ONE_SHOT;
                if(Build.VERSION.SDK_INT>=31)deliveryFlags|=PendingIntent.FLAG_MUTABLE;
                deliveredIntents.add(PendingIntent.getBroadcast(activity,i,deliveredIntent,deliveryFlags));
            }
            long started=System.currentTimeMillis();
            if(parts.size()==1)sms.sendTextMessage(to,null,text,sentIntents.get(0),deliveredIntents.get(0));
            else sms.sendMultipartTextMessage(to,null,parts,sentIntents,deliveredIntents);
            sent.await(SENT_WAIT_MS,TimeUnit.MILLISECONDS);
            long sentAt=System.currentTimeMillis();
            JSONObject reply=new JSONObject().put("parts",parts.size()).put("submittedAt",started)
                    .put("subscription",subscription).put("sentParts",state.sentParts()).put("status",state.status());
            if(state.status().equals("failed"))return reply.put("error",result(state.error()));
            if(state.status().equals("pending"))return reply.put("error","Radio result unknown; do not resend automatically");
            delivered.await(DELIVERED_WAIT_MS,TimeUnit.MILLISECONDS);
            return reply.put("sentAt",sentAt).put("delivery",state.delivery())
                    .put("delivered",state.delivery().equals("delivered"));
        } finally {
            try { activity.unregisterReceiver(receiver); } catch(IllegalArgumentException ignored) {}
        }
    }

    /** Messages of the SMS provider, newest first: {"box":"inbox"|"sent"|"all","from":"<number>","since":<ms>,"limit":<n>}. */
    private JSONObject list(JSONObject request) throws Exception {
        grant(Manifest.permission.READ_SMS);
        String box=request.optString("box","inbox");
        Uri uri;
        switch(box) {
            case "inbox": uri=Telephony.Sms.Inbox.CONTENT_URI;break;
            case "sent": uri=Telephony.Sms.Sent.CONTENT_URI;break;
            case "all": uri=Telephony.Sms.CONTENT_URI;break;
            default: throw new IllegalArgumentException("Unknown box");
        }
        int limit=Math.max(1,Math.min(100,request.optInt("limit",20)));
        StringBuilder where=new StringBuilder();
        ArrayList<String> args=new ArrayList<>();
        if(request.has("since") && request.getLong("since")<0)throw new IllegalArgumentException("Invalid since timestamp");
        if(request.has("since")) { where.append(Telephony.Sms.DATE+">=?");args.add(Long.toString(request.getLong("since"))); }
        String from=request.optString("from","");
        // Android stores the number as the network gave it (with or without +86): compare the
        // digits' end here rather than with SQL functions the provider may not accept.
        if(!from.isEmpty())SmsState.number(from);
        JSONArray messages=new JSONArray();
        SmsState.Scan scan=new SmsState.Scan(limit);
        try(Cursor c=activity.getContentResolver().query(uri,
                new String[]{Telephony.Sms._ID,Telephony.Sms.ADDRESS,Telephony.Sms.BODY,Telephony.Sms.DATE,Telephony.Sms.TYPE,Telephony.Sms.READ},
                where.length()==0?null:where.toString(),args.toArray(new String[0]),Telephony.Sms.DATE+" DESC")) {
            if(c==null)throw new IllegalStateException("SMS provider unavailable");
            while(c.moveToNext()) {
                boolean include=scan.accept(from.isEmpty() || SmsState.sameNumber(from,c.getString(1)));
                if(scan.stopped)break;
                if(!include)continue;
                int type=c.getInt(4);
                messages.put(new JSONObject().put("id",c.getLong(0)).put("address",c.getString(1)).put("text",c.getString(2))
                        .put("date",c.getLong(3)).put("box",type==Telephony.Sms.MESSAGE_TYPE_SENT?"sent":type==Telephony.Sms.MESSAGE_TYPE_INBOX?"inbox":"other")
                        .put("read",c.getInt(5)!=0));
            }
        }
        return new JSONObject().put("messages",messages).put("truncated",scan.truncated);
    }
}
