// SPDX-License-Identifier: MIT
package com.rungic.plasma;

import android.app.PendingIntent;
import android.content.Intent;
import android.os.Binder;
import android.os.Bundle;
import android.os.IBinder;
import android.os.Parcel;
import android.os.RemoteException;
import android.telephony.SmsMessage;
import java.util.ArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** A process-owned IIntentSender endpoint, not an AMS broadcast registered to an application.
 * Android's sendIntentSender explicitly supports non-PendingIntentRecord targets. The only
 * transaction is IIntentSender.send (AOSP android16-release); no vendor transaction IDs.
 * A token binds its part and purpose locally. Neither fill-in extras nor broadcasts select them.
 * Only the framework/radio may return results. Closing makes retained or late tokens inert. */
final class SmsCallbacks implements AutoCloseable {
    private static final String DESCRIPTOR="android.content.IIntentSender";
    final ArrayList<PendingIntent> sentIntents=new ArrayList<>(), deliveredIntents=new ArrayList<>();
    final SmsState state;
    private final CountDownLatch sent,delivered;
    private boolean closed;

    SmsCallbacks(int parts) {
        state=new SmsState(parts);sent=new CountDownLatch(parts);delivered=new CountDownLatch(parts);
        for(int part=0;part<parts;part++) {
            sentIntents.add(token(part,false));deliveredIntents.add(token(part,true));
        }
    }
    boolean awaitSent(long ms) throws InterruptedException {return sent.await(ms,TimeUnit.MILLISECONDS);}
    boolean awaitDelivered(long ms) throws InterruptedException {return delivered.await(ms,TimeUnit.MILLISECONDS);}
    private synchronized void receive(int part,boolean receipt,int code,Intent intent) {
        if(closed)return;
        if(!receipt) {if(state.sent(part,code))sent.countDown();return;}
        if(intent==null)return;
        byte[] pdu=intent.getByteArrayExtra("pdu");
        if(pdu==null || pdu.length>4096)return;
        String format=intent.getStringExtra("format");
        SmsMessage report=format==null?SmsMessage.createFromPdu(pdu):SmsMessage.createFromPdu(pdu,format);
        if(report!=null && report.isStatusReportMessage() && state.receipt(part,format,report.getStatus()))delivered.countDown();
    }
    private PendingIntent token(int part,boolean receipt) {
        Binder endpoint=new Binder() {
            @Override protected boolean onTransact(int transaction,Parcel data,Parcel reply,int flags) throws RemoteException {
                if(transaction==IBinder.INTERFACE_TRANSACTION) {if(reply!=null)reply.writeString(DESCRIPTOR);return true;}
                if(transaction!=IBinder.FIRST_CALL_TRANSACTION)return super.onTransact(transaction,data,reply,flags);
                int uid=Binder.getCallingUid();
                if(uid!=0 && uid!=1000 && uid!=1001)throw new SecurityException("SMS callback caller rejected");
                data.enforceInterface(DESCRIPTOR);
                int code=data.readInt();
                Intent intent=data.readInt()!=0?Intent.CREATOR.createFromParcel(data):null;
                data.readString();data.readStrongBinder();data.readStrongBinder();data.readString();
                if(data.readInt()!=0)Bundle.CREATOR.createFromParcel(data);
                if(data.dataAvail()!=0)throw new IllegalArgumentException("Unexpected SMS callback fields");
                receive(part,receipt,code,intent);return true;
            }
        };
        Parcel parcel=Parcel.obtain();
        try {parcel.writeStrongBinder(endpoint);parcel.setDataPosition(0);return PendingIntent.readPendingIntentOrNullFromParcel(parcel);}
        finally {parcel.recycle();}
    }
    @Override public synchronized void close() {closed=true;}
}
