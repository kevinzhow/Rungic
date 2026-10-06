package com.rungic.plasma;

import android.app.*;
import android.content.Context;
import android.content.Intent;
import android.os.IBinder;
import android.util.Log;
import java.io.File;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

public final class DesktopService extends Service {
    private final ScheduledExecutorService installation=Executors.newSingleThreadScheduledExecutor();
    private ScheduledFuture<?> installationPoll;
    private long generation;
    private boolean installWindow;
    private static volatile boolean activityVisible;
    static void visibility(Context context,boolean visible,boolean installing) {
        activityVisible=visible; // visible before the Activity can submit foreground work
        context.startForegroundService(new Intent(context,DesktopService.class)
            .putExtra("visible",visible).putExtra("installing",installing));
    }
    static void update(Context context,String state) {
        context.startForegroundService(new Intent(context,DesktopService.class).putExtra("state",state));
    }
    private Notification notification(String state,String body) {
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE);
        boolean action=(state.equals(getString(R.string.state_prepared)) && body.equals(getString(R.string.notification_account))) || state.equals(getString(R.string.state_prepare_failed));
        Notification.Builder builder=new Notification.Builder(this,action?"install-action":"desktop").setSmallIcon(android.R.drawable.ic_menu_view)
            .setContentTitle(state).setContentText(body).setContentIntent(open)
            .setOnlyAlertOnce(true).setOngoing(true);
        if(action)builder.setVisibility(Notification.VISIBILITY_PUBLIC);
        return builder.build();
    }
    @Override public void onCreate() {
        super.onCreate();
        getSystemService(NotificationManager.class).createNotificationChannel(
            new NotificationChannel("desktop","Rungic",NotificationManager.IMPORTANCE_LOW));
        NotificationChannel action=new NotificationChannel("install-action",getString(R.string.notification_action_channel),NotificationManager.IMPORTANCE_DEFAULT);
        action.setDescription(getString(R.string.notification_action_description));
        getSystemService(NotificationManager.class).createNotificationChannel(action);
        startForeground(1,notification(getString(R.string.state_preparing),getString(R.string.notification_return)));
    }
    private synchronized void publish(String state,String body) {
        getSystemService(NotificationManager.class).notify(1,notification(state,body));
    }
    private synchronized void stopInstallWatch() {
        generation++;
        if(installationPoll!=null)installationPoll.cancel(true);
        installationPoll=null;
    }
    private synchronized void watchInstallation() {
        if(installationPoll!=null || activityVisible || !installWindow)return;
        final long ticket=++generation;
        installationPoll=installation.scheduleWithFixedDelay(()->checkInstallation(ticket),5,5,TimeUnit.SECONDS);
    }
    private void checkInstallation(long ticket) {
        synchronized(this) { if(ticket!=generation || activityVisible || !installWindow)return; }
        FirstBootState state=FirstBootState.readSource(new File(getFilesDir(),"rungic-install-source.properties"),
            new File("/product/etc/rungic/seed.env"),new File(getFilesDir(),"rungic-install.properties"));
        // An unreadable file is unknown, not evidence that installation failed.
        if(state.reason!=null)return;
        if(state.ready) {
            try {
                org.json.JSONObject account;
                // MainActivity.control uses this same monitor: even at a lifecycle
                // transition background and foreground cannot call root concurrently.
                synchronized(MainActivity.class) {
                    if(activityVisible)return;
                    account=new org.json.JSONObject(MainActivity.control("account-status"));
                }
                if(!account.has("configured") || account.optBoolean("pending",false))return;
                boolean configured=account.getBoolean("configured");
                synchronized(this) {
                    if(ticket!=generation || activityVisible)return;
                    publish(getString(R.string.state_prepared),getString(configured?R.string.notification_return:R.string.notification_account));
                    installWindow=false; stopInstallWatch();
                }
            } catch(Exception error) { Log.w("RungicInstall","Account status not confirmed",error); }
        } else {
            synchronized(this) {
                if(ticket!=generation || activityVisible)return;
                if(state.failed) {
                    publish(getString(R.string.state_prepare_failed),getString(R.string.notification_failed));
                    installWindow=false; stopInstallWatch();
                } else publish(getString(R.string.state_preparing),StartupScreen.message(this,state));
            }
        }
    }
    @Override public void onDestroy() {
        stopInstallWatch(); installation.shutdownNow(); super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent) { return null; }
    @Override public synchronized int onStartCommand(Intent intent,int flags,int id) {
        if(intent==null)return START_NOT_STICKY;
        if(intent.hasExtra("visible")) {
            installWindow=intent.getBooleanExtra("installing",false);
            if(activityVisible || !installWindow)stopInstallWatch(); else watchInstallation();
        }
        String state=intent.getStringExtra("state");
        if(state!=null) {
            publish(state,getString(state.equals(getString(R.string.state_account))?R.string.notification_account:R.string.notification_return));
            if(!state.equals(getString(R.string.state_preparing)))stopInstallWatch();
        }
        return START_NOT_STICKY;
    }
}
