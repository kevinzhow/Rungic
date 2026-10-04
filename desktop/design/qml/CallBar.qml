// SPDX-License-Identifier: GPL-2.0-or-later
// The call with the Agent, while it goes on (Claude Design canvas "Agent 通话"): one line under the
// conversation's top bar. Who speaks, the time, mute and hang up; a tap on its words opens the call
// panel (CallPanel). The app derives `mode` from the phone session (call.js); `label` and `detail`
// are its words for it ("Answering", "04:12 · 1 task running").
// States, the first that applies: connecting, reconnecting, answer (a task waits for the user),
// agent (the Agent speaks), you (the user speaks), thinking (said, the reply not begun), muted,
// idle; elsewhere (the call is in another conversation: "Go there" only).
import QtQuick
import QtQuick.Layouts
import QtQuick.Templates as T
import com.rungic.design

Rectangle {
    id: bar
    property string mode: "idle"
    property string forcedState: ""
    readonly property string visualState: forcedState !== "" ? forcedState : mode
    state: visualState
    property string label: ""
    property string detail: ""
    property string muteText: "Mute"
    property string unmuteText: "Unmute"
    property string hangUpText: "Hang up"
    property string goText: "Go there"
    property string openText: ""         // the screen reader's name of the tap that opens the panel
    signal opened()
    signal muteToggled()
    signal hangUp()
    signal go()

    // What the state shows.
    property color ink: Theme.text
    property color accent: Theme.positive      // the dot, the wave or the ring
    property string marker: "dot"              // dot, wave, ring
    property bool waveActive: false
    property bool muted: false
    property bool shine: false                 // the label passes light over it (thinking)
    states: [
        State { name: "connecting"; PropertyChanges { bar.color: Theme.fill; bar.border.width: 0; bar.ink: Theme.dim; bar.accent: Theme.text; bar.marker: "ring" } },
        State { name: "reconnecting"; PropertyChanges { bar.color: Theme.fill; bar.border.width: 0; bar.ink: Theme.dim; bar.accent: Theme.attention; bar.marker: "ring" } },
        State { name: "answer"; PropertyChanges { bar.color: Theme.attentionFill; bar.border.width: 1; bar.ink: Theme.attention; bar.accent: Theme.attention; bar.marker: "dot" } },
        State { name: "agent"; PropertyChanges { bar.color: Theme.fill; bar.border.width: 0; bar.ink: Theme.text; bar.accent: Theme.positive; bar.marker: "wave"; bar.waveActive: true } },
        State { name: "you"; PropertyChanges { bar.color: Theme.fill; bar.border.width: 0; bar.ink: Theme.text; bar.accent: Theme.text; bar.marker: "wave"; bar.waveActive: true } },
        State { name: "thinking"; PropertyChanges { bar.color: Theme.fill; bar.border.width: 0; bar.ink: Theme.text; bar.accent: Theme.positive; bar.marker: "wave"; bar.waveActive: false; bar.shine: true } },
        State { name: "muted"; PropertyChanges { bar.color: Theme.fill; bar.border.width: 0; bar.ink: Theme.text; bar.accent: Theme.dim; bar.marker: "dot"; bar.muted: true } },
        State { name: "idle"; PropertyChanges { bar.color: Theme.fill; bar.border.width: 0; bar.ink: Theme.text; bar.accent: Theme.positive; bar.marker: "dot" } },
        State { name: "elsewhere"; PropertyChanges { bar.color: Theme.fill; bar.border.width: 0; bar.ink: Theme.dim; bar.accent: Theme.positive; bar.marker: "dot" } }
    ]
    implicitHeight: Theme.controlL
    implicitWidth: 330
    radius: Theme.radiusM
    color: Theme.fill
    border.color: Theme.attentionLine
    Behavior on color { ColorAnimation { duration: Theme.brisk } }

    RowLayout {
        anchors { fill: parent; leftMargin: Theme.spaceM; rightMargin: Theme.inset }
        spacing: Theme.spaceS
        // The words open the panel: the buttons beside them keep their own taps.
        T.AbstractButton {
            id: words
            Layout.fillWidth: true
            Layout.fillHeight: true
            enabled: bar.visualState !== "elsewhere"
            Accessible.name: bar.openText || bar.label
            onClicked: bar.opened()
            contentItem: RowLayout {
                spacing: Theme.spaceS
                Item {
                    Layout.preferredWidth: 26
                    Layout.preferredHeight: 20
                    Rectangle {
                        id: dot
                        visible: bar.marker === "dot"
                        anchors.centerIn: parent
                        width: 10; height: 10; radius: 5
                        color: bar.accent
                        // A waiting answer breathes, to be noticed (the design's c-breathe, 2.6 s).
                        SequentialAnimation on opacity {
                            running: bar.visualState === "answer"; loops: Animation.Infinite
                            NumberAnimation { to: 0.25; duration: 1300; easing.type: Easing.InOutQuad }
                            NumberAnimation { to: 1; duration: 1300; easing.type: Easing.InOutQuad }
                            onRunningChanged: if (!running) dot.opacity = 1
                        }
                    }
                    Wave {
                        visible: bar.marker === "wave"
                        anchors.centerIn: parent
                        bars: 5
                        barHeight: 18
                        spacing: 2
                        color: bar.accent
                        active: bar.waveActive
                    }
                    BusyRing {
                        visible: bar.marker === "ring"
                        anchors.centerIn: parent
                        width: 18; height: 18
                        ring: bar.accent
                    }
                }
                Column {
                    Layout.fillWidth: true
                    spacing: 0
                    Text {
                        visible: !bar.shine
                        width: parent.width
                        text: bar.label
                        elide: Text.ElideRight
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.metaSize
                        font.weight: Font.DemiBold
                        color: bar.ink
                    }
                    ShineText {
                        visible: bar.shine
                        width: parent.width
                        text: bar.label
                        pixelSize: Theme.metaSize
                    }
                    Text {
                        width: parent.width
                        visible: text !== ""
                        text: bar.detail
                        elide: Text.ElideRight
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.labelSize
                        font.features: { "tnum": 1 }
                        color: Theme.dim
                    }
                }
            }
        }
        PillButton {
            visible: bar.visualState === "elsewhere"
            text: bar.goText
            onClicked: bar.go()
        }
        // Mute; muted, a strong round button that unmutes (the state shows at a glance).
        IconButton {
            visible: bar.visualState !== "elsewhere" && !bar.muted
            iconName: "mic"
            text: bar.muteText
            onClicked: bar.muteToggled()
        }
        T.AbstractButton {
            id: unmute
            visible: bar.visualState !== "elsewhere" && bar.muted
            implicitWidth: Theme.touch
            implicitHeight: Theme.touch
            Accessible.name: bar.unmuteText
            onClicked: bar.muteToggled()
            // The circle within the touch target, the icon on it.
            contentItem: Item {
                Rectangle {
                    anchors.centerIn: parent
                    width: Theme.controlS; height: Theme.controlS; radius: width / 2
                    color: Theme.strong
                    scale: unmute.down ? 0.94 : 1
                    Icon { anchors.centerIn: parent; name: "mic-off"; color: Theme.strongInk }
                }
            }
        }
        // Hang up: the one red control, round, at the end.
        T.AbstractButton {
            id: hang
            visible: bar.visualState !== "elsewhere"
            implicitWidth: Theme.controlS
            implicitHeight: Theme.controlS
            Accessible.name: bar.hangUpText
            onClicked: bar.hangUp()
            background: Rectangle { radius: width / 2; color: Theme.negative; scale: hang.down ? 0.94 : 1 }
            contentItem: Item { Icon { anchors.centerIn: parent; name: "hang-up"; color: Theme.negativeInk } }
        }
    }
}
