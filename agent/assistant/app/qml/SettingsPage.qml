// SPDX-License-Identifier: GPL-2.0-or-later
// Settings (docs/87): Codex and the OpenAI API key, voice, appearance, about.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import com.rungic.design
import com.rungic.voiceassistant
import "efforts.js" as Efforts
import "account.js" as Account

SettingsFrame {
    id: page
    // i18nc for efforts.js (a library has no context to find it in).
    readonly property var tr: (context, text, ...args) => i18nc(context, text, ...args)
    title: i18nc("@title", "Settings")
    property var setup: ({})
    readonly property var settings: QQC2.ApplicationWindow.window ? QQC2.ApplicationWindow.window.settings : null
    readonly property var themeNames: [["system", i18nc("@item the app's theme", "System")], ["light", i18nc("@item the app's theme", "Light")],
                                       ["dark", i18nc("@item the app's theme", "Dark")]]

    // The agent's model (docs/98): Models(), and SetAgentModel's reply or event when it changes.
    property var models: ({})
    readonly property var agentModel: models.effective || ({})
    readonly property string modelLabel: !models.known ? "" : [agentModel.name || i18nc("@item the model", "Account default"),
        agentModel.effort ? Efforts.name(page.tr, agentModel.effort) : ""].filter(Boolean).join(" · ")
    function refresh() { AgentClient.request("Setup"); AgentClient.request("Models", ["{}"]) }
    Component.onCompleted: refresh()
    onVisibleChanged: if (visible) refresh()
    Connections {
        target: AgentClient
        function onReplied(method, json) {
            if (method === "Setup") page.setup = JSON.parse(json)
            else if (method === "Models" || method === "SetAgentModel") { const r = JSON.parse(json); if (r.models !== undefined) page.models = r }
        }
        function onEvent(json) {
            const e = JSON.parse(json)
            if (e.type === "agent-model") page.models = e
            else if (e.type === "codex-update") AgentClient.request("Setup")
        }
    }
    readonly property var codex: setup.codex || {}
    readonly property var key: setup.key || {}
    readonly property var prefs: setup.preferences || {}
    function prefer(name, value) {
        const p = Object.assign({}, prefs)
        p[name] = value
        setup = Object.assign({}, setup, { preferences: p })
        AgentClient.request("SetPreferences", [JSON.stringify(p)])
    }

    SectionLabel { Layout.fillWidth: true; text: "Codex" }
    ListGroup {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.groupMargin
        Layout.rightMargin: Theme.groupMargin
        ListRow {
            text: "Codex"
            dot: page.setup.codex ? (page.codex.installed === true ? "positive" : "") : ""
            value: !page.setup.codex ? "" : page.codex.installed === false ? i18nc("@info Codex", "Not installed")
                : page.codex.installed !== true ? i18nc("@info Codex", "Installation not confirmed")
                : page.codex.update && page.codex.update.available ? i18nc("@info Codex; %1 is a version", "Update to %1", page.codex.update.latest)
                : i18nc("@info Codex", "Installed")
            accessory: "chevron"
            onClicked: page.push("CodexPage.qml")
        }
        ListRow {
            text: i18nc("@label the agent's model", "Model")
            value: page.modelLabel
            dot: page.agentModel.fallback ? "negative" : ""
            enabled: page.codex.installed !== false
            accessory: "chevron"
            onClicked: page.push("ModelPage.qml")
        }
        // How Codex signs in, and so who pays for the Agent's tasks (docs/101).
        ListRow {
            text: i18nc("@label how Codex is signed in", "Sign-in")
            value: page.setup.codex === undefined ? "" : Account.label(page.tr, page.setup.account || null, page.setup.accountStatus || "unknown")
            dot: page.setup.codex !== undefined && page.setup.accountStatus === "signed-out" ? "negative" : ""
            enabled: page.codex.installed !== false
            accessory: "chevron"
            onClicked: page.push("AccountPage.qml")
        }
        ListRow { text: i18nc("@title", "Agent Usage"); accessory: "chevron"; onClicked: page.push("UsagePage.qml") }
        ListRow {
            text: i18nc("@title", "Desktop operation")
            value: (page.setup.desktop || {}).mode === "luna" ? i18nc("@item", "Luna · API")
                : (page.setup.desktop || {}).mode === "atspi" ? "AT-SPI / OCR" : "Codex"
            accessory: "chevron"
            onClicked: page.push("DesktopPage.qml")
        }
    }

    SectionLabel { Layout.fillWidth: true; text: i18nc("@title:group", "OpenAI API") }
    ListGroup {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.groupMargin
        Layout.rightMargin: Theme.groupMargin
        // What Codex's sign-in doesn't cover: the realtime voice, speech to text, calls (docs/101).
        ListRow {
            text: "OpenAI API Key"
            subtitle: i18nc("@info what the API key is for", "Voice, calls and optional Luna desktop operation")
            value: page.key.set ? page.key.masked : (page.setup.key ? i18nc("@info the API key", "Not set") : "")
            valueMono: page.key.set === true
            dot: page.setup.key && !page.key.set ? "negative" : ""
            accessory: "chevron"
            onClicked: page.push("KeyPage.qml")
        }
    }
    SectionLabel { Layout.fillWidth: true; text: i18nc("@title:group", "Voice") }
    ListGroup {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.groupMargin
        Layout.rightMargin: Theme.groupMargin
        ToggleRow {
            text: i18nc("@option:check", "Hold Home to open")
            checked: page.prefs.homeHold !== false
            onSwitched: on => page.prefer("homeHold", on)
        }
        ToggleRow {
            text: i18nc("@option:check", "Read answers aloud")
            checked: page.prefs.speak !== false
            onSwitched: on => page.prefer("speak", on)
        }
        ToggleRow {
            text: i18nc("@option:check", "Auto-send in hands-free mode")
            subtitle: i18nc("@info", "Sends after a pause of about a second")
            checked: page.prefs.handsFreeAutoSend !== false
            onSwitched: on => page.prefer("handsFreeAutoSend", on)
        }
    }

    SectionLabel { Layout.fillWidth: true; text: i18nc("@title:group", "Appearance") }
    ListGroup {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.groupMargin
        Layout.rightMargin: Theme.groupMargin
        // A short list of choices opens as a sheet from the bottom (docs/102).
        ListRow {
            readonly property var names: ({ system: page.themeNames[0][1], light: page.themeNames[1][1], dark: page.themeNames[2][1] })
            text: i18nc("@label", "Theme")
            value: page.settings ? names[page.settings.theme] || names.system : ""
            accessory: "chevron"
            onClicked: themeSheet.open()
        }
    }

    SectionLabel { Layout.fillWidth: true; text: i18nc("@title:group", "About") }
    ListGroup {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.groupMargin
        Layout.rightMargin: Theme.groupMargin
        ListRow { text: i18nc("@label", "Version"); value: page.setup.version || "" }
        ListRow { text: i18nc("@action:button", "Privacy"); accessory: "chevron"; onClicked: page.push(privacy) }
    }
    Item { implicitHeight: 24 }

    ChoiceSheet {
        id: themeSheet
        parent: QQC2.Overlay.overlay
        title: i18nc("@label", "Theme")
        choices: page.themeNames.map(n => ({ value: n[0], text: n[1] }))
        current: page.settings ? page.settings.theme : "system"
        onChosen: value => { if (page.settings) page.settings.theme = value }
    }

    Component {
        id: privacy
        SettingsFrame {
            title: i18nc("@title", "Privacy")
            Repeater {
                model: [
                    i18nc("@info privacy", "Your voice leaves the phone only while you hold to talk: it goes through the proxy you set up to OpenAI, where a voice model understands it and answers. Slide onto × before you let go to cancel, and it is never sent."),
                    i18nc("@info privacy", "The Agent runs commands and uses apps on this phone through Codex. What it sees on the screen and the results of its commands are sent to OpenAI to decide the next step."),
                    i18nc("@info privacy; %1 is a folder", "Conversations are kept only on this phone (%1). Deleting a conversation deletes its history too.",
                          "~/.local/share/rungic-voice-agent"),
                    i18nc("@info privacy; %1 is a folder", "Your OpenAI API key is kept in plain text in a config file on this phone (%1, readable only by your user) and is used only to call OpenAI. Programs running as you, including the Agent, can read it.",
                          "~/.config/rungic-voice-agent")
                ]
                Text {
                    required property string modelData
                    Layout.fillWidth: true
                    Layout.leftMargin: Theme.gutter
                    Layout.rightMargin: Theme.gutter
                    Layout.topMargin: 12
                    text: modelData
                    wrapMode: Text.Wrap
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.bodySize
                    lineHeight: Theme.bodyLine
                    lineHeightMode: Text.FixedHeight
                    color: Theme.text
                }
            }
        }
    }
}
