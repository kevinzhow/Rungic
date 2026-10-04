// SPDX-License-Identifier: GPL-2.0-or-later
// The call with the Agent, opened from its CallBar (Claude Design canvas "Agent 通话", board 6): who speaks, large; the
// tasks the call started, each with its state (one waiting for an answer can be answered here);
// mute, interrupt (while the Agent speaks) and hang up. States as CallBar's (`mode`).
// `tasks`: [{text, status, statusText}], status one of running, queued, answer, done, failed.
import QtQuick
import QtQuick.Layouts
import QtQuick.Templates as T
import com.rungic.design

ColumnLayout {
    id: panel
    property string mode: "listening"
    property string forcedState: ""
    readonly property string visualState: forcedState !== "" ? forcedState : mode
    property string title: ""            // "Call with the Agent"
    property string detail: ""           // "04:12 · Tidy up the Downloads folder"
    property string label: ""            // the state in words: "Agent is speaking"
    property var tasks: []
    property string tasksText: "Started in this call"
    property string muteText: "Mute"
    property string unmuteText: "Unmute"
    property string interruptText: "Interrupt"
    property string hangUpText: "Hang up"
    property string answerText: "Answer"
    signal muteToggled()
    signal interrupt()
    signal hangUp()
    signal answer(int index)
    readonly property bool muted: visualState === "muted"
    readonly property bool agentSpeaks: visualState === "agent"
    spacing: Theme.spaceL

    // ---- who speaks ---------------------------------------------------------------
    ColumnLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceXs
        Text {
            Layout.alignment: Qt.AlignHCenter
            text: panel.title
            font.family: Theme.fontFamily
            font.pixelSize: Theme.titleSize
            font.weight: Font.DemiBold
            color: Theme.text
        }
        Text {
            Layout.alignment: Qt.AlignHCenter
            Layout.maximumWidth: panel.width
            visible: text !== ""
            text: panel.detail
            elide: Text.ElideRight
            font.family: Theme.fontFamily
            font.pixelSize: Theme.metaSize
            font.features: { "tnum": 1 }
            color: Theme.dim
        }
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 72
            Wave {
                visible: panel.visualState !== "connecting" && panel.visualState !== "reconnecting"
                anchors.centerIn: parent
                bars: 24
                barHeight: 56
                color: panel.visualState === "you" ? Theme.text : panel.visualState === "answer" ? Theme.attention : Theme.positive
                active: panel.visualState === "agent" || panel.visualState === "you"
                level: panel.visualState === "you" ? 0.7 : 0.5
            }
            BusyRing {
                visible: panel.visualState === "connecting" || panel.visualState === "reconnecting"
                anchors.centerIn: parent
                width: 32; height: 32
            }
        }
        Text {
            Layout.alignment: Qt.AlignHCenter
            text: panel.label
            font.family: Theme.fontFamily
            font.pixelSize: Theme.heroSize
            font.weight: Font.DemiBold
            color: panel.visualState === "answer" ? Theme.attention : Theme.text
        }
    }

    // ---- the controls ---------------------------------------------------------------
    RowLayout {
        Layout.alignment: Qt.AlignHCenter
        spacing: Theme.space3xl
        CallControl {
            iconName: panel.muted ? "mic-off" : "mic"
            text: panel.muted ? panel.unmuteText : panel.muteText
            on: panel.muted
            onClicked: panel.muteToggled()
        }
        CallControl {
            iconName: "stop"
            text: panel.interruptText
            enabled: panel.agentSpeaks
            onClicked: panel.interrupt()
        }
        CallControl {
            iconName: "hang-up"
            text: panel.hangUpText
            danger: true
            onClicked: panel.hangUp()
        }
    }

    // ---- the call's tasks -----------------------------------------------------------
    SectionLabel {
        Layout.fillWidth: true
        visible: panel.tasks.length > 0
        text: panel.tasksText
    }
    ListGroup {
        Layout.fillWidth: true
        visible: panel.tasks.length > 0
        Repeater {
            model: panel.tasks
            delegate: RowLayout {
                id: task
                required property var modelData
                required property int index
                Layout.fillWidth: true
                Layout.preferredHeight: Theme.row
                spacing: Theme.spaceM
                readonly property color tone: ({running: Theme.positive, queued: Theme.dim, answer: Theme.attention,
                                                 done: Theme.dim, failed: Theme.negative})[modelData.status] || Theme.dim
                Rectangle { Layout.leftMargin: Theme.spaceL; width: 8; height: 8; radius: 4; color: task.tone }
                Column {
                    Layout.fillWidth: true
                    Text {
                        width: parent.width
                        text: task.modelData.text
                        elide: Text.ElideRight
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.calloutSize
                        color: Theme.text
                    }
                    Text {
                        text: task.modelData.statusText
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.labelSize
                        color: task.modelData.status === "answer" ? Theme.attention : Theme.dim
                    }
                }
                PillButton {
                    visible: task.modelData.status === "answer"
                    Layout.rightMargin: Theme.spaceS
                    text: panel.answerText
                    onClicked: panel.answer(task.index)
                }
            }
        }
    }

    // A round control of the call with its name under it (56 px); `danger` is hang up.
    component CallControl: T.AbstractButton {
        id: control
        property string iconName
        property bool danger: false
        property bool on: false
        // Its own state when it can't be used (interrupt while the Agent isn't speaking), else the panel's.
        readonly property string visualState: enabled ? panel.visualState : "disabled"
        implicitWidth: 72
        implicitHeight: 56 + Theme.spaceXs + Theme.labelLine
        Accessible.name: text
        opacity: enabled ? 1 : 0.4
        contentItem: Column {
            spacing: Theme.spaceXs
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                width: 56; height: 56; radius: 28
                color: control.danger ? Theme.negative : control.on ? Theme.strong : Theme.fill
                scale: control.down ? 0.94 : 1
                Icon {
                    anchors.centerIn: parent
                    name: control.iconName
                    color: control.danger ? Theme.negativeInk : control.on ? Theme.strongInk : Theme.text
                }
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: control.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.labelSize
                color: Theme.dim
            }
        }
    }
}
