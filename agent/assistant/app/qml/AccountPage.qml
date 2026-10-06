// SPDX-License-Identifier: GPL-2.0-or-later
// How Codex signs in (docs/101), and so who pays for the Agent's tasks: a ChatGPT account (its
// plan) or the OpenAI API key (by use). Only Codex's sign-in: the API key's other uses (voice,
// calls) are its own page. Changing to the API key asks first, since it moves the billing.
// States: loading, signedOut, chatgpt, apiKey; and on top of them signingIn (a ChatGPT device-code
// sign-in waits for the code to be entered) or confirming (the switch to the API key is asked).
import QtQuick
import QtQuick.Layouts
import com.rungic.design
import com.rungic.voiceassistant
import "account.js" as Account

SettingsFrame {
    id: page
    title: i18nc("@title how Codex signs in", "Sign-in")
    readonly property var tr: (context, text, ...args) => i18nc(context, text, ...args)
    property var setup: ({})
    property var login: null                  // the device-code sign-in under way, or its error
    property bool confirming: false
    property string forcedState: ""
    readonly property var account: setup.account || null
    readonly property var key: setup.key || {}
    readonly property string kind: Account.kind(account)
    readonly property string visualState: forcedState !== "" ? forcedState
        : setup.codex === undefined ? "loading"
        : setup.accountStatus === "offline" ? "unreachable"
        : login && !login.error ? "signingIn"
        : confirming ? "confirming"
        : kind === "chatgpt" ? "chatgpt" : kind === "apiKey" ? "apiKey" : "signedOut"
    state: visualState
    property bool choicesShown: false
    property bool codeShown: false
    property bool confirmShown: false
    states: [
        State { name: "loading"; PropertyChanges { page.choicesShown: false; page.codeShown: false; page.confirmShown: false } },
        State { name: "unreachable"; PropertyChanges { page.choicesShown: false; page.codeShown: false; page.confirmShown: false } },
        State { name: "signedOut"; PropertyChanges { page.choicesShown: true; page.codeShown: false; page.confirmShown: false } },
        State { name: "chatgpt"; PropertyChanges { page.choicesShown: true; page.codeShown: false; page.confirmShown: false } },
        State { name: "apiKey"; PropertyChanges { page.choicesShown: true; page.codeShown: false; page.confirmShown: false } },
        State { name: "signingIn"; PropertyChanges { page.choicesShown: false; page.codeShown: true; page.confirmShown: false } },
        State { name: "confirming"; PropertyChanges { page.choicesShown: false; page.codeShown: false; page.confirmShown: true } }
    ]

    Component.onCompleted: AgentClient.request("Setup")
    onVisibleChanged: if (visible) AgentClient.request("Setup")
    Connections {
        target: AgentClient
        function onReplied(method, json) {
            const r = JSON.parse(json)
            if (method === "Setup") {
                page.setup = r
                // A device-code sign-in still under way (the page was left and opened again): its code.
                if (!page.login && r.login) page.login = r.login
            } else if (method === "CodexLogin") {
                page.confirming = false
                page.login = r.error ? { error: r.error } : (r.userCode ? r : null)
                if (!r.error && !r.userCode) AgentClient.request("Setup")      // the API key: at once
            }
        }
        function onEvent(json) {
            const e = JSON.parse(json)
            if (e.type === "account") {
                // Only the sign-in this page shows: an older one's end must not cover its code (docs/101).
                if (e.loginId && page.login && page.login.loginId && e.loginId !== page.login.loginId) return
                // A failure of a sign-in this page doesn't show (another page's, one cancelled): only the state.
                if (!e.success && !page.login) { AgentClient.request("Setup"); return }
                page.login = e.success ? null : { error: e.error || i18nc("@info", "The sign-in didn't finish") }
                AgentClient.request("Setup")
            } else if (e.type === "agent-restarted") AgentClient.request("Setup")
        }
    }

    // ---- now ---------------------------------------------------------------------------
    SectionLabel { Layout.fillWidth: true; text: i18nc("@title:group how Codex is signed in now", "Now") }
    ListGroup {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.groupMargin
        Layout.rightMargin: Theme.groupMargin
        ListRow {
            text: page.visualState === "unreachable" ? i18nc("@info", "Cannot confirm the sign-in right now") : page.visualState === "loading" ? "" : Account.label(page.tr, page.account)
            subtitle: page.kind === "chatgpt" ? (page.account.email || "") : ""
            dot: page.visualState === "loading" ? "" : page.kind === "none" ? "negative" : "positive"
        }
    }
    Note {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.gutter
        Layout.rightMargin: Theme.gutter
        Layout.topMargin: Theme.spaceM
        visible: !["loading", "unreachable"].includes(page.visualState)
        text: Account.billing(page.tr, page.account)
    }
    Note {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.gutter
        Layout.rightMargin: Theme.gutter
        Layout.topMargin: Theme.spaceS
        visible: !!(page.login && page.login.error)
        tone: "negative"
        text: page.login && page.login.error ? page.login.error : ""
    }

    Note {
        Layout.fillWidth: true
        visible: page.visualState === "unreachable"
        text: i18nc("@info", "Cannot connect to Codex. Your sign-in has not been changed.")
    }
    PillButton {
        visible: page.visualState === "unreachable"
        text: i18nc("@action:button", "Check again")
        onClicked: AgentClient.request("Setup")
    }

    // ---- the ways to sign in ----------------------------------------------------------------
    SectionLabel { Layout.fillWidth: true; visible: page.choicesShown; text: i18nc("@title:group", "Sign in with") }
    ListGroup {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.groupMargin
        Layout.rightMargin: Theme.groupMargin
        visible: page.choicesShown
        ChoiceRow {
            text: i18nc("@option:radio how Codex signs in", "ChatGPT account")
            subtitle: i18nc("@info", "Sign in in a browser, on any device. Counts against your ChatGPT plan.")
            checked: page.kind === "chatgpt"
            onClicked: if (page.kind !== "chatgpt") { page.login = null; AgentClient.request("CodexLogin", ["chatgpt"]) }
        }
        ChoiceRow {
            text: i18nc("@option:radio how Codex signs in", "API key")
            subtitle: page.key.set ? i18nc("@info", "The configured OpenAI API key. Billed to the OpenAI API by use.")
                : i18nc("@info", "Set an OpenAI API key first")
            enabled: page.key.set === true || page.kind === "apiKey"
            checked: page.kind === "apiKey"
            onClicked: if (page.kind !== "apiKey") page.confirming = true
        }
    }

    // ---- confirming the API key -------------------------------------------------------------
    ColumnLayout {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.gutter
        Layout.rightMargin: Theme.gutter
        Layout.topMargin: Theme.spaceL
        visible: page.confirmShown
        spacing: Theme.spaceM
        Text {
            Layout.fillWidth: true
            text: i18nc("@title", "Sign Codex in with the API key?")
            wrapMode: Text.Wrap
            font.family: Theme.fontFamily
            font.pixelSize: Theme.heroSize
            font.weight: Font.DemiBold
            color: Theme.text
        }
        Text {
            Layout.fillWidth: true
            text: i18nc("@info", "The Agent's tasks will then be billed to the OpenAI API by use, no longer to your ChatGPT plan. You can sign in with ChatGPT again here.")
            wrapMode: Text.Wrap
            font.family: Theme.fontFamily
            font.pixelSize: Theme.bodySize
            lineHeight: Theme.bodyLine
            lineHeightMode: Text.FixedHeight
            color: Theme.text
        }
    }

    // ---- a device-code sign-in -----------------------------------------------------------------
    ColumnLayout {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.gutter
        Layout.rightMargin: Theme.gutter
        Layout.topMargin: Theme.spaceL
        visible: page.codeShown
        spacing: Theme.spaceM
        Text {
            Layout.fillWidth: true
            text: page.login && page.login.verificationUrl
                ? i18nc("@info a link; keep the markup", "On any device, open <a href='%1'>%1</a> and enter this code:", page.login.verificationUrl) : ""
            textFormat: Text.StyledText
            linkColor: Theme.link
            wrapMode: Text.Wrap
            font.family: Theme.fontFamily
            font.pixelSize: Theme.bodySize
            color: Theme.text
            onLinkActivated: link => Qt.openUrlExternally(link)
        }
        Text {
            text: page.login ? page.login.userCode || "" : ""
            font.family: Theme.monoFamily
            font.pixelSize: 28
            font.weight: Font.DemiBold
            color: Theme.text
        }
        RowLayout {
            spacing: Theme.spaceS
            BusyRing {}
            Text { text: i18nc("@info:status", "Waiting for the sign-in in the browser…"); color: Theme.dim; font.family: Theme.fontFamily; font.pixelSize: Theme.metaSize }
        }
    }

    // ---- the API key's own page ---------------------------------------------------------------
    Item { implicitHeight: Theme.spaceXxl; visible: page.choicesShown }
    ListGroup {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.groupMargin
        Layout.rightMargin: Theme.groupMargin
        visible: page.choicesShown
        ListRow {
            text: "OpenAI API Key"
            subtitle: i18nc("@info what the API key is for", "Voice, calls and optional Luna desktop operation")
            value: page.key.set ? page.key.masked : i18nc("@info the API key", "Not set")
            valueMono: page.key.set === true
            accessory: "chevron"
            onClicked: page.push("KeyPage.qml")
        }
    }
    Item { implicitHeight: Theme.spaceXxl }

    footer: [
        PrimaryButton {
            Layout.fillWidth: true
            visible: page.confirmShown
            text: i18nc("@action:button", "Use the API key")
            onClicked: AgentClient.request("CodexLogin", ["apiKey"])
        },
        SecondaryButton {
            Layout.fillWidth: true
            visible: page.confirmShown || page.codeShown
            text: i18nc("@action:button", "Cancel")
            onClicked: {
                if (page.codeShown) AgentClient.request("CancelCodexLogin")
                page.confirming = false
                page.login = null
            }
        }
    ]
}
