// SPDX-License-Identifier: GPL-3.0-or-later
// The fullscreen keyboard's look (FloatingKeyboard, docs/research/97 §19.10): Qt Virtual Keyboard's
// built-in styles size everything from the keyboard's width against a 2560-wide design, made for
// large screens; on the phone (800x360 logical, landscape) a key's letter was about 10 px. Here
// every size comes from the key itself (its height), and the keyboard's proportions from `aspect`,
// which the floating keyboard sets (floating or docked). Dark, as the window's toolbar.
// The candidates are the floating keyboard's own bar: this style's list has no height.
import QtQuick
import QtQuick.VirtualKeyboard
import QtQuick.VirtualKeyboard.Styles
import org.kde.kirigami as Kirigami

KeyboardStyle {
    id: theme
    // Width to height of the keys' area: set by FloatingKeyboard.
    property real aspect: 2.6
    keyboardDesignWidth: 1000
    keyboardDesignHeight: Math.round(1000 / aspect)
    keyboardRelativeLeftMargin: 4 / keyboardDesignWidth
    keyboardRelativeRightMargin: 4 / keyboardDesignWidth
    keyboardRelativeTopMargin: 2 / keyboardDesignHeight
    keyboardRelativeBottomMargin: 4 / keyboardDesignHeight

    readonly property color background: "#1c1e22"
    readonly property color keyColor: "#33363d"
    readonly property color functionKeyColor: "#25282d"
    readonly property color pressedColor: "#4b505a"
    readonly property color textColor: "#f2f3f5"
    readonly property color dimTextColor: "#9aa0a8"
    readonly property color accent: "#3daee9"     // Breeze's highlight
    // The space between keys and their corners, in logical pixels.
    readonly property real gap: 2.5
    readonly property real corner: 6

    keyboardBackground: Rectangle { color: theme.background }

    // ---- the keys ----------------------------------------------------------------------------------
    // A key's face: its background (pressed, disabled), sized from the key.
    // (Inline components see none of the style's ids: their colours are their own.)
    component Face: Rectangle {
        required property Item control
        property bool function_: false
        property bool accented: false
        anchors.fill: parent
        anchors.margins: 2.5
        radius: 6
        color: !control ? "#33363d"
             : control.pressed ? "#4b505a"
             : accented ? "#3daee9"
             : function_ || control.highlighted ? "#25282d" : "#33363d"
        opacity: control && !control.enabled ? 0.4 : 1
    }
    component Label: Text {
        required property Item control
        property real share: 0.46               // of the key's height
        anchors.centerIn: parent
        width: parent.width - 4
        color: "#f2f3f5"
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        fontSizeMode: Text.HorizontalFit
        minimumPixelSize: 8
        font.pixelSize: Math.max(10, Math.round(parent.height * share))
    }
    component Glyph: Kirigami.Icon {
        property real share: 0.5
        anchors.centerIn: parent
        width: Math.round(Math.min(parent.width, parent.height) * share)
        height: width
        color: "#f2f3f5"
        isMask: true
    }

    keyPanel: KeyPanel {
        id: key
        Accessible.role: Accessible.Button
        Accessible.id: key.control && key.control.text ? "key:" + key.control.text : "key"
        Accessible.name: key.control ? (key.control.uppercased ? key.control.displayText.toUpperCase() : key.control.displayText) : ""
        Face { id: face; control: key.control }
        Label {
            control: key.control
            text: key.control.displayText
            font.capitalization: key.control.uppercased ? Font.AllUppercase : Font.MixedCase
        }
        // A second character on the key (long press for it): small, in the corner.
        Text {
            visible: key.control.smallTextVisible && key.control.smallText !== "⚙"
            text: key.control.smallText
            color: theme.dimTextColor
            anchors { right: face.right; top: face.top; rightMargin: 3; topMargin: 1 }
            font.pixelSize: Math.max(7, Math.round(face.height * 0.24))
        }
    }
    backspaceKeyPanel: KeyPanel {
        id: back
        Accessible.role: Accessible.Button
        Accessible.id: "backspace"
        Accessible.name: qsTr("Backspace")
        Face { control: back.control; function_: true }
        Glyph { source: "edit-clear-symbolic"; share: 0.48 }
    }
    enterKeyPanel: KeyPanel {
        id: enter
        Accessible.role: Accessible.Button
        Accessible.id: "enter"
        Accessible.name: enter.control && enter.control.displayText ? enter.control.displayText : qsTr("Enter")
        Face { control: enter.control; accented: true }
        // A word for the field's action (Go, Search, Done...), else the return arrow.
        Label {
            control: enter.control
            share: 0.36
            visible: enter.control.actionId !== EnterKeyAction.None && enter.control.displayText !== ""
            text: enter.control.displayText
        }
        Glyph {
            visible: enter.control.actionId === EnterKeyAction.None || enter.control.displayText === ""
            source: "keyboard-enter-symbolic"
            fallback: "go-next-symbolic"
        }
    }
    shiftKeyPanel: KeyPanel {
        id: shift
        Accessible.role: Accessible.Button
        Accessible.id: "shift"
        Accessible.name: qsTr("Shift")
        Face {
            control: shift.control
            function_: true
            accented: InputContext.capsLockActive
            border.color: InputContext.shiftActive && !InputContext.capsLockActive ? theme.accent : "transparent"
            border.width: 1.5
        }
        Glyph {
            source: InputContext.capsLockActive ? "keyboard-caps-locked-symbolic"
                  : InputContext.shiftActive ? "keyboard-caps-enabled-symbolic" : "keyboard-caps-disabled-symbolic"
            fallback: "go-up-symbolic"
        }
    }
    spaceKeyPanel: KeyPanel {
        id: space
        Accessible.role: Accessible.Button
        Accessible.id: "space"
        Accessible.name: qsTr("Space")
        Face { control: space.control }
        // The language it types, faint.
        Label {
            control: space.control
            share: 0.32
            color: theme.dimTextColor
            text: Qt.locale(InputContext.locale).nativeLanguageName
        }
    }
    symbolKeyPanel: KeyPanel {
        id: symbol
        Accessible.role: Accessible.Button
        Accessible.id: "symbol"
        Accessible.name: symbol.control ? symbol.control.displayText : ""
        Face { control: symbol.control; function_: true }
        Label { control: symbol.control; share: 0.36; text: symbol.control.displayText }
    }
    modeKeyPanel: KeyPanel {
        id: modeKey
        Accessible.role: Accessible.Button
        Accessible.id: "mode"
        Accessible.name: modeKey.control ? modeKey.control.displayText : ""
        Face { control: modeKey.control; function_: true }
        Label {
            control: modeKey.control
            share: 0.36
            text: modeKey.control.displayText
            color: modeKey.control.mode ? theme.accent : theme.textColor
        }
    }
    languageKeyPanel: KeyPanel {
        id: language
        Accessible.role: Accessible.Button
        Accessible.id: "language"
        Accessible.name: qsTr("Switch language")
        Face { control: language.control; function_: true }
        Glyph { source: "globe"; fallback: "preferences-desktop-locale"; share: 0.48 }
    }
    hideKeyPanel: KeyPanel {
        id: hide
        Accessible.role: Accessible.Button
        Accessible.id: "hide"
        Accessible.name: qsTr("Hide keyboard")
        Face { control: hide.control; function_: true }
        Glyph { source: "input-keyboard-virtual-hide-symbolic"; fallback: "arrow-down"; share: 0.5 }
    }
    handwritingKeyPanel: KeyPanel {
        id: handwriting
        Accessible.role: Accessible.Button
        Accessible.id: "handwriting"
        Accessible.name: qsTr("Handwriting")
        Face { control: handwriting.control; function_: true }
        Glyph { source: "draw-freehand"; share: 0.48 }
    }

    // ---- the letter over the finger, and the alternatives of a long press -------------------------
    characterPreviewMargin: 0
    characterPreviewDelegate: Item {
        id: preview
        property string text
        property string flickLeft
        property string flickTop
        property string flickRight
        property string flickBottom
        Rectangle {
            anchors.fill: parent
            radius: theme.corner + 2
            color: theme.pressedColor
            border.color: Qt.rgba(1, 1, 1, 0.12)
            Text {
                anchors.centerIn: parent
                text: preview.text
                color: theme.textColor
                font.pixelSize: Math.round(parent.height * 0.55)
            }
        }
    }
    alternateKeysListItemWidth: 36
    alternateKeysListItemHeight: 44
    alternateKeysListDelegate: Item {
        id: alternate
        Accessible.role: Accessible.Button
        Accessible.id: "alternate"
        Accessible.name: model.text
        width: theme.alternateKeysListItemWidth
        height: theme.alternateKeysListItemHeight
        Text {
            anchors.centerIn: parent
            text: model.text
            color: alternate.ListView.isCurrentItem ? "white" : theme.textColor
            font.pixelSize: 20
        }
    }
    alternateKeysListHighlight: Rectangle { radius: theme.corner; color: theme.accent }
    alternateKeysListBackground: Rectangle {
        radius: theme.corner + 2
        color: theme.pressedColor
        border.color: Qt.rgba(1, 1, 1, 0.12)
    }

    // ---- lists: the candidates are the floating keyboard's (none here); popups -----------------------
    selectionListHeight: 0
    selectionListDelegate: SelectionListItem { width: 0; height: 0 }
    selectionListBackground: Item {}
    popupListDelegate: SelectionListItem {
        id: popupItem
        Accessible.role: Accessible.Button
        Accessible.id: "candidate"
        Accessible.name: display
        width: popupText.implicitWidth + 24
        height: 36
        Text {
            id: popupText
            anchors.centerIn: parent
            text: display
            color: theme.textColor
            font.pixelSize: 16
        }
    }
    popupListBackground: Rectangle { radius: theme.corner; color: theme.pressedColor }
    // The language key switches to the next language (two: Chinese, English) rather than a list.
    languagePopupListEnabled: false
    navigationHighlight: Rectangle { color: "transparent"; border.color: theme.accent; border.width: 2 }
    fullScreenInputContainerBackground: Rectangle { color: theme.background }
    fullScreenInputBackground: Rectangle { color: theme.keyColor }
}
