// SPDX-License-Identifier: GPL-2.0-or-later
// Agent Suggestions on the home screen (docs/research/96): the few cards the Agent picked,
// as a stack. One card at a time; swiping up brings the next one from below, like a pager
// (never a scrolling list, never a half-cut card). A tap opens a conversation about the
// card; "Not now" hides it until its records change.
// Deck states: cards, empty, firstrun, offline. `forced*` feed the state gallery.
import QtQuick
import QtQuick.Window
import com.rungic.design
import com.rungic.suggestions
import org.kde.ki18n
import "UsageText.js" as UsageText

Item {
    id: widget
    implicitWidth: 340
    implicitHeight: 330
    property bool activeView: visible && Window.active

    // State gallery: fixed data instead of the service.
    property string forcedState: ""
    property var forcedCards: null
    property var forcedBriefing: null
    property var forcedItems: null
    property int forcedIndex: -1
    property real forcedOffset: 0
    // The voice agent's Codex backend sorts the cards; its provider descriptor ships the mark.
    property string agentName: "Codex"
    property var agentIcon: ({ light: "/usr/share/rungic/agent-usage/icons/codex-light.svg",
                               dark: "/usr/share/rungic/agent-usage/icons/codex-dark.svg" })

    readonly property bool live: forcedCards === null && forcedState === ""
    readonly property var cards: forcedCards !== null ? forcedCards : client.cards
    readonly property var briefing: forcedBriefing !== null ? forcedBriefing : client.briefing
    readonly property var items: forcedItems !== null ? forcedItems : client.items
    readonly property string deckState: forcedState !== "" ? forcedState
        : cards.length > 0 ? "cards"
        : live && client.error !== "" ? "offline"
        : !briefing.generatedAt ? "firstrun" : "empty"

    // The card on top is kept by id across refreshes: a new briefing never jumps the user
    // to another card unless the one they were reading is gone.
    property string currentId: ""
    readonly property int current: {
        if (forcedIndex >= 0) return Math.min(forcedIndex, Math.max(0, cards.length - 1))
        const i = cards.findIndex(card => card.id === currentId)
        return i >= 0 ? i : 0
    }
    readonly property var currentCard: cards[current] || ({})
    property real dragOffset: forcedOffset
    property int targetIndex: -1
    readonly property bool moving: settle.running || dragOffset !== 0
    readonly property int neighbour: targetIndex >= 0 ? targetIndex : current + (dragOffset < 0 ? 1 : -1)
    property string openingId: ""
    property double now: Date.now() / 1000

    readonly property int edges: deckState === "cards" ? Math.min(2, cards.length - 1) : 0
    readonly property real faceHeight: height - edges * 8
    readonly property int bodyLines: height >= 420 ? 8 : height >= 300 ? 4 : 0

    KI18nContext { id: l10n; translationDomain: "rungic-suggestions" }
    SuggestionsClient { id: client }
    UsageClient { id: usage }
    readonly property var agentProvider: usage.providers.find(p => p.id === "codex") || ({})
    readonly property bool canAskAgent: UsageText.canAsk(agentProvider)
    Timer { interval: 60000; repeat: true; running: widget.activeView && widget.live; onTriggered: usage.refresh() }
    Connections {
        target: client
        function onReplied(id, action, result) { if (action === "openCard") widget.openingId = "" }
    }
    onActiveViewChanged: if (live) client.watching(activeView)
    Component.onCompleted: if (live) client.watching(activeView)
    Component.onDestruction: if (live) client.watching(false)
    Timer { interval: 60000; repeat: true; running: widget.activeView; onTriggered: widget.now = Date.now() / 1000 }

    function ago(seconds) {
        const minutes = Math.floor(Math.max(0, now - seconds) / 60)
        if (minutes < 1) return l10n.i18nc("@info relative time", "just now")
        if (minutes < 60) return l10n.i18ncp("@info relative time", "%1 min ago", "%1 min ago", minutes)
        const hours = Math.floor(minutes / 60)
        if (hours < 24) return l10n.i18ncp("@info relative time", "%1 h ago", "%1 h ago", hours)
        return new Date(seconds * 1000).toLocaleDateString(Qt.locale(), Locale.ShortFormat)
    }
    readonly property string meta: {
        if (briefing.curating) return l10n.i18nc("@info the agent is choosing the cards again", "Sorting new findings…")
        // A fallback card is either waiting for the first sort or stands in for an agent that failed.
        if (deckState === "cards" && briefing.source === "fallback")
            return canAskAgent ? l10n.i18nc("@info the agent has not chosen the cards yet", "Not sorted yet") : ""
        if (!briefing.generatedAt) return ""
        return deckState === "empty" ? l10n.i18nc("@info %1 relative time", "Checked %1", ago(briefing.generatedAt)) : ago(briefing.generatedAt)
    }
    // A card whose records have an investigation running: the Agent is already on it.
    function task(card) {
        for (const ref of card.refs || []) {
            const item = items.find(i => i.id === ref)
            if (item && item.state === "working") return item
        }
        return null
    }
    function workingText(card) {
        const item = task(card)
        if (!item) return ""
        const started = item.task && item.task.started ? item.task.started : now
        const minutes = Math.max(1, Math.round((now - started) / 60))
        return l10n.i18ncp("@info %2 the agent's name, %1 minutes", "%2 is on it · %1 min", "%2 is on it · %1 min", minutes, agentName)
    }
    function activate(part) {
        if (deckState === "empty") { if (part !== "secondary") client.open(); return }
        if (deckState !== "cards" || !currentCard.id) return
        const item = task(currentCard)
        if (part === "secondary") { if (!item) client.dismissCard(currentCard.id); return }
        if (openingId !== "") return   // a second tap while the first opens would start a second conversation
        client.presentCard(currentCard.id, true)
        if (item && item.conversation) { client.conversation(item.conversation); return }
        if (!canAskAgent && !item) { client.open((currentCard.refs || [])[0] || ""); return }
        openingId = currentCard.id
        client.openCard(currentCard.id)
    }

    Accessible.role: Accessible.Grouping
    Accessible.name: l10n.i18n("Agent Suggestions")

    // Only a card the user could actually read counts as presented.
    Timer {
        id: presented
        interval: 800
        running: widget.live && widget.activeView && !widget.moving && widget.deckState === "cards"
        property var sent: ({})
        onTriggered: {
            const id = widget.currentCard.id
            if (id && !sent[id]) { client.presentCard(id, false); sent[id] = true }
        }
    }
    onCurrentChanged: presented.restart()

    Item {
        id: deck
        anchors.fill: parent
        clip: widget.moving

        // The edges of the cards below: the stack shows there is more without a list.
        Repeater {
            model: widget.moving ? 0 : widget.edges
            Rectangle {
                required property int index
                x: 10 * (index + 1); width: deck.width - 20 * (index + 1)
                y: widget.faceHeight - 32 + 8 * (index + 1); height: 32
                z: -index - 1
                radius: Theme.radiusSheet
                color: index === 0 ? Theme.fill : Theme.fill2
                border.width: 1
                border.color: Theme.line
            }
        }
        BriefingCard {
            id: next
            width: deck.width; height: widget.faceHeight
            y: widget.dragOffset + (widget.dragOffset < 0 ? height + 12 : -(height + 12))
            visible: widget.moving && widget.neighbour >= 0 && widget.neighbour < widget.cards.length
            card: widget.cards[widget.neighbour] || ({})
            position: widget.neighbour + 1; count: widget.cards.length
            agentName: widget.agentName; agentIcon: widget.agentIcon
            canAskAgent: widget.canAskAgent
            agentStatus: widget.agentProvider.status || ""
            meta: widget.meta; busy: widget.briefing.curating === true
            workingText: widget.workingText(card)
            bodyLines: widget.bodyLines
            Accessible.ignored: true
        }
        BriefingCard {
            id: top
            width: deck.width; height: widget.faceHeight
            y: widget.dragOffset
            forcedState: widget.deckState === "cards" ? "" : widget.deckState
            card: widget.currentCard
            position: widget.current + 1; count: widget.cards.length
            agentName: widget.agentName; agentIcon: widget.agentIcon
            canAskAgent: widget.canAskAgent
            agentStatus: widget.agentProvider.status || ""
            meta: widget.meta; busy: widget.briefing.curating === true
            opening: widget.openingId !== "" && widget.openingId === widget.currentCard.id
            workingText: widget.deckState === "cards" ? widget.workingText(widget.currentCard) : ""
            bodyLines: widget.bodyLines
            pressedPart: pointer.pressed && !pointer.dragged ? pointer.part : ""
        }
    }

    // Pager: follow the finger; past a quarter of the card (or a quick flick) the next card
    // takes the place of the top one, otherwise it springs back. Past the ends it gives a little.
    function move(distance) {
        if (settle.running) return
        const candidate = current + (distance < 0 ? 1 : -1)
        dragOffset = candidate >= 0 && candidate < cards.length
            ? Math.max(-faceHeight, Math.min(faceHeight, distance))
            : Math.max(-32, Math.min(32, distance * 0.18))
    }
    function finish(velocity, cancelled) {
        if (settle.running) return
        const candidate = current + (dragOffset < 0 ? 1 : -1)
        const commit = !cancelled && candidate >= 0 && candidate < cards.length
            && (Math.abs(dragOffset) >= faceHeight * 0.25
                || (Math.abs(dragOffset) > 16 && Math.abs(velocity) > 650 && velocity * dragOffset > 0))
        targetIndex = commit ? candidate : -1
        slide.to = commit ? (dragOffset < 0 ? -(faceHeight + 12) : faceHeight + 12) : 0
        settle.start()
    }
    function step(direction) {
        if (moving) return
        const candidate = current + direction
        if (candidate >= 0 && candidate < cards.length) currentId = cards[candidate].id
    }
    Keys.onUpPressed: step(1)
    Keys.onDownPressed: step(-1)
    SequentialAnimation {
        id: settle
        NumberAnimation { id: slide; target: widget; property: "dragOffset"; duration: Theme.normal; easing.type: Easing.OutCubic }
        ScriptAction {
            script: {
                if (widget.targetIndex >= 0) widget.currentId = widget.cards[widget.targetIndex].id
                widget.dragOffset = 0
                widget.targetIndex = -1
            }
        }
    }

    // One pointer owner for the whole widget: Folio's outer swipe area would otherwise take
    // the drag halfway through. A long press stays Folio's (widget editing).
    MouseArea {
        id: pointer
        anchors.fill: parent
        enabled: widget.forcedState === "" || widget.forcedCards !== null
        preventStealing: true
        property real startY: 0
        property real lastY: 0
        property double lastTime: 0
        property real velocity: 0
        property bool dragged: false
        property bool held: false
        property string part: ""
        onPressed: mouse => {
            startY = lastY = mouse.y; lastTime = Date.now(); velocity = 0; dragged = false; held = false
            part = top.hitAction(mouse.x - top.x, mouse.y - top.y)
        }
        onPositionChanged: mouse => {
            if (!pressed || held) return
            if (Math.abs(mouse.y - startY) > Qt.styleHints.startDragDistance) dragged = true
            if (!dragged || widget.deckState !== "cards" || widget.cards.length < 2) return
            const now = Date.now()
            velocity = (mouse.y - lastY) * 1000 / Math.max(1, now - lastTime)
            lastY = mouse.y; lastTime = now
            widget.move(mouse.y - startY)
        }
        onPressAndHold: held = true
        onReleased: mouse => {
            const recent = Date.now() - lastTime < 100 ? velocity : 0
            if (dragged) { if (widget.dragOffset !== 0) widget.finish(recent, held); return }
            if (!held) widget.activate(part)
        }
        onCanceled: { if (widget.dragOffset !== 0) widget.finish(0, true); held = true }
        onWheel: wheel => { widget.step(wheel.angleDelta.y < 0 ? 1 : -1); wheel.accepted = true }
    }
}
