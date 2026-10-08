package com.rungic.plasma;

import java.util.function.Consumer;

/** Text an Android input method sends to the focused Linux window through Rungic's own input
 * connection (the "Android keyboard" of the menu, and casting's keyboard mode). Input methods end
 * a composition in two ways: some commit the chosen characters with commitText, others set them as
 * the composing text and then call finishComposingText. Only commitText reached Linux before, so the
 * second kind lost what was chosen (Kevin 2026-10-08: both kinds have to work). Composing text
 * itself is not shown in Linux; it reaches Linux once, when it is committed or finished. */
final class ComposingText {
    private final Consumer<String> send;
    private String composing = "";

    ComposingText(Consumer<String> send) { this.send = send; }

    /** setComposingText: what the input method shows as not yet chosen. */
    void compose(CharSequence text) { composing = text == null ? "" : text.toString(); }

    /** commitText: this text replaces the composition. */
    void commit(CharSequence text) {
        composing = "";
        if (text != null && text.length() > 0) send.accept(text.toString());
    }

    /** finishComposingText (also when the connection closes): the composition is kept as it is. */
    void finish() {
        String text = composing;
        composing = "";
        if (!text.isEmpty()) send.accept(text);
    }
}
