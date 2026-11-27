import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "pages"

ApplicationWindow {
    id: root
    visible: true
    width: 1280
    height: 800
    minimumWidth: 880
    minimumHeight: 580
    title: "ORION"
    color: Theme.window

    readonly property bool busy: (typeof orion !== "undefined" && orion !== null) ? orion.isBusy : false
    readonly property bool showThinking: (typeof orion !== "undefined" && orion !== null) ? orion.showThinking : false
    readonly property bool thinkingSupported: (typeof orion !== "undefined" && orion !== null) ? orion.thinkingSupported : false
    readonly property bool knowledgeAvailable: (typeof orion !== "undefined" && orion !== null) ? orion.knowledgeAvailable : false
    readonly property bool knowledgeBusy: (typeof orion !== "undefined" && orion !== null) ? orion.knowledgeBusy : false
    readonly property string knowledgeMode: (typeof orion !== "undefined" && orion !== null) ? orion.knowledgeMode : "chat"
    readonly property bool productivityAvailable: (typeof orion !== "undefined" && orion !== null) ? orion.productivityAvailable : false
    readonly property bool workspaceToolBusy: (typeof orion !== "undefined" && orion !== null) ? orion.workspaceToolBusy : false
    readonly property bool workspaceToolsAvailable: (typeof orion !== "undefined" && orion !== null) ? orion.workspaceToolsAvailable : false
    readonly property string workspaceRoot: (typeof orion !== "undefined" && orion !== null) ? orion.workspaceRoot : ""
    readonly property string providerDisplay: (typeof orion !== "undefined" && orion !== null) ? orion.providerDisplay : "Local"
    readonly property bool isSimulatedProvider: (typeof orion !== "undefined" && orion !== null) ? orion.isSimulatedProvider : false
    readonly property string supportedKnowledgeFormats: (typeof orion !== "undefined" && orion !== null) ? orion.supportedKnowledgeFormats : ""
    readonly property int maxThinkingChars: 32768

    property string activePage: "chat" // "chat" | "knowledge" | "workspace" | "productivity" | "settings"
    property bool workspacePanelOpen: false
    onWorkspacePanelOpenChanged: { if (workspacePanelOpen && activePage !== "workspace") activePage = "workspace" }
    property string activeConversationId: ""
    property bool isInitializing: true
    property bool isSubmitting: false
    property var selectedKnowledgeIds: []
    property string knowledgeError: ""
    property string knowledgeStatusNote: ""
    property var knowledgeSearchHits: []
    property var epubChapters: []
    property int epubChaptersTotal: 0
    property int epubChaptersOffset: 0
    property string epubChaptersDocId: ""
    property string workspaceCurrentPath: ""
    property string selectedWorkspaceFile: ""
    property string workspaceResultPath: ""
    property string workspaceResultContent: ""
    property int workspaceResultSizeBytes: 0
    property bool workspaceResultTruncated: false
    property bool workspaceDirectoryTruncated: false
    property string workspaceError: ""
    property var prodTodayTasks: []
    property var prodOverdueTasks: []
    property var prodUpcomingTasks: []
    property var prodUnscheduledTasks: []
    property var prodAllTasks: []
    property var prodHabits: []
    property string productivityError: ""
    property string voiceErrorMessage: ""
    property string pendingDeleteId: ""
    property string pendingDeleteTitle: ""
    property string pendingRenameId: ""
    property string inspectorDocId: ""
    property int inspectorChunkIndex: 0
    property string inspectorText: ""
    property int approvalRemainingSeconds: 60
    property string executionId: ""
    property string executionCommand: ""
    property var executionArgs: []
    property string executionCwd: ""
    property string executionStatus: "idle"
    property string executionStdout: ""
    property string executionStderr: ""
    property int executionExitCode: -1
    property real executionDurationMs: 0
    property real executionElapsedMs: 0
    property bool executionTruncated: false
    property string executionError: ""
    property bool executionPanelVisible: false
    readonly property int maxExecutionDisplayStreamChars: 131072

    onActivePageChanged: {
        if (activePage !== "chat" && typeof orion !== "undefined" && orion !== null)
            orion.cancelVoiceRecording()
        if (activePage === "productivity" && typeof orion !== "undefined" && orion !== null)
            orion.loadProductivityData("", "", "", "priority_due")
    }

    onClosing: {
        if (typeof orion !== "undefined" && orion !== null) {
            orion.cancelVoiceRecording()
            orion.stopSpeaking()
        }
    }

    ListModel { id: chatModel; objectName: "chatModel" }
    ListModel { id: conversationsModel; objectName: "conversationsModel" }
    ListModel { id: knowledgeSourcesModel; objectName: "knowledgeSourcesModel" }
    ListModel { id: workspaceEntriesModel; objectName: "workspaceEntriesModel" }
    ListModel { id: pendingApprovalsModel; objectName: "pendingApprovalsModel" }

    OrionState {
        root: root
        chatModel: chatModel
        conversationsModel: conversationsModel
        knowledgeSourcesModel: knowledgeSourcesModel
        workspaceEntriesModel: workspaceEntriesModel
        pendingApprovalsModel: pendingApprovalsModel
    }

    Component.onCompleted: {
        if (typeof orion !== "undefined" && orion !== null) {
            Theme.themeMode = orion.themeMode
            root.activeConversationId = orion.activeConversationId
            orion.loadInitialState()
            if (orion.knowledgeAvailable) orion.loadKnowledgeSources()
            if (orion.productivityAvailable) orion.loadProductivityData("", "", "", "priority_due")
        }
    }

    function getActiveTitle() {
        for (let i = 0; i < conversationsModel.count; i++) {
            const c = conversationsModel.get(i)
            if (c.id === root.activeConversationId) return c.title
        }
        return "New Chat"
    }

    function startNewChat() {
        root.activePage = "chat"
        if (typeof orion !== "undefined" && orion !== null)
            orion.createNewConversation()
    }

    function setChatKnowledgeMode(mode) {
        if (typeof orion !== "undefined" && orion !== null) orion.setKnowledgeMode(mode)
    }

    function generateRequestUuid() {
        return "req-" + Date.now().toString(16) + "-" + Math.random().toString(16).slice(2, 10)
    }

    function sendMessage() {
        const message = chatPage.composerField.text.trim()
        if (message.length === 0 || root.busy || root.isSubmitting) return
        root.isSubmitting = true
        chatPage.composerField.text = ""
        const reqId = root.generateRequestUuid()
        chatModel.append({ role: "user", text: message, status: "completed", isError: false, requestId: reqId, thinkingText: "", thinkingExpanded: false, userCollapsedThinking: false, isThinkingActive: false, isAnsweringActive: false, thinkingTruncated: false, isRagGrounded: false, referencesJson: "[]", referencesExpanded: false, productivityActionJson: "" })
        chatModel.append({ role: "assistant", text: "", status: "pending", isError: false, requestId: reqId, thinkingText: "", thinkingExpanded: false, userCollapsedThinking: false, isThinkingActive: false, isAnsweringActive: false, thinkingTruncated: false, isRagGrounded: false, referencesJson: "[]", referencesExpanded: false, productivityActionJson: "" })
        scrollChatToEnd()
        try {
            if (typeof orion !== "undefined" && orion !== null) {
                const effectiveMode = root.knowledgeMode === "selected_sources" ? "selected_sources" : (root.knowledgeMode === "ask_knowledge" ? "ask_knowledge" : (root.selectedKnowledgeIds.length > 0 ? "selected_sources" : "chat"))
                orion.sendMessageWithScopeAndRequestId(message, effectiveMode, root.selectedKnowledgeIds, reqId)
            }
        } finally {
            Qt.callLater(function() { root.isSubmitting = false })
        }
    }

    function scrollChatToEnd() { Qt.callLater(function() { chatPage.chatListView.positionViewAtEnd() }) }
    function focusComposer() { chatPage.composerField.forceActiveFocus() }
    function insertTranscriptIntoComposer(text) { chatPage.insertTranscript(text) }
    function openKnowledgePage() { root.activePage = "knowledge" }
    function confirmDeleteConversation(convId, convTitle) { root.pendingDeleteId = convId; root.pendingDeleteTitle = convTitle; deleteDialog.open() }
    function openDeleteConfirmation(convId, convTitle) { confirmDeleteConversation(convId, convTitle) }
    function openRenameDialog(convId, convTitle) { root.pendingRenameId = convId; renameInput.text = convTitle; renameDialog.open() }
    function openInspectorModal() { sourceInspectorModal.open() }
    function isSourceSelected(docId) { return root.selectedKnowledgeIds.indexOf(docId) !== -1 }
    function isKnowledgeSelected(docId) { return isSourceSelected(docId) }
    function toggleKnowledgeSource(docId) {
        let next = root.selectedKnowledgeIds.slice()
        const idx = next.indexOf(docId)
        if (idx === -1) next.push(docId); else next.splice(idx, 1)
        root.selectedKnowledgeIds = next
        if (typeof orion !== "undefined" && orion !== null) orion.setConversationSelectedSources(next)
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // Left Navigation (208px wide, collapses to 56px icon rail below 1000px)
        Rectangle {
            readonly property bool compactRail: root.width < 1000
            Layout.preferredWidth: compactRail ? 56 : 208
            Layout.fillHeight: true
            color: Theme.sidebar
            border.width: 1
            border.color: Theme.hairline

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Theme.spaceMd
                spacing: Theme.spaceXs

                Text {
                    Layout.bottomMargin: Theme.spaceMd
                    text: parent.parent.compactRail ? "O" : "ORION"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontHeading
                    font.weight: Font.Medium
                    color: Theme.text
                    horizontalAlignment: parent.parent.compactRail ? Text.AlignHCenter : Text.AlignLeft
                    Layout.fillWidth: true
                    textFormat: Text.PlainText
                }

                OrionListRow {
                    objectName: "navChatButton"
                    Layout.fillWidth: true
                    title: parent.parent.compactRail ? "C" : "Chat"
                    selected: root.activePage === "chat"
                    ToolTip.visible: parent.parent.compactRail && hovered
                    ToolTip.text: "Chat"
                    onClicked: root.activePage = "chat"
                }
                OrionListRow {
                    objectName: "knowledgeToggleButton"
                    Layout.fillWidth: true
                    title: parent.parent.compactRail ? "K" : "Knowledge"
                    selected: root.activePage === "knowledge"
                    ToolTip.visible: parent.parent.compactRail && hovered
                    ToolTip.text: "Knowledge"
                    onClicked: root.activePage = "knowledge"
                }
                OrionListRow {
                    objectName: "workspaceBrowserToggleButton"
                    Layout.fillWidth: true
                    title: parent.parent.compactRail ? "W" : "Workspace"
                    selected: root.activePage === "workspace"
                    ToolTip.visible: parent.parent.compactRail && hovered
                    ToolTip.text: "Workspace"
                    onClicked: root.activePage = "workspace"
                }
                OrionListRow {
                    objectName: "navProductivityButton"
                    Layout.fillWidth: true
                    title: parent.parent.compactRail ? "P" : "Productivity"
                    selected: root.activePage === "productivity"
                    ToolTip.visible: parent.parent.compactRail && hovered
                    ToolTip.text: "Productivity"
                    onClicked: root.activePage = "productivity"
                }

                Item { Layout.fillHeight: true }

                OrionListRow {
                    objectName: "navSettingsButton"
                    Layout.fillWidth: true
                    title: parent.parent.compactRail ? "S" : "Settings"
                    selected: root.activePage === "settings"
                    ToolTip.visible: parent.parent.compactRail && hovered
                    ToolTip.text: "Settings"
                    onClicked: root.activePage = "settings"
                }

                ColumnLayout {
                    visible: !parent.parent.compactRail
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spaceSm
                    spacing: 2
                    Text {
                        id: providerStatusText
                        objectName: "providerStatusText"
                        Layout.fillWidth: true
                        text: root.providerDisplay
                        font.pixelSize: Theme.fontCaption
                        font.weight: Font.Medium
                        color: Theme.textSecondary
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }
                    Text {
                        id: providerFooterText
                        objectName: "providerFooterText"
                        Layout.fillWidth: true
                        text: root.isSimulatedProvider ? "Simulation Mode" : "Local inference"
                        font.pixelSize: Theme.fontCaption
                        color: Theme.textSecondary
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }
                }
            }
        }

        // Main Content Stack
        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: ["chat", "knowledge", "workspace", "productivity", "settings"].indexOf(root.activePage)

            ChatPage { id: chatPage; root: root; chatModel: chatModel; conversationsModel: conversationsModel }
            KnowledgePage { objectName: "knowledgePanel"; root: root; theme: Theme; knowledgeSourcesModel: knowledgeSourcesModel }
            WorkspacePage { objectName: "workspaceBrowserPanel"; root: root; theme: Theme; workspaceEntriesModel: workspaceEntriesModel }
            ProductivityPage { root: root }
            SettingsPage { root: root }
        }
    }

    Dialog {
        id: deleteDialog
        objectName: "deleteDialog"
        title: "Delete conversation"
        modal: true
        anchors.centerIn: parent
        standardButtons: Dialog.Yes | Dialog.No
        onAccepted: if (root.pendingDeleteId) orion.deleteConversation(root.pendingDeleteId)
        Text { text: "Delete \"" + root.pendingDeleteTitle + "\"?"; color: Theme.text; textFormat: Text.PlainText }
    }

    Dialog {
        id: renameDialog
        title: "Rename conversation"
        modal: true
        anchors.centerIn: parent
        standardButtons: Dialog.Ok | Dialog.Cancel
        onAccepted: if (root.pendingRenameId && renameInput.text.trim()) orion.renameConversation(root.pendingRenameId, renameInput.text.trim())
        OrionTextField { id: renameInput; implicitWidth: 280; placeholderText: "Conversation title" }
    }

    Dialog {
        id: sourceInspectorModal
        title: "Passage Context"
        modal: true
        anchors.centerIn: parent
        width: Math.min(root.width * 0.7, 640)
        standardButtons: Dialog.Close
        ScrollView {
            implicitHeight: 280
            TextArea { readOnly: true; wrapMode: TextEdit.Wrap; text: root.inspectorText; color: Theme.text; background: null }
        }
    }

    OrionApprovalDialog {
        root: root
        pendingApprovalsModel: pendingApprovalsModel
    }

    OrionExecutionDrawer {
        root: root
    }
}
