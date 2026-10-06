// SPDX-License-Identifier: GPL-2.0-or-later
// The Agent app (docs/59, docs/87): quiet like a chat app. One conversation fills the
// window; the others are in the side panel; settings open on top. The look comes from
// the design system (com.rungic.design), dark or light as the system is unless the
// settings say otherwise.
import QtQuick
import QtQuick.Controls as QQC2
import QtCore
import com.rungic.design
import com.rungic.voiceassistant

QQC2.ApplicationWindow {
    id: root
    // --conversation ID: open straight in that conversation (the overlay's "open in app").
    property string initialConversation: ""
    property string initialPage: ""
    property bool initialSuggestions: false
    property string initialSuggestion: ""
    title: "Agent"
    width: 390
    height: 844
    visible: true
    // Flat: the navigation panel below takes this same colour (RungicVoiceAssistant.colors).
    color: Theme.background

    Settings {
        id: settings
        category: "App"
        property string theme: "system"         // system | light | dark
        property bool readAnswers: true
        property bool handsFreeAutoSend: false
    }
    Binding { target: Theme; property: "mode"; value: settings.theme }
    property alias settings: settings
    // The conversation is in front of the user (docs/89): the voice then says less.
    readonly property bool watching: visible && active && Qt.application.state === Qt.ApplicationActive
    onWatchingChanged: AgentClient.setWatching(watching)

    // Pages slide over from the right, as settings pages do on the phone, and a drag to the right
    // anywhere on one goes back (the design system's PageStack, docs/102).
    PageStack {
        id: stack
        anchors.fill: parent
        initialItem: ChatPage { id: chat }
    }

    function openSettings(page) {
        stack.push(Qt.resolvedUrl(page || "SettingsPage.qml"))
    }
    function back() { if (stack.depth > 1) stack.pop() }
    function openAgentPage(page) {
        while (stack.depth > 1) stack.pop(null)
        if (page === "usage") stack.push(Qt.resolvedUrl("UsagePage.qml"))
        else if (page === "sign-in") stack.push(Qt.resolvedUrl("AccountPage.qml"))
    }
    function openSuggestions(id) {
        while (stack.depth > 1) stack.pop(null)
        stack.push(Qt.resolvedUrl("SuggestionsPage.qml"), { suggestionId: id || "" })
    }

    // Called over D-Bus (a second start, the overlay's "open in app").
    function openConversation(id) {
        while (stack.depth > 1) stack.pop(null)
        chat.open(id, "")
    }
    Component.onCompleted: {
        AgentClient.setWatching(watching)
        if (initialConversation) openConversation(initialConversation)
        if (initialPage) openAgentPage(initialPage)
        if (initialSuggestions) openSuggestions(initialSuggestion)
    }

    // The back key closes a settings page, then the side panel.
    onClosing: close => {
        if (stack.depth > 1) { stack.pop(); close.accepted = false }
    }
}
