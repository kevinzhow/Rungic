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
    private static final int SERVICE_NOTIFICATION=1, ACCOUNT_NOTIFICATION=3, FAILURE_NOTIFICATION=4; // CaptureService owns 2
    private String actionState;
    private boolean failurePresented;
    static void accountResult(Context context,boolean successful) {
        // A notification/lifecycle refusal must never turn a committed account into a setup failure.
        try {
            if(successful)cancelActions(context);
            context.startForegroundService(new Intent(context,DesktopService.class).putExtra("account-result",successful));
        }
        catch(RuntimeException error) { Log.w("RungicInstall","Could not deliver account reminder result",error); }
    }
    static void failureViewed(Context context) {
        try {
            if(activityVisible)context.getSystemService(NotificationManager.class).cancel(FAILURE_NOTIFICATION);
            context.startForegroundService(new Intent(context,DesktopService.class).putExtra("failure-viewed",true));
        } catch(RuntimeException error) { Log.w("RungicInstall","Could not deliver viewed failure",error); }
    }
    private static volatile boolean activityVisible;
    static void visibility(Context context,boolean visible,boolean installing) {
        activityVisible=visible; // visible before the Activity can submit foreground work
        context.startForegroundService(new Intent(context,DesktopService.class)
            .putExtra("visible",visible).putExtra("installing",installing));
    }
    static void update(Context context,String state) {
        context.startForegroundService(new Intent(context,DesktopService.class).putExtra("state",state));
    }
    private Notification notification(String state,String body,boolean action) {
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder builder=new Notification.Builder(this,action?"install-action":"desktop").setSmallIcon(android.R.drawable.ic_menu_view)
            .setContentTitle(state).setContentText(body).setContentIntent(open)
            .setOnlyAlertOnce(!action).setOngoing(!action);
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
        for(android.service.notification.StatusBarNotification posted:
                getSystemService(NotificationManager.class).getActiveNotifications()) {
            if(posted.getId()==ACCOUNT_NOTIFICATION)actionState="account";
            if(posted.getId()==FAILURE_NOTIFICATION)actionState="failed";
        }
        startForeground(SERVICE_NOTIFICATION,notification(getString(R.string.state_preparing),getString(R.string.notification_return),false));
    }
    private synchronized void publish(String state,String body) {
        getSystemService(NotificationManager.class).notify(SERVICE_NOTIFICATION,notification(state,body,false));
        if(state.equals(getString(R.string.state_prepare_failed)))publishAction("failed",state,body);
        else if(state.equals(getString(R.string.state_prepared))) {
            if(body.equals(getString(R.string.notification_account)))publishAction("account",state,body);
            else clearAction(); // independently confirmed configured account
        }
        // Opening the account form (state_account) is not completion.
    }
    private void publishAction(String kind,String state,String body) {
        if(kind.equals(actionState))return; // one alert per transition, not per progress poll
        clearAction(); // replace the account reminder with a new failure alert
        actionState=kind;
        getSystemService(NotificationManager.class).notify(kind.equals("account")?ACCOUNT_NOTIFICATION:FAILURE_NOTIFICATION,notification(state,body,true));
    }
    private static void cancelActions(Context context) {
        context.getSystemService(NotificationManager.class).cancel(ACCOUNT_NOTIFICATION);
        context.getSystemService(NotificationManager.class).cancel(FAILURE_NOTIFICATION);
    }
    private void clearAction() {
        cancelActions(this);
        actionState=null; failurePresented=false;
    }
    private void acknowledgeFailure() {
        if("failed".equals(actionState)) {
            failurePresented=true;
            if(activityVisible)clearAction();
        }
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
            if(activityVisible && failurePresented)clearAction();
        }
        if(intent.hasExtra("account-result")) {
            installWindow=false;stopInstallWatch(); // a late background ready query must not replace the result
            if(intent.getBooleanExtra("account-result",false))clearAction();
            else publishAction("failed",getString(R.string.state_prepare_failed),getString(R.string.notification_failed));
        }
        if(intent.getBooleanExtra("failure-viewed",false))acknowledgeFailure();
        String state=intent.getStringExtra("state");
        if(state!=null) {
            publish(state,getString(state.equals(getString(R.string.state_account))?R.string.notification_account:R.string.notification_return));
            if(!state.equals(getString(R.string.state_preparing)))stopInstallWatch();
        }
        return START_NOT_STICKY;
    }
}
