package com.rungic.plasma;

import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.regex.*;

/** Use case: a taken username keeps the account form open (docs/95). Crosses the contract
 * between system/account/setup.py's SetupError texts, the controller's merged output and the
 * APK's classification; no Android device needed. args[0]: path to system/account/setup.py. */
public final class ControlExceptionTest {
    static void require(boolean value,String what) { if(!value)throw new AssertionError(what); }
    // covers: install.account-setup/E3
    public static void main(String[] args) throws Exception {
        String setup=new String(Files.readAllBytes(Paths.get(args[0])),StandardCharsets.UTF_8);
        List<String> errors=new ArrayList<>();
        Matcher m=Pattern.compile("SetupError\\('([^']+)'\\)").matcher(setup);
        while(m.find())errors.add(m.group(1));
        List<String> taken=Arrays.asList("这个用户名已被使用","这个用户名的主目录已存在");
        require(errors.containsAll(taken),"setup.py no longer raises the texts the form matches: "+errors);
        for(String text:errors) {
            // What MainActivity.control() receives: merged stdout/stderr, lxc-attach noise first.
            for(String output:new String[]{text+"\n","lxc-attach: warning\n"+text+"\n\n"}) {
                ControlException failure=new ControlException("account-setup",1,output);
                require(ControlException.usernameTaken(failure)==taken.contains(text),"classification of "+text);
                require(ControlException.usernameHomeExists(failure)==text.equals("这个用户名的主目录已存在"),"home-directory reason of "+text);
                require(ControlException.userText(failure).equals(text),"toast text of "+text);
                require(failure.getMessage().contains("account-setup") && failure.getMessage().contains(text),"diagnostic of "+text);
            }
        }
        require(!ControlException.usernameTaken(new java.io.IOException("这个用户名已被使用")),"only controller failures classify");
        require(!ControlException.usernameTaken(new ControlException("account-setup",1,"")),"empty output");
        require(ControlException.userText(new ControlException("home",1,"")).startsWith("Control home"),"empty output falls back");
        System.out.println("PASS "+errors.size()+" setup.py errors: taken username keeps the form, others fail, toast shows controller text");
    }
}
