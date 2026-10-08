package com.rungic.plasma;

import java.util.ArrayList;
import java.util.List;

/** The two ways input methods end a composition both reach Linux, once (ComposingText). */
public final class ComposingTextTest {
    private static void expect(List<String> actual, String... wanted) {
        if (!actual.equals(List.of(wanted))) throw new AssertionError("sent " + actual + ", wanted " + List.of(wanted));
    }

    public static void main(String[] args) {
        // WeType and Gboard style: compose, then commit what was chosen.
        List<String> sent = new ArrayList<>();
        ComposingText text = new ComposingText(sent::add);
        text.compose("ni'hao");
        text.commit("你好");
        text.finish();                          // the connection closes: nothing composed is left
        expect(sent, "你好");

        // Input methods that set the chosen characters as the composition, then finish it.
        sent.clear();
        text.compose("nihao");
        text.compose("你好");
        text.finish();
        text.finish();                          // once only
        expect(sent, "你好");

        // An English word in progress, then the word and a space.
        sent.clear();
        text.compose("hel");
        text.compose("hello");
        text.commit("hello");
        text.commit(" ");
        expect(sent, "hello", " ");

        // Nothing composed, nothing committed: nothing sent.
        sent.clear();
        text.finish();
        text.commit("");
        text.compose(null);
        text.finish();
        expect(sent);
        System.out.println("PASS commitText and finishComposingText each reach Linux once");
    }
}
