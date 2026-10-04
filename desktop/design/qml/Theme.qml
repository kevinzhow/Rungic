// SPDX-License-Identifier: GPL-2.0-or-later
// Rungic's design tokens (docs/87): quiet like a chat app. Flat grounds, no cards or
// shadows; Breeze's neutrals only, blue for links and focus. Two looks, dark and light,
// following the system (SystemTheme) unless `mode` says otherwise.
// The canvas "Agent · App 设计", page 基础, draws every token here (Colors, Typography,
// Dimensions, Icons, Components); change both together.
pragma Singleton
import QtQuick
import com.rungic.design

QtObject {
    id: theme

    // "system" | "light" | "dark": an app's own choice (its settings), else the system's.
    property string mode: "system"
    readonly property bool dark: mode === "dark" || (mode === "system" && SystemTheme.dark)

    // ---- Colour ----
    // Contrast, checked on all four grounds (background, side, fill, fill2) in both looks:
    // every text colour 4.5:1 or better (WCAG AA); faint 3:1 or better (marks, not text).

    // Grounds, from the page outwards.
    readonly property color background: dark ? "#141618" : "#ffffff"     // pages, sheets
    readonly property color side: dark ? "#1b1d20" : "#f7f7f7"           // side panels, keyboard and attach panels
    readonly property color fill: dark ? "#232629" : "#f2f3f4"           // fields, the user's bubbles, list groups
    readonly property color fill2: dark ? "#2e3134" : "#e8e9ea"          // pressed fills, tracks, handles
    readonly property color line: dark ? "#2c2f32" : "#e3e4e5"           // hairlines, outlined buttons

    // Text.
    readonly property color text: dark ? "#fcfcfc" : "#232629"
    readonly property color dim: dark ? "#a1a9b1" : "#5e6975"            // secondary text, placeholders
    // Marks that must be seen but are not words: a pending step's ring, a switch knob's edge,
    // a picture's placeholder. Never text on fills.
    readonly property color faint: dark ? "#7b848c" : "#7a838b"
    // The strong fill (send, talk, primary buttons, a switch on) and what sits on it. Not
    // "onStrong": QML takes a name of "on" and a capital as a signal handler, and that token
    // stayed black (the state gallery caught the black icons on the strong fill).
    readonly property color strong: dark ? "#fcfcfc" : "#232629"
    readonly property color strongInk: dark ? "#141618" : "#ffffff"
    readonly property color link: dark ? "#3daee9" : "#206da3"           // links and the focus ring
    readonly property color negative: dark ? "#ec7480" : "#bf3445"
    readonly property color negativeInk: dark ? "#141618" : "#ffffff"   // on a negative fill
    readonly property color positive: dark ? "#3ec57a" : "#1b7743"
    readonly property color attention: dark ? "#f0b84a" : "#9a5b00"     // waiting for the user (an answer the Agent needs)
    readonly property color attentionFill: dark ? Qt.rgba(0.94, 0.72, 0.29, 0.10) : Qt.rgba(0.60, 0.36, 0, 0.08)
    readonly property color attentionLine: dark ? Qt.rgba(0.94, 0.72, 0.29, 0.35) : Qt.rgba(0.60, 0.36, 0, 0.35)
    readonly property color scrim: dark ? Qt.rgba(0, 0, 0, 0.55) : Qt.rgba(0, 0, 0, 0.32)

    // ---- Type (px) ----
    // One family (the system's, Noto Sans and Noto Sans SC) plus a mono for keys and logs.
    // Seven sizes, each with its line height; weights regular, medium and semibold only.
    readonly property string fontFamily: Qt.application.font.family
    readonly property string monoFamily: "Noto Sans Mono"
    readonly property int weightRegular: Font.Normal
    readonly property int weightMedium: Font.Medium       // the current conversation, the live transcript
    readonly property int weightStrong: Font.DemiBold     // headings, titles, primary buttons

    readonly property int headingSize: 24        // the empty state's question, semibold
    readonly property int headingLine: 32
    readonly property int heroSize: 20           // status headings in settings, semibold
    readonly property int heroLine: 30
    readonly property int liveSize: heroSize     // the live transcript while holding, medium
    readonly property int liveLine: heroLine
    readonly property int titleSize: 16          // the top bar's title and primary buttons, semibold
    readonly property int titleLine: 26
    readonly property int bodySize: 16           // conversation and rows
    readonly property int bodyLine: 26
    readonly property int calloutSize: 15        // settings prose, row values, the key field
    readonly property int calloutLine: 22
    readonly property int metaSize: 14           // "Worked through 2 steps", buttons, tiles, chips
    readonly property int metaLine: 20
    readonly property int labelSize: 13          // group labels, subtitles, notes
    readonly property int labelLine: 18
    readonly property int footSize: 12           // the hint under the composer; mono logs
    readonly property int footLine: 16
    readonly property int codeLine: 18           // mono at footSize: a command's last line, logs

    // ---- Space (px) ----
    // A 4 px scale with 2 for hairline gaps. `inset` is not a step: it centres a touch
    // control in the composer's bar, (field - touch) / 2.
    readonly property int spaceXxs: 2
    readonly property int spaceXs: 4
    readonly property int spaceS: 8
    readonly property int spaceM: 12
    readonly property int spaceL: 16
    readonly property int spaceXl: 20
    readonly property int spaceXxl: 24
    readonly property int space3xl: 32
    readonly property int space4xl: 48
    readonly property int inset: (field - touch) / 2
    readonly property int gutter: spaceXl        // page side margin
    readonly property int groupMargin: spaceL    // a list group's side margin

    // ---- Sizes (px) ----
    readonly property int touch: 44              // the smallest target: icon buttons, pills, nav items
    readonly property int controlS: 36           // a small icon button (copy, read aloud, show key)
    readonly property int controlCompact: 32     // text-only meta buttons
    readonly property int controlM: 48           // secondary buttons, file chips
    readonly property int controlL: 52           // rows, primary buttons, fields, the top bar
    readonly property int topBar: controlL
    readonly property int row: controlL
    readonly property int field: 56              // the composer's bar
    readonly property int fieldHot: 64           // the bar while held
    readonly property int tile: 84               // attach panel tiles
    readonly property int drawerWidth: 320
    readonly property int readingWidth: 680      // a conversation's column on wide screens

    // ---- Radii (px) ----
    // Boxes take a step of the scale; pills and circles are fully round (half their height).
    readonly property int radiusXs: 2            // bars: progress, handles, wave
    readonly property int radiusS: 8             // photo cells
    readonly property int radiusM: 12            // inputs, cards, chips, pictures, nav items
    readonly property int radiusL: 16            // list groups, tiles, suggestion buttons
    readonly property int radiusXl: 20           // sheets, the user's bubble
    readonly property int radiusItem: radiusM
    readonly property int radiusInput: radiusM
    readonly property int radiusGroup: radiusL
    readonly property int radiusSheet: radiusXl
    readonly property int radiusPill: touch / 2
    readonly property int radiusField: field / 2 // the composer (voice and text bars, also when it grows)

    // ---- Icons (px) ----
    // icons/*.svg: a 24 px grid, 1.7 stroke, round caps and joins, no fills but `stop`.
    readonly property int iconXs: 14             // inline with meta text (chevrons of MetaButton)
    readonly property int iconS: 16              // inline with body text: notes, pills, external links
    readonly property int iconM: 18              // small icon buttons, row chevrons, search, card kinds
    readonly property int iconL: 20              // nav items, primary buttons
    readonly property int icon: 22               // icon buttons: the default
    readonly property int iconXl: 26             // tiles, hold targets
    readonly property int iconHero: 32           // the status mark in a settings page's 72 px circle

    // ---- States ----
    readonly property real disabledOpacity: 0.4  // a disabled control's ink
    readonly property real pressScale: 0.94      // a round strong button while pressed
    readonly property real pressScaleWide: 0.98  // a wide one (primary)
    readonly property real pressShade: 0.18      // black over a picture while pressed
    readonly property int focusWidth: 2          // the keyboard focus ring: `link`, outside the shape
    readonly property int focusGap: 2
    readonly property int tapShown: 150          // a row keeps its pressed fill this long after a quick tap

    // ---- Motion (ms) ----
    // Short and eased out; nothing moves when the system asks for less.
    readonly property int quick: 100             // press fills, icon fades
    readonly property int brisk: 150             // switches, the voice bar, hold targets
    readonly property int normal: 200            // fades, message entry, chevrons
    readonly property int slide: 250             // drawers and sheets
    readonly property var easing: [0.33, 1, 0.68, 1, 1, 1]   // cubic-bezier(0.33, 1, 0.68, 1)

    // A colour at an opacity.
    function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }
}
