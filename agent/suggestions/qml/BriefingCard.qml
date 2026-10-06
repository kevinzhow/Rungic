// SPDX-License-Identifier: GPL-2.0-or-later
// One face of the Agent Suggestions stack (docs/research/96): which agent wrote it and when,
// what kind of card it is, the title, the body and its two actions. The widget owns the
// pointer; it asks hitAction() where a tap landed and passes pressed feedback back.
// States: card (a curated card), working (the Agent is already on it), result (it finished and
// needs the user), empty, firstrun, offline.
import QtQuick
import QtQuick.Layouts
import com.rungic.design
import org.kde.ki18n

Rectangle {
    id: face
    property var card: ({})
    property int position: 1
    property int count: 1
    property string agentName: "Codex"
    property var agentIcon: ({})
    property string meta: ""
    property bool canAskAgent: true
    property bool busy: false            // the Agent is sorting the cards again
    property bool opening: false         // Open was tapped; waiting for the conversation
    property string workingText: ""      // "Codex is on it · 4 min" when a task runs for it
    property int bodyLines: 4
    property string pressedPart: ""      // "primary" | "secondary" | "card" while pressed
    // The state shown: the card's own, or `forcedState` (the state gallery).
    property string forcedState: ""
    readonly property string visualState: forcedState !== "" ? forcedState
        : workingText !== "" ? "working" : card.kind === "result" ? "result" : "card"
    state: visualState

    KI18nContext { id: l10n; translationDomain: "rungic-suggestions" }

    property string kindText: ""
    property color kindColor: Theme.dim
    property bool kindBusy: false
    property bool kindDot: false
    property string titleText: card.title || ""
    property string bodyText: card.body || ""
    property string primaryText: card.action ? (card.action.label || "") : ""
    property string secondaryText: l10n.i18nc("@action:button hide this card until something changes", "Not now")
    property color borderColor: Theme.line
    property bool quiet: false
    readonly property string kindName: ({
        attention: l10n.i18nc("@info kind of card", "Needs a look"),
        issues: l10n.i18nc("@info kind of card", "Needs a look"),
        improvement: l10n.i18nc("@info kind of card", "Could be better"),
        result: l10n.i18nc("@info kind of card", "Result ready"),
        followup: l10n.i18nc("@info kind of card", "Follow-up")
    })[card.kind] || l10n.i18nc("@info kind of card", "Suggestion")
    states: [
        State { name: "card"; PropertyChanges { face.kindText: face.kindName; face.kindColor: card.kind === "improvement" || card.kind === "followup" ? Theme.dim : Theme.link } },
        State { name: "working"; PropertyChanges { face.kindText: face.workingText; face.kindColor: Theme.link; face.kindBusy: true; face.borderColor: Theme.link
                face.primaryText: l10n.i18nc("@action:button", "View progress"); face.secondaryText: "" } },
        State { name: "result"; PropertyChanges { face.kindText: face.kindName; face.kindColor: Theme.link; face.kindDot: true; face.borderColor: Theme.link
                face.secondaryText: l10n.i18nc("@action:button", "Later") } },
        State { name: "empty"; PropertyChanges { face.kindText: l10n.i18nc("@info", "All clear"); face.quiet: true
                face.titleText: l10n.i18n("Nothing needs your attention")
                face.bodyText: l10n.i18nc("@info %1 the agent's name", "%1 keeps an eye on this phone in the background and puts anything worth your time here.", face.agentName)
                face.primaryText: l10n.i18nc("@action:button", "See all records"); face.secondaryText: "" } },
        State { name: "firstrun"; PropertyChanges { face.kindText: l10n.i18nc("@info", "Getting started"); face.quiet: true
                face.titleText: l10n.i18nc("@info %1 the agent's name", "%1 is getting to know this phone", face.agentName)
                face.bodyText: l10n.i18n("It looks at crashes, services and apps in the background. Anything worth your time will show up here.")
                face.primaryText: ""; face.secondaryText: "" } },
        State { name: "offline"; PropertyChanges { face.kindText: l10n.i18nc("@info", "Reconnecting…"); face.kindBusy: true; face.quiet: true
                face.titleText: l10n.i18n("Can’t reach Agent Suggestions")
                face.bodyText: l10n.i18n("The suggestion service is restarting. Your cards come back by themselves; nothing is lost.")
                face.primaryText: ""; face.secondaryText: "" } }
    ]

    radius: Theme.radiusSheet
    color: Theme.background
    border.width: 1
    border.color: pressedPart === "card" ? Theme.fill2 : borderColor
    Accessible.role: Accessible.Button
    Accessible.name: (count > 1 ? l10n.i18nc("@info accessible: %1 position, %2 count, %3 title", "Card %1 of %2: %3.", position, count, titleText)
                                : titleText + ".") + " " + bodyText
        + (count > 1 && position < count ? " " + l10n.i18n("Swipe up for the next card.") : "")

    function hitAction(x, y) {
        const p = primary.visible ? primary.mapFromItem(face, x, y) : null
        if (p && p.x >= 0 && p.y >= 0 && p.x < primary.width && p.y < primary.height) return "primary"
        const s = secondary.visible ? secondary.mapFromItem(face, x, y) : null
        if (s && s.x >= 0 && s.y >= 0 && s.x < secondary.width && s.y < secondary.height) return "secondary"
        return "card"
    }

    // Low (a wide, short cell: the home screen in landscape, docs/50): one line of title, tighter edges.
    readonly property bool low: height < 200

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: face.low ? 12 : 16
        spacing: 0
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 20
            spacing: 8
            AgentMark { name: face.agentName; icon: face.agentIcon; muted: face.visualState === "offline" }
            Text {
                text: face.agentName
                font.family: Theme.fontFamily; font.pixelSize: 13; font.weight: Font.DemiBold
                color: Theme.text
            }
            BusyRing { visible: face.busy; implicitWidth: 12; implicitHeight: 12 }
            Text {
                Layout.fillWidth: true
                text: face.meta
                font.family: Theme.fontFamily; font.pixelSize: 12
                color: Theme.dim
                elide: Text.ElideRight
            }
            Text {
                visible: face.count > 1
                text: l10n.i18nc("@info position in the stack: %1 of %2", "%1 of %2", face.position, face.count)
                font.family: Theme.fontFamily; font.pixelSize: 12; font.features: { "tnum": 1 }
                color: Theme.dim
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: face.low ? 8 : 14
            spacing: 6
            BusyRing { visible: face.kindBusy; implicitWidth: 12; implicitHeight: 12; ring: face.kindColor }
            Rectangle { visible: face.kindDot; implicitWidth: 6; implicitHeight: 6; radius: 3; color: face.kindColor }
            Text {
                Layout.fillWidth: true
                text: face.kindText
                font.family: Theme.fontFamily; font.pixelSize: 12; font.letterSpacing: 0.3
                color: face.kindColor
                elide: Text.ElideRight
            }
        }
        Text {
            Layout.fillWidth: true
            Layout.topMargin: 4
            text: face.titleText
            textFormat: Text.PlainText
            font.family: Theme.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold
            lineHeight: 25; lineHeightMode: Text.FixedHeight
            color: Theme.text
            wrapMode: Text.Wrap
            maximumLineCount: face.low ? 1 : 2
            elide: Text.ElideRight
        }
        Text {
            Layout.fillWidth: true
            Layout.topMargin: 6
            visible: face.bodyLines > 0 && text !== ""
            text: face.bodyText
            textFormat: Text.PlainText
            font.family: Theme.fontFamily; font.pixelSize: 15
            lineHeight: 23; lineHeightMode: Text.FixedHeight
            color: Theme.dim
            wrapMode: Text.Wrap
            maximumLineCount: Math.max(1, face.bodyLines)
            elide: Text.ElideRight
        }
        Item { Layout.fillHeight: true }
        RowLayout {
            Layout.fillWidth: true
            // From the texts, not the buttons: a hidden row hides its children, which would keep it hidden.
            visible: face.primaryText !== "" && (face.canAskAgent || ["empty", "working", "result"].includes(face.visualState)) || face.secondaryText !== ""
            spacing: 8
            PillButton {
                id: primary
                visible: face.primaryText !== "" && (face.canAskAgent || ["empty", "working", "result"].includes(face.visualState))
                text: face.primaryText
                forcedState: face.pressedPart === "primary" ? "pressed" : ""
                focusPolicy: Qt.NoFocus
                BusyRing {
                    visible: face.opening
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.right: parent.right; anchors.rightMargin: 12
                    implicitWidth: 14; implicitHeight: 14
                }
                rightPadding: face.opening ? 34 : 16
            }
            // A quiet text button: "Not now" must not compete with the card's action.
            Rectangle {
                id: secondary
                visible: face.secondaryText !== ""
                implicitWidth: secondaryLabel.implicitWidth + 24
                implicitHeight: Theme.touch
                radius: Theme.radiusPill
                color: face.pressedPart === "secondary" ? Theme.fill : "transparent"
                Accessible.role: Accessible.Button
                Accessible.name: face.secondaryText
                Text {
                    id: secondaryLabel
                    anchors.centerIn: parent
                    text: face.secondaryText
                    font.family: Theme.fontFamily; font.pixelSize: Theme.metaSize
                    color: Theme.dim
                }
            }
            Item { Layout.fillWidth: true }
        }
    }
}
