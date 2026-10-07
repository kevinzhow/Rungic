package com.rungic.plasma;

import java.io.IOException;

/** A failed root controller action. getMessage() is the diagnostic for logs; output is the
 * controller's own text, which the account form matches against system/account/setup.py's errors.
 * Plain Java (tests/ControlExceptionTest runs without Android). */
final class ControlException extends IOException {
    /** system/account/setup.py SetupError texts that mean "choose another username". */
    private static final String[] USERNAME_TAKEN={"这个用户名已被使用","这个用户名的主目录已存在"};
    final String action, output;
    final int exitCode;
    ControlException(String action, int exitCode, String output) {
        super("Control " + action + " failed (exit " + exitCode + ")" + (output.trim().isEmpty()?"":": " + output.trim()));
        this.action=action; this.exitCode=exitCode; this.output=output.trim();
    }
    /** The controller's last line: setup.py prints its error last, after any lxc-attach noise. */
    String lastLine() {
        String[] lines=output.split("\n");
        for(int i=lines.length-1;i>=0;i--) if(!lines[i].trim().isEmpty()) return lines[i].trim();
        return "";
    }
    static boolean usernameTaken(Throwable failure) {
        if(!(failure instanceof ControlException)) return false;
        String line=((ControlException)failure).lastLine();
        for(String text:USERNAME_TAKEN) if(line.equals(text)) return true;
        return false;
    }
    static boolean usernameHomeExists(Throwable failure) {
        return failure instanceof ControlException &&
            ((ControlException)failure).lastLine().equals(USERNAME_TAKEN[1]);
    }
    /** What a Toast may show: the controller's text, never the diagnostic wrapper. */
    static String userText(Throwable failure) {
        if(failure instanceof ControlException) {
            String line=((ControlException)failure).lastLine();
            if(!line.isEmpty()) return line;
        }
        return failure.getMessage();
    }
}
