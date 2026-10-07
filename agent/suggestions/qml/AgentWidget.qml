// SPDX-License-Identifier: GPL-2.0-or-later
// Agent Usage on the home screen (docs/research/95): one agent at a time, with its own mark,
// whether it is working, and how much of its limits is used. Any agent with a usage provider
// shows the same way; with several, a chip switches between them.
// Body states: meters (limit windows), stats (tokens, e.g. an API key), message (signed out,
// none connected, unreachable), skeleton (first load). Every text is sized for its column:
// compact numbers, countdowns under a day, weekday and time beyond.
import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import com.rungic.design
import org.kde.ki18n
import com.rungic.suggestions
import "UsageText.js" as UsageText

Item {
    id: widget
    implicitWidth: 340
    implicitHeight: 100
    property bool activeView: visible && Window.active
    property double now: forcedNow > 0 ? forcedNow : Date.now() / 1000

    // State gallery: fixed providers instead of the service.
    property var forcedProviders: null
    property double forcedNow: 0

    KI18nContext { id: l10n; translationDomain: "rungic-suggestions" }
    UsageClient { id: usage }
    SuggestionsClient { id: navigation }
    readonly property bool live: forcedProviders === null
    readonly property var providers: live ? usage.providers : forcedProviders
    property string chosenId: ""
    readonly property var provider: {
        const chosen = providers.find(p => p.id === chosenId)
        return chosen || (live ? usage.primary : providers[0]) || providers[0] || ({})
    }
    readonly property bool compact: width < 250
    // Flat (a wide, short cell: a 4x1 widget on the home screen in landscape, docs/50): one row, the
    // agent then its limits beside it, no reset notes.
    readonly property bool flat: height < 90
    readonly property var limits: {
        const list = (provider.limits || []).slice().sort((a, b) => (a.windowMinutes || 0) - (b.windowMinutes || 0))
        return compact ? list.sort((a, b) => b.usedPercent - a.usedPercent).slice(0, 1) : list.slice(0, 2)
    }
    readonly property var tokens: provider.tokens || ({})
    readonly property string body: !provider.id ? "none"
        : provider.status === "not-installed" ? "install"
        : provider.status === "signed-out" ? "signin"
        : ["offline", "error"].includes(provider.status) && (!provider.updatedAt || !limits.length && tokens.device == null) ? "unreachable"
        : provider.status === "connecting" || (!provider.updatedAt && !limits.length && tokens.device == null) ? "skeleton"
        : limits.length ? "meters"
        : tokens.today != null || tokens.device != null ? "stats"
        : provider.status === "offline" ? "unreachable" : "norecords"
    readonly property bool stale: provider.stale === true || provider.status === "offline" || provider.status === "error"
    readonly property var nextProvider: {
        if (providers.length < 2) return null
        const i = providers.findIndex(p => p.id === provider.id)
        return providers[(i + 1) % providers.length]
    }
    Timer { interval: 60000; repeat: true; running: widget.activeView && widget.live; onTriggered: { widget.now = Date.now() / 1000; usage.refresh() } }
    onActiveViewChanged: if (activeView && live) usage.refresh()

    readonly property string statusText: ({
        working: l10n.i18nc("@info agent status", "Working"), ready: l10n.i18nc("@info agent status", "Ready"),
        offline: l10n.i18nc("@info agent status", "Connection failed"), connecting: l10n.i18nc("@info agent status", "Connecting…"),
        "not-installed": l10n.i18nc("@info agent status", "Not installed"),
        "signed-out": l10n.i18nc("@info agent status", "Not signed in"), error: l10n.i18nc("@info agent status", "Couldn’t update")
    })[provider.status] || ""
    readonly property string rightText: {
        if (!provider.id || compact || nextProvider) return ""
        if (stale && provider.updatedAt)
            return l10n.i18nc("@info %1 a time", "Last read %1", new Date(provider.updatedAt * 1000).toLocaleString(Qt.locale(), l10n.i18nc("@info Qt time format", "HH:mm")))
        if (body === "meters" && tokens.today != null) return l10n.i18nc("@info tokens: %1 a compact number", "%1 today", UsageText.compact(l10n, tokens.today))
        if (body === "stats" && (provider.account || {}).kind === "api-key") return (provider.account || {}).label || "API Key"
        return ""
    }
    function meterValue(w) { return w.usedPercent >= 100 ? l10n.i18nc("@info a usage limit", "Used up") : l10n.i18nc("@info percent", "%1%", Math.floor(w.usedPercent)) }

    Accessible.role: Accessible.Button
    Accessible.name: l10n.i18n("Agent Usage")
    Accessible.description: [provider.name, statusText].concat(limits.map(w => UsageText.shortWindow(l10n, w) + " " + meterValue(w) + ", " + UsageText.resetNote(l10n, w, now))).filter(Boolean).join(". ")

    Rectangle {
        id: card
        anchors.fill: parent
        radius: Theme.radiusSheet
        color: Theme.background
        border.width: 1
        border.color: tap.pressed && !chip.hovered ? Theme.fill2 : Theme.line
        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: 16; anchors.rightMargin: 16
            anchors.topMargin: widget.flat ? 6 : 12; anchors.bottomMargin: widget.flat ? 6 : 12
            spacing: 0
            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: widget.flat
                Layout.preferredHeight: 22
                spacing: 8
                AgentMark {
                    name: widget.provider.name || "Agent"
                    icon: widget.provider.icon || ({})
                    muted: !widget.provider.id || widget.provider.status === "signed-out" || widget.provider.status === "not-installed" || widget.provider.status === "offline"
                    // The agent's own mark goes back to its conversation; the rest of the card to its usage.
                    MouseArea {
                        anchors.fill: parent; anchors.margins: -10
                        enabled: widget.live
                        onClicked: widget.body === "install" ? navigation.openCodex() : widget.body === "signin" ? navigation.signIn() : navigation.openAgent()
                        Accessible.role: Accessible.Button
                        Accessible.name: widget.provider.name || "Agent"
                        Accessible.onPressAction: widget.body === "install" ? navigation.openCodex() : widget.body === "signin" ? navigation.signIn() : navigation.openAgent()
                    }
                }
                Text {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    elide: Text.ElideRight
                    text: widget.provider.status === "not-installed" ? l10n.i18n("%1 isn’t installed yet", widget.provider.name || "Codex")
                        : widget.provider.status === "signed-out" ? l10n.i18n("Not signed in to %1", widget.provider.name || "Codex")
                        : ["offline", "error"].includes(widget.provider.status) ? l10n.i18n("Cannot reach %1 right now", widget.provider.name || "Codex")
                        : widget.compact && widget.provider.name === "Claude Code" ? "Claude" : (widget.provider.name || "Agent")
                    font.family: Theme.fontFamily; font.pixelSize: 15; font.weight: Font.DemiBold
                    color: Theme.text
                }
                BusyRing { visible: widget.provider.status === "working" || widget.provider.status === "connecting"; implicitWidth: 12; implicitHeight: 12 }
                Text {
                    visible: !widget.compact && !widget.flat && widget.statusText !== "" && !["not-installed", "signed-out", "offline", "error"].includes(widget.provider.status)
                    text: widget.statusText
                    font.family: Theme.fontFamily; font.pixelSize: 13
                    color: widget.provider.status === "working" ? Theme.text : Theme.dim
                }
                Item { Layout.fillWidth: true; Layout.minimumWidth: widget.flat ? 8 : 0 }
                // Flat: the limits in the same row, each its name, percentage and bar.
                Repeater {
                    model: widget.flat && widget.body === "meters" ? widget.limits : []
                    ColumnLayout {
                        required property var modelData
                        readonly property bool near: modelData.usedPercent >= 80
                        readonly property bool full: modelData.usedPercent >= 100
                        Layout.preferredWidth: 130
                        Layout.maximumWidth: 160
                        spacing: 3
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Text {
                                Layout.fillWidth: true
                                text: UsageText.shortWindow(l10n, modelData)
                                font.family: Theme.fontFamily; font.pixelSize: 12
                                color: Theme.dim
                                elide: Text.ElideRight
                            }
                            Text {
                                text: widget.meterValue(modelData)
                                font.family: Theme.fontFamily; font.pixelSize: 13; font.weight: Font.DemiBold; font.features: { "tnum": 1 }
                                color: widget.stale ? Theme.dim : full ? Theme.negative : Theme.text
                            }
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 4
                            radius: 2
                            color: Theme.fill2
                            Rectangle {
                                width: parent.width * Math.max(0, Math.min(1, modelData.usedPercent / 100))
                                height: parent.height
                                radius: 2
                                color: widget.stale ? Theme.faint : full ? Theme.negative : near ? Theme.text : Theme.dim
                            }
                        }
                    }
                }
                // Flat: tokens, or the message, as one dim line.
                Text {
                    visible: widget.flat && widget.body !== "meters" && text !== ""
                    Layout.maximumWidth: card.width * 0.55
                    text: widget.body === "stats"
                          ? l10n.i18nc("@info tokens used: %1 a compact number", "Today %1", UsageText.compact(l10n, widget.tokens.today != null ? widget.tokens.today : widget.tokens.device))
                          : ({ install: l10n.i18n("Install from Agent → Settings → Codex."), none: l10n.i18n("No agent is connected yet."), signin: l10n.i18n("Sign in so Agent can help with your tasks."),
                               unreachable: l10n.i18n("Will retry automatically."),
                               norecords: l10n.i18n("No usage recorded yet.") })[widget.body] || ""
                    font.family: Theme.fontFamily; font.pixelSize: 13
                    color: Theme.dim
                    elide: Text.ElideRight
                }
                Rectangle {
                    id: chip
                    property bool hovered: false
                    visible: widget.nextProvider !== null && !widget.compact
                    implicitHeight: 26
                    implicitWidth: chipRow.implicitWidth + 18
                    radius: 13
                    color: chipTap.pressed ? Theme.fill2 : Theme.fill
                    border.width: 1; border.color: Theme.line
                    Accessible.role: Accessible.Button
                    Accessible.name: widget.nextProvider ? l10n.i18nc("@action %1 an agent's name", "Switch to %1", widget.nextProvider.name) : ""
                    RowLayout {
                        id: chipRow
                        anchors.centerIn: parent
                        spacing: 5
                        Icon { name: "chevron-down"; rotation: -90; color: Theme.dim; implicitWidth: 12; implicitHeight: 12 }
                        Text {
                            text: widget.nextProvider ? widget.nextProvider.name : ""
                            font.family: Theme.fontFamily; font.pixelSize: 12
                            color: Theme.dim
                        }
                    }
                    MouseArea { id: chipTap; anchors.fill: parent; anchors.margins: -9; onClicked: widget.chosenId = widget.nextProvider.id }
                }
                Text {
                    visible: widget.rightText !== "" && !widget.flat
                    text: widget.rightText
                    font.family: Theme.fontFamily; font.pixelSize: 12; font.features: { "tnum": 1 }
                    color: Theme.dim
                }
            }
            Item { Layout.fillHeight: true; visible: !widget.flat }
            // Limit windows side by side (one in the compact size).
            RowLayout {
                visible: widget.body === "meters" && !widget.flat
                Layout.fillWidth: true
                spacing: 16
                Repeater {
                    model: widget.body === "meters" ? widget.limits : []
                    ColumnLayout {
                        required property var modelData
                        readonly property bool near: modelData.usedPercent >= 80
                        readonly property bool full: modelData.usedPercent >= 100
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        spacing: 5
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Text {
                                Layout.fillWidth: true
                                text: UsageText.shortWindow(l10n, modelData)
                                font.family: Theme.fontFamily; font.pixelSize: 12
                                color: Theme.dim
                                elide: Text.ElideRight
                            }
                            Text {
                                text: widget.meterValue(modelData)
                                font.family: Theme.fontFamily; font.pixelSize: 13; font.weight: Font.DemiBold; font.features: { "tnum": 1 }
                                color: widget.stale ? Theme.dim : full ? Theme.negative : Theme.text
                            }
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 4
                            radius: 2
                            color: Theme.fill2
                            Rectangle {
                                width: parent.width * Math.max(0, Math.min(1, modelData.usedPercent / 100))
                                height: parent.height
                                radius: 2
                                color: widget.stale ? Theme.faint : full ? Theme.negative : near ? Theme.text : Theme.dim
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: UsageText.resetNote(l10n, modelData, widget.now)
                            font.family: Theme.fontFamily; font.pixelSize: 12; font.features: { "tnum": 1 }
                            color: full && !widget.stale ? Theme.text : Theme.dim
                            elide: Text.ElideRight
                        }
                    }
                }
            }
            // Token counts where there are no limits (an API key, an agent that reports none).
            RowLayout {
                visible: widget.body === "stats" && !widget.flat
                Layout.fillWidth: true
                spacing: 16
                Repeater {
                    model: widget.body !== "stats" ? []
                        : [{ label: l10n.i18nc("@info tokens used", "Today"), value: widget.tokens.today },
                           { label: l10n.i18nc("@info tokens used", "This device"), value: widget.tokens.device }].slice(0, widget.compact ? 1 : 2)
                    ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        spacing: 2
                        Text { text: modelData.label; font.family: Theme.fontFamily; font.pixelSize: 12; color: Theme.dim }
                        Text {
                            text: UsageText.compact(l10n, modelData.value)
                            font.family: Theme.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold; font.features: { "tnum": 1 }
                            color: widget.stale ? Theme.dim : Theme.text
                        }
                    }
                }
            }
            ColumnLayout {
                visible: ["install", "none", "signin", "unreachable", "norecords"].includes(widget.body) && !widget.flat
                Layout.fillWidth: true
                spacing: 2
                Text {
                    Layout.fillWidth: true
                    text: ({
                        install: l10n.i18n("Install from Agent → Settings → Codex."), none: l10n.i18n("No agent is connected yet."),
                        signin: l10n.i18n("Sign in so Agent can help with your tasks."),
                        unreachable: l10n.i18n("Will retry automatically."),
                        norecords: l10n.i18n("No usage recorded yet.")
                    })[widget.body] || ""
                    font.family: Theme.fontFamily; font.pixelSize: 13
                    color: Theme.dim
                    wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                }
                Text {
                    visible: text !== ""
                    text: widget.body === "install" ? l10n.i18nc("@action", "Install Codex ›") : widget.body === "none" ? l10n.i18nc("@action", "Set one up in Agent ›") : widget.body === "signin" ? l10n.i18nc("@action", "Sign in ›") : ""
                    font.family: Theme.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
                    color: Theme.link
                }
            }
            // First load: the final layout in outline, so nothing jumps when data arrives.
            RowLayout {
                visible: widget.body === "skeleton" && !widget.flat
                Layout.fillWidth: true
                spacing: 16
                Repeater {
                    model: widget.body === "skeleton" ? (widget.compact ? 1 : 2) : 0
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        spacing: 8
                        Rectangle { Layout.preferredWidth: parent.width * 0.56; implicitHeight: 10; radius: 5; color: Theme.fill }
                        Rectangle { Layout.fillWidth: true; implicitHeight: 4; radius: 2; color: Theme.fill }
                        Rectangle { Layout.preferredWidth: parent.width * 0.72; implicitHeight: 10; radius: 5; color: Theme.fill }
                    }
                }
            }
        }
        MouseArea {
            id: tap
            anchors.fill: parent
            z: -1
            enabled: widget.live
            onClicked: widget.body === "install" ? navigation.openCodex() : widget.body === "signin" ? navigation.signIn() : widget.body === "none" ? navigation.openAgent() : navigation.openAgent(true)
        }
    }
}
