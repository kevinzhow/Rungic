// SPDX-License-Identifier: GPL-2.0-or-later
// The conversations, in the side panel (docs/87): search, "New conversation", "Call the Agent" and "Main conversation"
// (the one holding Home talks in, docs/67: always there, not deleted, not filtered by the search),
// then "Other conversations", the user's own, by day (the open one marked), and the settings at the
// bottom. Holding one of those offers to delete it.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import com.rungic.design
import com.rungic.voiceassistant

SideDrawer {
    id: drawer
    property string current: ""
    property var all: []
    property string deleting: ""               // the conversation held to delete
    signal openRequested(string id, string title)
    signal newRequested()
    signal callRequested()
    signal settingsRequested()
    signal suggestionsRequested()
    signal deleted(string id)

    function refresh() { AgentClient.listConversations() }
    onAboutToShow: { deleting = ""; search.text = ""; refresh() }

    Connections {
        target: AgentClient
        function onConversationsListed(json) { drawer.all = JSON.parse(json) }
    }

    // "Today", "Yesterday", "Sep 24" (the last 30 days), "Earlier".
    function dayOf(seconds) {
        const d = new Date(seconds * 1000)
        const today = new Date()
        today.setHours(0, 0, 0, 0)
        const days = Math.floor((today - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000)
        if (days <= 0) return i18nc("@title:group conversations of", "Today")
        if (days === 1) return i18nc("@title:group conversations of", "Yesterday")
        // TRANSLATORS: a Qt date format (MMM: the month's short name, d: the day)
        if (days < 30) return d.toLocaleDateString(Qt.locale(), i18nc("@title:group conversations of a day, as a Qt date format", "MMM d"))
        return i18nc("@title:group conversations of", "Earlier")
    }
    // The service marks the main conversation in the list (`main`; `assistant` before), and gives
    // the titles in words to show (an untitled one's too).
    readonly property var main: all.find(c => c.main || c.assistant) || null
    readonly property var shown: {
        const q = search.text.trim().toLowerCase()
        return all.filter(c => !(c.main || c.assistant))
                  .filter(c => !q || (c.title || "").toLowerCase().includes(q) || (c.preview || "").toLowerCase().includes(q))
                  .map(c => ({ cid: c.id, title: c.title || i18nc("@title a conversation not named yet", "New conversation"), day: dayOf(c.updated || c.created || 0) }))
    }
    // Sections need a ListModel.
    ListModel { id: rows }
    onShownChanged: {
        rows.clear()
        for (const c of shown) rows.append(c)
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        Item { implicitHeight: 12 }
        SearchField {
            id: search
            Layout.fillWidth: true
            Layout.leftMargin: 12
            Layout.rightMargin: 12
            placeholderText: i18nc("@info:placeholder", "Search conversations")
        }
        Item { implicitHeight: 8 }
        NavItem {
            iconName: "compose"
            text: i18nc("@action:button", "New conversation")
            onClicked: drawer.newRequested()
        }
        // A call with the Agent, in a new conversation (Claude Design canvas "Agent 通话", board 2).
        NavItem {
            iconName: "phone"
            text: i18nc("@action:button", "Call the Agent")
            onClicked: drawer.callRequested()
        }
        // Beside "New conversation", as one of the panel's own entries: the conversation holding
        // Home talks in.
        NavItem {
            visible: drawer.main !== null
            iconName: "voice"
            text: i18nc("@title the conversation holding Home talks in", "Main conversation")
            current: drawer.main !== null && drawer.main.id === drawer.current
            onClicked: drawer.openRequested(drawer.main.id, text)
        }
        NavItem {
            iconName: "alert"
            text: i18nc("@action:button proactive suggestions", "Suggestions")
            onClicked: drawer.suggestionsRequested()
        }
        SectionLabel {
            Layout.fillWidth: true
            text: i18nc("@title:group", "Other conversations")
            color: Theme.text
            font.weight: Font.Medium
            topPadding: 18
            bottomPadding: 0
        }
        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: rows
            boundsBehavior: Flickable.StopAtBounds
            section.property: "day"
            section.delegate: SectionLabel {
                required property string section
                width: ListView.view.width
                text: section
                // The first day sits right under "Other conversations".
                topPadding: rows.count > 0 && section === rows.get(0).day ? 6 : 18
                bottomPadding: 6
            }
            delegate: Item {
                id: row
                required property string cid
                required property string title
                width: ListView.view.width
                height: Theme.touch
                NavItem {
                    anchors { left: parent.left; right: parent.right; leftMargin: 8; rightMargin: 8 }
                    height: parent.height
                    visible: drawer.deleting !== row.cid
                    text: row.title
                    current: row.cid === drawer.current
                    onClicked: drawer.openRequested(row.cid, row.title)
                    onPressAndHold: drawer.deleting = row.cid
                }
                // Held: delete it, or not.
                RowLayout {
                    anchors { fill: parent; leftMargin: 16; rightMargin: 12 }
                    visible: drawer.deleting === row.cid
                    spacing: 8
                    Text {
                        Layout.fillWidth: true
                        text: row.title
                        elide: Text.ElideRight
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.bodySize
                        color: Theme.dim
                    }
                    PillButton {
                        text: i18nc("@action:button delete the conversation", "Delete")
                        negative: true
                        onClicked: {
                            AgentClient.deleteConversation(row.cid)
                            drawer.all = drawer.all.filter(c => c.id !== row.cid)
                            drawer.deleted(row.cid)
                            drawer.deleting = ""
                        }
                    }
                    IconButton { small: true; iconName: "close"; text: i18nc("@action:button keep the conversation", "Don't delete"); onClicked: drawer.deleting = "" }
                }
            }
            Text {
                anchors.centerIn: parent
                visible: list.count === 0
                text: search.text ? i18nc("@info", "No conversations found") : i18nc("@info", "No other conversations yet")
                font.family: Theme.fontFamily
                font.pixelSize: Theme.metaSize
                color: Theme.dim
            }
        }
        NavItem {
            Layout.bottomMargin: 12
            iconName: "settings"
            text: i18nc("@action:button", "Settings")
            onClicked: drawer.settingsRequested()
        }
    }
}
