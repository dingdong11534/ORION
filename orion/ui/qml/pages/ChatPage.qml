import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: chatPage
    required property var root
    required property var theme
    required property var chatModel
    property alias chatListView: chatList
    property alias composerField: composer

    radius: theme.radiusLg
    color: theme.bgSurface
    border.width: 1
    border.color: theme.border

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: theme.spaceXl
        spacing: theme.spaceMd

        // Header: Title + Mode Selector + Thinking Toggle
        RowLayout {
            Layout.fillWidth: true
            spacing: theme.spaceMd

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2

                Text {
                    Layout.fillWidth: true
                    text: root.getActiveTitle()
                    font.pixelSize: theme.fontTitle
                    font.bold: true
                    color: theme.textPrimary
                    elide: Text.ElideRight
                    textFormat: Text.PlainText
                }

                Text {
                    Layout.fillWidth: true
                    text: {
                        if (root.knowledgeMode === "ask_knowledge")
                            return "Ask Knowledge · Searches all indexed documents"
                        if (root.knowledgeMode === "selected_sources")
                            return "Selected Sources · " + root.selectedKnowledgeIds.length + " document(s) scoped"
                        return root.activeConversationId ? "Chat · General assistant" : "Start a new conversation"
                    }
                    font.pixelSize: theme.fontSm
                    color: theme.textMuted
                    elide: Text.ElideRight
                    textFormat: Text.PlainText
                }
            }

            // Explicit Knowledge Mode Segmented Selector
            Rectangle {
                implicitHeight: 34
                implicitWidth: modeRow.implicitWidth + 8
                radius: 17
                color: theme.bgSurfaceAlt
                border.width: 1
                border.color: theme.border
                visible: root.knowledgeAvailable

                RowLayout {
                    id: modeRow
                    anchors.centerIn: parent
                    spacing: 4

                    Button {
                        objectName: "modeChatButton"
                        text: "Chat"
                        implicitHeight: 26
                        implicitWidth: implicitContentWidth + 16
                        onClicked: root.setChatKnowledgeMode("chat")
                        background: Rectangle { radius: 13; color: root.knowledgeMode === "chat" ? theme.accent : "transparent" }
                        contentItem: Text {
                            text: parent.text; font.pixelSize: theme.fontXs; font.bold: root.knowledgeMode === "chat"
                            color: root.knowledgeMode === "chat" ? theme.bgSurface : theme.textPrimary
                            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                        }
                    }

                    Button {
                        objectName: "modeAskKnowledgeButton"
                        text: "Ask Knowledge"
                        implicitHeight: 26
                        implicitWidth: implicitContentWidth + 16
                        onClicked: root.setChatKnowledgeMode("ask_knowledge")
                        background: Rectangle { radius: 13; color: root.knowledgeMode === "ask_knowledge" ? theme.accent : "transparent" }
                        contentItem: Text {
                            text: parent.text; font.pixelSize: theme.fontXs; font.bold: root.knowledgeMode === "ask_knowledge"
                            color: root.knowledgeMode === "ask_knowledge" ? theme.bgSurface : theme.textPrimary
                            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                        }
                    }

                    Button {
                        objectName: "modeSelectedButton"
                        text: root.selectedKnowledgeIds.length > 0 ? ("Selected (" + root.selectedKnowledgeIds.length + ")") : "Selected"
                        implicitHeight: 26
                        implicitWidth: implicitContentWidth + 16
                        onClicked: root.setChatKnowledgeMode("selected_sources")
                        background: Rectangle { radius: 13; color: root.knowledgeMode === "selected_sources" ? theme.accent : "transparent" }
                        contentItem: Text {
                            text: parent.text; font.pixelSize: theme.fontXs; font.bold: root.knowledgeMode === "selected_sources"
                            color: root.knowledgeMode === "selected_sources" ? theme.bgSurface : theme.textPrimary
                            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                        }
                    }
                }
            }

            // Compact Show Thinking Switch
            Switch {
                id: showThinkingToggle
                objectName: "showThinkingToggle"
                visible: root.thinkingSupported
                text: "Thinking Mode"
                checked: root.showThinking
                font.pixelSize: theme.fontXs
                palette.windowText: theme.textSecondary
                palette.buttonText: theme.textSecondary
                onToggled: {
                    if (typeof orion !== "undefined" && orion !== null) {
                        orion.setShowThinking(checked)
                    }
                }
            }
        }

        // Active EPUB Reading-Scope Banner (when Ask Knowledge / Selected Sources active with an EPUB)
        Rectangle {
            visible: root.knowledgeAvailable && root.knowledgeMode !== "chat" && root.activeEpubScopeBanner() !== ""
            Layout.fillWidth: true
            implicitHeight: epubScopeRow.implicitHeight + 10
            radius: theme.radiusSm
            color: theme.accentLight
            border.width: 1
            border.color: theme.borderStrong

            RowLayout {
                id: epubScopeRow
                anchors.fill: parent
                anchors.margins: 6
                spacing: theme.spaceSm

                Text {
                    objectName: "epubScopeBannerText"
                    Layout.fillWidth: true
                    text: root.activeEpubScopeBanner()
                    font.pixelSize: theme.fontXs
                    font.bold: true
                    color: theme.accent
                    elide: Text.ElideRight
                    textFormat: Text.PlainText
                }

                Button {
                    text: "Change Scope"
                    implicitHeight: 22
                    implicitWidth: implicitContentWidth + 12
                    onClicked: root.openKnowledgePage()
                    background: Rectangle { radius: 6; color: theme.bgSurface; border.width: 1; border.color: theme.borderStrong }
                    contentItem: Text {
                        text: parent.text; font.pixelSize: 10; color: theme.textPrimary
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                    }
                }
            }
        }

        // Divider
        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: theme.border
        }

        // Message List + Useful Empty State
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: chatList
                objectName: "chatList"
                anchors.fill: parent
                clip: true
                spacing: theme.spaceMd
                model: chatModel
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

                    ColumnLayout {
                        id: msgCol
                        width: isUser ? Math.min(parent.width * 0.72, 640) : Math.min(parent.width * 0.88, 820)
                        anchors.right: isUser ? parent.right : undefined
                        anchors.left: isUser ? undefined : parent.left
                        spacing: 6

                        // Collapsible Thinking Trace Card (assistant only, never persisted)
                        Rectangle {
                            id: thinkingCard
                            objectName: "thinkingCard"
                            visible: !isUser && ((model.thinkingText && model.thinkingText.length > 0) || model.isThinkingActive)
                            clip: true
                            Layout.fillWidth: true
                            implicitHeight: thinkingCol.implicitHeight + 16
                            radius: theme.radiusMd
                            color: theme.bgSurfaceAlt
                            border.width: 1
                            border.color: theme.border

                            ColumnLayout {
                                id: thinkingCol
                                anchors.fill: parent
                                anchors.margins: 10
                                spacing: 6

                                RowLayout {
                                    objectName: "thinkingToggleHeader"
                                    Layout.fillWidth: true
                                    spacing: 8

                                    Text {
                                        text: model.isThinkingActive ? "Thinking..." : "Thinking trace"
                                        font.pixelSize: theme.fontXs
                                        font.bold: true
                                        color: theme.accent
                                        textFormat: Text.PlainText
                                    }

                                    Item { Layout.fillWidth: true }

                                    Button {
                                        text: model.thinkingExpanded ? "Hide" : "Show"
                                        implicitHeight: 22
                                        implicitWidth: implicitContentWidth + 14
                                        onClicked: {
                                            const next = !model.thinkingExpanded
                                            chatModel.setProperty(index, "thinkingExpanded", next)
                                            chatModel.setProperty(index, "userCollapsedThinking", !next)
                                        }
                                        background: Rectangle {
                                            radius: 6
                                            color: theme.bgHover
                                        }
                                        contentItem: Text {
                                            text: parent.text
                                            font.pixelSize: 10
                                            color: theme.textPrimary
                                            horizontalAlignment: Text.AlignHCenter
                                            verticalAlignment: Text.AlignVCenter
                                            textFormat: Text.PlainText
                                        }
                                    }
                                }

                                ScrollView {
                                    visible: Boolean(model.thinkingExpanded)
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: Math.min(thinkingPlainText.implicitHeight + 4, 200)
                                    contentWidth: availableWidth
                                    clip: true
                                    ScrollBar.vertical.policy: ScrollBar.AsNeeded

                                    Text {
                                        id: thinkingPlainText
                                        objectName: "thinkingPlainText"
                                        width: parent.width
                                        text: model.thinkingText || ""
                                        font.pixelSize: theme.fontXs
                                        color: theme.textSecondary
                                        wrapMode: Text.WordWrap
                                        textFormat: Text.PlainText
                                    }
                                }
                            }
                        }

                        // Main Message Bubble
                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: bubbleCol.implicitHeight + 20
                            radius: theme.radiusMd
                            color: isUser ? theme.accent : (model.isError ? theme.dangerBg : theme.bgSurfaceAlt)
                            border.width: isUser ? 0 : 1
                            border.color: model.isError ? theme.danger : theme.border

                            ColumnLayout {
                                id: bubbleCol
                                anchors.fill: parent
                                anchors.margins: 12
                                spacing: 8

                                // Streaming Status Badge
                                Text {
                                    visible: !isUser && model.status === "pending" && (!model.text || model.text.length === 0)
                                    text: model.isThinkingActive ? "Reasoning..." : "Generating response..."
                                    font.pixelSize: theme.fontXs
                                    font.italic: true
                                    color: theme.textMuted
                                    textFormat: Text.PlainText
                                }

                                TextEdit {
                                    Layout.fillWidth: true
                                    readOnly: true
                                    selectByMouse: true
                                    text: model.text || ""
                                    font.pixelSize: theme.fontMd
                                    color: isUser ? theme.bgSurface : (model.isError ? theme.danger : theme.textPrimary)
                                    wrapMode: Text.Wrap
                                    textFormat: TextEdit.PlainText
                                }

                                // Grounding Indicator + Expandable References (assistant only, when sources > 0)
                                ColumnLayout {
                                    visible: !isUser && msgDelegate.refsList.length > 0
                                    Layout.fillWidth: true
                                    spacing: 6

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 8

                                        Rectangle {
                                            implicitHeight: 24
                                            implicitWidth: groundingLabel.implicitWidth + 16
                                            radius: 12
                                            color: theme.accentLight
                                            border.width: 1
                                            border.color: theme.borderStrong

                                            Text {
                                                id: groundingLabel
                                                anchors.centerIn: parent
                                                text: "Answered using " + msgDelegate.refsList.length + (msgDelegate.refsList.length === 1 ? " source" : " sources")
                                                font.pixelSize: theme.fontXs
                                                font.bold: true
                                                color: theme.accent
                                                textFormat: Text.PlainText
                                            }
                                        }

                                        Button {
                                            text: model.referencesExpanded ? "Hide sources" : "Show sources"
                                            implicitHeight: 24
                                            implicitWidth: implicitContentWidth + 14
                                            onClicked: chatModel.setProperty(index, "referencesExpanded", !model.referencesExpanded)
                                            background: Rectangle {
                                                radius: 12
                                                color: theme.bgHover
                                            }
                                            contentItem: Text {
                                                text: parent.text
                                                font.pixelSize: theme.fontXs
                                                color: theme.textPrimary
                                                horizontalAlignment: Text.AlignHCenter
                                                verticalAlignment: Text.AlignVCenter
                                                textFormat: Text.PlainText
                                            }
                                        }
                                    }

                                    ColumnLayout {
                                        visible: Boolean(model.referencesExpanded)
                                        Layout.fillWidth: true
                                        spacing: 6

                                        Repeater {
                                            model: msgDelegate.refsList
                                            delegate: Rectangle {
                                                Layout.fillWidth: true
                                                implicitHeight: refCol.implicitHeight + 12
                                                radius: theme.radiusSm
                                                color: theme.bgSurface
                                                border.width: 1
                                                border.color: theme.border

                                                ColumnLayout {
                                                    id: refCol
                                                    anchors.fill: parent
                                                    anchors.margins: 8
                                                    spacing: 3

                                                    RowLayout {
                                                        Layout.fillWidth: true
                                                        spacing: 6
                                                        Text {
                                                            Layout.fillWidth: true
                                                            text: {
                                                                let hdr = modelData.display_name || "Document"
                                                                if (modelData.display_chapter_number !== undefined && modelData.display_chapter_number > 0)
                                                                    hdr += " · Ch. " + modelData.display_chapter_number
                                                                else if (modelData.page_number !== undefined && modelData.page_number > 0)
                                                                    hdr += " · Page " + modelData.page_number
                                                                if (modelData.chapter_title)
                                                                    hdr += " · " + modelData.chapter_title
                                                                else if (modelData.section_title)
                                                                    hdr += " · " + modelData.section_title
                                                                return hdr
                                                            }
                                                            font.pixelSize: theme.fontXs
                                                            font.bold: true
                                                            color: theme.accent
                                                            elide: Text.ElideRight
                                                            textFormat: Text.PlainText
                                                        }
                                                        Button {
                                                            text: "Inspect"
                                                            implicitHeight: 20
                                                            implicitWidth: implicitContentWidth + 10
                                                            onClicked: root.openReferenceModal(modelData)
                                                            background: Rectangle { radius: 6; color: theme.bgHover }
                                                            contentItem: Text {
                                                                text: parent.text
                                                                font.pixelSize: 10
                                                                color: theme.textPrimary
                                                                horizontalAlignment: Text.AlignHCenter
                                                                verticalAlignment: Text.AlignVCenter
                                                                textFormat: Text.PlainText
                                                            }
                                                        }
                                                    }

                                                    Text {
                                                        Layout.fillWidth: true
                                                        text: modelData.excerpt || ""
                                                        font.pixelSize: theme.fontXs
                                                        color: theme.textSecondary
                                                        wrapMode: Text.Wrap
                                                        maximumLineCount: 3
                                                        elide: Text.ElideRight
                                                        textFormat: Text.PlainText
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // First-Use Empty State
            ColumnLayout {
                visible: chatModel.count === 0
                anchors.centerIn: parent
                width: Math.min(parent.width - 48, 480)
                spacing: theme.spaceMd

                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: root.knowledgeMode === "ask_knowledge" ? "Ask Your Knowledge Base" : "Welcome to ORION"
                    font.pixelSize: 22
                    font.bold: true
                    color: theme.textPrimary
                    textFormat: Text.PlainText
                }

                Text {
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    text: root.knowledgeMode === "ask_knowledge"
                        ? "ORION will search all indexed documents using local FTS5 retrieval and cite matching sources."
                        : "Use Chat for general local reasoning, or switch to Ask Knowledge to search across your indexed documents."
                    font.pixelSize: theme.fontMd
                    color: theme.textSecondary
                    textFormat: Text.PlainText
                }

                RowLayout {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: theme.spaceSm
                    visible: root.knowledgeAvailable

                    Button {
                        text: root.knowledgeMode === "ask_knowledge" ? "Switch to Chat" : "Switch to Ask Knowledge"
                        implicitHeight: 34
                        implicitWidth: implicitContentWidth + 20
                        onClicked: root.setChatKnowledgeMode(root.knowledgeMode === "ask_knowledge" ? "chat" : "ask_knowledge")
                        background: Rectangle { radius: theme.radiusSm; color: theme.accentLight; border.width: 1; border.color: theme.accent }
                        contentItem: Text {
                            text: parent.text; font.pixelSize: theme.fontSm; font.bold: true; color: theme.accent
                            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                        }
                    }

                    Button {
                        text: "Open Knowledge"
                        implicitHeight: 34
                        implicitWidth: implicitContentWidth + 20
                        onClicked: root.openKnowledgePage()
                        background: Rectangle { radius: theme.radiusSm; color: theme.bgSurfaceAlt; border.width: 1; border.color: theme.borderStrong }
                        contentItem: Text {
                            text: parent.text; font.pixelSize: theme.fontSm; color: theme.textPrimary
                            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                        }
                    }
                }
            }
        }

        // Bottom Composer
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: Math.min(Math.max(composerScroll.implicitHeight + 16, 56), 140)
            radius: theme.radiusMd
            color: theme.bgSurfaceAlt
            border.width: 1
            border.color: composer.activeFocus ? theme.accent : theme.borderStrong

            RowLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: theme.spaceSm

                ScrollView {
                    id: composerScroll
                    Layout.fillWidth: true
                    Layout.fillHeight: true
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
                        font.pixelSize: theme.fontMd
                        color: theme.textPrimary
                        wrapMode: TextEdit.Wrap
                        background: null
                        Keys.onReturnPressed: function(event) {
                            if (!(event.modifiers & Qt.ShiftModifier)) {
                                event.accepted = true
                                root.sendMessage()
                            }
                        }
                    }
                }

                Button {
                    id: stopButton
                    objectName: "stopGenerationButton"
                    visible: root.busy
                    text: "Stop"
                    implicitHeight: 36
                    implicitWidth: 72
                    onClicked: {
                        if (typeof orion !== "undefined" && orion !== null) {
                            orion.cancelCurrentRequest()
                        }
                    }
                    background: Rectangle { radius: theme.radiusSm; color: theme.danger }
                    contentItem: Text {
                        text: parent.text; font.pixelSize: theme.fontSm; font.bold: true; color: theme.bgSurface
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                    }
                }

                Button {
                    id: sendButton
                    objectName: "sendButton"
                    visible: !root.busy
                    enabled: composer.text.trim().length > 0
                    text: "Send"
                    implicitHeight: 36
                    implicitWidth: 78
                    onClicked: root.sendMessage()
                    background: Rectangle {
                        radius: theme.radiusSm
                        color: parent.enabled ? theme.accent : theme.borderStrong
                    }
                    contentItem: Text {
                        text: parent.text; font.pixelSize: theme.fontSm; font.bold: true; color: theme.bgSurface
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                    }
                }
            }
        }
    }
}
