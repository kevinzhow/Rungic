// SPDX-License-Identifier: GPL-2.0-or-later
// Codex (docs/87, docs/99): ready (version and whether a newer stable release is out, sign-in,
// where credentials are kept, the config file), not installed (OpenAI's install script), or
// installing / updating (steps and output). The system has no Codex of its own: the script puts
// OpenAI's standalone Codex in the home, and the same script updates it.
import QtQuick
import QtQuick.Layouts
import com.rungic.design
import com.rungic.voiceassistant
import "account.js" as Account

SettingsFrame {
    id: page
    title: install.running ? (install.updating ? i18nc("@title", "Update Codex") : i18nc("@title", "Install Codex")) : "Codex"
    property var setup: ({})
    readonly property var tr: (context, text, ...args) => i18nc(context, text, ...args)
    readonly property var codex: setup.codex || {}
    readonly property var update: codex.update || {}
    // The installation under way: InstallCodex events.
    QtObject {
        id: install
        property bool running: false
        property bool updating: false
        property string step: ""
        property string log: ""
        property string error: ""
    }
    readonly property var steps: [["download", i18nc("@info an installation step", "Download the installer")],
                                  ["install", i18nc("@info an installation step", "Install Codex")],
                                  ["check", i18nc("@info an installation step", "Check that it runs")],
                                  ["connect", i18nc("@info an installation step", "Connect to OpenAI")]]
    function stepIndex(name) { return steps.findIndex(s => s[0] === name) }

    Component.onCompleted: AgentClient.request("Setup")
    Connections {
        target: AgentClient
        function onReplied(method, json) { if (method === "Setup") page.setup = JSON.parse(json) }
        function onEvent(json) {
            const e = JSON.parse(json)
            if (e.type === "codex-update") { AgentClient.request("Setup"); return }
            if (e.type !== "install") return
            if (e.line) install.log = (install.log + e.line + "\n").split("\n").slice(-40).join("\n")
            if (e.step) install.step = e.step
            if (e.state === "done" || e.state === "failed" || e.state === "cancelled") {
                install.running = false
                install.error = e.state === "failed" ? (e.error || i18nc("@info", "The installation didn't finish")) : ""
                AgentClient.request("Setup")
            }
        }
    }

    // ---- ready or not ---------------------------------------------------------------
    ColumnLayout {
        Layout.fillWidth: true
        visible: !install.running && page.setup.codex !== undefined
        spacing: 0
        ColumnLayout {
            Layout.fillWidth: true
            Layout.topMargin: 20
            Layout.leftMargin: 32
            Layout.rightMargin: 32
            spacing: 10
            Rectangle {
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: 72
                Layout.preferredHeight: 72
                radius: 36
                color: Theme.fill
                Icon {
                    anchors.centerIn: parent
                    name: page.codex.installed ? "check" : "terminal"
                    color: page.codex.installed ? Theme.positive : Theme.text
                    implicitWidth: Theme.iconHero
                    implicitHeight: Theme.iconHero
                }
            }
            Text {
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                text: page.codex.installed === true ? (page.codex.runs === false ? i18nc("@title", "Codex can't run") : i18nc("@title", "Codex is installed"))
                    : page.codex.installed === false ? i18nc("@title", "Codex isn't installed yet")
                    : i18nc("@title", "Codex installation not confirmed")
                font.family: Theme.fontFamily
                font.pixelSize: Theme.heroSize
                font.weight: Font.DemiBold
                color: Theme.text
            }
            Text {
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                text: page.codex.installed === true ? i18nc("@info", "The Agent uses it to run commands and use apps on the phone.")
                    : page.codex.installed === false ? i18nc("@info", "The Agent relies on Codex to run commands and use apps on the phone. Install it, and the Agent can get things done for you.")
                    : i18nc("@info", "The installation check is unavailable. Check again before installing or signing in.")
                wrapMode: Text.Wrap
                font.family: Theme.fontFamily
                font.pixelSize: Theme.metaSize
                color: Theme.dim
            }
            Note {
                Layout.fillWidth: true
                visible: install.error !== ""
                tone: "negative"
                text: install.error
            }
        }
        Item { implicitHeight: 20 }

        PillButton {
            visible: page.codex.installed !== true && page.codex.installed !== false
            text: i18nc("@action:button", "Check again")
            onClicked: AgentClient.request("Setup")
        }

        // Installed.
        ListGroup {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.groupMargin
            Layout.rightMargin: Theme.groupMargin
            visible: page.codex.installed === true
            ListRow {
                text: i18nc("@label", "Version")
                value: page.codex.version || ""
                subtitle: page.update.available ? i18nc("@info %1 is a version", "Version %1 is out", page.update.latest)
                    : page.update.checked && !page.update.error ? i18nc("@info", "The latest stable release") : ""
                dot: page.update.available ? "positive" : ""
            }
            ListRow {
                text: i18nc("@label how Codex is signed in", "Sign-in")
                value: Account.label(page.tr, page.setup.account || null, page.setup.accountStatus || "unknown")
                subtitle: page.setup.accountStatus === "ready" ? Account.billing(page.tr, page.setup.account || null) : ""
                accessory: "chevron"
                onClicked: page.push("AccountPage.qml")
            }
            ListRow {
                text: i18nc("@label", "Credentials kept in")
                value: page.setup.credentials === "keyring" ? i18nc("@info where credentials are kept", "System keyring") : i18nc("@info where credentials are kept", "A file on the phone")
            }
            ListRow {
                text: i18nc("@label", "Config file")
                value: "~/.codex/config.toml"
                valueMono: true
                accessory: "external"
                onClicked: Qt.openUrlExternally("file://" + page.setup.home + "/.codex/config.toml")
            }
        }
        Item { implicitHeight: 20; visible: page.codex.installed === true }
        ListGroup {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.groupMargin
            Layout.rightMargin: Theme.groupMargin
            visible: page.codex.installed === true
            ListRow {
                text: i18nc("@action:button", "Check for updates")
                subtitle: page.update.error ? i18nc("@info %1 is the reason", "Couldn't check: %1", page.update.error) : ""
                interactive: true
                onClicked: { AgentClient.request("CheckCodexUpdate", [JSON.stringify({ force: true })]); AgentClient.request("Setup") }
            }
        }

        // Not installed: OpenAI's script, the only way (the system has no Codex of its own, docs/99).
        SectionLabel { Layout.fillWidth: true; text: i18nc("@title:group", "How to install"); visible: page.codex.installed === false }
        ListGroup {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.groupMargin
            Layout.rightMargin: Theme.groupMargin
            visible: page.codex.installed === false
            ListRow {
                text: i18nc("@info", "OpenAI's official install script")
                subtitle: "curl -fsSL https://chatgpt.com/codex/install.sh | sh"
            }
        }
        Note {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gutter
            Layout.rightMargin: Theme.gutter
            Layout.topMargin: Theme.spaceM
            visible: page.codex.installed === false
            text: i18nc("@info", "Codex goes into your home folder (~/.codex), the latest stable release. When a newer one is out, you can update it here.")
        }
    }

    // ---- installing -------------------------------------------------------------------
    ColumnLayout {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.gutter
        Layout.rightMargin: Theme.gutter
        Layout.topMargin: 16
        visible: install.running
        spacing: 12
        ShineText { Layout.fillWidth: true; pixelSize: Theme.heroSize; text: install.updating ? i18nc("@info:status", "Updating Codex…") : i18nc("@info:status", "Installing Codex…") }
        Progress { Layout.fillWidth: true }
        Text { text: i18nc("@info", "About a minute left. You can leave this page."); font.family: Theme.fontFamily; font.pixelSize: Theme.labelSize; color: Theme.dim }
    }
    Item { implicitHeight: 16; visible: install.running }
    ListGroup {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.groupMargin
        Layout.rightMargin: Theme.groupMargin
        visible: install.running
        Repeater {
            model: page.steps
            ListRow {
                required property var modelData
                required property int index
                readonly property int at: page.stepIndex(install.step)
                text: modelData[1]
                leading: Item {
                    implicitWidth: 20
                    implicitHeight: 20
                    BusyRing { anchors.fill: parent; visible: index === at }
                    Icon {
                        anchors.fill: parent
                        visible: index !== at
                        name: index < at ? "check" : "chevron"
                        color: index < at ? Theme.positive : Theme.dim
                    }
                }
            }
        }
    }
    SectionLabel { Layout.fillWidth: true; text: i18nc("@title:group the installer's output", "Details"); visible: install.running && install.log !== "" }
    Text {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.groupMargin
        Layout.rightMargin: Theme.groupMargin
        visible: install.running && install.log !== ""
        padding: 14
        text: install.log.trim()
        wrapMode: Text.WrapAnywhere
        font.family: Theme.monoFamily
        font.pixelSize: 12
        lineHeight: 19
        lineHeightMode: Text.FixedHeight
        color: Theme.dim
        Rectangle { anchors.fill: parent; z: -1; radius: Theme.radiusInput; color: Theme.fill }
    }
    Item { implicitHeight: 20 }

    function runInstaller(updating) {
        install.running = true
        install.updating = updating
        install.error = ""
        install.log = ""
        install.step = "download"
        AgentClient.request("InstallCodex", ["script"])
    }

    footer: [
        PrimaryButton {
            Layout.fillWidth: true
            visible: page.codex.installed === true && page.update.available === true && !install.running
            iconName: "download"
            text: i18nc("@action:button %1 is a version", "Update to %1", page.update.latest || "")
            onClicked: page.runInstaller(true)
        },
        PrimaryButton {
            Layout.fillWidth: true
            visible: page.codex.installed === false && !install.running
            iconName: "download"
            text: i18nc("@action:button", "Install Codex")
            onClicked: page.runInstaller(false)
        },
        SecondaryButton {
            Layout.fillWidth: true
            visible: page.codex.installed === false && !install.running
            text: i18nc("@action:button", "Not now")
            onClicked: page.back()
        },
        SecondaryButton {
            Layout.fillWidth: true
            visible: install.running
            text: i18nc("@action:button", "Cancel installation")
            onClicked: AgentClient.request("CancelInstall")
        }
    ]
}
