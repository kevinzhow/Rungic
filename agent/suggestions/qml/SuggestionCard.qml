// SPDX-License-Identifier: GPL-2.0-or-later
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import com.rungic.design
import org.kde.ki18n

Rectangle {
    id: card
    required property var item
    property bool canAskAgent: false
    property bool expanded: false
    signal action(string name, var args)
    readonly property var evidence: item.evidence || ({})
    // This module runs inside plasmashell and the voice assistant: its own catalog, not theirs.
    KI18nContext { id: l10n; translationDomain: "rungic-suggestions" }
    readonly property string displayTitle: item.displayTitle || item.title || l10n.i18n("Suggestion")
    // evidence.scope is recorded data (collector.cpp); its one known value is shown translated.
    readonly property string basis: evidence.reports ? l10n.i18np("%1 crash report saved. Component version: %2", "%1 crash reports saved. Component version: %2", evidence.reports, evidence.version || l10n.i18n("not yet confirmed"))
        : evidence.scope === "版本匹配，需复核本机实际表现" ? l10n.i18n("The package version matches; how it behaves on this device still needs checking.")
        : evidence.scope || (evidence.unit ? l10n.i18n("A service failed; its actual impact still needs checking.") : l10n.i18n("From diagnostics on this device; checked again before anything is done."))
    readonly property string stateName: item.state || "new"
    implicitHeight: content.implicitHeight + 36
    radius: 20
    color: Theme.background
    border.width: 1
    border.color: expanded ? Theme.link : Theme.line
    Accessible.role: Accessible.Grouping
    Accessible.name: displayTitle

    ColumnLayout {
        id: content
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 18 }
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Icon { name: card.item.kind === "fault" ? "alert" : "compose"; color: card.item.severity >= 2 ? Theme.negative : Theme.link; implicitWidth: 20; implicitHeight: 20 }
            Text {
                Layout.fillWidth: true
                text: ({ working: l10n.i18n("In progress"), attention: l10n.i18n("Needs your review"), snoozed: l10n.i18n("Scheduled"), resolved: l10n.i18n("Archived"), dismissed: l10n.i18n("Muted") })[card.stateName] || (card.item.kind === "fault" ? l10n.i18n("Problem") : l10n.i18n("Improvement"))
                font.pixelSize: 12; color: Theme.dim
            }
            QQC2.ToolButton { text: "···"; Accessible.name: l10n.i18n("Suggestion options"); onClicked: options.open() }
            QQC2.Menu {
                id: options
                QQC2.MenuItem { text: l10n.i18n("Show details"); onTriggered: card.expanded = !card.expanded }
                QQC2.MenuItem { text: l10n.i18n("Keep for later"); enabled: card.stateName !== "working" && card.stateName !== "resolved"; onTriggered: card.action("later", {}) }
                QQC2.MenuItem { text: l10n.i18n("Don't remind me about this"); enabled: card.stateName !== "working"; onTriggered: card.action("dismiss", {}) }
                QQC2.MenuItem { text: l10n.i18n("Prepare feedback"); onTriggered: card.action("feedback", {}) }
            }
        }
        Text { Layout.fillWidth: true; text: card.displayTitle; textFormat: Text.PlainText; wrapMode: Text.Wrap; font.pixelSize: 19; font.weight: Font.DemiBold; color: Theme.text }
        Text { Layout.fillWidth: true; text: card.item.summary || card.item.body || ""; textFormat: Text.PlainText; wrapMode: Text.Wrap; font.pixelSize: 14; lineHeight: 1.35; color: Theme.dim }
        Text { Layout.fillWidth: true; visible: card.stateName === "working" && !!card.item.note; text: card.item.note || ""; textFormat: Text.PlainText; wrapMode: Text.Wrap; font.pixelSize: 12; color: Theme.link }
        Text { Layout.fillWidth: true; visible: !!card.item.nextStep; text: card.item.nextStep || ""; textFormat: Text.PlainText; wrapMode: Text.Wrap; color: Theme.link; font.pixelSize: 13 }
        PillButton { text: card.expanded ? l10n.i18n("Hide details") : l10n.i18n("Show the full investigation"); onClicked: card.expanded = !card.expanded }
        Text {
            Layout.fillWidth: true
            visible: card.expanded
            text: [card.item.issueNote, card.item.result, card.evidence.package ? l10n.i18n("Component: %1", card.evidence.package) : "", l10n.i18n("Plan status: %1", ({ready: l10n.i18n("A plan is ready to review and apply"), unavailable: l10n.i18n("No plan can be applied right now"), needs_investigation: l10n.i18n("Still needs investigation")})[card.item.planStatus] || l10n.i18n("Still needs investigation")), card.item.plan ? l10n.i18n("Plan: %1", card.item.plan) : "", card.item.verification ? l10n.i18n("Verification: %1", card.item.verification) : "", card.item.rollback ? l10n.i18n("Rollback: %1", card.item.rollback) : "", l10n.i18n("Basis: %1", card.basis)].filter(Boolean).join("\n\n")
            textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere; font.pixelSize: 13; color: Theme.dim
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            PillButton {
                visible: card.canAskAgent || ["dismissed", "resolved"].includes(card.stateName) || !!card.item.conversation
                text: card.stateName === "working" ? l10n.i18n("View progress") : card.stateName === "dismissed" ? l10n.i18n("Restore") : card.item.conversation ? l10n.i18n("View result") : card.stateName === "resolved" ? l10n.i18n("Show details") : l10n.i18n("Ask Agent to check")
                onClicked: {
                    if (card.stateName === "dismissed") card.action("restore", {})
                    else if (card.item.conversation) card.action("conversation", {})
                    else if (card.stateName === "resolved") card.expanded = !card.expanded
                    else card.action(card.stateName === "dismissed" ? "restore" : "investigate", {})
                }
            }
            PillButton {
                visible: card.stateName !== "resolved" && card.stateName !== "dismissed"
                text: card.stateName === "working" ? l10n.i18n("Stop") : l10n.i18n("Later")
                onClicked: card.action(card.stateName === "working" ? "stop" : "snooze-menu", {})
            }
            Item { Layout.fillWidth: true }
        }
        PillButton {
            visible: card.item.issueState === "absent" && card.stateName === "attention"
            text: l10n.i18n("Got it, archive")
            onClicked: card.action("reviewed", {})
        }
        PillButton {
            visible: card.expanded && !!card.item.canApply
            text: l10n.i18n("Apply this plan")
            onClicked: card.action("apply-confirm", {})
        }
    }
}
