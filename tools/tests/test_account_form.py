# SPDX-License-Identifier: MIT
"""Exercise the actual form callbacks with JVM view stand-ins, not Android rendering.

This protects the visible message binding and retry boundary; focus, popup, lockscreen
and actual Android lifecycle behavior remain device verification.
"""
import subprocess
from pathlib import Path
import xml.etree.ElementTree as ET

from test_install_notification import STUBS as SERVICE_STUBS

STUBS = {
'android/content/res/Resources.java': '''package android.content.res; public class Resources {public Metrics getDisplayMetrics(){return new Metrics();} public static class Metrics {public float density=1;}}''',
'android/content/SharedPreferences.java': '''package android.content; public class SharedPreferences {public String getString(String k,String d){return d;} public SharedPreferences edit(){return this;}public SharedPreferences putString(String k,String v){return this;}public void apply(){}}''',
'android/app/Activity.java': '''package android.app; import android.content.*;public class Activity extends Context {public static final int MODE_PRIVATE=0;public boolean destroyed=false;public android.content.res.Resources getResources(){return new android.content.res.Resources();}public SharedPreferences getPreferences(int m){return new SharedPreferences();}public void finish(){}public boolean isDestroyed(){return destroyed;}public void runOnUiThread(Runnable r){r.run();}}''',
'android/view/View.java': '''package android.view;import android.content.*;public class View {public static final int IMPORTANT_FOR_AUTOFILL_NO=0;int id;protected Context context;public boolean enabled=true;public View(Context c){context=c;}public Context getContext(){return context;}public void setId(int i){id=i;}public int getId(){return id;}public static int generateViewId(){return 1;}public void setEnabled(boolean v){enabled=v;}public interface Click {void click(View v);}Click click;public void setOnClickListener(Click c){click=c;}public void performClick(){if(enabled&&click!=null)click.click(this);}}''',
'android/view/WindowManager.java': '''package android.view;public class WindowManager {public static class LayoutParams {public static final int FLAG_SECURE=1,SOFT_INPUT_ADJUST_RESIZE=2;}}''',
'android/view/inputmethod/EditorInfo.java': '''package android.view.inputmethod;public class EditorInfo {public static final int IME_ACTION_NEXT=1,IME_ACTION_DONE=2;}''',
'android/text/InputType.java': '''package android.text;public class InputType {public static final int TYPE_CLASS_TEXT=1,TYPE_TEXT_FLAG_NO_SUGGESTIONS=2,TYPE_TEXT_VARIATION_PASSWORD=4;}''',
'android/text/method/PasswordTransformationMethod.java': '''package android.text.method;public class PasswordTransformationMethod {public static PasswordTransformationMethod getInstance(){return new PasswordTransformationMethod();}}''',
'android/widget/TextView.java': '''package android.widget;import android.content.*;import android.view.*;public class TextView extends View {String text="";public TextView(Context c){super(c);}public void setText(int id){text=context.getString(id);}public void setText(CharSequence s){text=s.toString();}public CharSequence getText(){return text;}public void setLabelFor(int i){}}''',
'android/widget/EditText.java': '''package android.widget;import android.content.*;public class EditText extends TextView {public String error;public EditText(Context c){super(c);}public void setHint(int i){}public void setInputType(int i){}public void setSingleLine(boolean b){}public void setImeOptions(int i){}public void setImportantForAutofill(int i){}public void setTransformationMethod(Object m){}public void setSaveEnabled(boolean b){}public void setSelection(int i){}public int length(){return text.length();}public void setError(CharSequence s){error=s.toString();}public interface Editor {boolean action(TextView v,int a,Object e);}public void setOnEditorActionListener(Editor l){}}''',
'android/widget/LinearLayout.java': '''package android.widget;import android.content.*;import android.view.*;import java.util.*;public class LinearLayout extends View {public static final int VERTICAL=1;public List<View> children=new ArrayList<>();public LinearLayout(Context c){super(c);}public void setOrientation(int o){}public void setPadding(int a,int b,int c,int d){}public void addView(View v){children.add(v);}}''',
'android/widget/ScrollView.java': '''package android.widget;import android.content.*;import android.view.*;public class ScrollView extends View {public View child;public ScrollView(Context c){super(c);}public void addView(View v){child=v;}}''',
'android/widget/CheckBox.java': '''package android.widget;import android.content.*;public class CheckBox extends TextView {public CheckBox(Context c){super(c);}public interface Changed {void changed(CheckBox b,boolean c);}Changed changed;public void setOnCheckedChangeListener(Changed c){changed=c;}public void setChecked(boolean b){if(changed!=null)changed.changed(this,b);}}''',
'android/app/AlertDialog.java': '''package android.app;import android.view.*;import android.widget.*;import android.content.*;public class AlertDialog {public static final int BUTTON_POSITIVE=1,BUTTON_NEGATIVE=2;public static AlertDialog last;public View view;public boolean dismissed;View positive,negative;java.util.function.Consumer<AlertDialog> onShow;public void setCancelable(boolean b){}public void setOnShowListener(java.util.function.Consumer<AlertDialog> l){onShow=l;}public Window getWindow(){return new Window();}public View getButton(int i){return i==1?positive:negative;}public void show(){last=this;onShow.accept(this);}public void dismiss(){dismissed=true;}public static class Window {public void addFlags(int i){}public void setSoftInputMode(int i){}}public static class Builder {AlertDialog d=new AlertDialog();public Builder(Context c){d.positive=new View(c);d.negative=new View(c);}public Builder setTitle(int i){return this;}public Builder setView(View v){d.view=v;return this;}public Builder setPositiveButton(int i,Object v){return this;}public interface Negative {void click(AlertDialog d,int which);}public Builder setNegativeButton(int i,Negative l){return this;}public AlertDialog create(){return d;}}}''',
'org/json/JSONObject.java': '''package org.json;public class JSONObject {public JSONObject(){}public JSONObject put(String k,String v){return this;}public String toString(){return "{}";}}''',
'com/rungic/plasma/DesktopService.java': '''package com.rungic.plasma;import android.content.*;public class DesktopService {static int failed,viewed;static void accountResult(Context c,boolean ok){if(!ok)failed++;}static void failureViewed(Context c){viewed++;}}''',
'com/rungic/plasma/AccountFormTest.java': r'''package com.rungic.plasma;
import android.app.*;import android.widget.*;import android.view.*;import java.util.*;
public class AccountFormTest {
static int submits,done,failed;
static void require(boolean v,String why){if(!v)throw new AssertionError(why);}
static AlertDialog form(String error) {
AccountSetup.show(new Activity(),Runnable::run,payload->{submits++;if(error!=null)throw new ControlException("account-setup",1,"lxc noise\n"+error+"\n");},()->done++,e->failed++);
return AlertDialog.last;}
static List<EditText> inputs(AlertDialog d){List<EditText> r=new ArrayList<>();for(View v:fields(d).children)if(v instanceof EditText)r.add((EditText)v);return r;}
static LinearLayout fields(AlertDialog d){return (LinearLayout)((ScrollView)d.view).child;}
static TextView message(AlertDialog d){List<View> c=fields(d).children;return (TextView)c.get(c.size()-1);}
static void fill(AlertDialog d,String name,String secret,String again){List<EditText> f=inputs(d);f.get(0).setText(name);f.get(1).setText(secret);f.get(2).setText(again);}
static void click(AlertDialog d){d.getButton(AlertDialog.BUTTON_POSITIVE).performClick();}
public static void main(String[] a){
for(String error:new String[]{"这个用户名已被使用","这个用户名的主目录已存在"}) {
AlertDialog d=form(error);fill(d,"linux","fake-test-pass","fake-test-pass");click(d);
String reason=R.values[error.contains("主目录")?R.string.account_home_exists:R.string.account_username_taken];
require(!d.dismissed&&failed==0&&done==0,"rejection preserves form");
require(message(d).getText().toString().equals(reason+"\n"+R.values[R.string.account_not_created]),"inline reason and next step without focus");
require(inputs(d).get(0).error.equals(reason),"field error preserved");
require(inputs(d).get(1).length()==0&&inputs(d).get(2).length()==0,"passwords cleared");
require(d.getButton(AlertDialog.BUTTON_POSITIVE).enabled&&inputs(d).get(0).enabled,"retry available");
}
require(DesktopService.failed==2&&DesktopService.viewed==2,"rejection replaces reminder and acknowledges displayed cause");
int prior=submits;
AlertDialog d=form(null);fill(d,"Bad Name","fake-test-pass","fake-test-pass");click(d);require(message(d).getText().equals(R.values[R.string.account_username_invalid]),"invalid name inline");
fill(d,"valid","short","short");click(d);require(message(d).getText().equals(R.values[R.string.account_password_invalid]),"invalid password inline");
fill(d,"valid","fake-test-pass","different-pass");click(d);require(message(d).getText().equals(R.values[R.string.account_password_mismatch]),"mismatch inline");require(submits==prior,"invalid form never submits");
fill(d,"valid","fake-test-pass","fake-test-pass");click(d);require(d.dismissed&&done==1,"valid retry completes");
d=form("unsupported failure");fill(d,"valid","fake-test-pass","fake-test-pass");click(d);require(d.dismissed&&failed==1,"other failure closes form and delegates");
// Submission result outlives the Activity: service still receives it, no dead UI callback.
for(String error:new String[]{null,"这个用户名已被使用"}) {
Activity gone=new Activity();final Runnable[] queued={null};int priorFailures=DesktopService.failed,priorViewed=DesktopService.viewed;
AccountSetup.show(gone,r->queued[0]=r,payload->{if(error!=null)throw new ControlException("account-setup",1,error);},()->{throw new AssertionError("dead Activity completed UI");},e->{throw new AssertionError("dead Activity error UI");});
d=AlertDialog.last;fill(d,"valid","fake-test-pass","fake-test-pass");click(d);gone.destroyed=true;queued[0].run();
require(DesktopService.failed==priorFailures+(error==null?0:1)&&DesktopService.viewed==priorViewed,"result persists, failure is not acknowledged before viewing");
}
System.out.println("PASS actual form inline rejection, localized reason, cleared passwords, retry and invalid-input boundary");
}}
''',
}


