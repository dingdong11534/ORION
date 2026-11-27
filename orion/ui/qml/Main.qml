import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "pages"

ApplicationWindow {
    id: root
    visible: !backgroundMode
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
    readonly property string sessionState: (typeof orion !== "undefined" && orion !== null) ? orion.sessionState : "locked"
    readonly property bool backgroundMode: (typeof orion !== "undefined" && orion !== null) ? orion.backgroundMode : false
    readonly property int maxThinkingChars: 32768

    property string activePage: "home" // "home" | "chat" | "knowledge" | "workspace" | "productivity" | "settings"
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
    property var memoryProfile: ({ preferred_name: "", timezone: "Asia/Kolkata" })
    property var personalMemories: []
    property var homeDashboard: ({})
    property var backgroundJobs: []
    property bool orbHidden: false
    property bool orbExpanded: false
    property bool orbForceVisible: false
    property bool orbWakeFlash: false
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
        if (activePage === "settings" && typeof orion !== "undefined" && orion !== null) {
            orion.loadPersonalMemory()
            orion.loadBackgroundJobs()
        }
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
            if (orion.productivityAvailable) {
                orion.loadProductivityData("", "", "", "priority_due")
                orion.loadHomeDashboard()
            }
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
    function activateOrbFromWakeWord() {
        root.orbHidden = false
        root.orbForceVisible = true
        root.orbExpanded = true
        root.orbWakeFlash = true
        wakeResetTimer.restart()
        if (typeof orion !== "undefined" && orion !== null)
            orion.loadBackgroundJobs()
        if (orionOrbWindow.x < 0 || orionOrbWindow.x > Screen.desktopAvailableWidth - orionOrbWindow.width)
            orionOrbWindow.x = Math.max(16, Screen.desktopAvailableWidth - orionOrbWindow.width - 24)
        if (orionOrbWindow.y < 0 || orionOrbWindow.y > Screen.desktopAvailableHeight - orionOrbWindow.height)
            orionOrbWindow.y = Math.max(16, Screen.desktopAvailableHeight - orionOrbWindow.height - 24)
        orionOrbWindow.show()
        orionOrbWindow.raise()
        orionOrbWindow.requestActivate()
    }

    Timer {
        id: wakeResetTimer
        interval: 3500
        repeat: false
        onTriggered: root.orbWakeFlash = false
    }
    function startAskKnowledge(promptText, docIds) {
        const ids = docIds ? docIds.slice() : []
        root.selectedKnowledgeIds = ids
        if (typeof orion !== "undefined" && orion !== null) {
            orion.setConversationSelectedSources(ids)
            orion.setKnowledgeMode(ids.length > 0 ? "selected_sources" : "ask_knowledge")
        }
        root.activePage = "chat"
        if (promptText && promptText.length > 0) {
            chatPage.composerField.text = promptText
        }
        chatPage.composerField.forceActiveFocus()
    }
    function startSelectedSourcesChat() { startAskKnowledge("", root.selectedKnowledgeIds) }
    function clearKnowledgeScope() {
        root.selectedKnowledgeIds = []
        if (typeof orion !== "undefined" && orion !== null) {
            orion.setConversationSelectedSources([])
            orion.setKnowledgeMode("chat")
        }
    }
    function openReferenceModal(refObj) {
        if (!refObj) return
        root.inspectorDocId = refObj.document_id || ""
        root.inspectorChunkIndex = refObj.chunk_index || 0
        root.inspectorText = refObj.content || refObj.excerpt || ""
        sourceInspectorModal.open()
    }
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

        // Left Navigation (resizable on normal windows, collapses to 56px icon rail below 1000px)
        Rectangle {
            id: navSidebar
            readonly property bool compactRail: root.width < 1000
            property int customNavWidth: (typeof orion !== "undefined" && orion !== null) ? orion.navSidebarWidth : 208
            Layout.preferredWidth: compactRail ? 56 : Math.max(160, Math.min(280, customNavWidth))
            Layout.fillHeight: true
            color: Theme.sidebar
            border.width: 1
            border.color: Theme.hairline

            MouseArea {
                visible: !navSidebar.compactRail
                width: 6
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.right: parent.right
                cursorShape: Qt.SplitHCursor
                z: 10
                property real startX: 0
                property int startWidth: 208
                onPressed: function(mouse) {
                    startX = mapToGlobal(mouse.x, mouse.y).x
                    startWidth = navSidebar.Layout.preferredWidth
                }
                onPositionChanged: function(mouse) {
                    if (pressed) {
                        const dx = mapToGlobal(mouse.x, mouse.y).x - startX
                        const nextW = Math.max(160, Math.min(280, Math.round(startWidth + dx)))
                        navSidebar.customNavWidth = nextW
                    }
                }
                onReleased: {
                    if (typeof orion !== "undefined" && orion !== null)
                        orion.setNavSidebarWidth(navSidebar.customNavWidth)
                }
            }

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
                    objectName: "navHomeButton"
                    Layout.fillWidth: true
                    title: parent.parent.compactRail ? "H" : "Home"
                    selected: root.activePage === "home"
                    ToolTip.visible: parent.parent.compactRail && hovered
                    ToolTip.text: "Home"
                    onClicked: root.activePage = "home"
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

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spaceXs
                    OrionButton {
                        objectName: "sessionLockButton"
                        Layout.fillWidth: true
                        compact: true
                        variant: root.sessionState === "unlocked" ? "secondary" : "primary"
                        text: parent.parent.parent.compactRail
                            ? (root.sessionState === "unlocked" ? "L" : "U")
                            : (root.sessionState === "unlocked" ? "Lock" : "Unlock")
                        ToolTip.visible: parent.parent.parent.compactRail && hovered
                        ToolTip.text: root.sessionState === "unlocked" ? "Lock ORION" : "Unlock ORION"
                        onClicked: {
                            if (typeof orion !== "undefined" && orion !== null)
                                orion.toggleSessionLock()
                        }
                    }
                    OrionButton {
                        objectName: "hideToBackgroundButton"
                        visible: !parent.parent.parent.compactRail
                        compact: true
                        variant: "quiet"
                        text: "Orb"
                        onClicked: {
                            root.orbHidden = false
                            root.orbExpanded = false
                            if (typeof orion !== "undefined" && orion !== null) {
                                orion.loadBackgroundJobs()
                                orion.setBackgroundMode(true)
                            }
                        }
                    }
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
            currentIndex: ["home", "chat", "knowledge", "workspace", "productivity", "settings"].indexOf(root.activePage)

            HomePage { root: root }
            ChatPage { id: chatPage; root: root; chatModel: chatModel; conversationsModel: conversationsModel; knowledgeSourcesModel: knowledgeSourcesModel }
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

    Window {
        id: orionOrbWindow
        objectName: "orionOrbWindow"
        transientParent: null
        visible: (root.backgroundMode || root.orbForceVisible) && !root.orbHidden
        width: root.orbExpanded ? 300 : 72
        height: root.orbExpanded ? 280 : 72
        flags: Qt.FramelessWindowHint | Qt.Window | Qt.WindowStaysOnTopHint
        color: "transparent"
        title: "ORION Orb"
        Component.onCompleted: {
            orionOrbWindow.transientParent = null
        }

        // Collapsed 72x72 circular Orb
        Rectangle {
            id: collapsedOrbBody
            objectName: "collapsedOrbBody"
            visible: !root.orbExpanded
            anchors.fill: parent
            radius: 36
            color: root.sessionState === "unlocked" ? Theme.accent : Theme.surface
            border.width: 2
            border.color: root.sessionState === "unlocked" ? Theme.onAccent : Theme.border

            Column {
                anchors.centerIn: parent
                spacing: 2
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: root.sessionState === "unlocked" ? "ORION" : "LOCKED"
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    color: root.sessionState === "unlocked" ? Theme.onAccent : Theme.text
                    textFormat: Text.PlainText
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    visible: root.backgroundJobs.length > 0
                    text: root.backgroundJobs.length + " job" + (root.backgroundJobs.length === 1 ? "" : "s")
                    font.family: Theme.fontFamily
                    font.pixelSize: 9
                    color: root.sessionState === "unlocked" ? Theme.onAccent : Theme.textSecondary
                    textFormat: Text.PlainText
                }
            }

            MouseArea {
                objectName: "orbMouseArea"
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                cursorShape: Qt.PointingHandCursor
                property point pressPos: Qt.point(0, 0)
                property bool dragged: false
                onPressed: function(mouse) {
                    pressPos = Qt.point(mouse.x, mouse.y)
                    dragged = false
                }
                onPositionChanged: function(mouse) {
                    if (pressed && !dragged) {
                        const dx = mouse.x - pressPos.x
                        const dy = mouse.y - pressPos.y
                        if ((dx * dx + dy * dy) > 25) {
                            dragged = true
                            orionOrbWindow.startSystemMove()
                        }
                    }
                }
                onClicked: function(mouse) {
                    if (dragged) return
                    if (mouse.button === Qt.RightButton) {
                        root.orbExpanded = true
                        if (typeof orion !== "undefined" && orion !== null)
                            orion.loadBackgroundJobs()
                        return
                    }
                    root.orbExpanded = true
                    if (typeof orion !== "undefined" && orion !== null)
                        orion.loadBackgroundJobs()
                }
            }
        }

        // Expanded compact Orb panel
        Rectangle {
            id: expandedOrbPanel
            objectName: "expandedOrbPanel"
            visible: root.orbExpanded
            anchors.fill: parent
            radius: 16
            color: Theme.surface
            border.width: 1
            border.color: Theme.border

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Theme.spaceMd
                spacing: Theme.spaceSm

                // Drag handle header
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spaceXs
                    MouseArea {
                        Layout.fillWidth: true
                        implicitHeight: 24
                        cursorShape: Qt.SizeAllCursor
                        onPressed: orionOrbWindow.startSystemMove()
                        RowLayout {
                            anchors.fill: parent
                            spacing: Theme.spaceXs
                            Rectangle {
                                width: 8; height: 8; radius: 4
                                color: root.sessionState === "unlocked" ? Theme.accent : Theme.danger
                            }
                            Text {
                                objectName: "orbStatusText"
                                text: root.orbWakeFlash ? ("WAKE · " + root.sessionState.toUpperCase()) : ("ORION · " + root.sessionState.toUpperCase())
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSecondary
                                font.weight: Font.DemiBold
                                color: root.orbWakeFlash ? Theme.accent : Theme.text
                                textFormat: Text.PlainText
                            }
                        }
                    }
                    OrionButton {
                        objectName: "orbCollapseButton"
                        text: "—"
                        variant: "quiet"
                        compact: true
                        onClicked: {
                            root.orbExpanded = false
                            root.orbForceVisible = false
                        }
                    }
                    OrionButton {
                        objectName: "orbHideButton"
                        text: "Hide"
                        variant: "quiet"
                        compact: true
                        onClicked: {
                            root.orbExpanded = false
                            root.orbForceVisible = false
                            root.orbHidden = true
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spaceXs
                    OrionButton {
                        objectName: "orbSessionLockButton"
                        Layout.fillWidth: true
                        compact: true
                        variant: root.sessionState === "unlocked" ? "secondary" : "primary"
                        text: root.sessionState === "unlocked" ? "Lock Session" : "Unlock Session"
                        onClicked: {
                            if (typeof orion !== "undefined" && orion !== null)
                                orion.toggleSessionLock()
                        }
                    }
                    OrionButton {
                        objectName: "orbMicButton"
                        compact: true
                        variant: (typeof orion !== "undefined" && orion !== null && orion.voiceState === "recording") ? "danger" : "secondary"
                        text: (typeof orion !== "undefined" && orion !== null && orion.voiceState === "recording") ? "Stop Mic" : "Mic"
                        enabled: (typeof orion !== "undefined" && orion !== null) && orion.voiceAvailable
                        onClicked: {
                            if (typeof orion === "undefined" || orion === null) return
                            if (orion.voiceState === "recording")
                                orion.stopVoiceRecording()
                            else
                                orion.startVoiceRecording()
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: "Mic: " + ((typeof orion !== "undefined" && orion !== null) ? orion.voiceState : "idle")
                        + " · Jobs: " + root.backgroundJobs.length
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontCaption
                    color: Theme.textSecondary
                    textFormat: Text.PlainText
                }

                Text {
                    Layout.fillWidth: true
                    text: (typeof orion !== "undefined" && orion !== null)
                        ? (orion.wakeWordStatus + "\n" + orion.speakerVerificationStatus)
                        : ""
                    font.family: Theme.fontFamily
                    font.pixelSize: 10
                    color: Theme.textSecondary
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                }

                Item { Layout.fillHeight: true }

                OrionButton {
                    objectName: "orbRestoreWindowButton"
                    Layout.fillWidth: true
                    compact: true
                    variant: "primary"
                    text: "Open ORION"
                    onClicked: {
                        root.orbExpanded = false
                        root.orbForceVisible = false
                        root.orbHidden = false
                        if (typeof orion !== "undefined" && orion !== null)
                            orion.setBackgroundMode(false)
                    }
                }
            }
        }
    }
}
