// SPDX-License-Identifier: GPL-2.0-or-later
import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import com.rungic.design
import com.rungic.suggestions
import org.kde.ki18n
import "UsageText.js" as UsageText

Rectangle {
    id: feed
    property bool home: false
    property string selectedId: ""
    readonly property bool groupDetail: selectedId.startsWith("group:")
    property bool activeView: visible && Window.active
    property bool history: false
    property string message: ""
    property var pendingItem: ({})
    property var shown: []
    property bool pendingRefresh: false
    property bool populated: false
    property bool positioned: false
    color: Theme.side
    KI18nContext { id: l10n; translationDomain: "rungic-suggestions" }
    SuggestionsClient { id: client }
    UsageClient { id: usage }
    readonly property bool canAskAgent: UsageText.canAsk(usage.providers.find(p => p.id === "codex"))
    Timer { interval: 60000; repeat: true; running: feed.activeView; onTriggered: usage.refresh() }
    PresentationTracker { view: list; suggestionsClient: client; active: feed.activeView && !snooze.visible && !applyDialog.visible; selectedId: feed.selectedId }
    readonly property var clientItems: [client.items, client.groups, client.historyGroups]
    onClientItemsChanged: {
        if (list.moving) pendingRefresh = true
        else rebuild()
    }
    onHistoryChanged: rebuild()
    onActiveViewChanged: client.watching(activeView)
    Component.onCompleted: { rebuild(); client.watching(activeView) }
    Component.onDestruction: client.watching(false)
    function beginning() {
        list.forceLayout()
        list.positionViewAtBeginning()
        list.contentY = list.originY - list.topMargin
    }
    function rebuild() {
        const atBeginning = !populated || list.contentY <= list.originY + 1
        const y = list.contentY - list.originY
        populated = client.items.length > 0
        const items = client.items.filter(i => history ? ["resolved", "dismissed"].includes(i.state) : !["resolved", "dismissed"].includes(i.state))
        shown = groupDetail ? items.filter(i => i.groupId === selectedId) : selectedId ? items : (history ? client.historyGroups : client.groups)
        pendingRefresh = false
        Qt.callLater(() => {
            if (selectedId && !positioned && client.items.some(i => i.id === selectedId)) {
                positioned = true; select(selectedId)
            }
            else if (atBeginning) beginning()
            else list.contentY = list.originY + Math.max(-list.topMargin, Math.min(y, Math.max(0, list.contentHeight - list.height)))
        })
    }
    function select(id) {
        selectedId = id
        const item = client.items.find(i => i.id === id)
        if (item && ["resolved", "dismissed"].includes(item.state)) history = true
        const index = shown.findIndex(i => i.id === id)
        // An expanded investigation can be taller than the whole screen. Keep
        // its title and conclusion visible instead of centering its middle.
        if (index === 0) beginning()
        else if (index > 0) list.positionViewAtIndex(index, ListView.Beginning)
    }
    onSelectedIdChanged: { positioned = false; Qt.callLater(rebuild) }
    Connections {
        target: client
        function onReplied(id, action, result) {
            if (result.error) {
                feed.message = result.error
                if (action === "apply") { applyDialog.failureMessage = result.error; applyDialog.open() }
            }
            else if (result.message) feed.message = result.message
            else if (action === "investigate") feed.message = l10n.i18n("Agent got the request. You can leave this page; the result will stay in your suggestions.")
        }
    }
    ListView {
        id: list
        objectName: "suggestionsFeed"
        anchors.fill: parent
        clip: true
        spacing: 14
        topMargin: 12; bottomMargin: 24
        boundsBehavior: Flickable.StopAtBounds
        reuseItems: true
        model: feed.shown
        onMovingChanged: if (!moving && feed.pendingRefresh) feed.rebuild()
        QQC2.ScrollBar.vertical: QQC2.ScrollBar {
            policy: QQC2.ScrollBar.AsNeeded
            implicitWidth: 4
            padding: 0
            background: null
            contentItem: Rectangle { implicitWidth: 3; radius: 2; color: Theme.dim; opacity: parent.active ? 0.5 : 0.15 }
        }
        header: ColumnLayout {
            width: list.width
            spacing: 14
            Item { implicitHeight: feed.home ? 14 : 0 }
            RowLayout {
                Layout.fillWidth: true; Layout.leftMargin: 22; Layout.rightMargin: 22
                Text { text: feed.home ? l10n.i18n("Today") : l10n.i18n("Suggestions"); font.pixelSize: feed.home ? 32 : 26; font.weight: Font.DemiBold; color: Theme.text; Layout.fillWidth: true }
                PillButton { text: feed.selectedId ? l10n.i18n("All suggestions") : "Agent"; onClicked: { if (feed.selectedId) feed.selectedId = ""; else client.openAgent() } }
            }
            Text {
                Layout.leftMargin: 22; Layout.rightMargin: 22; Layout.fillWidth: true
                text: feed.groupDetail ? l10n.i18n("Each related record keeps its own evidence and findings; being grouped doesn't mean they share a root cause.")
                    : feed.home ? l10n.i18nc("@info %1 is today's date", "%1 · Looking out for how your phone is doing", new Date().toLocaleDateString(Qt.locale(), l10n.i18nc("today's date on the home feed, Qt date format", "dddd, MMMM d")))
                    : l10n.i18n("Problems found, suggested improvements and their progress all stay here.")
                font.pixelSize: 13; color: Theme.dim; wrapMode: Text.Wrap
            }
            // What the Agent picked for now (docs/research/96), the same stack as on the home screen.
            SuggestionsWidget {
                visible: !feed.selectedId
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: Math.min(list.width - 32, 680)
                Layout.preferredHeight: 330
            }
            Text {
                visible: !feed.selectedId
                Layout.fillWidth: true; Layout.leftMargin: 22; Layout.rightMargin: 22; Layout.topMargin: 10
                text: l10n.i18nc("@title the ledger below the Agent's cards", "All records")
                font.pixelSize: 17; font.weight: Font.DemiBold; color: Theme.text
            }
            RowLayout {
                Layout.leftMargin: 20; Layout.rightMargin: 20
                PillButton { text: l10n.i18n("Pending"); checked: !feed.history; onClicked: feed.history = false }
                PillButton { text: l10n.i18n("History"); checked: feed.history; onClicked: feed.history = true }
                Item { Layout.fillWidth: true }
                PillButton { text: l10n.i18n("Refresh"); enabled: !client.busy; onClicked: { client.scan(); client.curate(); feed.message = l10n.i18n("Checking again, and Agent sorts the cards again.") } }
            }
            Text {
                Layout.fillWidth: true; Layout.leftMargin: 22; Layout.rightMargin: 22
                visible: !!client.error || !!feed.message
                text: client.error || feed.message; textFormat: Text.PlainText; color: client.error ? Theme.negative : Theme.dim; font.pixelSize: 13; wrapMode: Text.Wrap
            }
            Rectangle {
                Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
                visible: feed.shown.length === 0
                implicitHeight: 150; radius: 20; color: Theme.background; border.color: Theme.line
                ColumnLayout {
                    anchors { fill: parent; margins: 22 }
                    Text { text: feed.history ? l10n.i18n("No history yet") : l10n.i18n("Nothing needs your attention right now"); color: Theme.text; font.pixelSize: 18; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Text { text: client.coverage.length ? l10n.i18n("Some checks haven't finished yet. Refresh later to see them.") : l10n.i18n("New findings will appear here as cards."); color: Theme.dim; font.pixelSize: 14; wrapMode: Text.Wrap; Layout.fillWidth: true }
                }
            }
            Text {
                Layout.fillWidth: true; Layout.leftMargin: 22; Layout.rightMargin: 22
                visible: client.coverage.length > 0
                text: client.coverage.join("\n"); textFormat: Text.PlainText; font.pixelSize: 12; color: Theme.dim; wrapMode: Text.Wrap
            }
            Item { implicitHeight: 2 }
        }
        delegate: Item {
            required property var modelData
            readonly property var suggestionRecord: modelData.members ? modelData.members[0] : modelData
            width: list.width
            height: modelData.members ? stackCard.height : card.height
            SuggestionStack {
                id: stackCard
                visible: !!parent.modelData.members
                item: parent.modelData
                width: Math.min(list.width - 32, 680)
                anchors.horizontalCenter: parent.horizontalCenter
                onClicked: { feed.selectedId = item.id; feed.beginning() }
            }
            SuggestionCard {
                canAskAgent: feed.canAskAgent
            id: card
            visible: !parent.modelData.members
            item: parent.modelData
            width: Math.min(list.width - 32, 680)
            anchors.horizontalCenter: parent.horizontalCenter
            expanded: item.id === feed.selectedId
            onAction: (name, args) => {
                if (name === "snooze-menu") { feed.pendingItem = item; snooze.open() }
                else if (name === "apply-confirm") { feed.pendingItem = JSON.parse(JSON.stringify(item)); applyDialog.failureMessage = ""; applyDialog.open() }
                else if (name === "conversation") client.conversation(item.conversation || "")
                else client.act(item.id, name, args)
            }
        }
        }
        footer: Item { width: list.width; height: 40 }
    }
    QQC2.Popup {
        id: snooze
        parent: QQC2.Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(340, feed.width - 24)
        modal: true; focus: true; padding: 18
        background: Rectangle { color: Theme.background; radius: 20; border.color: Theme.line }
        contentItem: ColumnLayout {
            spacing: 12
            Text { text: l10n.i18n("When should we come back to this?"); color: Theme.text; font.pixelSize: 20; font.weight: Font.DemiBold }
            Text { Layout.fillWidth: true; text: l10n.i18n("This only schedules a reminder. No software is changed automatically."); color: Theme.dim; font.pixelSize: 13; wrapMode: Text.Wrap }
            QQC2.Button { Layout.fillWidth: true; text: l10n.i18n("Keep for later"); onClicked: { client.act(feed.pendingItem.id, "later"); snooze.close() } }
            QQC2.Button { Layout.fillWidth: true; text: l10n.i18n("Remind me in an hour"); onClicked: { client.act(feed.pendingItem.id, "snooze", { at: Math.floor(Date.now() / 1000) + 3600 }); snooze.close() } }
            QQC2.Button { Layout.fillWidth: true; text: l10n.i18n("Remind me tomorrow at 10:00"); onClicked: { client.act(feed.pendingItem.id, "snooze", { at: client.tomorrow(10) }); snooze.close() } }
            QQC2.Button { Layout.fillWidth: true; text: l10n.i18n("Cancel"); onClicked: snooze.close() }
        }
    }
    QQC2.Dialog {
        id: applyDialog
        parent: QQC2.Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(340, feed.width - 24)
        height: Math.min(500, feed.height - 40)
        title: l10n.i18n("Apply the fix")
        property string failureMessage: ""
        readonly property bool outdated: !!feed.pendingItem.id && !client.items.some(i => i.id === feed.pendingItem.id && i.canApply && i.planRevision === feed.pendingItem.planRevision)
        function updateButtons() {
            const ok = standardButton(QQC2.Dialog.Ok)
            if (ok) { ok.enabled = !outdated && !client.busy; ok.text = l10n.i18n("Apply") }
            const cancel = standardButton(QQC2.Dialog.Cancel)
            if (cancel) cancel.text = l10n.i18n("Cancel")
        }
        onOpened: updateButtons()
        onOutdatedChanged: updateButtons()
        Connections { target: client; function onChanged() { if (applyDialog.visible) applyDialog.updateButtons() } }
        modal: true
        standardButtons: QQC2.Dialog.Ok | QQC2.Dialog.Cancel
        contentItem: ColumnLayout {
            spacing: 12
            Text {
                Layout.fillWidth: true
                visible: applyDialog.outdated || !!applyDialog.failureMessage
                text: applyDialog.outdated ? l10n.i18n("The plan or its evidence has changed. Cancel, review the current plan, then decide whether to apply it.") : applyDialog.failureMessage
                textFormat: Text.PlainText; wrapMode: Text.Wrap; color: Theme.negative; font.pixelSize: 14
                Accessible.role: Accessible.StaticText
                Accessible.name: text
            }
            QQC2.ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true
                clip: true
                Text {
                    width: applyDialog.availableWidth
                    text: l10n.i18n("Agent will carry out the plan shown and verify the result. Any steps that close apps or restart follow the plan.") + "\n\n" + (feed.pendingItem.plan || "")
                    textFormat: Text.PlainText; wrapMode: Text.Wrap; color: Theme.text; font.pixelSize: 14
                }
            }
        }
        onAccepted: client.act(feed.pendingItem.id, "apply", { planRevision: feed.pendingItem.planRevision })
    }
}
