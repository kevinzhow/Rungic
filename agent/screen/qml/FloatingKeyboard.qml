// Fullscreen's own keyboard (docs/research/97 §19.9, §19.10): Qt Virtual Keyboard with the phone
// keyboard's Rime and layouts (rungic-plasma-input), inside the stage, so it turns with the picture;
// the phone's keyboard (plasma-keyboard, placed by KWin at the phone's bottom) can neither turn nor
// float. Its look is ours (vkb/rungic/style.qml): Qt's styles are made for large screens.
//
// Floating, as a tablet's floating keyboard: moved by its bar (let go, it settles at the bottom's
// middle or a corner), resized with two fingers; pinched out to the full width it docks at the
// bottom, pinched in it floats again. Size, place and docking are remembered.
//
// Its bar, one of three states:
//   keys       nothing being composed: the keys a desktop needs and a phone keyboard has not (Esc,
//              Tab, Ctrl and Alt held for the next key, the arrows), docking and hiding;
//   composing  pinyin being typed: what is typed, then the candidates, large enough to read and
//              tap, in a row that scrolls; the arrow opens them all;
//   expanded   all the candidates in a grid over the keys.
// forcedState shows one of them whatever is typed (tests, design review).
//
// What it types goes to the focused field it is given (Main's keyboardField), as any input method's.
import QtCore
import QtQuick
import QtQuick.Effects
import QtQuick.VirtualKeyboard
import QtQuick.VirtualKeyboard.Settings
import org.kde.kirigami as Kirigami

