// The floating window of the assistant's screen (docs/65).
//
// The surface covers the phone's screen and is transparent; the picture, the toolbar and the tab
// move inside it (Floater), and only they take touches. So a drag follows the finger frame by frame
// and every change of place or size is animated.
//
// Window: the live picture, looked at and not touched through. One finger moves it; let go with it
// a quarter past a side edge, or flick it so it would get there, and it tucks into a tab there. Two
// fingers pinch it between half and the full width of the phone; a pinch never tucks it. A tap, a drag or a pinch shows a toolbar of
// icons below the picture, a small gap away (above it near the bottom of the screen), which hides a
// few seconds later.
// Fullscreen (docs/research/97 §17, §21): the picture and its controls move into an ordinary
// fullscreen window, an app to KWin and Plasma Mobile (whose panels hide for it; what goes above a
// fullscreen app goes above it), and morph there from the floating place to the whole screen, on the
// blurred wallpaper. On a portrait screen the window's content is turned a quarter (the phone held
// sideways, its top to the left), not the phone: the desktop and its apps stay as they are; the picture as large as fits, the director's other screens in its column. The
// finger works the screen in the picture (FullTouch: tap, long press, drag, two-finger scroll); a
// swipe up from the bottom edge or a tap beside the picture shows the toolbar (leave, zoom, TV, close).
// All of it Linux: no Android view, so a PC's desktop gets the same.
// Tab: a handle on the edge; tap to bring the window back, drag to slide it along the edge.
// Caption (docs/88): while the assistant works on this screen, what it is doing now sits over the
// bottom of the picture (its dot breathes on the tab); how it ended shows for a few seconds.
// While a TV or the phone's fullscreen presents the screen everything hides; then it comes back.
import QtCore
import QtQuick
import QtQuick.Effects
import QtQuick.Window
import org.kde.kirigami as Kirigami
import org.kde.pipewire as PipeWire

