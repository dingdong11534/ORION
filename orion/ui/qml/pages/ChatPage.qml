import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Rectangle {
    id: chatPage
    required property var root
    required property var chatModel
    required property var conversationsModel
    property var knowledgeSourcesModel: null
    property string pickerQuery: ""
    property string pickerFormat: "all"
    property alias chatListView: chatList
    property alias composerField: composer

    property int recordingSeconds: 0

    color: Theme.window

    Timer {
        id: recTimer
        interval: 1000
        repeat: true
        running: (typeof orion !== "undefined" && orion !== null) && orion.voiceState === "recording"
        onRunningChanged: {
            if (running) chatPage.recordingSeconds = 0
        }
        onTriggered: chatPage.recordingSeconds += 1
    }

    function formatTimer(secs) {
        const m = Math.floor(secs / 60)
        const s = secs % 60
        return m + ":" + (s < 10 ? ("0" + s) : ("" + s))
    }

    function insertTranscript(text) {
        if (!text) return
        const pos = composer.cursorPosition
        const cur = composer.text
        const prefix = (pos > 0 && cur[pos - 1] !== " " && cur[pos - 1] !== "\n") ? " " : ""
        composer.insert(pos, prefix + text)
        composer.forceActiveFocus()
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // Conversation list panel (hidden below 1000px to keep chat spacious)
        Rectangle {
            visible: chatPage.width >= 980
            Layout.preferredWidth: 236
            Layout.fillHeight: true
            color: Theme.sidebar
            border.width: 1
            border.color: Theme.hairline

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Theme.spaceMd
                spacing: Theme.spaceSm

                OrionButton {
                    objectName: "newChatButton"
                    Layout.fillWidth: true
                    text: "+ New Chat"
                    variant: "secondary"
                    enabled: !root.busy
                    onClicked: root.startNewChat()
                }

                Text {
                    text: "Conversations"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontCaption
                    font.weight: Font.Medium
                    color: Theme.textSecondary
                    textFormat: Text.PlainText
                }

                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: Theme.spaceXs
                    model: chatPage.conversationsModel
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    delegate: RowLayout {
                        width: ListView.view.width
                        spacing: Theme.spaceXs

                        OrionListRow {
                            Layout.fillWidth: true
                            title: model.title || "New Conversation"
                            subtitle: model.relative_time || ""
                            selected: model.id === root.activeConversationId
                            onClicked: {
                                if (typeof orion !== "undefined" && orion !== null)
                                    orion.selectConversation(model.id)
                            }
                        }

                        OrionButton {
                            text: "Rename"
                            variant: "quiet"
                            compact: true
                            onClicked: root.openRenameDialog(model.id, model.title || "")
                        }

                        OrionButton {
                            text: "×"
                            variant: "quiet"
                            compact: true
                            onClicked: root.confirmDeleteConversation(model.id, model.title || "Conversation")
                        }
                    }
                }
            }
        }

        // Main Chat Column
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            anchors.margins: Theme.spaceXl
            spacing: Theme.spaceMd

            // Header
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: Theme.spaceLg
                Layout.leftMargin: Theme.spaceXl
                Layout.rightMargin: Theme.spaceXl
                spacing: Theme.spaceMd

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2

                    Text {
                        Layout.fillWidth: true
                        text: root.getActiveTitle()
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontPageTitle
                        font.weight: Font.Medium
                        color: Theme.text
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }

                    Text {
                        Layout.fillWidth: true
                        text: {
                            if (root.knowledgeMode === "ask_knowledge")
                                return "Knowledge · All indexed documents"
                            if (root.knowledgeMode === "selected_sources")
                                return "Knowledge · " + root.selectedKnowledgeIds.length + " selected document(s)"
                            if (root.knowledgeMode === "web_search")
                                return "Web · Public browser search"
                            if (root.knowledgeMode === "web_and_selected")
                                return "Web + Knowledge · " + root.selectedKnowledgeIds.length + " selected document(s)"
                            return "Chat · No external sources"
                        }
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontSecondary
                        color: Theme.textSecondary
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }
                }

                RowLayout {
                    spacing: Theme.spaceXs
                    visible: root.knowledgeAvailable

                    OrionButton {
                        objectName: "modeChatButton"
                        text: "Chat"
                        compact: true
                        variant: root.knowledgeMode === "chat" ? "primary" : "secondary"
                        onClicked: root.setChatKnowledgeMode("chat")
                    }
                    OrionButton {
                        objectName: "modeAskKnowledgeButton"
                        text: "Ask Knowledge"
                        compact: true
                        variant: root.knowledgeMode === "ask_knowledge" ? "primary" : "secondary"
                        onClicked: root.setChatKnowledgeMode("ask_knowledge")
                    }
                    OrionButton {
                        objectName: "modeSelectedButton"
                        text: root.selectedKnowledgeIds.length > 0 ? ("Selected (" + root.selectedKnowledgeIds.length + ")") : "Selected"
                        compact: true
                        variant: root.knowledgeMode === "selected_sources" ? "primary" : "secondary"
                        onClicked: {
                            root.setChatKnowledgeMode("selected_sources")
                            docPickerDialog.open()
                        }
                    }
                    OrionButton {
                        objectName: "modeWebSearchButton"
                        text: "Web"
                        compact: true
                        variant: (root.knowledgeMode === "web_search" || root.knowledgeMode === "web_and_selected") ? "primary" : "secondary"
                        onClicked: {
                            if (root.selectedKnowledgeIds.length > 0)
                                root.setChatKnowledgeMode(root.knowledgeMode === "web_and_selected" ? "web_search" : "web_and_selected")
                            else
                                root.setChatKnowledgeMode("web_search")
                        }
                    }
                }

                OrionButton {
                    visible: chatPage.width < 980
                    text: "+ New Chat"
                    variant: "secondary"
                    compact: true
                    enabled: !root.busy
                    onClicked: root.startNewChat()
                }
            }

            // Voice / General Error Banner
            OrionBanner {
                Layout.fillWidth: true
                Layout.leftMargin: Theme.spaceXl
                Layout.rightMargin: Theme.spaceXl
                message: root.voiceErrorMessage
                severity: "warning"
                actionLabel: "Dismiss"
                onActionClicked: root.voiceErrorMessage = ""
            }

            // Message List + Empty State
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.leftMargin: Theme.spaceXl
                Layout.rightMargin: Theme.spaceXl

                ListView {
                    id: chatList
                    objectName: "chatList"
                    anchors.fill: parent
                    clip: true
                    spacing: Theme.spaceLg
                    model: chatPage.chatModel
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    delegate: Item {
                        id: msgDelegate
                        width: chatList.width
                        height: msgCol.implicitHeight

                        readonly property bool isUser: model.role === "user"
                        readonly property var refsList: {
                            if (!model.referencesJson) return []
                            try { return JSON.parse(model.referencesJson) } catch (e) { return [] }
                        }
                        readonly property var prodAction: {
                            if (!model.productivityActionJson) return null
                            try { return JSON.parse(model.productivityActionJson) } catch (e) { return null }
                        }

                        ColumnLayout {
                            id: msgCol
                            width: isUser ? Math.min(parent.width * 0.72, 640) : Math.min(parent.width * 0.88, 760)
                            anchors.right: isUser ? parent.right : undefined
                            anchors.left: isUser ? undefined : parent.left
                            spacing: Theme.spaceSm

                            // Collapsible Thinking Trace Card (assistant only, never persisted)
                            Rectangle {
                                id: thinkingCard
                                objectName: "thinkingCard"
                                Layout.fillWidth: true
                                clip: true
                                visible: !isUser && ((model.thinkingText || "").length > 0 || model.isThinkingActive)
                                radius: Theme.radiusCard
                                color: Theme.sidebar
                                border.width: 1
                                border.color: Theme.hairline
                                implicitHeight: thinkCol.implicitHeight + Theme.spaceMd * 2

                                ColumnLayout {
                                    id: thinkCol
                                    anchors.fill: parent
                                    anchors.margins: Theme.spaceMd
                                    spacing: Theme.spaceXs

                                    RowLayout {
                                        objectName: "thinkingToggleHeader"
                                        Layout.fillWidth: true
                                        Text {
                                            Layout.fillWidth: true
                                            text: model.isThinkingActive ? "Thinking..." : "Thinking"
                                            font.family: Theme.fontFamily
                                            font.pixelSize: Theme.fontCaption
                                            font.weight: Font.Medium
                                            color: Theme.textSecondary
                                            textFormat: Text.PlainText
                                        }
                                        OrionButton {
                                            text: model.thinkingExpanded ? "Hide" : "Show"
                                            variant: "quiet"
                                            compact: true
                                            onClicked: {
                                                chatPage.chatModel.setProperty(index, "userCollapsedThinking", model.thinkingExpanded)
                                                chatPage.chatModel.setProperty(index, "thinkingExpanded", !model.thinkingExpanded)
                                            }
                                        }
                                    }

                                    ScrollView {
                                        Layout.fillWidth: true
                                        Layout.maximumHeight: 200
                                        contentWidth: availableWidth
                                        visible: model.thinkingExpanded
                                        clip: true

                                        Text {
                                            id: thinkingPlainText
                                            objectName: "thinkingPlainText"
                                            width: parent.width
                                            text: model.thinkingText || ""
                                            font.family: Theme.fontFamily
                                            font.pixelSize: Theme.fontSecondary
                                            color: Theme.textSecondary
                                            wrapMode: Text.WordWrap
                                            textFormat: Text.PlainText
                                        }
                                    }
                                }
                            }

                            // Action Card when productivity action was executed
                            OrionActionCard {
                                Layout.fillWidth: true
                                visible: !isUser && prodAction !== null && prodAction.summary !== undefined
                                headerText: prodAction ? (prodAction.intent || "Action").replace(/_/g, " ") : ""
                                titleText: prodAction ? (prodAction.entity_label || "") : ""
                                failed: prodAction ? (prodAction.status === "failed") : false
                                undoAvailable: prodAction ? Boolean(prodAction.undo_supported && prodAction.action_id) : false
                                onUndoClicked: {
                                    if (prodAction && prodAction.action_id)
                                        orion.undoProductivityAction(prodAction.action_id)
                                }
                            }

                            // Message bubble (user) or clean surface text (assistant)
                            Rectangle {
                                Layout.fillWidth: true
                                visible: (model.text || "").length > 0 || model.status === "pending"
                                radius: Theme.radiusCard
                                color: isUser ? Theme.userBubble : Theme.surface
                                border.width: isUser ? 0 : 1
                                border.color: model.isError ? Theme.danger : Theme.hairline
                                implicitHeight: msgBodyCol.implicitHeight + Theme.spaceMd * 2

                                ColumnLayout {
                                    id: msgBodyCol
                                    anchors.fill: parent
                                    anchors.margins: Theme.spaceMd
                                    spacing: Theme.spaceSm

                                    Text {
                                        Layout.fillWidth: true
                                        text: model.text && model.text.length > 0 ? model.text : (model.status === "pending" ? "Thinking..." : "")
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.fontChat
                                        lineHeight: Theme.chatLineHeight
                                        color: model.isError ? Theme.danger : Theme.text
                                        wrapMode: Text.WordWrap
                                        textFormat: (!isUser && model.status === "completed") ? Text.MarkdownText : Text.PlainText
                                        onLinkActivated: function(link) {
                                            if (link.startsWith("https://") || link.startsWith("http://"))
                                                Qt.openUrlExternally(link)
                                        }
                                    }

                                    // Play / Stop TTS button on completed assistant messages
                                    RowLayout {
                                        visible: !isUser && model.status === "completed" && (model.text || "").length > 0 && (typeof orion !== "undefined" && orion !== null && orion.voiceAvailable)
                                        spacing: Theme.spaceSm

                                        OrionButton {
                                            text: (typeof orion !== "undefined" && orion !== null && orion.voiceState === "speaking") ? "Stop" : "Play"
                                            variant: "quiet"
                                            compact: true
                                            onClicked: {
                                                if (typeof orion !== "undefined" && orion !== null) {
                                                    if (orion.voiceState === "speaking")
                                                        orion.stopSpeaking()
                                                    else
                                                        orion.speakMessage(model.text)
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                OrionEmptyState {
                    anchors.centerIn: parent
                    width: Math.min(parent.width - Theme.space2xl, 460)
                    visible: chatPage.chatModel.count === 0
                    title: "Ready when you are"
                    subtitle: "Ask a question, manage your tasks and habits, or search your local knowledge base."
                }
            }

            // Composer Card
            OrionCard {
                Layout.fillWidth: true
                Layout.leftMargin: Theme.spaceXl
                Layout.rightMargin: Theme.spaceXl
                Layout.bottomMargin: Theme.spaceLg
                implicitHeight: composerCol.implicitHeight + Theme.spaceMd * 2

                ColumnLayout {
                    id: composerCol
                    anchors.fill: parent
                    anchors.margins: Theme.spaceMd
                    spacing: Theme.spaceSm

                    // Active Voice Recording / Transcribing Row
                    RowLayout {
                        visible: (typeof orion !== "undefined" && orion !== null) && (orion.voiceState === "recording" || orion.voiceState === "transcribing")
                        Layout.fillWidth: true
                        spacing: Theme.spaceMd

                        Rectangle {
                            width: 10
                            height: 10
                            radius: 5
                            color: (typeof orion !== "undefined" && orion !== null && orion.voiceState === "recording") ? Theme.danger : Theme.warning
                        }

                        Text {
                            Layout.fillWidth: true
                            text: (typeof orion !== "undefined" && orion !== null && orion.voiceState === "recording")
                                ? ("Recording " + chatPage.formatTimer(chatPage.recordingSeconds) + " · Microphone open")
                                : "Transcribing offline..."
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSecondary
                            font.weight: Font.Medium
                            color: Theme.text
                            textFormat: Text.PlainText
                        }

                        OrionButton {
                            visible: (typeof orion !== "undefined" && orion !== null) && orion.voiceState === "recording"
                            text: "Stop"
                            variant: "primary"
                            compact: true
                            onClicked: if (typeof orion !== "undefined" && orion !== null) orion.stopVoiceRecording()
                        }

                        OrionButton {
                            text: "Cancel"
                            variant: "secondary"
                            compact: true
                            onClicked: if (typeof orion !== "undefined" && orion !== null) orion.cancelVoiceRecording()
                        }
                    }

                    ScrollView {
                        Layout.fillWidth: true
                        Layout.minimumHeight: 40
                        Layout.maximumHeight: 132
                        clip: true

                        TextArea {
                            id: composer
                            objectName: "composer"
                            placeholderText: {
                                if (root.knowledgeMode === "ask_knowledge")
                                    return "Ask across all indexed documents..."
                                if (root.knowledgeMode === "selected_sources")
                                    return "Ask across " + root.selectedKnowledgeIds.length + " selected source(s)..."
                                return "Message ORION..."
                            }
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontChat
                            color: Theme.text
                            placeholderTextColor: Theme.textSecondary
                            wrapMode: TextEdit.Wrap
                            background: null

                            Keys.onReturnPressed: function(event) {
                                if (composer.inputMethodComposing)
                                    return
                                if (!(event.modifiers & Qt.ShiftModifier)) {
                                    event.accepted = true
                                    root.sendMessage()
                                }
                            }
                            Keys.onEnterPressed: function(event) {
                                if (composer.inputMethodComposing)
                                    return
                                if (!(event.modifiers & Qt.ShiftModifier)) {
                                    event.accepted = true
                                    root.sendMessage()
                                }
                            }
                        }
                    }

                    // Composer Toolbar
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spaceSm

                        OrionToggle {
                            objectName: "showThinkingToggle"
                            text: "Thinking Mode"
                            checked: root.showThinking
                            enabled: root.thinkingSupported && !root.busy
                            onToggled: if (typeof orion !== "undefined" && orion !== null) orion.setShowThinking(checked)
                        }

                        OrionButton {
                            visible: root.knowledgeAvailable
                            text: {
                                if (root.knowledgeMode === "ask_knowledge") return "Scope: All Docs"
                                if (root.knowledgeMode === "selected_sources") return "Scope: Selected (" + root.selectedKnowledgeIds.length + ")"
                                if (root.knowledgeMode === "web_search") return "Scope: Web"
                                if (root.knowledgeMode === "web_and_selected") return "Scope: Web + Docs (" + root.selectedKnowledgeIds.length + ")"
                                return "Scope: Chat"
                            }
                            variant: root.knowledgeMode === "chat" ? "quiet" : "secondary"
                            compact: true
                            onClicked: docPickerDialog.open()
                        }

                        OrionButton {
                            visible: root.knowledgeAvailable && root.selectedKnowledgeIds.length > 0
                            text: "Clear (" + root.selectedKnowledgeIds.length + ")"
                            variant: "quiet"
                            compact: true
                            onClicked: root.clearKnowledgeScope()
                        }

                        Item { Layout.fillWidth: true }

                        OrionButton {
                            id: micButton
                            objectName: "micButton"
                            visible: (typeof orion !== "undefined" && orion !== null) && orion.voiceAvailable && orion.voiceState === "idle"
                            text: "Mic"
                            variant: "secondary"
                            compact: true
                            enabled: !root.busy && (typeof orion !== "undefined" && orion !== null && orion.voiceEnabled)
                            onClicked: if (typeof orion !== "undefined" && orion !== null) orion.startVoiceRecording()
                        }

                        OrionButton {
                            objectName: "stopGenerationButton"
                            visible: root.busy
                            text: "Stop"
                            variant: "danger"
                            compact: true
                            onClicked: orion.stopGeneration()
                        }

                        OrionButton {
                            id: sendButton
                            objectName: "sendButton"
                            visible: !root.busy
                            enabled: !root.busy && !root.isSubmitting && composer.text.trim().length > 0
                            text: "Send"
                            variant: "primary"
                            compact: true
                            onClicked: root.sendMessage()
                        }
                    }
                }
            }
        }
    }

    // Chat Document Picker Dialog
    Dialog {
        id: docPickerDialog
        title: "Select Sources & Mode"
        modal: true
        anchors.centerIn: parent
        width: Math.min(chatPage.width * 0.8, 560)
        height: Math.min(chatPage.height * 0.8, 480)
        standardButtons: Dialog.Close

        ColumnLayout {
            anchors.fill: parent
            spacing: Theme.spaceSm

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceSm
                OrionTextField {
                    Layout.fillWidth: true
                    placeholderText: "Search indexed documents..."
                    onTextChanged: chatPage.pickerQuery = text.trim().toLowerCase()
                }
                ComboBox {
                    model: ["all", "pdf", "epub", "docx", "md", "txt"]
                    implicitHeight: Theme.controlHeight
                    onCurrentTextChanged: chatPage.pickerFormat = currentText
                }
            }

            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: Theme.spaceXs
                model: chatPage.knowledgeSourcesModel
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                delegate: Rectangle {
                    readonly property bool matchesQuery: !chatPage.pickerQuery || (model.display_name || "").toLowerCase().indexOf(chatPage.pickerQuery) !== -1
                    readonly property bool matchesFmt: chatPage.pickerFormat === "all" || (model.file_type || "").toLowerCase() === chatPage.pickerFormat
                    visible: matchesQuery && matchesFmt
                    width: ListView.view ? ListView.view.width : 0
                    height: visible ? 38 : 0
                    radius: Theme.radiusControl
                    color: root.isSourceSelected(model.id) ? Theme.bgSelected : Theme.surface
                    border.width: 1
                    border.color: Theme.hairline

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Theme.spaceSm
                        anchors.rightMargin: Theme.spaceSm
                        spacing: Theme.spaceSm

                        CheckBox {
                            checked: root.isSourceSelected(model.id)
                            onToggled: root.toggleKnowledgeSource(model.id)
                        }
                        Text {
                            Layout.fillWidth: true
                            text: (model.display_name || "Document") + " (" + (model.file_type || "") + " · " + (model.status || "") + ")"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSecondary
                            color: Theme.text
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }
                    }
                }
            }
        }
    }
}