Item {
    id: board
    required property Item area              // where it floats: the stage
    required property string place           // remembered apart: desktop mode, the assistant's screens
    property string composing: ""            // the field's preedit
    property bool ctrl: false
    property bool alt: false
    property string forcedState: ""
    signal keyWanted(int code)                // a remote key (Linux key code)
    signal hideWanted()

    readonly property var candidates: InputContext.inputEngine.wordCandidateListModel
    readonly property int candidateCount: candidates ? candidates.count : 0
    property bool expanded: false
    onCandidateCountChanged: if (candidateCount === 0) expanded = false
    readonly property string barState: forcedState
        || (expanded && candidateCount > 0 ? "expanded" : candidateCount > 0 || composing !== "" ? "composing" : "keys")

    // Sizes in the phone's logical pixels (about 5 to a millimetre).
    readonly property real pad: 4
    readonly property real barHeight: 40
    readonly property real candidateFont: 19
    // Floating: from a narrow phone keyboard to most of the stage; docked: the full width, flatter.
    readonly property real smallest: Math.min(300, area.width * 0.6)
    readonly property real largest: area.width * 0.78
    readonly property real standard: Math.max(smallest, Math.min(480, area.width * 0.52))
    property real floatWidth: standard
    property bool docked: false
    readonly property real keysAspect: docked ? 4.4 : 2.6
    // The bottom's strip, where a swipe up shows the toolbar (FullTouch, 3.2 mm), stays free.
    readonly property real bottomGap: docked ? 0 : 18
    width: docked ? area.width : Math.max(smallest, Math.min(largest, floatWidth))
    height: barHeight + panel.height + pad
    // Its place: the centre's share of the stage (it keeps it when the stage turns).
    property real cx: 0.5
    property real cy: 1
    x: docked ? 0 : clampX(cx * area.width - width / 2)
    y: docked ? area.height - height : clampY(cy * area.height - height / 2)
    function clampX(v) { return Math.max(6, Math.min(area.width - width - 6, v)) }
    function clampY(v) { return Math.max(6, Math.min(area.height - height - bottomGap, v)) }
    Behavior on x { enabled: !move.active && !pinch.active; NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
    Behavior on y { enabled: !move.active && !pinch.active; NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
    Behavior on width { enabled: !pinch.active; NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }

    Settings {
        category: "Keyboard-" + board.place
        location: StandardPaths.writableLocation(StandardPaths.ConfigLocation) + "/rungic-agent-screenrc"
        property alias floatWidth: board.floatWidth
        property alias docked: board.docked
        property alias cx: board.cx
        property alias cy: board.cy
    }
    // The phone keyboard's languages (plasmakeyboardrc, its settings page), read by the window
    // (phonekeyboard.h): only read, as QML's Settings would rewrite that KConfig file.
    property var phoneLocales: []
    function locales() {
        const list = [].concat(phoneLocales || []).filter(l => !!l)
        return list.length ? list : ["zh_CN", "en_US"]
    }
    function choose(index) {
        candidates.selectItem(index)
        expanded = false
    }

    // Let go: to the bottom's middle or a corner, whichever is nearest.
    function settle() {
        const half = width / 2 / area.width
        const spots = [half + 6 / area.width, 0.5, 1 - half - 6 / area.width]
        const centre = (x + width / 2) / area.width
        cx = spots.reduce((a, b) => Math.abs(b - centre) < Math.abs(a - centre) ? b : a)
        cy = 1
    }
    // The keyboard's own hide key (or the system hiding it): the keyboard goes.
    Connections {
        target: Qt.inputMethod
        function onVisibleChanged() { if (!Qt.inputMethod.visible) board.hideWanted() }
    }

    RectangularShadow {
        anchors.fill: background
        radius: background.radius
        blur: 20
        offset.y: 4
        color: Qt.rgba(0, 0, 0, 0.5)
        visible: !board.docked
    }
    Rectangle {
        id: background
        anchors.fill: parent
        radius: board.docked ? 0 : 12
        color: "#1c1e22"
        border.color: Qt.rgba(1, 1, 1, board.docked ? 0 : 0.1)
        border.width: 1
    }
    // Touches between the keys stay here (not to the picture below).
    TapHandler { gesturePolicy: TapHandler.WithinBounds }
    // Two fingers: its size; out past the largest docks it, in from docked floats it.
    PinchHandler {
        id: pinch
        target: null
        property real from
        onActiveChanged: {
            if (active) {
                from = board.docked ? board.area.width : board.width
            } else if (!board.docked) {
                board.cx = (board.x + board.width / 2) / board.area.width
                board.settle()
            }
        }
        onActiveScaleChanged: {
            const wanted = from * activeScale
            if (!board.docked && wanted > board.largest * 1.12) {
                board.docked = true
            } else if (board.docked && wanted < board.area.width * 0.85) {
                board.docked = false
                board.floatWidth = board.standard
            } else if (!board.docked) {
                board.floatWidth = Math.max(board.smallest, Math.min(board.largest, wanted))
            }
        }
    }

    // ---- the bar ------------------------------------------------------------------------------------
    component BarKey: Item {
        id: key
        property string icon: ""
        property string label: ""
        property bool checked: false
        property string accessibleName: label
        property string accessibleId: ""
        Accessible.role: Accessible.Button
        Accessible.name: accessibleName
        Accessible.id: accessibleId
        signal tapped()
        width: label ? Math.max(36, caption.implicitWidth + 16) : 36
        height: board.barHeight - 6
        Rectangle {
            anchors.fill: parent
            anchors.margins: 2
            radius: 7
            color: tap.pressed ? "#4b505a" : key.checked ? "#3daee9" : "transparent"
        }
        Kirigami.Icon {
            anchors.centerIn: parent
            visible: !!key.icon
            width: 18; height: width
            source: key.icon
            color: "white"
            isMask: true
        }
        Text {
            id: caption
            anchors.centerIn: parent
            visible: !!key.label
            text: key.label
            color: "white"
            font.pixelSize: 14
        }
        TapHandler { id: tap; gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: key.tapped() }
    }
    Item {
        id: bar
        anchors { left: parent.left; right: parent.right; top: parent.top; leftMargin: board.pad; rightMargin: board.pad }
        height: board.barHeight
        // Its free space moves the keyboard (not while docked).
        DragHandler {
            id: move
            target: null
            enabled: !board.docked
            property point offset
            onActiveChanged: {
                const p = board.area.mapFromItem(null, centroid.scenePosition.x, centroid.scenePosition.y)
                if (active)
                    offset = Qt.point(p.x - (board.x + board.width / 2), p.y - (board.y + board.height / 2))
                else
                    board.settle()
            }
            onCentroidChanged: {
                if (!active)
                    return
                const p = board.area.mapFromItem(null, centroid.scenePosition.x, centroid.scenePosition.y)
                board.cx = (p.x - offset.x) / board.area.width
                board.cy = (p.y - offset.y) / board.area.height
            }
        }
        Rectangle {  // the grip
            anchors { horizontalCenter: parent.horizontalCenter; top: parent.top; topMargin: 3 }
            width: 32; height: 3; radius: 1.5
            color: Qt.rgba(1, 1, 1, 0.25)
            visible: !board.docked && board.barState === "keys"
        }

        // keys: the desktop's keys | docking, hiding
        Row {
            id: desktopKeys
            visible: board.barState === "keys"
            anchors { left: parent.left; verticalCenter: parent.verticalCenter; verticalCenterOffset: 1 }
            BarKey { label: "Esc"; accessibleId: "esc"; onTapped: board.keyWanted(1) }
            BarKey { label: "Tab"; accessibleId: "tab"; onTapped: board.keyWanted(15) }
            BarKey { label: "Ctrl"; accessibleId: "ctrl"; checked: board.ctrl; onTapped: board.ctrl = !board.ctrl }
            BarKey { label: "Alt"; accessibleId: "alt"; checked: board.alt; onTapped: board.alt = !board.alt }
            BarKey { icon: "go-previous"; accessibleName: qsTr("Left"); accessibleId: "left"; onTapped: board.keyWanted(105) }
            BarKey { icon: "go-up"; accessibleName: qsTr("Up"); accessibleId: "up"; onTapped: board.keyWanted(103) }
            BarKey { icon: "go-down"; accessibleName: qsTr("Down"); accessibleId: "down"; onTapped: board.keyWanted(108) }
            BarKey { icon: "go-next"; accessibleName: qsTr("Right"); accessibleId: "right"; onTapped: board.keyWanted(106) }
        }
        Row {
            visible: board.barState === "keys"
            anchors { right: parent.right; verticalCenter: parent.verticalCenter; verticalCenterOffset: 1 }
            BarKey {
                icon: board.docked ? "window-restore" : "view-fullscreen"
                accessibleName: board.docked ? qsTr("Float keyboard") : qsTr("Dock keyboard")
                accessibleId: "dock"
                onTapped: { board.docked = !board.docked; if (!board.docked) { board.floatWidth = board.standard; board.settle() } }
            }
            BarKey { icon: "arrow-down"; accessibleName: qsTr("Hide keyboard"); accessibleId: "hide"; onTapped: board.hideWanted() }
        }

        // composing: what is typed, the candidates in a row, the arrow to all of them
        Text {
            id: typed
            visible: board.barState !== "keys"
            anchors { left: parent.left; leftMargin: 6; top: parent.top; topMargin: 1 }
            text: board.composing
            color: "#8ec5ff"
            font.pixelSize: 11
        }
        ListView {
            id: row
            visible: board.barState === "composing"
            anchors { left: parent.left; right: more.left; top: typed.bottom; bottom: parent.bottom }
            orientation: ListView.Horizontal
            clip: true
            model: board.barState === "composing" ? board.candidates : null
            boundsBehavior: Flickable.StopAtBounds
            delegate: Item {
                required property int index
                required property string display
                Accessible.role: Accessible.Button
                Accessible.name: display
                Accessible.id: "candidate"
                width: word.implicitWidth + 22
                height: row.height
                Rectangle {
                    anchors.fill: parent
                    anchors.margins: 2
                    radius: 6
                    color: pick.pressed ? "#4b505a" : "transparent"
                }
                Text {
                    id: word
                    anchors.centerIn: parent
                    text: display
                    // The first is what space types.
                    color: index === 0 ? "#3daee9" : "white"
                    font.pixelSize: board.candidateFont
                }
                Rectangle {  // between candidates
                    anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                    width: 1; height: parent.height * 0.4
                    color: Qt.rgba(1, 1, 1, 0.12)
                }
                TapHandler { id: pick; gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: board.choose(index) }
            }
        }
        BarKey {
            id: more
            visible: board.barState !== "keys"
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            icon: board.barState === "expanded" ? "go-up" : "go-down"
            accessibleName: board.barState === "expanded" ? qsTr("Collapse candidates") : qsTr("Expand candidates")
            accessibleId: "candidates"
            onTapped: board.expanded = !board.expanded
        }
    }

    InputPanel {
        id: panel
        anchors { top: bar.bottom; horizontalCenter: parent.horizontalCenter }
        width: board.width - 2 * board.pad
        // Its languages are switched on its own key, not by a list of the phone's.
        externalLanguageSwitchEnabled: false
        // Ours once loaded (Qt's default style comes first and has no aspect).
        Binding {
            target: panel.keyboard.style
            property: "aspect"
            value: board.keysAspect
            when: !!panel.keyboard.style && panel.keyboard.style.aspect !== undefined
        }
        Component.onCompleted: {
            VirtualKeyboardSettings.styleName = "rungic"
            VirtualKeyboardSettings.activeLocales = board.locales()
            VirtualKeyboardSettings.locale = board.locales()[0]
            VirtualKeyboardSettings.wordCandidateList.alwaysVisible = false
            VirtualKeyboardSettings.closeOnReturn = false
        }
    }

    // expanded: all the candidates, over the keys
    Rectangle {
        z: 1
        visible: board.barState === "expanded"
        anchors { left: panel.left; right: panel.right; top: panel.top; bottom: panel.bottom }
        color: "#1c1e22"
        // Touches stay here, not to the keys under it.
        TapHandler { gesturePolicy: TapHandler.WithinBounds }
        Flickable {
            anchors.fill: parent
            anchors.margins: 2
            clip: true
            contentHeight: grid.height
            boundsBehavior: Flickable.StopAtBounds
            Flow {
                id: grid
                width: parent.width
                spacing: 3
                Repeater {
                    model: board.barState === "expanded" ? board.candidates : null
                    delegate: Rectangle {
                        required property int index
                        required property string display
                        Accessible.role: Accessible.Button
                        Accessible.name: display
                        Accessible.id: "candidate"
                        width: Math.max(52, cell.implicitWidth + 22)
                        height: 42
                        radius: 6
                        color: tap.pressed ? "#4b505a" : "#33363d"
                        Text {
                            id: cell
                            anchors.centerIn: parent
                            text: display
                            color: index === 0 ? "#3daee9" : "white"
                            font.pixelSize: board.candidateFont
                        }
                        TapHandler { id: tap; gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: board.choose(index) }
                    }
                }
            }
        }
    }
}
