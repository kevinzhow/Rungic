package com.rungic.plasma;

import android.content.Intent;
import android.content.IntentFilter;

/** Runs against Android's actual IntentFilter via app_process, without using a SIM. */
public class SmsIntentDriver {
    private static void check(boolean ok) { if(!ok)throw new AssertionError(); }
    // covers: desktop.sms/E2
    public static void main(String[] args) {
        String pkg="com.rungic.plasma", id="sms-runtime-test";
        Intent sent=SmsIntents.callback(pkg,id,"sent",0);
        Intent delivered=SmsIntents.callback(pkg,id,"delivered",1);
        IntentFilter filter=SmsIntents.filter(sent.getAction(),delivered.getAction());
        check(filter.match(null,sent,false,"sms-tests")>=0);
        check(filter.match(null,delivered,false,"sms-tests")>=0);
        check(sent.getIntExtra("part",-1)==0 && delivered.getIntExtra("part",-1)==1);
        check(!sent.filterEquals(SmsIntents.callback(pkg,id,"sent",1)));
        check(!sent.filterEquals(SmsIntents.callback(pkg,"other-request","sent",0)));
        check(filter.match(null,SmsIntents.callback(pkg,"other-request","sent",0),false,"sms-tests")<0);
        IntentFilter broken=new IntentFilter(sent.getAction());
        check(broken.match(null,sent,false,"sms-tests")==IntentFilter.NO_MATCH_DATA);
        System.out.println("SMS intents: sent/delivery match, unique parts/requests, old missing-scheme regression passed");
    }
}
