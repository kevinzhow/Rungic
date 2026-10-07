# SPDX-License-Identifier: MIT
"""Run the actual Android service on JVM API stand-ins, without an Activity or phone.

NotificationManager and controller are stand-ins. Files, state parser, service
callback, cancellation, and the Java ownership monitor run as implemented.
"""
import subprocess
from pathlib import Path

# covers: install.first-run-progress/E1
# covers: install.first-run-progress/E7
STUBS = {
'android/service/notification/StatusBarNotification.java': 'package android.service.notification;public class StatusBarNotification {int id;public StatusBarNotification(int i){id=i;}public int getId(){return id;}}',
'android/os/IBinder.java': 'package android.os; public interface IBinder {}',
'android/util/Log.java': 'package android.util; public class Log {public static int w(String a,String b,Throwable t){return 0;}}',
'android/R.java': 'package android; public class R {public static class drawable {public static final int ic_menu_view=1;}}',
'android/content/Intent.java': '''package android.content;
import java.util.*;
public class Intent {Map<String,Object> extras=new HashMap<>();public Intent(Context c,Class<?> cls){}
public Intent putExtra(String k,String v){extras.put(k,v);return this;}public Intent putExtra(String k,boolean v){extras.put(k,v);return this;}
public String getStringExtra(String k){return (String)extras.get(k);}public boolean hasExtra(String k){return extras.containsKey(k);}
public boolean getBooleanExtra(String k,boolean d){return extras.containsKey(k)?(boolean)extras.get(k):d;}}''',
'android/content/Context.java': '''package android.content;
import java.io.*;import android.app.*;
public class Context {public static File files;public static NotificationManager manager=new NotificationManager();
public static boolean refuseStart;public void startForegroundService(Intent i){if(refuseStart)throw new IllegalStateException("lifecycle refusal");}public File getFilesDir(){return files;}
public <T>T getSystemService(Class<T> cls){return cls.cast(manager);}public String getString(int id){return com.rungic.plasma.R.values[id];}}''',
'android/app/Service.java': '''package android.app;
import android.content.*;import android.os.*;
public class Service extends Context {public static final int START_NOT_STICKY=2;
public void onCreate(){}public void onDestroy(){}public IBinder onBind(Intent i){return null;}
public int onStartCommand(Intent i,int flags,int id){return 0;}public void startForeground(int id,Notification n){n.foreground=true;manager.notify(id,n);}}''',
'android/app/Notification.java': '''package android.app;
import android.content.*;
public class Notification {public static final int VISIBILITY_PUBLIC=1;public String channel,title,body;public int visibility=0;public boolean ongoing,onlyAlertOnce,foreground;public PendingIntent open;
public static class Builder {Notification n=new Notification();public Builder(Context c,String channel){n.channel=channel;}
public Builder setSmallIcon(int i){return this;}public Builder setContentTitle(String v){n.title=v;return this;}
public Builder setContentText(String v){n.body=v;return this;}public Builder setContentIntent(PendingIntent v){n.open=v;return this;}
public Builder setOnlyAlertOnce(boolean v){n.onlyAlertOnce=v;return this;}public Builder setOngoing(boolean v){n.ongoing=v;return this;}
public Builder setVisibility(int v){n.visibility=v;return this;}public Notification build(){return n;}}}''',
'android/app/NotificationManager.java': '''package android.app;
import java.util.*;
public class NotificationManager {public static final int IMPORTANCE_LOW=2,IMPORTANCE_DEFAULT=3;public int count;public Notification last;
public Map<Integer,Notification> notifications=new HashMap<>();public Map<String,NotificationChannel> channels=new HashMap<>();public synchronized void notify(int id,Notification n){count++;last=n;notifications.put(id,n);}public synchronized void cancel(int id){notifications.remove(id);}public android.service.notification.StatusBarNotification[] getActiveNotifications(){return notifications.keySet().stream().map(i->new android.service.notification.StatusBarNotification(i)).toArray(android.service.notification.StatusBarNotification[]::new);}
public void createNotificationChannel(NotificationChannel c){channels.put(c.id,c);}}''',
'android/app/NotificationChannel.java': '''package android.app;
public class NotificationChannel {public String id,name,description;public int importance;
public NotificationChannel(String i,String n,int v){id=i;name=n;importance=v;}public void setDescription(String v){description=v;}}''',
'android/app/PendingIntent.java': '''package android.app;
import android.content.*;
public class PendingIntent {public static final int FLAG_IMMUTABLE=1;
public static PendingIntent getActivity(Context c,int i,Intent intent,int f){return new PendingIntent();}}''',
'org/json/JSONObject.java': '''package org.json;
public class JSONObject {String value;public JSONObject(String v){value=v;}public boolean has(String k){return value.contains(k);}
public boolean optBoolean(String k,boolean d){return value.contains("\\\""+k+"\\\":true")?true:value.contains("\\\""+k+"\\\":false")?false:d;}
public boolean getBoolean(String k){if(!has(k))throw new IllegalArgumentException();return optBoolean(k,false);}}''',
'com/rungic/plasma/MainActivity.java': '''package com.rungic.plasma;
public class MainActivity {static int calls;static boolean fail;static String account="{\\\"configured\\\":false}";
static String control(String action)throws Exception {if(!Thread.holdsLock(MainActivity.class))throw new AssertionError("unowned controller");calls++;if(fail)throw new Exception("unreadable");return account;}}''',
'com/rungic/plasma/StartupScreen.java': '''package com.rungic.plasma;import android.content.*;
public class StartupScreen {static String message(Context c,FirstBootState s){return s.phase;}}''',
'com/rungic/plasma/NotificationServiceTest.java': r'''package com.rungic.plasma;
import android.content.*;import android.app.*;import java.io.*;import java.nio.file.*;import java.lang.reflect.*;
public class NotificationServiceTest {
static void require(boolean b){if(!b)throw new AssertionError();}
static void event(DesktopService s,Intent i){s.onStartCommand(i,0,1);}
static Intent intent(DesktopService s){return new Intent(s,DesktopService.class);}
static Notification action(){return Context.manager.notifications.containsKey(4)?Context.manager.notifications.get(4):Context.manager.notifications.get(3);}
static void write(String state)throws Exception {Files.write(new File(Context.files,"rungic-install-source.properties").toPath(),"RELEASE_ID=test\n".getBytes());Files.write(new File(Context.files,"rungic-install.properties").toPath(),("schema=2\nrelease=test\nstate="+state+"\nphase=rootfs\n").getBytes());}
static DesktopService start(String state)throws Exception {Context.files=Files.createTempDirectory("service-test").toFile();Context.manager=new NotificationManager();MainActivity.calls=0;MainActivity.fail=false;MainActivity.account="{\"configured\":false}";
write(state);DesktopService s=new DesktopService();s.onCreate();DesktopService.visibility(s,false,true);s.onStartCommand(new Intent(s,DesktopService.class).putExtra("visible",false).putExtra("installing",true),0,1);return s;}
static Object field(DesktopService s,String key)throws Exception {Field f=DesktopService.class.getDeclaredField(key);f.setAccessible(true);return f.get(s);}
static void check(DesktopService s)throws Exception {Method m=DesktopService.class.getDeclaredMethod("checkInstallation",long.class);m.setAccessible(true);m.invoke(s,(long)field(s,"generation"));}
public static void main(String[] args)throws Exception {
DesktopService s=start("installing");check(s);require(MainActivity.calls==0&&Context.manager.last.body.equals("rootfs"));write("ready");check(s);require(MainActivity.calls==1&&Context.manager.last.title.equals("state_prepared")&&Context.manager.last.channel.equals("install-action")&&Context.manager.last.visibility==Notification.VISIBILITY_PUBLIC);require(field(s,"installationPoll")==null);require(Context.manager.notifications.get(1).channel.equals("desktop"));require(!action().foreground&&!action().ongoing&&!action().onlyAlertOnce);
check(s);require(MainActivity.calls==1);
event(s,intent(s).putExtra("state","state_account"));require(action()!=null); // opening form is not completion
int actionCount=Context.manager.count;event(s,intent(s).putExtra("state","state_account"));require(action()!=null);
event(s,intent(s).putExtra("account-result",true));require(action()==null);s.onDestroy();
s=start("ready");MainActivity.account="{\"configured\":true}";check(s);require(field(s,"installationPoll")==null&&Context.manager.last.channel.equals("desktop")&&action()==null);s.onDestroy();
s=start("failed");check(s);require(MainActivity.calls==0&&field(s,"installationPoll")==null&&action().title.equals("state_prepare_failed"));
DesktopService.visibility(s,true,true);event(s,intent(s).putExtra("visible",true).putExtra("installing",true));require(action()!=null); // entering Activity alone is not failure viewed
 event(s,intent(s).putExtra("failure-viewed",true));require(action()==null);s.onDestroy();
s=start("ready");check(s);Notification reminder=action();event(s,intent(s).putExtra("account-result",false));require(action()!=reminder&&action().title.equals("state_prepare_failed")&&!action().onlyAlertOnce);
event(s,intent(s).putExtra("failure-viewed",true));require(action()!=null); // rendered while background: keep until actually foreground
DesktopService.visibility(s,true,false);event(s,intent(s).putExtra("visible",true).putExtra("installing",false));require(action()==null);s.onDestroy();
s=start("ready");check(s);event(s,intent(s).putExtra("failure-viewed",true));require(action()!=null); // unrelated failure acknowledgement cannot discard ready
DesktopService.visibility(s,true,false);event(s,intent(s).putExtra("visible",true).putExtra("installing",false));require(action()!=null);
event(s,intent(s).putExtra("account-result",true));require(action()==null);s.onDestroy();
s=start("ready");MainActivity.fail=true;int prior=Context.manager.count;check(s);require(Context.manager.count==prior&&field(s,"installationPoll")!=null);s.onDestroy();
s=start("installing");write("garbage");prior=Context.manager.count;check(s);require(Context.manager.count==prior&&MainActivity.calls==0);s.onDestroy();
s=start("ready");DesktopService current=s;Thread background;
synchronized(MainActivity.class) {background=new Thread(()->{try{check(current);}catch(Exception e){throw new RuntimeException(e);}});background.start();long end=System.currentTimeMillis()+1000;while(background.getState()!=Thread.State.BLOCKED&&System.currentTimeMillis()<end)Thread.yield();require(background.getState()==Thread.State.BLOCKED);DesktopService.visibility(s,true,true);}
background.join(1000);require(!background.isAlive()&&MainActivity.calls==0);s.onStartCommand(new Intent(s,DesktopService.class).putExtra("visible",true).putExtra("installing",true),0,1);require(field(s,"installationPoll")==null);s.onDestroy();
s=start("ready");s.onStartCommand(new Intent(s,DesktopService.class).putExtra("visible",false).putExtra("installing",false),0,1);check(s);require(MainActivity.calls==0&&field(s,"installationPoll")==null);s.onDestroy();
s=start("ready");check(s);s.onDestroy();DesktopService restored=new DesktopService();restored.onCreate();require(action()!=null);DesktopService.visibility(restored,true,false);event(restored,intent(restored).putExtra("failure-viewed",true));require(action()!=null);event(restored,intent(restored).putExtra("account-result",true));require(action()==null);restored.onDestroy();
s=start("failed");check(s);s.onDestroy();restored=new DesktopService();restored.onCreate();DesktopService.visibility(restored,true,false);event(restored,intent(restored).putExtra("failure-viewed",true));require(action()==null);restored.onDestroy();
s=start("ready");check(s);Context.refuseStart=true;DesktopService.accountResult(s,true);DesktopService.failureViewed(s);require(action()==null);Context.refuseStart=false;s.onDestroy(); // a committed result does not depend on restarting a service
s=start("ready");final DesktopService resultService=s;
synchronized(MainActivity.class) {background=new Thread(()->{try{check(resultService);}catch(Exception e){throw new RuntimeException(e);}});background.start();long end=System.currentTimeMillis()+1000;while(background.getState()!=Thread.State.BLOCKED&&System.currentTimeMillis()<end)Thread.yield();require(background.getState()==Thread.State.BLOCKED);event(s,intent(s).putExtra("account-result",false));}
background.join(1000);require(!background.isAlive()&&action().title.equals("state_prepare_failed")&&field(s,"installationPoll")==null);s.onDestroy(); // late ready query cannot replace a failed submission
System.out.println("PASS background ready, configured, failed, unknown, query error, foreground ownership and cancellation");
}}
''',
}

def test_actual_service_notifies_without_activity_and_stops_at_boundaries(tmp_path):
    root = Path(__file__).resolve().parents[2]
    source = root / 'android/app/src/com/rungic/plasma'
    for name, content in STUBS.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    names = ['state_preparing','notification_return','state_prepared','notification_account',
             'state_prepare_failed','notification_failed','state_account','notification_action_channel','notification_action_description']
    (tmp_path / 'com/rungic/plasma/R.java').write_text('package com.rungic.plasma; public class R {public static String[] values={' + ','.join('"'+n+'"' for n in names) + '}; public static class string {' + ''.join('public static final int '+n+'='+str(i)+';' for i,n in enumerate(names)) + '}}')
    out = tmp_path / 'classes'
    subprocess.run(['javac','-encoding','UTF-8','-d',str(out), *map(str,tmp_path.rglob('*.java')),
                    str(source/'DesktopService.java'), str(source/'FirstBootState.java')],check=True,capture_output=True,text=True)
    result = subprocess.run(['java','-cp',str(out),'com.rungic.plasma.NotificationServiceTest'],check=True,capture_output=True,text=True,timeout=10)
    assert 'PASS background ready' in result.stdout
