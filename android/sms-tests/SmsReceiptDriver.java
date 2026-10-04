package com.rungic.plasma;

import android.telephony.SmsMessage;
import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;

/** Synthetic 3GPP2 DELIVERY_ACK PDUs decoded by Android, without using the SIM. */
public class SmsReceiptDriver {
    private static void check(boolean ok) { if(!ok)throw new AssertionError(); }
    private static byte[] pdu(int type,int errorClass,int code) throws Exception {
        byte[] bearer=new byte[]{0,3,(byte)(type<<4),0,0x10,20,1,(byte)((errorClass<<6)|code)};
        ByteArrayOutputStream raw=new ByteArrayOutputStream();
        DataOutputStream out=new DataOutputStream(raw);
        out.writeInt(0);out.writeInt(4098);out.writeInt(0); // point-to-point, WMT
        out.write(new byte[]{1,0,0,1,5});out.writeBytes("10000");
        out.writeInt(0);out.write(new byte[]{0,0,0});
        out.writeInt(bearer.length);out.write(bearer);out.close();
        return raw.toByteArray();
    }
    // covers: desktop.sms/E2
    public static void main(String[] args) throws Exception {
        int[][] cases={{0,2,SmsState.DELIVERED},{0,0,SmsState.PENDING},
                {0,1,SmsState.PENDING},{0,3,SmsState.FAILED},
                {2,4,SmsState.PENDING},{3,10,SmsState.FAILED},{1,2,SmsState.UNANSWERED}};
        for(int[] item:cases) {
            SmsMessage message=SmsMessage.createFromPdu(pdu(4,item[0],item[1]),"3gpp2");
            check(message!=null && message.isStatusReportMessage());
            check(message.getStatus()==(((item[0]<<8)|item[1])<<16));
            check(SmsState.deliveryStatus("3gpp2",message.getStatus())==item[2]);
        }
        SmsMessage incoming=SmsMessage.createFromPdu(pdu(1,0,2),"3gpp2");
        check(incoming!=null && !incoming.isStatusReportMessage());
        System.out.println("SMS receipts: Android 3GPP2 PDU decoding and delivered/pending/failed/unknown mapping passed");
    }
}
