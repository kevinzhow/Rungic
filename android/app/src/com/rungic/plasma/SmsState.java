package com.rungic.plasma;

import java.util.Arrays;

/** SMS validation and per-part radio/receipt state, without Android dependencies. */
final class SmsState {
    static final int MAX_TEXT=1000, UNANSWERED=Integer.MIN_VALUE;
    static final int DELIVERED=0, PENDING=1, FAILED=2;
    static String number(String raw) {
        String value=raw==null?"":raw.replaceAll("[\\s-]","");
        if(!value.matches("\\+?[0-9]{3,20}"))throw new IllegalArgumentException("Invalid phone number");
        return value;
    }
    static boolean sameNumber(String expected,String actual) {
        String wanted=number(expected).replace("+","");
        String received=actual==null?"":actual.replaceAll("[\\s-]","");
        if(!received.matches("\\+?[0-9]{3,20}"))return false;
        received=received.replace("+","");
        // Service short codes must match exactly; international prefixes are permitted for
        // subscriber numbers only, never e.g. 13912310000 matching service number 10000.
        return wanted.equals(received) || (Math.min(wanted.length(),received.length())>=7 &&
                (wanted.endsWith(received) || received.endsWith(wanted)));
    }
    static void text(String value) {
        if(value==null || value.isEmpty() || value.codePointCount(0,value.length())>MAX_TEXT)
            throw new IllegalArgumentException("Text must be 1 to "+MAX_TEXT+" characters");
    }
    private final int[] sent, receipt;
    SmsState(int parts) {
        if(parts<1)throw new IllegalArgumentException("No SMS parts");
        sent=new int[parts];receipt=new int[parts];
        Arrays.fill(sent,UNANSWERED);Arrays.fill(receipt,UNANSWERED);
    }
    synchronized boolean sent(int part,int code) {
        if(part<0 || part>=sent.length || sent[part]!=UNANSWERED)return false;
        sent[part]=code;return true;
    }
    /** SmsMessage.getStatus has different layouts for 3GPP and 3GPP2 (Android API docs). */
    static int deliveryStatus(String format,int status) {
        if(status<0)return UNANSWERED;
        if("3gpp".equals(format)) {
            if(status>0x7f)return UNANSWERED; // reserved GSM values
            return status<0x20?DELIVERED:status<0x40?PENDING:FAILED;
        }
        if("3gpp2".equals(format)) {
            int value=status>>>16;
            if((status&0xffff)!=0 || (value&~0x33f)!=0)return UNANSWERED;
            int errorClass=(value>>>8)&3, code=value&0x3f;
            if(errorClass==2)return PENDING;
            if(errorClass==3)return FAILED;
            if(errorClass!=0)return UNANSWERED;
            if(code==2)return DELIVERED;
            if(code==0 || code==1)return PENDING; // accepted/deposited, not received
            if(code==3)return FAILED; // cancelled
        }
        return UNANSWERED; // missing format or reserved status cannot prove delivery
    }
    synchronized boolean receipt(int part,String format,int status) {
        int normalized=deliveryStatus(format,status);
        if(part<0 || part>=receipt.length || normalized==UNANSWERED || receipt[part]!=UNANSWERED)return false;
        receipt[part]=normalized;return true;
    }
    /** Bounded filtered scan: look for an extra match before reporting result truncation. */
    static final class Scan {
        private final int limit;
        private int scanned, matched;
        boolean stopped, truncated;
        Scan(int limit) { this.limit=limit; }
        boolean accept(boolean match) {
            if(stopped)return false;
            if(scanned==2000 || (match && matched==limit)) {
                stopped=true;truncated=true;return false;
            }
            scanned++;
            if(match)matched++;
            return match;
        }
    }
    synchronized int sentParts() { int n=0;for(int code:sent)if(code==-1)n++;return n; }
    synchronized int error() { for(int code:sent)if(code!=UNANSWERED && code!=-1)return code;return -1; }
    synchronized String status() {
        if(error()!=-1)return "failed";
        return sentParts()==sent.length?"sent":"pending";
    }
    synchronized String delivery() {
        boolean unknown=false,pending=false;
        for(int code:receipt) {
            if(code==UNANSWERED)unknown=true;
            else if(code==FAILED)return "failed";
            else if(code==PENDING)pending=true;
        }
        return unknown?"unconfirmed":pending?"pending":"delivered";
    }
}
