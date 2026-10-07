// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <QString>
#include <QStringList>

// Whether what the application reports during a composition is only our own preedit coming back.
// Some editors (KTextEditor, so KWrite and Kate) count the preedit as part of the text around the
// cursor; over Wayland that reaches the keyboard as a change of the surrounding text, and Qt Virtual
// Keyboard calls the input method's update(), which commits a composition on a real outside change
// (the user moved the cursor or the text changed). Without this test every pinyin letter was committed
// as its first candidate (G100, 2026-10-07: "nihao" became "你i和啊哦"; docs/41).
// base/baseCursor: the surrounding text and cursor when the composition started. preedits: every
// preedit shown during this composition: the editor's report can come after the next key changed the
// preedit again ("ni" reported while "ni h" is shown), so an earlier one is an echo too.
inline bool preeditEcho(const QString &base, int baseCursor, const QStringList &preedits,
                        const QString &surrounding, int cursor) {
    if (baseCursor < 0 || baseCursor > base.size()) return false;
    if (surrounding == base && cursor == baseCursor) return true;             // the preedit kept apart
    for (const QString &preedit : preedits) {
        const QString withPreedit = base.left(baseCursor) + preedit + base.mid(baseCursor);
        if (surrounding == withPreedit && (cursor == baseCursor || cursor == baseCursor + preedit.size()))
            return true;
    }
    return false;
}
