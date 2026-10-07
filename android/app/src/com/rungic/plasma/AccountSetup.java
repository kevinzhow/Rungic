package com.rungic.plasma;

import android.app.Activity;
import android.app.AlertDialog;
import android.text.InputType;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.text.method.PasswordTransformationMethod;
import java.util.function.Consumer;
import android.view.WindowManager;
import android.widget.*;
import org.json.JSONObject;
import java.util.concurrent.Executor;

/** First-run form. Passwords travel only over the root process stdin pipe. */
final class AccountSetup {
    interface Submit { void apply(String json) throws Exception; }
    static void show(Activity activity, Executor worker, Submit submit, Runnable done, Consumer<Exception> failed) {
        int padding=(int)(20*activity.getResources().getDisplayMetrics().density);
        LinearLayout fields=new LinearLayout(activity);
        fields.setOrientation(LinearLayout.VERTICAL);
        fields.setPadding(padding,padding,padding,padding);
        TextView explanation=new TextView(activity);
        explanation.setText(R.string.account_explanation);
        fields.addView(explanation);
        EditText username=new EditText(activity);
        username.setHint(R.string.account_username_hint);
        username.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        username.setSingleLine(true); username.setText(activity.getPreferences(Activity.MODE_PRIVATE).getString("setup-username","linux"));
        username.setImeOptions(EditorInfo.IME_ACTION_NEXT);
        username.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        addField(fields,username,R.string.account_username);
        EditText password=new EditText(activity);
        password.setHint(R.string.account_password_hint);
        password.setSingleLine(true);
        password.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);
        password.setTransformationMethod(PasswordTransformationMethod.getInstance());
        password.setSaveEnabled(false);
        password.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        password.setImeOptions(EditorInfo.IME_ACTION_NEXT);
        addField(fields,password,R.string.account_password);
        EditText confirmation=new EditText(activity);
        confirmation.setHint(R.string.account_confirm);
        confirmation.setSingleLine(true);
        confirmation.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);
        confirmation.setTransformationMethod(PasswordTransformationMethod.getInstance());
        confirmation.setSaveEnabled(false);
        confirmation.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        confirmation.setImeOptions(EditorInfo.IME_ACTION_DONE);
        addField(fields,confirmation,R.string.account_confirm);
        CheckBox visible=new CheckBox(activity); visible.setText(R.string.account_show_password);
        visible.setOnCheckedChangeListener((button,checked)->{
            password.setTransformationMethod(checked?null:PasswordTransformationMethod.getInstance());
            confirmation.setTransformationMethod(checked?null:PasswordTransformationMethod.getInstance());
            password.setSelection(password.length()); confirmation.setSelection(confirmation.length());
        }); fields.addView(visible);
        TextView message=new TextView(activity); fields.addView(message);
        ScrollView scroll=new ScrollView(activity); scroll.addView(fields);
        AlertDialog dialog=new AlertDialog.Builder(activity).setTitle(R.string.account_title)
            .setView(scroll).setPositiveButton(R.string.account_create,null)
            .setNegativeButton(R.string.leave_for_later,(d,w)->activity.finish()).create();
        dialog.setCancelable(false);
        dialog.setOnShowListener(ignored -> {
            dialog.getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
            dialog.getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
                String name=username.getText().toString().trim();
                String secret=password.getText().toString();
                if(!name.matches("[a-z][a-z0-9_-]{0,31}")) {username.setError(activity.getString(R.string.account_username_invalid));message.setText(R.string.account_username_invalid);return;}
                if(secret.codePointCount(0,secret.length())<8 || secret.getBytes(java.nio.charset.StandardCharsets.UTF_8).length>256
                        || secret.indexOf('\n')>=0 || secret.indexOf('\r')>=0 || secret.indexOf('\0')>=0) {
                    password.setError(activity.getString(R.string.account_password_invalid));message.setText(R.string.account_password_invalid);return;
                }
                if(!secret.equals(confirmation.getText().toString())) {confirmation.setError(activity.getString(R.string.account_password_mismatch));message.setText(R.string.account_password_mismatch);return;}
                final String payload;
                try {payload=new JSONObject().put("username",name).put("password",secret).toString();}
                catch(Exception e) {message.setText(R.string.account_prepare_failed);return;}
                activity.getPreferences(Activity.MODE_PRIVATE).edit().putString("setup-username",name).apply();
                password.setText(""); confirmation.setText(""); visible.setChecked(false);
                username.setEnabled(false);password.setEnabled(false);confirmation.setEnabled(false);
                dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);
                dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setEnabled(false);
                message.setText(R.string.account_setting_up);
                worker.execute(() -> {
                    try {
                        submit.apply(payload);
                        DesktopService.accountResult(activity,true);
                        activity.runOnUiThread(() -> {if(activity.isDestroyed())return; dialog.dismiss();done.run();});
                    } catch(Exception e) {
                        DesktopService.accountResult(activity,false);
                        activity.runOnUiThread(() -> {
                            if(activity.isDestroyed())return;
                            // system/account/setup.py's SetupError texts: matched, never shown.
                            if(ControlException.usernameTaken(e)) {
                                String reason=activity.getString(ControlException.usernameHomeExists(e)?
                                    R.string.account_home_exists:R.string.account_username_taken);
                                username.setError(reason);
                                message.setText(reason+"\n"+activity.getString(R.string.account_not_created));
                                DesktopService.failureViewed(activity);
                                username.setEnabled(true);password.setEnabled(true);confirmation.setEnabled(true);
                                dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true);
                                dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setEnabled(true);
                            } else { dialog.dismiss(); failed.accept(e); }
                        });
                    }
                });
            });
            confirmation.setOnEditorActionListener((view,action,event)->{
                if(action==EditorInfo.IME_ACTION_DONE) { dialog.getButton(AlertDialog.BUTTON_POSITIVE).performClick(); return true; }
                return false;
            });
        });
        dialog.show();
    }
    private static void addField(LinearLayout fields,EditText input,int label) {
        input.setId(View.generateViewId());
        TextView text=new TextView(fields.getContext()); text.setText(label); text.setLabelFor(input.getId());
        fields.addView(text); fields.addView(input);
    }
}
