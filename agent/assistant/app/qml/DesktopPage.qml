// SPDX-License-Identifier: GPL-2.0-or-later
import QtQuick
import QtQuick.Layouts
import com.rungic.design
import com.rungic.voiceassistant

SettingsFrame {
    id: page
    title: i18nc("@title", "Desktop operation")
    property var setup: ({})
    property string error: ""
    property bool saving: false
    readonly property string mode: (setup.desktop || {}).mode || "codex"
    Component.onCompleted: AgentClient.request("Setup")
    Connections {
        target: AgentClient
        function onReplied(method, json) {
            const r = JSON.parse(json)
            if (method === "Setup") page.setup = r
            else if (method === "SetDesktopMode") {
                page.saving = false
                page.error = r.error || ""
                if (r.mode) page.setup = Object.assign({}, page.setup, { desktop: { mode: r.mode } })
            }
        }
        function onEvent(json) {
            const e = JSON.parse(json)
            if (e.type === "agent-restarted") AgentClient.request("Setup")
        }
        function onFailed(text) {
            if (page.saving) { page.saving = false; page.error = text }
        }
    }
    function choose(mode) {
        page.error = ""
        page.saving = true
        AgentClient.request("SetDesktopMode", [mode])
    }
    SectionLabel { Layout.fillWidth: true; text: i18nc("@title:group", "Who operates the desktop") }
    ListGroup {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.groupMargin
        Layout.rightMargin: Theme.groupMargin
        ChoiceRow {
            text: i18nc("@option:radio", "Codex (default)")
            subtitle: i18nc("@info", "Uses the Agent's model and sign-in. Steps and usage appear with the task.")
            checked: page.mode === "codex"
            enabled: !page.saving && page.setup.codex !== undefined
            onClicked: page.choose("codex")
        }
        ChoiceRow {
            text: i18nc("@option:radio", "Luna via OpenAI API")
            subtitle: i18nc("@info", "Uses a separate API key. Desktop operation is billed to the OpenAI API, apart from a ChatGPT plan.")
            checked: page.mode === "luna"
            enabled: !page.saving && (page.setup.key || {}).set === true
            onClicked: page.choose("luna")
        }
    }
    Text {
        Layout.fillWidth: true; Layout.margins: Theme.groupMargin
        text: i18nc("@info", "Changing this setting restarts the Agent connection after the current task has ended. Voice and speech synthesis still use the API key in either mode.")
        color: Theme.dim; font.pixelSize: Theme.bodySize; wrapMode: Text.Wrap
    }
    Text {
        visible: page.mode === "atspi"
        Layout.fillWidth: true; Layout.margins: Theme.groupMargin
        text: i18nc("@info", "The accessibility and OCR mode is selected. Choose Codex or Luna to use screenshots.")
        color: Theme.dim; font.pixelSize: Theme.bodySize; wrapMode: Text.Wrap
    }
    Text {
        visible: page.error !== ""
        Layout.fillWidth: true; Layout.margins: Theme.groupMargin
        text: page.error; color: Theme.negative; font.pixelSize: Theme.bodySize; wrapMode: Text.Wrap
    }
}