def test_actual_form_rejection_remains_visible_without_field_focus(tmp_path):
    # covers: install.account-setup/E2
    # covers: install.account-setup/E3
    root = Path(__file__).resolve().parents[2]
    source = root / 'android/app/src/com/rungic/plasma'
    common = {k: SERVICE_STUBS[k] for k in ('android/content/Context.java', 'android/content/Intent.java',
               'android/app/Notification.java', 'android/app/NotificationManager.java',
               'android/app/NotificationChannel.java', 'android/app/PendingIntent.java', 'android/service/notification/StatusBarNotification.java')}
    for name, content in {**common, **STUBS}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    # Actual localized resources feed the actual callback in both supported locales.
    for locale in ('values', 'values-zh-rCN'):
        resources = ET.parse(root / f'android/app/res/{locale}/strings.xml').getroot()
        strings = {node.attrib['name']: ''.join(node.itertext()).replace("\\'", "'") for node in resources.findall('string')}
        names = sorted(name for name in strings if name.startswith('account_') or name == 'leave_for_later')
        import json
        (tmp_path / 'com/rungic/plasma/R.java').write_text('package com.rungic.plasma;public class R {public static String[] values={' + ','.join(json.dumps(strings[n], ensure_ascii=False) for n in names) + '};public static class string {' + ''.join(f'public static final int {n}={i};' for i, n in enumerate(names)) + '}}')
        out = tmp_path / locale
        subprocess.run(['javac','-encoding','UTF-8','-d',str(out),*map(str,tmp_path.rglob('*.java')),
                        str(source/'AccountSetup.java'),str(source/'ControlException.java')],check=True,capture_output=True,text=True)
        result = subprocess.run(['java','-cp',str(out),'com.rungic.plasma.AccountFormTest'],check=True,capture_output=True,text=True,timeout=10)
        assert 'PASS actual form inline rejection' in result.stdout
