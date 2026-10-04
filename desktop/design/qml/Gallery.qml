// SPDX-License-Identifier: GPL-2.0-or-later
// The state gallery (docs/87): every control of the design system in every state it has,
// forced with `forcedState`, so states are checked side by side (light and dark) instead of
// by pressing things on the phone. rungic-design-gallery [--theme light|dark] [--section NAME].
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import com.rungic.design

QQC2.ApplicationWindow {
    id: gallery
    property string initialTheme: "system"
    property string section: ""                 // show only these sections, comma-separated (screenshots)
    visible: true
    width: 390
    height: 844
    title: DesignI18n.i18nc("@title:window", "Rungic Design System · States")
    color: Theme.background
    Component.onCompleted: Theme.mode = initialTheme

    readonly property var buttonStates: ["normal", "pressed", "checked", "disabled"]
    // A picture for Thumbnail and ImageViewer: a 4:3 sky with a sun.
    readonly property string samplePicture: "data:image/svg+xml;utf8," + encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><rect width="400" height="300" fill="#3b6ea5"/>'
        + '<rect y="200" width="400" height="100" fill="#2d5a3c"/><circle cx="300" cy="90" r="40" fill="#f5c542"/></svg>')
    ImageViewer { id: viewer }
    ChoiceSheet {
        id: themeSheet
        title: DesignI18n.i18nc("@title sample", "Theme")
        choices: [{ value: "system", text: DesignI18n.i18nc("@item sample", "System") }, { value: "light", text: DesignI18n.i18nc("@item sample", "Light") },
                  { value: "dark", text: DesignI18n.i18nc("@item sample", "Dark") }]
        current: Theme.mode
        onChosen: value => Theme.mode = value
    }

    Flickable {
        anchors.fill: parent
        contentHeight: column.implicitHeight + 40
        clip: true
        ColumnLayout {
            id: column
            x: 16
            width: parent.width - 32
            spacing: 6
            RowLayout {
                Layout.topMargin: 12
                Text {
                    Layout.fillWidth: true
                    text: Theme.dark ? DesignI18n.i18nc("@title", "States · Dark") : DesignI18n.i18nc("@title", "States · Light")
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.headingSize
                    font.weight: Font.DemiBold
                    color: Theme.text
                }
                PillButton { text: Theme.dark ? DesignI18n.i18nc("@action:button switch to the light look", "Light") : DesignI18n.i18nc("@action:button switch to the dark look", "Dark"); onClicked: Theme.mode = Theme.dark ? "light" : "dark" }
            }

            Section {
                name: "Icon colours"
                Repeater {
                    model: ["#ffffff", "strongInk", "#ff3333", "#000000"]
                    Variant {
                        label: modelData
                        Rectangle {
                            width: 44; height: 44; radius: 22; color: modelData === "strongInk" ? Theme.strong : "#232629"
                            Icon { anchors.centerIn: parent; name: "send"; color: modelData === "strongInk" ? Theme.strongInk : modelData }
                        }
                    }
                }
                Variant { label: "strongInk=" + Theme.strongInk; Item { width: 1; height: 1 } }
            }
            Section {
                name: "IconButton"
                Repeater {
                    model: gallery.buttonStates
                    Variant { label: modelData; IconButton { iconName: "keyboard"; text: DesignI18n.i18nc("@info sample text", "Keyboard"); forcedState: modelData } }
                }
                Repeater {
                    model: gallery.buttonStates
                    Variant { label: "small " + modelData; IconButton { small: true; iconName: "copy"; text: DesignI18n.i18nc("@info sample text", "Copy"); forcedState: modelData } }
                }
            }
            Section {
                name: "CircleButton"
                Repeater {
                    model: ["normal", "pressed", "disabled"]
                    Variant { label: "stop " + modelData; CircleButton { iconName: "stop"; text: DesignI18n.i18nc("@info sample text", "Stop"); forcedState: modelData } }
                }
                Repeater {
                    model: ["normal", "pressed", "disabled"]
                    Variant { label: "send " + modelData; CircleButton { iconName: "send"; text: DesignI18n.i18nc("@info sample text", "Send"); forcedState: modelData } }
                }
            }
            Section {
                name: "PillButton"
                Repeater {
                    model: gallery.buttonStates
                    Variant { label: modelData; PillButton { iconName: "headset"; text: DesignI18n.i18nc("@info sample text", "Listen in"); forcedState: modelData } }
                }
                Repeater {
                    model: gallery.buttonStates
                    Variant { label: "negative " + modelData; PillButton { iconName: "hang-up"; text: DesignI18n.i18nc("@info sample text", "Hang up"); negative: true; forcedState: modelData } }
                }
            }
            Section {
                name: "PrimaryButton"
                wide: true
                Repeater {
                    model: ["normal", "pressed", "disabled", "busy"]
                    Variant { label: modelData; wide: true; PrimaryButton { width: 300; iconName: "download"; text: DesignI18n.i18nc("@info sample text", "Install Codex"); forcedState: modelData } }
                }
            }
            Section {
                name: "SecondaryButton"
                wide: true
                Repeater {
                    model: ["normal", "pressed", "disabled"]
                    Variant { label: modelData; wide: true; SecondaryButton { width: 300; text: DesignI18n.i18nc("@info sample text", "Remove key"); negative: true; forcedState: modelData } }
                }
            }
            Section {
                name: "NavItem"
                wide: true
                Repeater {
                    model: ["normal", "pressed", "current", "disabled"]
                    Variant { label: modelData; wide: true; NavItem { width: 300; iconName: "settings"; text: DesignI18n.i18nc("@info sample text", "Settings"); forcedState: modelData } }
                }
            }
            Section {
                name: "ListRow"
                wide: true
                Repeater {
                    model: ["normal", "pressed", "disabled"]
                    Variant {
                        label: modelData
                        wide: true
                        ListGroup {
                            width: 300
                            ListRow { text: "Codex"; value: DesignI18n.i18nc("@info sample text", "Installed"); dot: "positive"; accessory: "chevron"; forcedState: modelData }
                            ListRow { text: "API Key"; value: "sk-…3f9a"; valueMono: true; accessory: "chevron"; forcedState: modelData }
                        }
                    }
                }
            }
            // Rows of choices and switches (docs/102): pressed like every row that can be tapped.
            Section {
                name: "ChoiceRow"
                wide: true
                Repeater {
                    model: [["normal", false], ["pressed", false], ["normal", true], ["pressed", true], ["disabled", false]]
                    Variant {
                        label: modelData[0] + (modelData[1] ? " · on" : " · off")
                        wide: true
                        ListGroup {
                            width: 300
                            ChoiceRow {
                                text: "GPT-6.1-Sol"
                                subtitle: DesignI18n.i18nc("@info sample text", "Latest workhorse model for coding and everyday work.")
                                checked: modelData[1]
                                enabled: modelData[0] !== "disabled"
                                forcedState: modelData[0]
                            }
                        }
                    }
                }
            }
            Section {
                name: "ToggleRow"
                wide: true
                Repeater {
                    model: [["normal", false], ["pressed", false], ["normal", true], ["pressed", true], ["disabled", true]]
                    Variant {
                        label: modelData[0] + (modelData[1] ? " · on" : " · off")
                        wide: true
                        ListGroup {
                            width: 300
                            ToggleRow {
                                text: DesignI18n.i18nc("@info sample text", "Read answers aloud")
                                checked: modelData[1]
                                enabled: modelData[0] !== "disabled"
                                forcedState: modelData[0]
                            }
                        }
                    }
                }
            }
            Section {
                name: "ChoiceSheet"
                Variant {
                    label: "open"
                    PillButton { text: DesignI18n.i18nc("@action:button sample", "Theme…"); onClicked: themeSheet.open() }
                }
            }
            Section {
                name: "Toggle"
                Repeater {
                    model: ["off", "on", "pressed-off", "pressed-on", "disabled-off", "disabled-on"]
                    Variant { label: modelData; Toggle { text: DesignI18n.i18nc("@info sample text", "Switch"); forcedState: modelData } }
                }
            }
            Section {
                name: "RadioMark"
                Repeater {
                    model: ["off", "on", "disabled-off", "disabled-on"]
                    Variant { label: modelData; RadioMark { forcedState: modelData } }
                }
            }
            Section {
                name: "Tile"
                Repeater {
                    model: ["normal", "pressed", "disabled"]
                    Variant { label: modelData; Rectangle { width: 104; height: 96; color: Theme.side; Tile { anchors.fill: parent; anchors.margins: 6; iconName: "image"; text: DesignI18n.i18nc("@info sample text", "Photos"); forcedState: modelData } } }
                }
            }
            Section {
                name: "PlanStep"
                wide: true
                Repeater {
                    model: ["pending", "active", "done"]
                    Variant { label: modelData; wide: true; PlanStep { width: 300; text: DesignI18n.i18nc("@info sample text", "Render a small teacup in Blender"); forcedState: modelData } }
                }
            }
            Section {
                name: "ActivityCard"
                wide: true
                Variant { label: "thinking"; wide: true; ActivityCard { width: 330 } }
                Variant { label: "working"; wide: true; ActivityCard { width: 330; kind: "files"; text: DesignI18n.i18nc("@info sample text", "Create make_teacup.py"); detail: DesignI18n.i18nc("@info sample text", "+85 −0 lines"); seconds: 4 } }
                Variant { label: "progress"; wide: true; ActivityCard { width: 330; kind: "command"; text: DesignI18n.i18nc("@info sample text", "Run Blender in the background (make_teacup.py)"); detail: "Fra:1 Mem:212M | Rendering | Sample 38/64"; progress: 0.59; seconds: 27 } }
            }
            Section {
                name: "LivePicture"
                Variant { label: "waiting"; LivePicture { maxWidth: 150; maxHeight: 150; text: DesignI18n.i18nc("@info sample text", "Blender is starting the render"); progress: 0 } }
                Variant { label: "live"; LivePicture { maxWidth: 150; maxHeight: 150; source: gallery.samplePicture; text: DesignI18n.i18nc("@info sample text", "28/64 samples"); progress: 0.44 } }
                Variant { label: "done"; LivePicture { maxWidth: 150; maxHeight: 150; source: gallery.samplePicture; text: DesignI18n.i18nc("@info sample text", "Render finished"); finished: true } }
                Variant { label: "pressed"; LivePicture { maxWidth: 150; maxHeight: 150; source: gallery.samplePicture; text: DesignI18n.i18nc("@info sample text", "28/64 samples"); progress: 0.44; down: true } }
            }
            Section {
                name: "Thumbnail"
                Repeater {
                    model: ["loading", "ready", "pressed", "error"]
                    Variant { label: modelData; Thumbnail { source: gallery.samplePicture; name: DesignI18n.i18nc("@info sample text", "little-rocket.png"); maxWidth: 150; maxHeight: 150; forcedState: modelData } }
                }
            }
            Section {
                name: "FileChip"
                Repeater {
                    model: ["normal", "pressed", "disabled"]
                    Variant { label: modelData; FileChip { name: DesignI18n.i18nc("@info sample text", "little-rocket.blend"); maxWidth: 200; forcedState: modelData } }
                }
            }
            Section {
                name: "ImageViewer"
                Repeater {
                    model: ["loading", "ready", "error"]
                    Variant { label: modelData; PillButton { text: DesignI18n.i18nc("@action:button %1 is a state name", "View · %1", modelData); onClicked: { viewer.forcedState = modelData; viewer.show(gallery.samplePicture, DesignI18n.i18nc("@info sample text", "little-rocket.png")) } } }
                }
            }
            Section {
                name: "MetaButton"
                Repeater {
                    model: ["collapsed", "expanded", "pressed"]
                    Variant { label: modelData; MetaButton { text: DesignI18n.i18nc("@info sample text", "Worked through 2 steps"); forcedState: modelData } }
                }
            }
            Section {
                name: "HoldTarget"
                Repeater {
                    model: ["idle", "active"]
                    Variant { label: "cancel " + modelData; HoldTarget { iconName: "close"; text: DesignI18n.i18nc("@info sample text", "Cancel"); forcedState: modelData } }
                }
                Repeater {
                    model: ["idle", "active"]
                    Variant { label: "text " + modelData; HoldTarget { iconName: "text"; text: DesignI18n.i18nc("@info sample text", "To text"); edit: true; forcedState: modelData } }
                }
            }
            Section {
                name: "VoiceBar"
                wide: true
                Repeater {
                    model: ["idle", "pressed", "hot", "cancel", "handsFree", "disabled"]
                    Variant {
                        label: modelData
                        wide: true
                        VoiceBar {
                            id: vb
                            width: 330
                            forcedState: modelData
                            IconButton { iconName: "plus"; text: DesignI18n.i18nc("@info sample text", "Add"); tint: vb.ink; visible: vb.visualState === "idle" || vb.visualState === "pressed" || vb.visualState === "disabled" }
                            Wave {
                                visible: vb.visualState === "hot" || vb.visualState === "cancel" || vb.visualState === "handsFree"
                                Layout.fillWidth: true
                                Layout.leftMargin: 10
                                bars: 24
                                color: vb.ink
                                active: vb.visualState !== "cancel"
                                clip: true
                            }
                            Text {
                                visible: vb.visualState === "idle" || vb.visualState === "pressed" || vb.visualState === "disabled"
                                Layout.fillWidth: true
                                horizontalAlignment: Text.AlignHCenter
                                text: DesignI18n.i18nc("@info sample text", "Hold to talk")
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.bodySize
                                font.weight: Font.DemiBold
                                color: vb.ink
                            }
                            CircleButton { visible: vb.visualState === "handsFree"; iconName: "stop"; text: DesignI18n.i18nc("@info sample text", "Stop listening") }
                            IconButton { iconName: "keyboard"; text: DesignI18n.i18nc("@info sample text", "Keyboard"); tint: vb.ink; visible: vb.visualState === "idle" || vb.visualState === "pressed" || vb.visualState === "disabled" }
                        }
                    }
                }
            }
            // ---- the call with the Agent: where it starts, the bar, the panel -----------------
            Section {
                name: "Call · entry"
                wide: true
                Variant {
                    label: "conversation top bar: call"
                    wide: true
                    SampleTopBar { callState: "normal" }
                }
                Variant {
                    label: "cannot call now (the reason under it)"
                    wide: true
                    Column {
                        spacing: 2
                        SampleTopBar { callState: "disabled" }
                        Text {
                            width: 330
                            horizontalAlignment: Text.AlignHCenter
                            text: DesignI18n.i18nc("@info sample text", "The Agent is on a call for you: call it when that ends")
                            wrapMode: Text.Wrap
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.labelSize
                            color: Theme.dim
                        }
                    }
                }
                Variant {
                    label: "in a call: the bar under the top bar"
                    wide: true
                    Column {
                        spacing: 4
                        SampleTopBar { callState: "hidden" }
                        CallBar { width: 330; forcedState: "agent"; label: DesignI18n.i18nc("@info sample text", "Answering"); detail: "04:12 · 1 task running" }
                    }
                }
                Variant {
                    label: "conversations panel: call without opening one"
                    wide: true
                    Rectangle {
                        width: 330; height: Theme.topBar; radius: Theme.radiusM; color: Theme.side
                        Text {
                            anchors { left: parent.left; leftMargin: Theme.spaceL; verticalCenter: parent.verticalCenter }
                            text: DesignI18n.i18nc("@info sample text", "Conversations")
                            font.family: Theme.fontFamily; font.pixelSize: Theme.titleSize; font.weight: Font.DemiBold; color: Theme.text
                        }
                        Row {
                            anchors { right: parent.right; rightMargin: 6; verticalCenter: parent.verticalCenter }
                            IconButton { iconName: "phone"; text: DesignI18n.i18nc("@info sample text", "Call the Agent") }
                            IconButton { iconName: "compose"; text: DesignI18n.i18nc("@info sample text", "New conversation") }
                        }
                    }
                }
            }
            Section {
                name: "CallBar"
                wide: true
                Repeater {
                    model: [
                        ["connecting", "Connecting…", ""],
                        ["reconnecting", "Reconnecting…", "Wait a moment, then say it again in full"],
                        ["answer", "Waiting for your answer", "Where should the screenshots go? · 05:03"],
                        ["agent", "Answering", "04:12 · 1 task running"],
                        ["you", "Listening", "04:12"],
                        ["thinking", "Working", "04:12"],
                        ["muted", "Microphone off", "04:12 · The Agent can't hear you"],
                        ["idle", "On a call", "04:12 · 2 tasks running"],
                        ["elsewhere", "Call in another conversation", "Tidy up the Downloads folder · 04:12"]
                    ]
                    Variant {
                        required property var modelData
                        label: modelData[0]
                        wide: true
                        CallBar {
                            width: 330
                            forcedState: modelData[0]
                            label: DesignI18n.i18nc("@info sample text", modelData[1])
                            detail: modelData[2]
                        }
                    }
                }
            }
            Section {
                name: "CallPanel"
                wide: true
                Repeater {
                    model: [
                        ["agent", "Answering"],
                        ["answer", "Waiting for your answer"],
                        ["muted", "Microphone off"],
                        ["connecting", "Connecting…"]
                    ]
                    Variant {
                        required property var modelData
                        label: modelData[0]
                        wide: true
                        Rectangle {
                            width: 358
                            height: callPanel.implicitHeight + 2 * Theme.spaceXl
                            radius: Theme.radiusSheet
                            color: Theme.background
                            border.color: Theme.line
                            CallPanel {
                                id: callPanel
                                x: Theme.spaceL; y: Theme.spaceXl
                                width: parent.width - 2 * Theme.spaceL
                                forcedState: modelData[0]
                                title: DesignI18n.i18nc("@info sample text", "Call with the Agent")
                                detail: modelData[0] === "connecting" ? "" : "04:12 · Tidy up the Downloads folder"
                                label: DesignI18n.i18nc("@info sample text", modelData[1])
                                tasks: modelData[0] === "connecting" ? [] : [
                                    {text: "Pick the screenshots out of Downloads", status: "running", statusText: "Working · 2 steps"},
                                    {text: "Which album new photos go to", status: modelData[0] === "answer" ? "answer" : "queued",
                                     statusText: modelData[0] === "answer" ? "Waiting for your answer" : "Queued"},
                                    {text: "How much storage is left", status: "done", statusText: "Done · 41 GB free"}
                                ]
                            }
                        }
                    }
                }
            }
            Section {
                name: "SecretField"
                wide: true
                Repeater {
                    model: ["normal", "focused", "error", "disabled"]
                    Variant { label: modelData; wide: true; SecretField { width: 300; text: "sk-proj-xxxxxxxx3f9a"; forcedState: modelData } }
                }
            }
            Section {
                name: "Note"
                wide: true
                Repeater {
                    model: ["", "positive", "negative"]
                    Variant { label: modelData || "plain"; wide: true; Note { width: 300; tone: modelData; text: DesignI18n.i18nc("@info sample text", "The key works. Connected to OpenAI.") } }
                }
            }
            Section {
                name: "Progress · BusyRing · ShineText"
                wide: true
                Variant { label: "progress"; wide: true; Progress { width: 300 } }
                Variant { label: "busy ring"; BusyRing {} }
                Variant { label: "shine"; wide: true; ShineText { width: 300; text: DesignI18n.i18nc("@info sample text", "Working · 12s · Searching WeChat for a contact") } }
            }
        }
    }

    // A titled run of variants.
    component Section: ColumnLayout {
        id: sectionBox
        property string name
        property bool wide: false
        default property alias variants: flow.data
        Layout.fillWidth: true
        Layout.topMargin: 14
        visible: gallery.section === "" || gallery.section.split(",").indexOf(name) >= 0
        spacing: 8
        Text {
            text: sectionBox.name
            font.family: Theme.monoFamily
            font.pixelSize: Theme.labelSize
            color: Theme.dim
        }
        Flow {
            id: flow
            Layout.fillWidth: true
            spacing: 12
        }
    }
    // The conversation page's top bar (ChatPage), for the call's entry: `callState` normal,
    // disabled, or hidden (in a call: the CallBar under it says so).
    component SampleTopBar: Item {
        property string callState: "normal"
        width: 330
        height: Theme.topBar
        IconButton {
            anchors { left: parent.left; verticalCenter: parent.verticalCenter }
            iconName: "menu"
            text: DesignI18n.i18nc("@info sample text", "Conversations")
        }
        Text {
            anchors { left: parent.left; right: parent.right; leftMargin: 52; rightMargin: 96; verticalCenter: parent.verticalCenter }
            horizontalAlignment: Text.AlignHCenter
            text: DesignI18n.i18nc("@info sample text", "Tidy the Downloads folder")
            elide: Text.ElideRight
            font.family: Theme.fontFamily; font.pixelSize: Theme.titleSize; font.weight: Font.DemiBold; color: Theme.text
        }
        Row {
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            IconButton {
                visible: parent.parent.callState !== "hidden"
                iconName: "phone"
                text: DesignI18n.i18nc("@info sample text", "Call the Agent")
                forcedState: parent.parent.callState === "disabled" ? "disabled" : ""
            }
            IconButton { iconName: "compose"; text: DesignI18n.i18nc("@info sample text", "New conversation") }
        }
    }
    // One state: the control above its state's name.
    component Variant: Column {
        property string label
        property bool wide: false
        spacing: 4
        Text {
            text: parent.label
            font.family: Theme.monoFamily
            font.pixelSize: 11
            color: Theme.faint
        }
    }
}
