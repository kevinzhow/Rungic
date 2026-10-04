// SPDX-License-Identifier: MIT
package com.rungic.plasma;

import android.content.ContentProviderClient;
import android.content.ContentResolver;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.os.Binder;
import android.os.IBinder;

/** app_process has no AMS application record: acquire only the SMS provider via the framework's
 * external-provider API, with a bounded token lifetime. No Binder transaction numbers or writes. */
final class SmsProvider implements AutoCloseable {
    private final ContentResolver resolver;
    private ContentProviderClient client;
    private Object manager;
    private IBinder token;
    SmsProvider(Context context) throws Exception {
        resolver=context.getContentResolver();
        if(android.os.Process.myUid()!=0)return;
        manager=Class.forName("android.app.ActivityManager").getMethod("getService").invoke(null);
        token=new Binder();
        try {
            Object holder=manager.getClass().getMethod("getContentProviderExternal",String.class,int.class,IBinder.class,String.class)
                    .invoke(manager,"sms",0,token,"rungic-device-sms");
            if(holder==null)throw new IllegalStateException("SMS provider unavailable");
            Object provider=holder.getClass().getField("provider").get(holder);
            Class<?> type=Class.forName("android.content.IContentProvider");
            client=ContentProviderClient.class.getConstructor(ContentResolver.class,type,boolean.class)
                    .newInstance(resolver,provider,false);
        } catch(Exception e) { close();throw e; }
    }
    Cursor query(Uri uri,String[] columns,String selection,String[] args,String order) throws Exception {
        return client==null?resolver.query(uri,columns,selection,args,order):client.query(uri,columns,selection,args,order);
    }
    @Override public void close() throws Exception {
        try {if(client!=null) {client.close();client=null;}}
        finally {
            if(token!=null) {
                IBinder acquired=token;token=null;
                manager.getClass().getMethod("removeContentProviderExternal",String.class,IBinder.class)
                        .invoke(manager,"sms",acquired);
            }
        }
    }
}
