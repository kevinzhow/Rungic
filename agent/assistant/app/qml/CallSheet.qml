// SPDX-License-Identifier: GPL-2.0-or-later
// The call with the Agent, opened from its call bar (Claude Design canvas "Agent 通话", board 6): a
// sheet up from the bottom with the design system's CallPanel. Mute, interrupt and hang up go to the
// phone session; "Answer" on a task closes the sheet and shows that task's card in the conversation.
// The scrim, dragging the sheet down and Back close it.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import com.rungic.design
import com.rungic.voiceassistant
import "call.js" as Call

QQC2.Drawer {
    id: sheet
    property var phone: ({})
    property string conversation: ""
    property string title: ""                 // the conversation's
    property real now: Date.now()
    signal answerRequested(string taskId)
    edge: Qt.BottomEdge
    width: parent ? parent.width : 0
    height: Math.min(column.implicitHeight, parent ? parent.height * 0.85 : column.implicitHeight)
    dragMargin: 0
    modal: true
    padding: 0
    background: Rectangle {
        color: Theme.background
        topLeftRadius: Theme.radiusSheet
        topRightRadius: Theme.radiusSheet
    }
    QQC2.Overlay.modal: Rectangle { color: Theme.scrim }
    enter: Transition { NumberAnimation { property: "position"; to: 1; duration: Theme.slide; easing.type: Easing.Bezier; easing.bezierCurve: Theme.easing } }
    exit: Transition { NumberAnimation { property: "position"; to: 0; duration: Theme.normal; easing.type: Easing.Bezier; easing.bezierCurve: Theme.easing } }
    Shortcut {
        sequences: [StandardKey.Back, "Back"]
        enabled: sheet.opened
        onActivated: sheet.close()
    }
    // The call ended (hung up here or anywhere): nothing left to show.
    readonly property string mode: Call.mode(phone, conversation)
    onModeChanged: if (opened && (mode === "" || mode === "elsewhere")) close()

    ColumnLayout {
        id: column
        width: sheet.width
        spacing: 0
        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: 10
            Layout.bottomMargin: Theme.spaceS
            width: 36; height: 4; radius: 2
            color: Theme.fill2
        }
        CallPanel {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gutter
            Layout.rightMargin: Theme.gutter
            Layout.bottomMargin: Theme.space3xl
            mode: sheet.mode === "" || sheet.mode === "elsewhere" ? "idle" : sheet.mode
            title: i18nc("@title the call with the Agent", "Call with the Agent")
            detail: Call.clock(Call.elapsed(sheet.phone, sheet.now)) + (sheet.title ? " · " + sheet.title : "")
            label: Call.words(i18nc, sheet.phone, sheet.conversation, sheet.now, sheet.title, "")[0]
            tasks: Call.panelTasks(i18nc, sheet.phone, sheet.conversation)
            tasksText: i18nc("@title:group the tasks started during the call", "Tasks in this call")
            muteText: i18nc("@action:button the call with the Agent", "Mute")
            unmuteText: i18nc("@action:button the call with the Agent", "Unmute")
            interruptText: i18nc("@action:button stop the Agent's spoken answer", "Interrupt")
            hangUpText: i18nc("@action:button the call with the Agent", "Hang up")
            answerText: i18nc("@action:button a task waits for the user's answer", "Answer")
            onMuteToggled: AgentClient.request("SetPhoneMuted", [sheet.phone.sessionId, !sheet.phone.muted])
            onInterrupt: AgentClient.request("StopSpeaking", [sheet.phone.sessionId])
            onHangUp: { AgentClient.request("StopPhoneMode", [sheet.phone.sessionId]); sheet.close() }
            onAnswer: index => { const row = tasks[index]; sheet.close(); if (row) sheet.answerRequested(row.taskId) }
        }
    }
}
