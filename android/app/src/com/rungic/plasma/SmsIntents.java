package com.rungic.plasma;

import android.content.Intent;
import android.content.IntentFilter;
import android.net.Uri;

/** Request-unique SMS callbacks. The filter must accept the same data scheme as the intents. */
final class SmsIntents {
    static IntentFilter filter(String sent, String delivered) {
        IntentFilter filter=new IntentFilter();
        filter.addAction(sent);filter.addAction(delivered);
        filter.addDataScheme("rungic-sms");
        return filter;
    }
    static Intent callback(String packageName,String id,String event,int part) {
        return new Intent(packageName+".SMS_"+event.toUpperCase(java.util.Locale.ROOT)+"."+id)
                .setPackage(packageName).setData(Uri.parse("rungic-sms://"+id+"/"+event+"/"+part))
                .putExtra("part",part);
    }
}