Window {
    id: root
    // Shown once main.cpp has made it a layer surface.
    property bool ready: false
    // The screen this window shows: its workspace's, or the director's focus (docs/58).
    readonly property QtObject screen: director ? director.focusScreen : agent
    readonly property bool directing: !!director
    readonly property int others: directing ? director.screens.length - 1 : 0
    visible: ready && !!root.screen && root.screen.status !== "tv" && root.screen.status !== "off"
    title: "rungic-agent-screen"
    color: "transparent"

    property string mode: "window"      // window | tab | fullscreen
    readonly property bool full: mode === "fullscreen"
    property string edge: "right"
    property real px: 12
    // Desktop mode's window above, the assistant's screen's below it: both may be out at once. A
    // team's workspaces 2, 3, 4 (docs/research/91) one under another from the top, all out at once.
    property real py: directing ? 110 : root.screen.workspace > 1 ? 50 + (root.screen.workspace - 2) * 165 : root.screen.workspace > 0 ? 330 : 110
    property real panelWidth: 260
    property real tabY: 180
    property bool toolbarShown: false
    property bool dragging: false
    property bool pinching: false
    readonly property rect area: floater.area
    readonly property real minWidth: area.width * 0.5
    // The director's other screens in a column on the right of its focus, live and small (docs/58),
    // as the TV lays them out. Fullscreen, the zoom button makes the focus larger: standard,
    // enlarged, alone (the director's level).
    readonly property real columnRatio: others < 1 ? 0 : !full ? 0.24 : [0.24, 0.15, 0][director.level] ?? 0.24
    // Desktop mode fullscreen is the user's second screen filling the phone, edge to edge: no
    // margin, corners, wallpaper or shadow (those are the assistant's screens' look); black bars
    // where the phone is longer than 16:9, and a tap there shows the toolbar.
    readonly property bool edgeToEdge: full && root.screen.workspace === 0
    // Fullscreen, as large as fits the phone with a margin.
    readonly property int fullMargin: edgeToEdge ? 0 : 12
    readonly property real fullWidth: Math.min(stage.width - 2 * fullMargin,
        ((stage.height - 2 * fullMargin) * 16 / 9 + (columnRatio > 0 ? 4 : 0)) / (1 - columnRatio))
    // Whole pixels: a picture at a fraction of one showed a line of the black under it (2026-10-02).
    readonly property real shownWidth: full ? Math.floor(fullWidth) : Math.round(panelWidth)
    readonly property real columnWidth: columnRatio === 0 ? 0 : Math.round(shownWidth * columnRatio)
    readonly property real pictureWidth: shownWidth - (columnRatio === 0 ? 0 : columnWidth + 4)
    readonly property real pictureHeight: Math.round(pictureWidth * 9 / 16)
    readonly property int pictureRadius: edgeToEdge ? 0 : 14
    readonly property real stripHeight: 0
    readonly property real panelHeight: pictureHeight + stripHeight
    readonly property int gap: 10
    readonly property int tabWidth: 26
    readonly property int tabHeight: 76
    readonly property int btnLeft: 0x110
    readonly property int btnRight: 0x111
    readonly property int btnMiddle: 0x112
    readonly property int motion: 240
    // The toolbar goes above the picture when there is no room below it.
    readonly property bool barAbove: py + panelHeight + gap + toolbar.height + 12 > area.height
    readonly property bool onLeftHalf: px + panelWidth / 2 < area.width / 2

    // Keep the window on the screen (animated back after a drag or a turn of the phone).
    function settle() {
        // From the area itself: in onAreaChanged, minWidth may still hold the old area's (turned back
        // from landscape, the window grew to half the landscape width, 2026-10-02).
        panelWidth = Math.max(area.width * 0.5, Math.min(area.width, panelWidth))
        px = Math.max(0, Math.min(area.width - panelWidth, px))
        py = Math.max(0, Math.min(area.height - panelHeight, py))
        tabY = Math.max(0, Math.min(area.height - tabHeight, tabY))
    }
    // Show the toolbar; it hides by itself a few seconds after the last touch.
    function showToolbar(ms) {
        if (mode === "tab")
            return
        toolbarShown = true
        hideTimer.interval = ms || 3000
        hideTimer.restart()
    }
    function tuck(side) {
        edge = side
        tabY = py + panelHeight / 2 - tabHeight / 2
        toolbarShown = false
        mode = "tab"
        settle()
    }
    function expand() {
        mode = "window"
        py = tabY + tabHeight / 2 - panelHeight / 2
        px = edge === "left" ? 8 : area.width - panelWidth - 8
        settle()
    }
    // ---- the director's focus changes (docs/58) ----------------------------------------------------
    // A screen's name: in a team the member's role and state (rungic_cua.team), else its number.
    function teamState(screen) {
        return ({ review: i18nc("@info:status a team member reviews the brief", "reviewing"),
                  progress: i18nc("@info:status a team member works", "working"),
                  blocked: i18nc("@info:status a team member cannot go on", "blocked"),
                  question: i18nc("@info:status a team member asks", "has a question"),
                  done: i18nc("@info:status a team member finished", "done"),
                  failed: i18nc("@info:status", "failed"),
                  ended: i18nc("@info:status a team member stopped", "ended") })[screen.teamKind] || ""
    }
    function screenName(screen, compact) {
        if (screen.workspace === 100)
            return i18nc("@label the team's board in the director", "Team board")
        if (screen.teamRole) {
            const state = teamState(screen)
            return state ? screen.teamRole + " · " + state : screen.teamRole
        }
        return compact ? String(screen.workspace)
                     : i18nc("@label name of an assistant's screen, %1 its number", "Assistant Screen %1", screen.workspace)
    }
    function needsAttention(screen) { return screen.teamKind === "blocked" || screen.teamKind === "question" }
    // Where a screen other than the focus sits in the column (0 first).
    function columnIndex(screen) {
        let at = 0
        for (const other of director.screens) {
            if (other === screen)
                return at
            if (other !== director.focusTile)
                at++
        }
        return 0
    }
    // ---- fullscreen (docs/research/97 §17, §21) ---------------------------------------------------
    // Its own window (fullWindow): an ordinary fullscreen window, so KWin's and the shell's stacking
    // works for it as for any fullscreen app (a layer surface in the overlay layer, as it was, covered
    // the keyboard, the dialogs and the notifications meant to be above one, §21). The stage (picture,
    // controls, touches, keyboard) moves into it and back. Neither way shows a frame without the
    // picture: the window the stage leaves keeps a still of it (the handoff) until the window it goes
    // to has drawn it, which costs ~110 ms there the first time (§17.2). Then the picture morphs from
    // its floating place, size and angle to fullscreen's while the background fades in; leaving plays
    // it backwards, and the fullscreen window goes after.
    property string backgroundUrl: ""
    property bool leaving: false         // the way back is playing
    property bool entering: false        // the fullscreen window maps; the stage not in it yet
    property bool stageInFull: false     // the stage is in the fullscreen window
    // Where the picture was on the screen when fullscreen started: where the morph starts.
    property var enterFrom: null
    // A still of the picture in the window the stage left, where the picture was on the screen, kept
    // there ("floater" or "full") until the other window has drawn the picture.
    property string handoffIn: ""
    property string handoffUrl: ""
    property var handoffGrab: null       // its url is good while the grab is kept
    property rect handoffRect: Qt.rect(0, 0, 0, 0)
    // Frames the window the stage went to draws before the still goes (handover()).
    property int handoffFrames: 0
    property var handoffDone: null
    // The fullscreen window has a frame on the screen: until then it is not (on the phone its first
    // frame came ~0.6 s after it was shown, and the stage was gone from both windows meanwhile).
    property bool fullDrawn: false
    // The system's pointer in the picture: fullscreen's touchpad mode on desktop mode (an assistant's
    // screen's picture always has the agent's pointer).
    readonly property bool pointerWanted: full && !leaving && fullscreenSettings.touchpad
                                          && !!root.screen && root.screen.workspace === 0
    // Its picture goes a moment after it is not wanted: the picture without the pointer, paused under
    // it (a second screencast for nothing, docs/research/97 §20), has to have a frame again first.
    property bool pointerHeld: false
    onPointerWantedChanged: {
        if (pointerWanted) {
            pointerRelease.stop()
            pointerHeld = true
        } else {
            pointerRelease.restart()
        }
    }
    Timer { id: pointerRelease; interval: 400; onTriggered: root.pointerHeld = false }
    onPointerHeldChanged: if (root.screen) root.screen.setPointerShown(pointerHeld)
    // Fullscreen's touch mode, remembered (the APK's agent_fullscreen_touchpad).
    Settings {
        id: fullscreenSettings
        location: StandardPaths.writableLocation(StandardPaths.ConfigLocation) + "/rungic-agent-screenrc"
        category: "Fullscreen"
        property bool touchpad: false
    }
    property bool morphing: false        // layout changes at once: the picture's transform moves it
    // Arrived: opaque all over (the background under everything), so the phone's KWin draws nothing
    // of Plasma Mobile under it (docs/research/97 §20); see-through again from the moment it leaves.
    readonly property bool opaque: full && !leaving && !morphing && visible && backdrop.opacity >= 1
    onOpaqueChanged: floater.setOpaque(fullWindow, opaque)
    // The picture's transform: from where it was (relative to its new place) at 0, to none at 1.
    property real morphX: 0
    property real morphY: 0
    property real morphAngle: 0
    property real morphScale: 1
    property real morphT: 1
    function setFullscreen() {
        if (full || entering || morphing)
            return
        toolbarShown = false
        backgroundUrl = root.screen.backgroundFile()
        const centre = panel.mapToItem(null, panel.width / 2, panel.height / 2)
        enterFrom = { x: centre.x, y: centre.y, width: panel.width, angle: 0 }
        const at = panel.mapToItem(null, 0, 0)
        entering = true
        console.info("fullscreen: entering")
        const grabbing = panel.grabToImage(result => {
            console.info("fullscreen: still taken, the fullscreen window maps")
            if (!root.entering)
                return
            root.handoffGrab = result
            root.handoffUrl = result.url
            root.handoffRect = Qt.rect(at.x, at.y, panel.width, panel.height)
            root.handoffIn = "floater"
            floater.showFullscreen(fullWindow)
            root.fullWindowReady()
        })
        if (!grabbing) {
            console.warn("fullscreen: no still of the picture")
            entering = false
        }
    }
    // The fullscreen window is on the screen at its size: the stage goes in, laid out for fullscreen
    // and transformed to where the picture was; the morph starts once it is drawn there.
    function fullWindowReady() {
        if (!entering || stageInFull || !fullWindow.visible || !fullDrawn
                || fullWindow.width !== area.width || fullWindow.height !== area.height)
            return
        console.info("fullscreen: the stage goes into the fullscreen window", fullWindow.width, fullWindow.height)
        morphing = true
        stageInFull = true
        mode = "fullscreen"
        morphTo(enterFrom)
        handover(fullWindow, () => {
            console.info("fullscreen: drawn there, the morph starts")
            root.entering = false
            morphAnim.from = 0
            morphAnim.to = 1
            morphAnim.restart()
        })
    }
    // The still goes after `window` has drawn two more frames: the frame the change was synced into
    // is then on the screen (frameSwapped comes queued from the render thread).
    function handover(window, done) {
        handoffFrames = 2
        handoffDone = done
        window.update()
    }
    function frameDrawn(window) {
        if (handoffFrames <= 0 || window !== (stageInFull ? fullWindow : root))
            return
        if (--handoffFrames > 0) {
            window.update()
            return
        }
        handoffIn = ""
        handoffUrl = ""
        handoffGrab = null
        const done = handoffDone
        handoffDone = null
        if (done)
            done()
    }
    onFrameSwapped: frameDrawn(root)
    // The transform that puts the picture, laid out where it is now, where `from` says on the screen.
    function morphTo(from) {
        const here = Qt.point(panel.x + panel.width / 2, panel.y + panel.height / 2)
        const there = stage.mapFromItem(null, from.x, from.y)
        morphX = there.x - here.x
        morphY = there.y - here.y
        morphAngle = from.angle - stage.rotation
        morphScale = from.width / panel.width
        morphT = 0
    }
    // The floating window's picture on the screen, as the window lays it out.
    function windowedCentre() {
        const w = Math.round(panelWidth)
        const column = others < 1 ? 0 : Math.round(w * 0.24) + 4
        const h = Math.round((w - column) * 9 / 16)
        return { x: Math.round(px) + w / 2, y: Math.round(py) + h / 2, width: w, angle: 0 }
    }
    function leaveFullscreen() {
        if (!full || leaving || entering)
            return
        toolbarShown = false
        fullTouch.reset()
        leaving = true
        morphing = true
        morphTo(windowedCentre())
        morphAnim.from = 1
        morphAnim.to = 0
        morphAnim.restart()
    }
    // The way back has played: a still where the picture now is, in the fullscreen window, while the
    // stage goes back to this one; the fullscreen window goes once this one has drawn it.
    function leaveNow() {
        const to = windowedCentre()
        const height = panel.height * morphScale
        panel.grabToImage(result => {
            if (!root.leaving)
                return
            root.handoffGrab = result
            root.handoffUrl = result.url
            root.handoffRect = Qt.rect(to.x - to.width / 2, to.y - height / 2, to.width, height)
            root.handoffIn = "full"
            root.backToWindow()
            root.handover(root, () => {
                console.info("fullscreen: left")
                fullWindow.hide()
                root.morphing = false
            })
        })
    }
    function backToWindow() {
        stageInFull = false
        mode = "window"
        morphT = 1
        morphX = morphY = morphAngle = 0
        morphScale = 1
        leaving = false
    }
    // Out at once, without the way back: this window hidden (a TV shows the screen) or the
    // fullscreen window closed before it arrived.
    function dropFullscreen() {
        if (!full && !entering)
            return
        morphAnim.stop()
        entering = false
        handoffFrames = 0
        handoffDone = null
        handoffIn = ""
        handoffUrl = ""
        handoffGrab = null
        fullTouch.reset()
        backToWindow()
        morphing = false
        fullWindow.hide()
    }
    onVisibleChanged: if (!visible) dropFullscreen()
    NumberAnimation {
        id: morphAnim
        target: root
        property: "morphT"
        duration: 320
        easing.type: Easing.OutCubic
        onFinished: {
            if (root.leaving) {
                root.leaveNow()
                return
            }
            root.morphing = false
            // Where the toolbar is: it shows a second on arriving, then fades and slides away.
            root.showToolbar(1000)
        }
    }
    // ---- the fullscreen window (§21): the stage in it while fullscreen; the keyboard is its ---------
    Window {
        id: fullWindow
        transientParent: null
        title: root.screen && root.screen.workspace === 0 ? i18nc("@label name of the user's second screen", "Desktop")
                                                           : i18nc("@label name of the agent's screen", "Assistant Screen")
        flags: Qt.FramelessWindowHint
        color: "transparent"
        visible: false
        onVisibleChanged: if (!visible) root.fullDrawn = false
        onWidthChanged: root.fullWindowReady()
        onHeightChanged: root.fullWindowReady()
        onFrameSwapped: {
            root.frameDrawn(fullWindow)
            if (visible && !root.fullDrawn) {
                root.fullDrawn = true
                root.fullWindowReady()
            }
        }
        // Closed from outside (the task switcher): back to the floating window, the screen kept on.
        onClosing: (close) => {
            close.accepted = false
            if (root.full)
                root.leaveFullscreen()
            else
                root.dropFullscreen()
        }
        // Esc leaves fullscreen.
        Item {
            id: escapeKey
            focus: true
            Keys.onEscapePressed: root.leaveFullscreen()
            Keys.onBackPressed: root.leaveFullscreen()
        }
        TextInput {
            id: keyboardField
            width: 1; height: 1
            opacity: 0
            enabled: root.typing
            // Always empty: no capital at its start, no prediction of what it never keeps.
            inputMethodHints: Qt.ImhNoAutoUppercase | Qt.ImhNoPredictiveText | Qt.ImhNoTextHandles
            onTextEdited: {
                if (text.length === 0)
                    return
                const held = !!root.keyboard && (root.keyboard.ctrl || root.keyboard.alt)
                const code = root.letterCodes[text.toLowerCase()]
                if (held && code !== undefined)
                    root.sendKey(code)
                else
                    root.screen.typeText(text)
                text = ""
            }
            Keys.onPressed: (event) => {
                const code = root.keyCodes[event.key]
                if (code === undefined || (event.key === Qt.Key_Backspace && text.length > 0))
                    return
                root.sendKey(code)
                event.accepted = true
            }
        }
        Image {  // the still of the picture on the way back (leaveNow)
            z: 10
            visible: root.handoffIn === "full"
            source: visible ? root.handoffUrl : ""
            cache: false
            x: root.handoffRect.x; y: root.handoffRect.y
            width: root.handoffRect.width; height: root.handoffRect.height
        }
    }
    // The still of the picture until the fullscreen window has drawn it (setFullscreen); over the
    // floating window's own picture meanwhile, and alone once the stage has gone.
    Image {
        z: 10
        visible: root.handoffIn === "floater"
        source: visible ? root.handoffUrl : ""
        cache: false
        x: root.handoffRect.x; y: root.handoffRect.y
        width: root.handoffRect.width; height: root.handoffRect.height
    }
    // ---- desktop mode on a TV (docs/research/97 §19.5) ----------------------------------------------
    // Computer mode: the TV shows the phone KWin's cast output (CAST-n, the host presents it); a
    // surface over all of it shows the independent desktop, and the phone as the TV's touchpad and
    // keyboard (the cast controls) reaches it: the host's pointer and text land here and go on into
    // the desktop. The pointer seen is the phone KWin's, where the desktop's is: its picture has none.
    readonly property bool tvWanted: !!root.screen && root.screen.workspace === 0 && root.screen.status === "tv"
                                     && floater.castPresent
    property var tvWindow: null
    onTvWantedChanged: {
        if (root.screen)
            root.screen.setTvShown(tvWanted)
        if (tvWanted && !tvWindow) {
            tvWindow = tvComponent.createObject(root)
        } else if (!tvWanted && tvWindow) {
            tvWindow.destroy()
            tvWindow = null
        }
    }
    Component {
        id: tvComponent
        Window {
            id: tv
            transientParent: null
            title: i18nc("@title:window desktop mode on the TV", "Desktop on the TV")
            color: "black"
            visible: false
            Component.onCompleted: if (floater.placeOnCast(tv)) tv.visible = true
            PipeWire.PipeWireSourceItem {
                anchors.fill: parent
                nodeId: root.screen ? root.screen.tvNodeId : 0
                visible: nodeId > 0
            }
            MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.AllButtons
                function code(button) { return button === Qt.RightButton ? root.btnRight : button === Qt.MiddleButton ? root.btnMiddle : root.btnLeft }
                onPositionChanged: (mouse) => root.screen.pointerMove(mouse.x / width, mouse.y / height)
                onPressed: (mouse) => { root.screen.pointerMove(mouse.x / width, mouse.y / height); root.screen.pointerButton(code(mouse.button), true) }
                onReleased: (mouse) => root.screen.pointerButton(code(mouse.button), false)
                // A notch (120) is KWin's 15 axis units; up is negative there.
                onWheel: (wheel) => root.screen.scroll(-wheel.angleDelta.x / 8, -wheel.angleDelta.y / 8)
            }
            TextInput {
                width: 1; height: 1
                opacity: 0
                focus: true
                onTextEdited: if (text.length > 0) { root.screen.typeText(text); text = "" }
                Keys.onPressed: (event) => {
                    const code = root.keyCodes[event.key]
                    if (code === undefined || (event.key === Qt.Key_Backspace && text.length > 0))
                        return
                    root.screen.key(code, true)
                    root.screen.key(code, false)
                    event.accepted = true
                }
            }
        }
    }
    // ---- typing into the screen (fullscreen's keyboard button, docs/research/97 §19.4, §19.9) --------
    // A field of its own (keyboardField, in the fullscreen window), invisible, takes the window's
    // keyboard (FloatingKeyboard, in the stage):
    // what it commits goes to the screen's focused field as an input method commits it (any
    // language), and is cleared; the keys it does not type (Backspace on an empty field, Enter,
    // arrows...) go there as keys. Linux key codes (input-event-codes.h).
    readonly property var keyCodes: ({
        [Qt.Key_Backspace]: 14, [Qt.Key_Return]: 28, [Qt.Key_Enter]: 28, [Qt.Key_Tab]: 15, [Qt.Key_Escape]: 1,
        [Qt.Key_Left]: 105, [Qt.Key_Right]: 106, [Qt.Key_Up]: 103, [Qt.Key_Down]: 108, [Qt.Key_Delete]: 111,
        [Qt.Key_Home]: 102, [Qt.Key_End]: 107, [Qt.Key_PageUp]: 104, [Qt.Key_PageDown]: 109 })
    // With the keyboard's Ctrl or Alt held, a letter or digit goes as its key (Ctrl+C, Alt+F4...).
    readonly property var letterCodes: ({
        q: 16, w: 17, e: 18, r: 19, t: 20, y: 21, u: 22, i: 23, o: 24, p: 25, a: 30, s: 31, d: 32, f: 33, g: 34,
        h: 35, j: 36, k: 37, l: 38, z: 44, x: 45, c: 46, v: 47, b: 48, n: 49, m: 50,
        "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11, " ": 57 })
    readonly property Item keyboard: keyboardLoader.item
    // A key into the screen, with the keyboard's held Ctrl and Alt (let go after it).
    function sendKey(code) {
        const mods = !keyboard ? [] : [].concat(keyboard.ctrl ? [29] : [], keyboard.alt ? [56] : [])
        mods.forEach(m => root.screen.key(m, true))
        root.screen.key(code, true)
        root.screen.key(code, false)
        mods.reverse().forEach(m => root.screen.key(m, false))
        if (keyboard) {
            keyboard.ctrl = false
            keyboard.alt = false
        }
    }
    property bool typing: false
    onFullChanged: {
        if (!full)
            typing = false
        if (root.screen)
            root.screen.setFullscreen(full)   // desktop mode's: what its quick setting shows
    }
    onTypingChanged: {
        if (typing) {
            keyboardField.forceActiveFocus()
            Qt.inputMethod.show()
        } else {
            escapeKey.forceActiveFocus()
            Qt.inputMethod.hide()
        }
    }
    Component.onCompleted: {
        panelWidth = area.width * 0.72
        settle()
        followActivity()
    }
    onAreaChanged: settle()
    onReadyChanged: Qt.callLater(updateMask)

    // ---- caption: what the assistant is doing (docs/88) ---------------------------------------------
    // hidden, working, done, question, failed, stopped. An ending shows a few seconds, then hides.
    property string captionState: ""
    // A prompt waits in the screen while it is not fullscreen (rungic-workspace-stream's "prompting").
    readonly property bool prompting: !!root.screen && !!root.screen.prompting && !full
    function followActivity() {
        const state = root.screen.activityState
        captionState = ["working", "done", "question", "failed", "stopped"].indexOf(state) >= 0 ? state : ""
        if (captionState !== "" && captionState !== "working")
            endTimer.restart()
    }
    Connections { target: root.screen; function onActivityChanged() { root.followActivity() } }
    Timer { id: endTimer; interval: 4000; onTriggered: if (root.captionState !== "working") root.captionState = "" }
    // The dots breathe (1.4 s from bright to dim and back) in ten steps a second, not at every frame
    // of the phone's screen: each frame of this window has the phone's KWin composite the whole
    // screen again (docs/research/97 §20).
    property real breath: 1
    Timer {
        interval: 100
        repeat: true
        running: (root.captionState === "working" || root.prompting) && (caption.visible || tab.visible)
        onTriggered: root.breath = 0.5 + 0.5 * Math.cos(Date.now() / 1400 * 2 * Math.PI)
        onRunningChanged: if (!running) root.breath = 1
    }

    Timer {
        id: hideTimer
        interval: 3000
        onTriggered: if (root.dragging || root.pinching) restart(); else root.toolbarShown = false
    }

    // Only what is visible takes touches; fullscreen's are the fullscreen window's.
    function updateMask() {
        const rects = []
        if (stageInFull || entering)
            ;
        else if (mode === "window")
            rects.push(Qt.rect(panel.x, panel.y, panel.width, panel.height))
        else if (mode === "tab")
            rects.push(Qt.rect(tab.x, tab.y, tab.width, tab.height))
        if (toolbar.visible && !full && !entering)
            rects.push(Qt.rect(toolbar.x, toolbar.y, toolbar.width, toolbar.height))
        floater.setInputRects(rects)
    }
    readonly property string maskKey: [mode, stageInFull, entering, panel.x, panel.y, panel.width, panel.height, tab.x, tab.y,
                                       toolbar.visible, toolbar.x, toolbar.y, width, height].join()
    onMaskKeyChanged: Qt.callLater(updateMask)

    // ---- a capsule of icon buttons -----------------------------------------------------------------
    component Toolbar: Item {
        id: bar
        property bool shown: false
        property real slide: -8          // where it comes from while appearing
        property var actions: []
        signal used()
        width: row.implicitWidth + 16
        height: 40
        opacity: shown ? 1 : 0
        scale: shown ? 1 : 0.9
        visible: opacity > 0.01
        // Fades and slides in and out together.
        transform: Translate {
            y: bar.shown ? 0 : bar.slide
            Behavior on y { NumberAnimation { duration: bar.shown ? 180 : 280; easing.type: Easing.OutCubic } }
        }
        Behavior on opacity { NumberAnimation { duration: bar.shown ? 180 : 280; easing.type: Easing.OutCubic } }
        Behavior on scale { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }

        // Touches on the capsule stay here, between the buttons too.
        TapHandler { gesturePolicy: TapHandler.WithinBounds }
        // Dark translucent material. A real blur of what is behind needs KWin's blur effect, which
        // Plasma Mobile does not load (docs/65).
        Rectangle {
            anchors.fill: parent
            radius: height / 2
            color: Qt.rgba(0.11, 0.12, 0.15, 0.84)
            border.color: Qt.rgba(1, 1, 1, 0.16)
            border.width: 1
        }
        Row {
            id: row
            anchors.centerIn: parent
            spacing: 4
            Repeater {
                model: bar.actions
                delegate: Item {
                    width: bar.height; height: bar.height
                    Rectangle {
                        anchors.centerIn: parent
                        width: parent.height - 8; height: width; radius: width / 2
                        // Pressed, or on (a switch: the touchpad mode).
                        color: Qt.rgba(1, 1, 1, press.pressed ? 0.22 : modelData.checked ? 0.16 : 0)
                        border.color: modelData.checked ? Qt.rgba(1, 1, 1, 0.35) : "transparent"
                        border.width: 1
                        Behavior on color { ColorAnimation { duration: 90 } }
                    }
                    Kirigami.Icon {
                        anchors.centerIn: parent
                        width: 19; height: width
                        source: modelData.icon
                        color: "white"
                        isMask: true
                    }
                    TapHandler {
                        id: press
                        gesturePolicy: TapHandler.ReleaseWithinBounds
                        onTapped: { bar.used(); modelData.act() }
                    }
                }
            }
        }
    }

    Item {
        id: stage
        // In the fullscreen window while fullscreen (§21), in this one otherwise.
        parent: root.stageInFull ? fullWindow.contentItem : root.contentItem
        // Fullscreen on a portrait screen: landscape, turned a quarter clockwise about its centre.
        // Touches reach the items inside in their own (turned) coordinates.
        readonly property bool turned: root.full && !!parent && parent.height > parent.width
        readonly property real outerWidth: parent ? parent.width : 0
        readonly property real outerHeight: parent ? parent.height : 0
        width: turned ? outerHeight : outerWidth
        height: turned ? outerWidth : outerHeight
        x: (outerWidth - width) / 2
        y: (outerHeight - height) / 2
        rotation: turned ? 90 : 0

    // ---- fullscreen's background: the wallpaper, blurred and dimmed (rungic-agent-screen background)
    Rectangle {
        id: backdrop
        anchors.fill: parent
        color: root.edgeToEdge ? "black" : "#101215"
        opacity: root.full && !root.leaving ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
        Image {
            anchors.fill: parent
            visible: !root.edgeToEdge
            source: root.backgroundUrl
            fillMode: Image.PreserveAspectCrop
            cache: false
            asynchronous: true
        }
    }

    // ---- picture ---------------------------------------------------------------------------------
    Item {
        id: panel
        readonly property bool tucked: root.mode === "tab"
        x: root.full ? Math.round((stage.width - width) / 2)
           : tucked ? (root.edge === "left" ? -width * 0.6 : stage.width - width * 0.4) : Math.round(root.px)
        y: root.full ? Math.round((stage.height - height) / 2)
           : tucked ? root.tabY + root.tabHeight / 2 - height / 2 : Math.round(root.py)
        width: root.shownWidth
        height: root.panelHeight
        opacity: root.mode !== "tab" ? 1 : 0
        scale: tucked ? 0.6 : 1
        visible: opacity > 0.01
        // Follow the finger exactly while it is down; glide everywhere else.
        Behavior on x { enabled: !root.dragging && !root.pinching && !root.morphing; NumberAnimation { duration: root.motion; easing.type: Easing.OutCubic } }
        Behavior on y { enabled: !root.dragging && !root.pinching && !root.morphing; NumberAnimation { duration: root.motion; easing.type: Easing.OutCubic } }
        Behavior on width { enabled: !root.pinching && !root.morphing; NumberAnimation { duration: root.motion; easing.type: Easing.OutCubic } }
        Behavior on height { enabled: !root.pinching && !root.morphing; NumberAnimation { duration: root.motion; easing.type: Easing.OutCubic } }
        // Into and out of fullscreen: from where the picture was to where it is laid out (above).
        transform: [
            Scale {
                origin.x: panel.width / 2; origin.y: panel.height / 2
                xScale: root.morphScale + (1 - root.morphScale) * root.morphT
                yScale: xScale
            },
            Rotation { origin.x: panel.width / 2; origin.y: panel.height / 2; angle: root.morphAngle * (1 - root.morphT) },
            Translate { x: root.morphX * (1 - root.morphT); y: root.morphY * (1 - root.morphT) }
        ]
        Behavior on opacity { NumberAnimation { duration: root.motion } }
        Behavior on scale { NumberAnimation { duration: root.motion; easing.type: Easing.OutCubic } }

        // A soft shadow under the picture: it floats above the phone's screen.
        RectangularShadow {
            anchors.fill: picture
            opacity: root.edgeToEdge && !root.leaving ? 0 : 1
            visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
            radius: root.pictureRadius
            offset.y: 6
            blur: 28
            spread: 0
            color: Qt.rgba(0, 0, 0, 0.45)
        }
        Rectangle {
            id: picture
            anchors { left: parent.left; top: parent.top }
            width: root.pictureWidth
            height: root.pictureHeight
            radius: root.pictureRadius
            // Black only until there is a picture (it showed at the picture's edge). The director's
            // screens bring their own black (below): this one would stay behind a focus breathing
            // in, a black shadow around it.
            color: root.directing || (stream.visible && stream.ready) ? "transparent" : "black"
            // Rounded corners for the picture too; none edge to edge, nor its offscreen pass.
            layer.enabled: root.pictureRadius > 0
            layer.effect: MultiEffect {
                maskEnabled: true
                maskSource: roundMask
            }
            PipeWire.PipeWireSourceItem {
                id: stream
                anchors.fill: parent
                // The director's screens have a picture each (below), never switched.
                nodeId: root.directing ? 0 : root.screen.nodeId
                // Receiving only while seen (KPipeWire pauses a hidden one): not while the window is
                // hidden (on a TV) or the pointer's picture covers it.
                visible: nodeId > 0 && root.visible && !(root.pointerWanted && pointerStream.ready)
            }
            // Fullscreen's touchpad mode: the same picture with the system's pointer drawn in, over
            // the one without, shown once it has a frame (no black while it starts) and hidden before
            // it goes. Touchscreen mode and the floating window have no pointer.
            PipeWire.PipeWireSourceItem {
                id: pointerStream
                anchors.fill: parent
                nodeId: root.pointerHeld ? root.screen.pointerNodeId : 0
                // Visible to receive at all (KPipeWire takes frames only while it is), seen once ready.
                visible: nodeId > 0
                opacity: ready ? 1 : 0
            }
            Kirigami.Icon {
                anchors.centerIn: parent
                width: 32; height: 32
                visible: !root.directing && (!stream.visible || !stream.ready) && !(pointerStream.visible && pointerStream.ready)
                source: "video-display"
                color: "#99ffffff"
                isMask: true
            }
        }
        // ---- the director's screens (docs/58): each its own live picture, never switched, so a change
        // of focus only moves them (switching one picture's stream showed black while it connected):
        // the focus over the picture's place, the others in a column on the right. Tap one to focus it;
        // the new focus breathes in, from a little smaller to its size.
        Repeater {
            id: tiles
            model: root.directing ? director.screens : []
            delegate: Item {
                id: tile
                required property QtObject modelData
                readonly property bool focused: modelData === director.focusTile
                readonly property bool isBoard: modelData.workspace === 100
                readonly property int place: root.columnIndex(modelData)
                readonly property real thumbHeight: Math.min(Math.round(root.columnWidth * 9 / 16),
                    (root.pictureHeight - 4 * (root.others - 1)) / Math.max(1, root.others))
                readonly property real columnTop: (root.pictureHeight - (root.others * thumbHeight + (root.others - 1) * 4)) / 2
                x: focused ? 0 : root.pictureWidth + 4
                y: focused ? 0 : columnTop + place * (thumbHeight + 4)
                width: focused ? root.pictureWidth : root.columnWidth
                height: focused ? root.pictureHeight : thumbHeight
                visible: focused || root.columnWidth > 0
                layer.enabled: true
                layer.effect: MultiEffect { maskEnabled: true; maskSource: tileMask }
                Rectangle { id: tileMask; anchors.fill: parent; radius: tile.focused ? 14 : 8; visible: false; layer.enabled: true }
                Rectangle { anchors.fill: parent; color: "black" }
                PipeWire.PipeWireSourceItem {
                    anchors.fill: parent
                    nodeId: tile.modelData.nodeId
                    visible: nodeId > 0
                }
                Loader {  // the team's board (rungic_cua.team): drawn here, no picture
                    anchors.fill: parent
                    active: tile.isBoard
                    sourceComponent: TeamBoard { board: tile.modelData.board; compact: !tile.focused }
                }
                Text {  // a member whose workspace is not open yet (docs/58): its name for now
                    anchors.centerIn: parent
                    visible: tile.modelData.nodeId === 0 && !tile.isBoard
                    text: root.screenName(tile.modelData, !tile.focused)
                    color: Qt.rgba(1, 1, 1, 0.7)
                    font.pixelSize: tile.focused ? 16 : 9
                    width: parent.width - 8
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                }
                Rectangle {  // its number
                    visible: !tile.focused
                    anchors { left: parent.left; bottom: parent.bottom; margins: 3 }
                    width: Math.max(height, number.width + 6); height: number.implicitHeight + 2
                    radius: height / 2
                    color: root.needsAttention(tile.modelData) ? Qt.rgba(0.94, 0.70, 0.37, 0.92) : Qt.rgba(0.11, 0.12, 0.15, 0.8)
                    Text { id: number; anchors.centerIn: parent; text: root.screenName(tile.modelData, true); color: "white"; font.pixelSize: 9
                           width: Math.min(implicitWidth, tile.width - 12); elide: Text.ElideRight }
                }
                Rectangle {  // the agent at work there
                    visible: !tile.focused && tile.modelData.activityState === "working"
                    anchors { right: parent.right; top: parent.top; margins: 4 }
                    width: 6; height: 6; radius: 3
                    color: "#63d471"
                }
                TapHandler { enabled: !tile.focused && !root.full; onTapped: director.setFocus(tile.modelData.workspace) }
                onFocusedChanged: if (focused) breathe.restart()
                NumberAnimation on scale { id: breathe; running: false; from: 0.95; to: 1; duration: 260; easing.type: Easing.OutCubic }
            }
        }
        Rectangle {
            id: caption
            z: 2
            property string label: ""
            property color dot: "#63d471"
            property bool shown: false
            anchors { horizontalCenter: picture.horizontalCenter; bottom: picture.bottom; bottomMargin: 8 }
            width: Math.min(picture.width - 16, captionRow.implicitWidth + 20)
            height: captionText.implicitHeight + 10
            radius: Math.min(14, height / 2)
            color: Qt.rgba(0.11, 0.12, 0.15, 0.86)
            border.color: Qt.rgba(1, 1, 1, 0.16)
            border.width: 1
            opacity: shown ? 1 : 0
            visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: 180 } }
            // The prompt's caption opens fullscreen (the touch is the caption's alone: no toolbar).
            TapHandler {
                enabled: caption.state === "prompt"
                gesturePolicy: TapHandler.ReleaseWithinBounds
                onTapped: root.setFullscreen()
            }
            // polkit's prompt waits in the screen (desktop mode's apps, docs/research/97 §21): answered
            // there, in fullscreen. The board in focus says it all itself: no screen's caption over it.
            state: root.prompting ? "prompt"
                 : root.captionState === "" || (root.directing && director.focus === 100) ? "hidden" : root.captionState
            states: [
                State { name: "hidden"; PropertyChanges { caption.shown: false } },
                State { name: "prompt"; PropertyChanges { caption.shown: true; caption.dot: "#e0a83c"; caption.label: i18nc("@info:status an app of the desktop asks for the password; tap to answer it in fullscreen", "Authentication needed · tap to answer in fullscreen") } },
                State { name: "working"; PropertyChanges { caption.shown: true; caption.dot: "#63d471"; caption.label: root.screen.activityText || i18nc("@info:status the agent is at work on this screen", "Working") } },
                State { name: "done"; PropertyChanges { caption.shown: true; caption.dot: "#8ab4f8"; caption.label: root.screen.activityText ? i18nc("@info:status %1 is what the agent did", "Done · %1", root.screen.activityText) : i18nc("@info:status", "Done") } },
                State { name: "question"; PropertyChanges { caption.shown: true; caption.dot: "#e0a83c"; caption.label: root.screen.activityText ? i18nc("@info:status %1 is the agent's question", "Needs your answer · %1", root.screen.activityText) : i18nc("@info:status", "Needs your answer") } },
                State { name: "failed"; PropertyChanges { caption.shown: true; caption.dot: "#e0606d"; caption.label: root.screen.activityText ? i18nc("@info:status %1 is what the agent tried", "Didn't work · %1", root.screen.activityText) : i18nc("@info:status", "Didn't work") } },
                State { name: "stopped"; PropertyChanges { caption.shown: true; caption.dot: "#a1a9b1"; caption.label: i18nc("@info:status", "Stopped") } }
            ]
            Row {
                id: captionRow
                anchors { left: parent.left; leftMargin: 10; verticalCenter: parent.verticalCenter }
                spacing: 7
                Rectangle {
                    id: captionDot
                    anchors.verticalCenter: parent.verticalCenter
                    width: 7; height: 7; radius: 3.5
                    color: caption.dot
                    opacity: root.captionState === "working" ? 0.3 + 0.7 * root.breath : 1
                }
                Text {
                    id: captionText
                    // Sized from the picture, not from the capsule (whose width follows this text).
                    width: Math.min(implicitWidth, picture.width - 16 - 20 - 14)
                    text: caption.label
                    color: "white"
                    font.pixelSize: 12
                    elide: Text.ElideRight
                    wrapMode: Text.Wrap
                    maximumLineCount: 2
                }
            }
        }
        // Which screen this is, with the toolbar: desktop mode's and the assistant's may both be out.
        Rectangle {
            anchors { left: parent.left; top: parent.top; margins: 8 }
            opacity: root.toolbarShown ? 1 : 0
            visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: 180 } }
            width: nameText.implicitWidth + 16
            height: nameText.implicitHeight + 8
            radius: height / 2
            color: Qt.rgba(0.11, 0.12, 0.15, 0.86)
            Text {
                id: nameText
                anchors.centerIn: parent
                text: root.directing ? root.screenName(root.screen, false)
                    : root.screen.workspace > 0 ? i18nc("@label name of the agent's screen", "Assistant Screen") : i18nc("@label name of the user's second screen", "Desktop")
                color: "white"
                font.pixelSize: 12
            }
        }
        Rectangle {
            id: roundMask
            anchors.fill: picture
            radius: root.pictureRadius
            visible: false
            layer.enabled: true
        }

        // One finger moves, two pinch; a tap shows or hides the toolbar.
        DragHandler {
            id: drag
            enabled: !root.full
            target: null
            maximumPointCount: 1
            property point offset
            // The finger was lifted. A second finger often lands while the first is already moving
            // out fast: this ends then with both fingers still down, and the pinch follows (docs/65).
            property bool lifted: false
            // A finger left over from a pinch: when one finger of a pinch lifts, the pinch lets the
            // other go and this takes it at once (it has long moved past the drag distance since it
            // was pressed), jumps the window by all that way and tucks it on the let-go (logged on
            // the phone, Qt 6.10, docs/65). Such a finger neither moves nor tucks the window.
            property bool leftover: false
            onGrabChanged: (transition, point) => {
                if (transition === PointerDevice.GrabExclusive)
                    leftover = pinch.took(point)
                if (point.state === EventPoint.Released)
                    lifted = true
            }
            onActiveChanged: {
                if (active) {
                    offset = Qt.point(centroid.scenePressPosition.x - root.px, centroid.scenePressPosition.y - root.py)
                    lifted = false
                    root.dragging = true
                } else {
                    root.dragging = false
                    // The finger's grab is given up right after this: decide then.
                    const flick = centroid.velocity.x
                    Qt.callLater(() => drag.letGo(flick))
                }
                root.showToolbar()
            }
            // Only a let-go may tuck it: a quarter of it past a side edge, there or where a flick
            // carries it (~0.15 s of its speed).
            function letGo(flick) {
                if (lifted)
                    pinch.fingers = ({})            // its fingers are all up
                if (!lifted || leftover || pinch.active || root.mode !== "window") {
                    if (!pinch.active)
                        root.settle()
                    return
                }
                const ahead = root.px + (Math.abs(flick) > 900 ? flick * 0.15 : 0)
                if (ahead < -root.panelWidth / 4)
                    root.tuck("left")
                else if (ahead + root.panelWidth > root.area.width + root.panelWidth / 4)
                    root.tuck("right")
                else
                    root.settle()
            }
            onCentroidChanged: if (active && !leftover) {
                root.px = centroid.scenePosition.x - offset.x
                root.py = Math.max(0, Math.min(root.area.height - root.panelHeight, centroid.scenePosition.y - offset.y))
            }
        }
        PinchHandler {
            id: pinch
            enabled: !root.full
            target: null
            // The fingers it took, by id and press time (Android reuses the ids), until a drag ends.
            property var fingers: ({})
            function key(point) { return point.id + ":" + point.pressTimestamp }
            function took(point) { return fingers[key(point)] === true }
            onGrabChanged: (transition, point) => {
                if (transition === PointerDevice.GrabExclusive)
                    fingers[key(point)] = true
            }
            property real startWidth
            property point centre
            onActiveChanged: {
                root.pinching = active
                if (active) {
                    startWidth = root.panelWidth
                    centre = Qt.point(root.px + root.panelWidth / 2, root.py + root.panelHeight / 2)
                } else {
                    root.settle()
                }
                root.showToolbar()
            }
            onActiveScaleChanged: if (active) {
                root.panelWidth = Math.max(root.minWidth, Math.min(root.area.width, startWidth * activeScale))
                root.px = centre.x - root.panelWidth / 2
                root.py = centre.y - root.panelHeight / 2
            }
        }
        TapHandler { enabled: !root.full; onTapped: root.toolbarShown ? (root.toolbarShown = false) : root.showToolbar() }
    }

    // ---- fullscreen's touches (FullTouch: the APK's gestures, direct or touchpad) -----------------
    FullTouch {
        id: fullTouch
        anchors.fill: parent
        enabled: root.full && !root.morphing
        visible: root.full
        target: root.screen
        picture: Qt.rect(panel.x + picture.x, panel.y + picture.y, picture.width, picture.height)
        output: root.screen ? root.screen.outputSize() : Qt.size(1920, 1080)
        touchpad: fullscreenSettings.touchpad
        // The team's board in focus: no screen under it.
        inert: root.directing && director.focus === 100
        tileAt: function (point) {
            if (!root.directing)
                return null
            for (let i = 0; i < tiles.count; i++) {
                const tile = tiles.itemAt(i)
                if (tile && tile.visible && !tile.focused && within(Qt.rect(panel.x + tile.x, panel.y + tile.y, tile.width, tile.height), point))
                    return tile.modelData
            }
            return null
        }
        onToolbarWanted: root.showToolbar()
        onToolbarToggled: root.toolbarShown ? (root.toolbarShown = false) : root.showToolbar()
        onTileTapped: (screen) => director.setFocus(screen.workspace)
    }

    // ---- fullscreen's own keyboard (docs/research/97 §19.9): turned with the stage, floating --------
    Loader {
        id: keyboardLoader
        active: root.typing
        sourceComponent: FloatingKeyboard {
            area: stage
            place: root.screen && root.screen.workspace === 0 ? "desktop" : "screens"
            composing: keyboardField.preeditText
            onKeyWanted: (code) => root.sendKey(code)
            onHideWanted: root.typing = false
        }
    }

    Toolbar {
        id: toolbar
        // Gone at once when the picture starts to morph (it would turn with the stage).
        visible: opacity > 0.01 && !root.morphing
        shown: root.toolbarShown && root.mode !== "tab"
        // Fullscreen: in from and out to the bottom edge, further.
        slide: root.full ? 28 : root.barAbove ? 8 : -8
        x: root.full ? Math.round((stage.width - width) / 2)
           : Math.max(6, Math.min(stage.width - width - 6, panel.x + (panel.width - width) / 2))
        // Fullscreen: above the keyboard while it is at the bottom.
        y: root.full ? (root.keyboard ? Math.min(stage.height - height - 20, root.keyboard.y - height - 8) : stage.height - height - 20)
           : root.barAbove ? panel.y - root.gap - height : panel.y + panel.height + root.gap
        actions: root.full
            ? [{ icon: "view-restore", act: () => root.leaveFullscreen() }]
              .concat(root.directing && root.others > 0 ? [{ icon: "zoom-in", act: () => director.nextLevel() }] : [])
              // Touchpad or direct touch (the APK's fullscreen had it; remembered).
              .concat([{ icon: "input-touchpad", checked: fullscreenSettings.touchpad,
                         act: () => { fullscreenSettings.touchpad = !fullscreenSettings.touchpad } }])
              // The phone's keyboard into the screen.
              .concat([{ icon: "input-keyboard", checked: root.typing, act: () => { root.typing = !root.typing } }])
              .concat([{ icon: "video-television", act: () => { root.leaveFullscreen(); root.screen.castToTv() } },
                       { icon: "window-close", act: () => { root.leaveFullscreen(); root.screen.close() } }])
            : [{ icon: "view-fullscreen", act: () => root.setFullscreen() },
               { icon: "video-television", act: () => root.screen.castToTv() },
               { icon: root.onLeftHalf ? "go-previous" : "go-next", act: () => root.tuck(root.onLeftHalf ? "left" : "right") },
               { icon: "window-close", act: () => root.screen.close() }]
        onUsed: root.showToolbar()
    }

    // ---- tab on the edge -------------------------------------------------------------------------
    Rectangle {
        id: tab
        readonly property bool shown: root.mode === "tab"
        width: root.tabWidth
        height: root.tabHeight
        x: root.edge === "left" ? (shown ? 0 : -width) : (shown ? stage.width - width : stage.width)
        y: root.tabY
        opacity: shown ? 1 : 0
        visible: opacity > 0.01
        radius: 12
        color: Qt.rgba(0.11, 0.12, 0.15, 0.84)
        border.color: Qt.rgba(1, 1, 1, 0.2)
        border.width: 1
        Behavior on x { NumberAnimation { duration: root.motion; easing.type: Easing.OutCubic } }
        Behavior on opacity { NumberAnimation { duration: root.motion } }
        Kirigami.Icon {
            anchors.centerIn: parent
            width: 16; height: 16
            source: "video-display"
            color: "white"
            isMask: true
        }
        Rectangle {  // alive: green, starting: amber; breathes while the assistant works (docs/88)
            id: tabDot
            anchors { horizontalCenter: parent.horizontalCenter; top: parent.top; topMargin: 8 }
            width: 6; height: 6; radius: 3
            color: root.screen.status === "running" && !root.prompting ? "#63d471" : "#e0a83c"
            opacity: root.captionState === "working" || root.prompting ? 0.25 + 0.75 * root.breath : 1
        }
        TapHandler { onTapped: root.expand() }
        DragHandler {
            target: null
            xAxis.enabled: false
            property real offset
            onActiveChanged: if (active) offset = centroid.scenePressPosition.y - root.tabY
            onCentroidChanged: if (active)
                root.tabY = Math.max(0, Math.min(root.area.height - root.tabHeight, centroid.scenePosition.y - offset))
        }
    }
    }
}
