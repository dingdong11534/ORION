import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "pages"

ApplicationWindow {
    id: root
    objectName: "mainWindow"

    visible: true
    width: 1160
    height: 760
    minimumWidth: 820
    minimumHeight: 560

    title: "ORION"
    color: theme.bgWindow

    OrionTheme { id: theme }

    readonly property bool busy: (typeof orion !== "undefined" && orion !== null) ? orion.isBusy : false
    readonly property bool showThinking: (typeof orion !== "undefined" && orion !== null) ? orion.showThinking : false
    readonly property bool thinkingSupported: (typeof orion !== "undefined" && orion !== null) ? orion.thinkingSupported : false
    readonly property string providerDisplay: (typeof orion !== "undefined" && orion !== null) ? orion.providerDisplay : "Ollama · qwen3:1.7b"
    readonly property bool isSimulatedProvider: (typeof orion !== "undefined" && orion !== null) ? orion.isSimulatedProvider : false
    readonly property bool workspaceToolsAvailable: (typeof orion !== "undefined" && orion !== null) ? orion.workspaceToolsAvailable : false
    readonly property string workspaceRoot: (typeof orion !== "undefined" && orion !== null) ? orion.workspaceRoot : ""
    readonly property bool workspaceToolBusy: (typeof orion !== "undefined" && orion !== null) ? orion.workspaceToolBusy : false
    readonly property bool knowledgeAvailable: (typeof orion !== "undefined" && orion !== null) ? orion.knowledgeAvailable : false
    readonly property bool knowledgeBusy: (typeof orion !== "undefined" && orion !== null) ? orion.knowledgeBusy : false
    readonly property string knowledgeMode: (typeof orion !== "undefined" && orion !== null) ? orion.knowledgeMode : "chat"
    readonly property bool productivityAvailable: (typeof orion !== "undefined" && orion !== null) ? orion.productivityAvailable : false
    readonly property int maxThinkingChars: 32768

    property string activePage: "chat"  // "chat" | "knowledge" | "workspace" | "productivity"
    property var prodTodayTasks: []
    property var prodOverdueTasks: []
    property var prodUpcomingTasks: []
    property var prodAllTasks: []
    property var prodHabits: []
    property string productivityError: ""
    property bool workspacePanelOpen: false
    onWorkspacePanelOpenChanged: { if (workspacePanelOpen && activePage !== "workspace") activePage = "workspace" }
    property string activeConversationId: (typeof orion !== "undefined" && orion !== null) ? orion.activeConversationId : ""
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
    property string lastWorkspaceAddedDocId: ""

    property var inspectedReference: null
    property string inspectedPassageContext: ""
    property string pendingDeleteId: ""
    property string pendingDeleteTitle: ""

    property alias knowledgeSourcesModelRef: knowledgeSourcesModel

    ListModel { id: workspaceEntriesModel; objectName: "workspaceEntriesModel" }
    ListModel { id: knowledgeSourcesModel; objectName: "knowledgeSourcesModel" }
    ListModel { id: conversationsModel; objectName: "conversationsModel" }
    ListModel { id: chatModel; objectName: "chatModel" }
    ListModel { id: pendingApprovalsModel; objectName: "pendingApprovalsModel" }

    property int approvalRemainingSeconds: 60

    // Execution display state
    property string executionId: ""
    property string executionCommand: ""
    property var executionArgs: []
    property string executionCwd: ""
    property string executionStatus: ""
    property string executionStdout: ""
    property string executionStderr: ""
    property int executionExitCode: -1
    property double executionDurationMs: 0
    property double executionElapsedMs: 0
    property bool executionTruncated: false
    property string executionError: ""
    property bool executionPanelVisible: false
    readonly property int maxExecutionDisplayStreamChars: 131072

    Timer {
        id: executionElapsedTimer
        interval: 100
        repeat: true
        running: root.executionStatus === "running"
        onTriggered: root.executionElapsedMs += 100
    }

    function isKnowledgeSelected(docId) {
        return root.selectedKnowledgeIds.indexOf(docId) !== -1
    }

    function toggleKnowledgeSource(docId) {
        const next = root.selectedKnowledgeIds.slice()
        const idx = next.indexOf(docId)
        if (idx === -1) next.push(docId)
        else next.splice(idx, 1)
        root.selectedKnowledgeIds = next
        if (typeof orion !== "undefined" && orion !== null)
            orion.setConversationSelectedSources(next)
    }

    function clearKnowledgeScope() {
        root.selectedKnowledgeIds = []
        if (typeof orion !== "undefined" && orion !== null)
            orion.setConversationSelectedSources([])
    }

    function setChatKnowledgeMode(mode) {
        if (typeof orion !== "undefined" && orion !== null) {
            orion.setKnowledgeMode(mode)
        }
    }

    function openKnowledgePage() {
        root.activePage = "knowledge"
        if (typeof orion !== "undefined" && orion !== null) {
            orion.loadKnowledgeSources()
        }
    }

    function openWorkspacePage() {
        root.activePage = "workspace"
        if (typeof orion !== "undefined" && orion !== null && workspaceEntriesModel.count === 0) {
            orion.listWorkspaceDirectory(root.workspaceCurrentPath)
        }
    }

    function openProductivityPage() {
        root.activePage = "productivity"
        if (root.productivityAvailable && typeof orion !== "undefined" && orion !== null) {
            orion.loadProductivityData("", "", "", "priority_due")
        }
    }

    function openReferenceModal(refObj) {
        root.inspectedReference = refObj
        root.inspectedPassageContext = ""
        if (refObj && refObj.document_id && typeof orion !== "undefined" && orion !== null) {
            orion.loadPassageContext(refObj.document_id, refObj.chunk_index || 0)
        }
    }

    function startAskKnowledge(prefillPrompt, docIds) {
        if (docIds && docIds.length > 0) {
            root.selectedKnowledgeIds = docIds.slice()
            setChatKnowledgeMode("selected_sources")
        } else {
            setChatKnowledgeMode("ask_knowledge")
        }
        root.activePage = "chat"
        if (prefillPrompt && prefillPrompt.trim().length > 0) {
            chatPage.composerField.text = prefillPrompt.trim()
            chatPage.composerField.forceActiveFocus()
        }
    }

    function startSelectedSourcesChat() {
        setChatKnowledgeMode("selected_sources")
        root.activePage = "chat"
        chatPage.composerField.forceActiveFocus()
    }

    function activeEpubScopeBanner() {
        if (root.knowledgeMode === "chat") return ""
        const parts = []
        for (let i = 0; i < knowledgeSourcesModel.count; i++) {
            const s = knowledgeSourcesModel.get(i)
            if (s.file_type !== "epub" || s.ingestion_status !== "indexed") continue
            if (root.knowledgeMode === "selected_sources" && root.selectedKnowledgeIds.indexOf(s.id) === -1)
                continue
            const name = s.title || s.display_name
            const chDisp = s.display_reading_chapter > 0 ? s.display_reading_chapter : s.current_reading_position
            if (chDisp > 0) {
                parts.push(name + " · Whole book searchable (Reading: Ch " + chDisp + ")")
            } else {
                parts.push(name + " · Whole book searchable")
            }
        }
        return parts.join(" | ")
    }

    function getActiveTitle() {
        if (!root.activeConversationId) return "New Chat"
        for (let i = 0; i < conversationsModel.count; i++) {
            if (conversationsModel.get(i).id === root.activeConversationId)
                return conversationsModel.get(i).title
        }
        return "Assistant"
    }

    function generateRequestUuid() {
        return "req-" + Date.now().toString(16) + "-" + Math.random().toString(16).slice(2, 10)
    }

    function sendMessage() {
        const message = chatPage.composerField.text.trim()
        if (message.length === 0 || root.busy || root.isSubmitting)
            return

        root.isSubmitting = true
        chatPage.composerField.text = ""
        const reqId = root.generateRequestUuid()

        chatModel.append({
            role: "user",
            text: message,
            status: "completed",
            isError: false,
            requestId: reqId,
            thinkingText: "",
            thinkingExpanded: false,
            userCollapsedThinking: false,
            isThinkingActive: false,
            isAnsweringActive: false,
            thinkingTruncated: false,
            isRagGrounded: false,
            referencesJson: "[]",
            referencesExpanded: false,
            productivityActionJson: ""
        })

        chatModel.append({
            role: "assistant",
            text: "",
            status: "pending",
            isError: false,
            requestId: reqId,
            thinkingText: "",
            thinkingExpanded: false,
            userCollapsedThinking: false,
            isThinkingActive: false,
            isAnsweringActive: false,
            thinkingTruncated: false,
            isRagGrounded: false,
            referencesJson: "[]",
            referencesExpanded: false,
            productivityActionJson: ""
        })

        Qt.callLater(function() {
            chatPage.chatListView.positionViewAtEnd()
        })

        try {
            if (typeof orion !== "undefined" && orion !== null) {
                const effectiveMode = root.knowledgeMode === "selected_sources"
                    ? "selected_sources"
                    : (root.knowledgeMode === "ask_knowledge" ? "ask_knowledge" : (root.selectedKnowledgeIds.length > 0 ? "selected_sources" : "chat"))
                orion.sendMessageWithScopeAndRequestId(message, effectiveMode, root.selectedKnowledgeIds, reqId)
            }
        } finally {
            Qt.callLater(function() {
                root.isSubmitting = false
            })
        }
    }

    function openDeleteConfirmation(convId, convTitle) {
        confirmDeleteConversation(convId, convTitle)
    }

    function confirmDeleteConversation(convId, convTitle) {
        root.pendingDeleteId = convId
        root.pendingDeleteTitle = convTitle
        deleteDialog.visible = true
    }

    Component.onCompleted: {
        if (typeof orion !== "undefined" && orion !== null) {
            orion.loadInitialState()
            if (root.knowledgeAvailable) {
                orion.loadKnowledgeSources()
            }
        } else {
            root.isInitializing = false
        }
    }

    Connections {
        target: orion

        function onConversationsLoaded(convs, activeId) {
            conversationsModel.clear()
            for (let i = 0; i < convs.length; i++) conversationsModel.append(convs[i])
            root.activeConversationId = activeId
        }

        function onActiveConversationChanged(convId) {
            root.activeConversationId = convId
        }

        function onFocusComposerRequested() {
            root.activePage = "chat"
            chatPage.composerField.forceActiveFocus()
        }

        function onHistoryLoaded(messages) {
            chatModel.clear()
            for (let i = 0; i < messages.length; i++) {
                const msg = messages[i]
                let isErr = (msg.status === "failed" || msg.status === "interrupted")
                let displayText = msg.content
                if (!displayText && msg.error_message) displayText = msg.error_message
                if (!displayText && msg.status === "interrupted")
                    displayText = "Request was interrupted. A retry must use a new client_request_id."
                chatModel.append({
                    role: msg.role,
                    text: displayText || "",
                    status: msg.status,
                    isError: isErr,
                    requestId: "",
                    thinkingText: "",
                    thinkingExpanded: false,
                    userCollapsedThinking: false,
                    isThinkingActive: false,
                    isAnsweringActive: false,
                    thinkingTruncated: false,
                    isRagGrounded: false,
                    referencesJson: "[]",
                    referencesExpanded: false
                })
            }
            root.isInitializing = false
            Qt.callLater(function() { chatPage.chatListView.positionViewAtEnd() })
        }

        function onConversationCreated(conversation) {
            root.activeConversationId = conversation.id
            chatModel.clear()
            root.isInitializing = false
        }

        function onConversationDeleted(conversationId) {
            if (root.activeConversationId === conversationId) {
                root.activeConversationId = ""
                chatModel.clear()
            }
        }

        function onApprovalRequested(requestDict) {
            pendingApprovalsModel.append(requestDict)
            root.approvalRemainingSeconds = 60
        }

        function onApprovalResolved(approvalId, status) {
            for (let i = 0; i < pendingApprovalsModel.count; i++) {
                if (pendingApprovalsModel.get(i).approval_id === approvalId) {
                    pendingApprovalsModel.remove(i)
                    root.approvalRemainingSeconds = 60
                    break
                }
            }
        }

        function onApprovalCancelled(approvalId) {
            for (let i = 0; i < pendingApprovalsModel.count; i++) {
                if (pendingApprovalsModel.get(i).approval_id === approvalId) {
                    pendingApprovalsModel.remove(i)
                    root.approvalRemainingSeconds = 60
                    break
                }
            }
        }

        function onExecutionStarted(executionId, command, args, cwd) {
            root.executionId = executionId
            root.executionCommand = command
            root.executionArgs = args
            root.executionCwd = cwd
            root.executionStatus = "running"
            root.executionStdout = ""
            root.executionStderr = ""
            root.executionExitCode = -1
            root.executionDurationMs = 0
            root.executionElapsedMs = 0
            root.executionTruncated = false
            root.executionError = ""
            root.executionPanelVisible = true
        }

        function onExecutionOutputChunk(executionId, stream, text) {
            if (executionId === "" || executionId !== root.executionId) return
            if (stream === "stdout") {
                const rem = root.maxExecutionDisplayStreamChars - root.executionStdout.length
                if (rem > 0) root.executionStdout += (text.length > rem ? text.slice(0, rem) : text)
                if (text.length > rem) root.executionTruncated = true
            } else {
                const rem = root.maxExecutionDisplayStreamChars - root.executionStderr.length
                if (rem > 0) root.executionStderr += (text.length > rem ? text.slice(0, rem) : text)
                if (text.length > rem) root.executionTruncated = true
            }
        }

        function onExecutionFinished(executionId, status, exitCode, durationMs, truncated, errorMessage) {
            if (executionId !== root.executionId) return
            root.executionStatus = status
            root.executionExitCode = exitCode
            root.executionDurationMs = durationMs
            root.executionTruncated = truncated
            root.executionError = errorMessage
        }

        function onThinkingChunk(chunk, requestId) {
            if (chatModel.count > 0) {
                let lastIdx = chatModel.count - 1
                let lastItem = chatModel.get(lastIdx)
                if (lastItem.role === "assistant" && !lastItem.isError && lastItem.status !== "completed") {
                    if (lastItem.requestId && requestId && lastItem.requestId !== requestId) return
                    if (!lastItem.requestId && requestId) chatModel.setProperty(lastIdx, "requestId", requestId)
                    chatModel.setProperty(lastIdx, "isThinkingActive", true)
                    chatModel.setProperty(lastIdx, "isAnsweringActive", false)
                    let cur = lastItem.thinkingText || ""
                    if (!cur && !lastItem.userCollapsedThinking) chatModel.setProperty(lastIdx, "thinkingExpanded", true)
                    let rem = root.maxThinkingChars - cur.length
                    if (rem > 0) chatModel.setProperty(lastIdx, "thinkingText", cur + (chunk.length > rem ? chunk.slice(0, rem) : chunk))
                    if (chunk.length > rem) chatModel.setProperty(lastIdx, "thinkingTruncated", true)
                    chatPage.chatListView.positionViewAtEnd()
                }
            }
        }

        function onResponseChunk(chunk, requestId) {
            if (chatModel.count > 0) {
                let lastIdx = chatModel.count - 1
                let lastItem = chatModel.get(lastIdx)
                if (lastItem.role === "assistant" && !lastItem.isError && lastItem.status !== "completed") {
                    if (lastItem.requestId && requestId && lastItem.requestId !== requestId) return
                    if (!lastItem.requestId && requestId) chatModel.setProperty(lastIdx, "requestId", requestId)
                    chatModel.setProperty(lastIdx, "isThinkingActive", false)
                    chatModel.setProperty(lastIdx, "isAnsweringActive", true)
                    chatModel.setProperty(lastIdx, "text", (lastItem.text || "") + chunk)
                    chatPage.chatListView.positionViewAtEnd()
                }
            }
        }

        function onResponseReady(replyText) {
            if (chatModel.count > 0) {
                let lastIdx = chatModel.count - 1
                let lastItem = chatModel.get(lastIdx)
                if (lastItem.role === "assistant" && !lastItem.isError && lastItem.status !== "completed") {
                    chatModel.setProperty(lastIdx, "isThinkingActive", false)
                    chatModel.setProperty(lastIdx, "isAnsweringActive", false)
                    chatModel.setProperty(lastIdx, "text", replyText)
                    chatModel.setProperty(lastIdx, "status", "completed")
                    Qt.callLater(function() { chatPage.chatListView.positionViewAtEnd() })
                    return
                }
            }
            chatModel.append({
                role: "assistant", text: replyText, status: "completed", isError: false,
                requestId: "", thinkingText: "", thinkingExpanded: false, userCollapsedThinking: false,
                isThinkingActive: false, isAnsweringActive: false, thinkingTruncated: false,
                isRagGrounded: false, referencesJson: "[]", referencesExpanded: false
            })
            Qt.callLater(function() { chatPage.chatListView.positionViewAtEnd() })
        }

        function onResponseInterrupted(partialText, requestId) {
            if (chatModel.count > 0) {
                let lastIdx = chatModel.count - 1
                let lastItem = chatModel.get(lastIdx)
                if (lastItem.role === "assistant" && (lastItem.status === "pending" || !lastItem.isError)) {
                    if (lastItem.requestId && requestId && lastItem.requestId !== requestId) return
                    let displayText = partialText && partialText.length > 0 ? partialText : "Request was interrupted. A retry must use a new client_request_id."
                    chatModel.setProperty(lastIdx, "isThinkingActive", false)
                    chatModel.setProperty(lastIdx, "isAnsweringActive", false)
                    chatModel.setProperty(lastIdx, "text", displayText)
                    chatModel.setProperty(lastIdx, "status", "interrupted")
                    chatModel.setProperty(lastIdx, "isError", true)
                    Qt.callLater(function() { chatPage.chatListView.positionViewAtEnd() })
                    return
                }
            }
            chatModel.append({
                role: "assistant",
                text: partialText && partialText.length > 0 ? partialText : "Request was interrupted. A retry must use a new client_request_id.",
                status: "interrupted", isError: true, requestId: requestId || "",
                thinkingText: "", thinkingExpanded: false, userCollapsedThinking: false,
                isThinkingActive: false, isAnsweringActive: false, thinkingTruncated: false,
                isRagGrounded: false, referencesJson: "[]", referencesExpanded: false
            })
            Qt.callLater(function() { chatPage.chatListView.positionViewAtEnd() })
        }

        function onErrorOccurred(errorMessage) {
            if (chatModel.count > 0) {
                let lastIdx = chatModel.count - 1
                let lastItem = chatModel.get(lastIdx)
                if (lastItem.role === "assistant" && (lastItem.status === "pending" || !lastItem.isError)) {
                    let errorText = "Error: " + errorMessage
                    if (lastItem.text && lastItem.text.length > 0) errorText = lastItem.text + "\n\n[Error: " + errorMessage + "]"
                    chatModel.setProperty(lastIdx, "isThinkingActive", false)
                    chatModel.setProperty(lastIdx, "isAnsweringActive", false)
                    chatModel.setProperty(lastIdx, "text", errorText)
                    chatModel.setProperty(lastIdx, "status", "failed")
                    chatModel.setProperty(lastIdx, "isError", true)
                    Qt.callLater(function() { chatPage.chatListView.positionViewAtEnd() })
                    return
                }
            }
            chatModel.append({
                role: "assistant", text: "Error: " + errorMessage, status: "failed", isError: true,
                requestId: "", thinkingText: "", thinkingExpanded: false, userCollapsedThinking: false,
                isThinkingActive: false, isAnsweringActive: false, thinkingTruncated: false,
                isRagGrounded: false, referencesJson: "[]", referencesExpanded: false
            })
            Qt.callLater(function() { chatPage.chatListView.positionViewAtEnd() })
        }

        function onWorkspaceDirectoryLoaded(relativePath, entries, totalEntries, truncated, requestId) {
            root.workspaceError = ""
            root.workspaceCurrentPath = relativePath || ""
            root.selectedWorkspaceFile = ""
            root.workspaceDirectoryTruncated = Boolean(truncated)
            workspaceEntriesModel.clear()
            for (let i = 0; i < entries.length; i++) workspaceEntriesModel.append(entries[i])
        }

        function onWorkspaceFileLoaded(relativePath, content, sizeBytes, truncated, requestId) {
            root.workspaceError = ""
            root.workspaceResultPath = relativePath || ""
            root.workspaceResultContent = content || ""
            root.workspaceResultSizeBytes = sizeBytes || 0
            root.workspaceResultTruncated = Boolean(truncated)
        }

        function onWorkspaceToolError(toolId, relativePath, errorMessage) {
            root.workspaceError = errorMessage || "Workspace tool request failed."
        }

        function onKnowledgeSourcesLoaded(sources) {
            knowledgeSourcesModel.clear()
            const validIds = {}
            for (let i = 0; i < sources.length; i++) {
                knowledgeSourcesModel.append(sources[i])
                validIds[sources[i].id] = true
            }
            root.selectedKnowledgeIds = root.selectedKnowledgeIds.filter(function(id) { return Boolean(validIds[id]) })
        }

        function onKnowledgeImportCompleted(count, errors) {
            root.knowledgeError = (errors && errors.length > 0) ? errors.join("\n") : ""
            root.knowledgeStatusNote = count > 0 ? ("Indexed " + count + " document(s).") : ""
        }

        function onKnowledgeSourceRemoved(docId) {
            root.selectedKnowledgeIds = root.selectedKnowledgeIds.filter(function(id) { return id !== docId })
        }

        function onKnowledgeError(errMsg) {
            root.knowledgeError = errMsg || "Knowledge Base error."
        }

        function onProductivityDataLoaded(payload) {
            if (!payload) return
            root.prodTodayTasks = payload.today_tasks || []
            root.prodOverdueTasks = payload.overdue_tasks || []
            root.prodUpcomingTasks = payload.upcoming_tasks || []
            root.prodAllTasks = payload.all_tasks || []
            root.prodHabits = payload.habits || []
        }

        function onProductivityError(errMsg) {
            root.productivityError = errMsg || "Productivity operation failed."
        }

        function onProductivityActionResult(requestId, actionObj) {
            if (!actionObj) return
            const jsonStr = JSON.stringify(actionObj)
            for (let i = chatModel.count - 1; i >= 0; i--) {
                const item = chatModel.get(i)
                if (item.role === "assistant" && (!requestId || !item.requestId || item.requestId === requestId)) {
                    chatModel.setProperty(i, "productivityActionJson", jsonStr)
                    break
                }
            }
        }

        function onKnowledgeProgress(info) {
            if (!info) return
            if (info.stage === "indexing") {
                let msg = "Indexing " + (info.display_name || "") + ": " + (info.sections_extracted || 0) + " sections, " + (info.chunks_indexed || 0) + " chunks"
                if (info.warning) msg += " (" + info.warning + ")"
                root.knowledgeStatusNote = msg
            } else if (info.stage === "folder" || info.stage === "folder_done") {
                root.knowledgeStatusNote = "Folder: " + (info.files_completed || 0) + "/" + (info.files_total || 0) + " indexed, " + (info.files_skipped || 0) + " skipped, " + (info.files_failed || 0) + " failed"
            } else if (info.stage === "files") {
                root.knowledgeStatusNote = "Imported " + (info.files_completed || 0) + "/" + (info.files_total || 0) + ": " + (info.display_name || "")
            }
        }

        function onKnowledgeSearchResults(query, hits) {
            root.knowledgeSearchHits = hits || []
        }

        function onEpubChaptersLoaded(docId, chapters, total, offset) {
            root.epubChaptersDocId = docId || ""
            root.epubChapters = chapters || []
            root.epubChaptersTotal = total || 0
            root.epubChaptersOffset = offset || 0
        }

        function onConversationKnowledgeContextChanged(mode, selectedIds) {
            root.selectedKnowledgeIds = selectedIds ? selectedIds.slice() : []
        }

        function onPassageContextLoaded(docId, chunkIdx, contextText) {
            if (root.inspectedReference && root.inspectedReference.document_id === docId) {
                root.inspectedPassageContext = contextText || ""
            }
        }

        function onWorkspaceAddedToKnowledge(relativePath, docId, alreadyIndexed) {
            root.lastWorkspaceAddedDocId = docId || ""
            root.knowledgeStatusNote = alreadyIndexed
                ? (relativePath + " is already indexed in Knowledge.")
                : ("Added " + relativePath + " to Knowledge.")
        }

        function onResponseReferences(requestId, refs) {
            if (chatModel.count > 0) {
                let lastIdx = chatModel.count - 1
                let lastItem = chatModel.get(lastIdx)
                if (lastItem.role === "assistant") {
                    if (lastItem.requestId && requestId && lastItem.requestId !== requestId) return
                    if (!lastItem.requestId && requestId) chatModel.setProperty(lastIdx, "requestId", requestId)
                    chatModel.setProperty(lastIdx, "isRagGrounded", true)
                    chatModel.setProperty(lastIdx, "referencesJson", JSON.stringify(refs || []))
                    chatModel.setProperty(lastIdx, "referencesExpanded", false)
                }
            }
        }
    }

    // Main Shell Layout: Sidebar + StackLayout (Chat / Knowledge / Workspace)
    RowLayout {
        anchors.fill: parent
        anchors.margins: theme.spaceLg
        spacing: theme.spaceLg

        // Left Navigation Rail & Conversation List
        Rectangle {
            Layout.preferredWidth: 250
            Layout.fillHeight: true
            radius: theme.radiusLg
            color: theme.bgSidebar

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: theme.spaceLg
                spacing: theme.spaceMd

                // App Brand
                RowLayout {
                    spacing: 10
                    Rectangle {
                        Layout.preferredWidth: 34
                        Layout.preferredHeight: 34
                        radius: 10
                        color: theme.accentStrong
                        Text { anchors.centerIn: parent; text: "O"; color: "white"; font.pixelSize: 19; font.bold: true }
                    }
                    ColumnLayout {
                        spacing: 0
                        Text { text: "ORION"; font.pixelSize: 17; font.bold: true; color: theme.textPrimary }
                        Text { text: "LOCAL ASSISTANT"; font.pixelSize: 8; font.letterSpacing: 1.2; color: theme.textMuted }
                    }
                }

                // 3 Primary Experience Tabs: Chat | Knowledge | Workspace
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4

                    Button {
                        id: navChatButton
                        objectName: "navChatButton"
                        Layout.fillWidth: true
                        text: "Chat"
                        implicitHeight: 34
                        onClicked: root.activePage = "chat"
                        background: Rectangle {
                            radius: theme.radiusSm
                            color: root.activePage === "chat" ? theme.accent : "transparent"
                        }
                        contentItem: Text {
                            text: parent.text; font.pixelSize: theme.fontSm; font.bold: true
                            color: root.activePage === "chat" ? theme.bgSurface : theme.textPrimary
                            verticalAlignment: Text.AlignVCenter; leftPadding: 10
                        }
                    }

                    Button {
                        id: knowledgeToggleButton
                        objectName: "knowledgeToggleButton"
                        visible: root.knowledgeAvailable
                        Layout.fillWidth: true
                        text: root.selectedKnowledgeIds.length > 0 ? ("Knowledge (" + root.selectedKnowledgeIds.length + ")") : "Knowledge"
                        implicitHeight: 34
                        onClicked: root.openKnowledgePage()
                        background: Rectangle {
                            radius: theme.radiusSm
                            color: root.activePage === "knowledge" ? theme.accent : "transparent"
                        }
                        contentItem: Text {
                            text: parent.text; font.pixelSize: theme.fontSm; font.bold: true
                            color: root.activePage === "knowledge" ? theme.bgSurface : theme.textPrimary
                            verticalAlignment: Text.AlignVCenter; leftPadding: 10
                        }
                    }

                    Button {
                        id: workspaceBrowserToggleButton
                        objectName: "workspaceBrowserToggleButton"
                        visible: root.workspaceToolsAvailable
                        Layout.fillWidth: true
                        text: "Workspace"
                        implicitHeight: 34
                        onClicked: root.openWorkspacePage()
                        background: Rectangle {
                            radius: theme.radiusSm
                            color: root.activePage === "workspace" ? theme.accent : "transparent"
                        }
                        contentItem: Text {
                            text: parent.text; font.pixelSize: theme.fontSm; font.bold: true
                            color: root.activePage === "workspace" ? theme.bgSurface : theme.textPrimary
                            verticalAlignment: Text.AlignVCenter; leftPadding: 10
                        }
                    }

                    Button {
                        id: productivityToggleButton
                        objectName: "productivityToggleButton"
                        visible: root.productivityAvailable
                        Layout.fillWidth: true
                        text: "Productivity"
                        implicitHeight: 34
                        onClicked: root.openProductivityPage()
                        background: Rectangle {
                            radius: theme.radiusSm
                            color: root.activePage === "productivity" ? theme.accent : "transparent"
                        }
                        contentItem: Text {
                            text: parent.text; font.pixelSize: theme.fontSm; font.bold: true
                            color: root.activePage === "productivity" ? theme.bgSurface : theme.textPrimary
                            verticalAlignment: Text.AlignVCenter; leftPadding: 10
                        }
                    }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: theme.borderStrong }

                // New Chat CTA
                Button {
                    objectName: "newChatButton"
                    Layout.fillWidth: true
                    text: "+ New Chat"
                    enabled: !root.busy
                    implicitHeight: 34
                    onClicked: {
                        root.activePage = "chat"
                        if (typeof orion !== "undefined" && orion !== null)
                            orion.createNewConversation()
                    }
                    background: Rectangle { radius: theme.radiusSm; color: theme.bgSurface; border.width: 1; border.color: theme.borderStrong }
                    contentItem: Text {
                        text: parent.text; font.pixelSize: theme.fontSm; font.bold: true; color: theme.textPrimary
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                    }
                }

                // Conversations List
                Text {
                    text: "RECENT CHATS"
                    font.pixelSize: 10
                    font.bold: true
                    font.letterSpacing: 0.8
                    color: theme.textMuted
                }

                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 4
                    model: conversationsModel
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    delegate: Rectangle {
                        width: ListView.view.width
                        implicitHeight: 34
                        radius: theme.radiusSm
                        readonly property bool isCurrent: model.id === root.activeConversationId
                        color: isCurrent ? theme.bgSelected : "transparent"

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 4
                            spacing: 4

                            Text {
                                Layout.fillWidth: true
                                text: model.title || "Untitled"
                                font.pixelSize: theme.fontXs
                                font.bold: isCurrent
                                color: theme.textPrimary
                                elide: Text.ElideRight
                                textFormat: Text.PlainText

                                MouseArea {
                                    anchors.fill: parent
                                    onClicked: {
                                        root.activePage = "chat"
                                        if (typeof orion !== "undefined" && orion !== null)
                                            orion.selectConversation(model.id)
                                    }
                                }
                            }

                            Button {
                                text: "×"
                                implicitWidth: 22
                                implicitHeight: 22
                                onClicked: root.confirmDeleteConversation(model.id, model.title || "Conversation")
                                background: Rectangle { radius: 4; color: "transparent" }
                                contentItem: Text { text: "×"; font.pixelSize: 13; color: theme.textMuted; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                            }
                        }
                    }
                }

                // Provider Status Footer
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        id: providerStatusText
                        objectName: "providerStatusText"
                        Layout.fillWidth: true
                        text: root.providerDisplay
                        font.pixelSize: 10
                        font.bold: true
                        color: theme.textSecondary
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }
                    Text {
                        id: providerFooterText
                        objectName: "providerFooterText"
                        Layout.fillWidth: true
                        text: root.isSimulatedProvider ? "Simulation Mode" : "Local inference · Private"
                        font.pixelSize: 9
                        color: theme.textMuted
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
            currentIndex: root.activePage === "knowledge" ? 1 : (root.activePage === "workspace" ? 2 : (root.activePage === "productivity" ? 3 : 0))

            ChatPage {
                id: chatPage
                root: root
                theme: theme
                chatModel: chatModel
            }

            KnowledgePage {
                id: knowledgePage
                root: root
                theme: theme
                knowledgeSourcesModel: knowledgeSourcesModel
            }

            WorkspacePage {
                id: workspacePage
                root: root
                theme: theme
                workspaceEntriesModel: workspaceEntriesModel
            }

            ProductivityPage {
                id: productivityPage
                root: root
                theme: theme
            }
        }
    }

    // Source Reference Inspector Modal
    Rectangle {
        objectName: "sourceInspectorModal"
        visible: root.inspectedReference !== null
        anchors.fill: parent
        color: "#66000000"
        z: 995
        MouseArea { anchors.fill: parent; onClicked: root.inspectedReference = null }

        Rectangle {
            width: Math.min(parent.width - 48, 640)
            height: Math.min(parent.height - 48, 480)
            radius: theme.radiusLg
            color: theme.bgSurface
            anchors.centerIn: parent

            MouseArea { anchors.fill: parent; onClicked: {} }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: theme.spaceXl
                spacing: theme.spaceMd

                RowLayout {
                    Layout.fillWidth: true
                    spacing: theme.spaceSm
                    Text {
                        Layout.fillWidth: true
                        text: {
                            if (!root.inspectedReference) return "Source Reference"
                            const cid = root.inspectedReference.citation_id ? ("[" + root.inspectedReference.citation_id + "] ") : ""
                            return cid + (root.inspectedReference.display_name || "Source Reference")
                        }
                        font.pixelSize: theme.fontLg
                        font.bold: true
                        color: theme.textPrimary
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }
                    Button {
                        objectName: "openOriginalSourceButton"
                        visible: Boolean(root.inspectedReference && root.inspectedReference.source_path)
                        text: "Open Original"
                        implicitHeight: 28
                        implicitWidth: implicitContentWidth + 16
                        onClicked: {
                            if (root.inspectedReference && root.inspectedReference.source_path) {
                                Qt.openUrlExternally("file://" + root.inspectedReference.source_path)
                            }
                        }
                        background: Rectangle { radius: 6; color: theme.accentLight; border.width: 1; border.color: theme.accent }
                        contentItem: Text { text: parent.text; font.pixelSize: theme.fontXs; font.bold: true; color: theme.accent; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                    Button {
                        text: "Close"
                        implicitHeight: 28
                        implicitWidth: 64
                        onClicked: root.inspectedReference = null
                        background: Rectangle { radius: 6; color: theme.bgHover }
                        contentItem: Text { text: parent.text; font.pixelSize: theme.fontXs; color: theme.textPrimary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: {
                        if (!root.inspectedReference) return ""
                        let meta = root.inspectedReference.source_path || ""
                        if (root.inspectedReference.display_chapter_number > 0) meta += " · Ch. " + root.inspectedReference.display_chapter_number
                        else if (root.inspectedReference.page_number > 0) meta += " · Page " + root.inspectedReference.page_number
                        if (root.inspectedReference.chapter_title) meta += " · " + root.inspectedReference.chapter_title
                        else if (root.inspectedReference.section_title) meta += " · " + root.inspectedReference.section_title
                        return meta
                    }
                    font.pixelSize: theme.fontXs
                    color: theme.accent
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                }

                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true

                    TextArea {
                        readOnly: true
                        selectByMouse: true
                        text: root.inspectedPassageContext
                            ? root.inspectedPassageContext
                            : (root.inspectedReference ? (root.inspectedReference.content || root.inspectedReference.excerpt || "") : "")
                        font.pixelSize: theme.fontMd
                        color: theme.textPrimary
                        wrapMode: TextEdit.Wrap
                        background: Rectangle { color: theme.bgSurfaceAlt; radius: theme.radiusSm; border.width: 1; border.color: theme.border }
                    }
                }
            }
        }
    }

    // Delete Confirmation Modal Dialog
    Rectangle {
        id: deleteDialog
        objectName: "deleteDialog"
        visible: false
        anchors.fill: parent
        color: "#66000000"
        z: 999
        MouseArea { anchors.fill: parent; onClicked: {} }

        Rectangle {
            width: 380
            height: 180
            radius: theme.radiusLg
            color: theme.bgSurface
            anchors.centerIn: parent

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 22
                spacing: 14

                Text { text: "Delete Conversation"; font.pixelSize: 17; font.bold: true; color: theme.textPrimary }
                Text {
                    Layout.fillWidth: true
                    text: "Are you sure you want to delete \"" + root.pendingDeleteTitle + "\"? All messages in this conversation will be permanently removed."
                    font.pixelSize: 13; color: theme.textSecondary; wrapMode: Text.Wrap
                }
                Item { Layout.fillHeight: true }
                RowLayout {
                    Layout.alignment: Qt.AlignRight
                    spacing: 10
                    Button {
                        text: "Cancel"; implicitWidth: 80; implicitHeight: 34
                        onClicked: deleteDialog.visible = false
                        background: Rectangle { radius: 8; color: theme.bgHover }
                        contentItem: Text { text: "Cancel"; color: theme.textPrimary; font.pixelSize: 13; font.bold: true; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                    Button {
                        text: "Delete"; implicitWidth: 80; implicitHeight: 34
                        onClicked: {
                            deleteDialog.visible = false
                            if (typeof orion !== "undefined" && orion !== null)
                                orion.deleteConversation(root.pendingDeleteId)
                        }
                        background: Rectangle { radius: 8; color: theme.danger }
                        contentItem: Text { text: "Delete"; color: "white"; font.pixelSize: 13; font.bold: true; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                }
            }
        }
    }

    // Tool Execution Approval Modal Dialog
    Timer {
        id: approvalTimer
        interval: 1000
        repeat: true
        running: pendingApprovalsModel.count > 0
        onTriggered: { if (root.approvalRemainingSeconds > 0) root.approvalRemainingSeconds -= 1 }
    }

    Rectangle {
        id: approvalDialog
        objectName: "approvalDialog"
        visible: pendingApprovalsModel.count > 0
        anchors.fill: parent
        color: "#66000000"
        z: 1000
        MouseArea { anchors.fill: parent; onClicked: {} }

        readonly property var currentApproval: pendingApprovalsModel.count > 0 ? pendingApprovalsModel.get(0) : null
        readonly property string currentApprovalId: currentApproval ? currentApproval.approval_id : ""
        readonly property string currentNonce: currentApproval ? currentApproval.nonce : ""
        readonly property bool isMutating: currentApproval ? currentApproval.is_mutating : false
        readonly property bool isExpired: root.approvalRemainingSeconds <= 0

        Rectangle {
            width: Math.min(parent.width - 40, 520)
            implicitHeight: approvalContentColumn.implicitHeight + 40
            radius: theme.radiusLg
            color: theme.bgSurface
            anchors.centerIn: parent

            ColumnLayout {
                id: approvalContentColumn
                anchors.fill: parent
                anchors.margins: 20
                spacing: 12

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Text { text: "Action Approval Required"; font.pixelSize: 17; font.bold: true; color: theme.textPrimary; textFormat: Text.PlainText }
                    Item { Layout.fillWidth: true }
                    Rectangle {
                        implicitWidth: mutatingLabel.implicitWidth + 12; implicitHeight: 22; radius: 6
                        color: approvalDialog.isMutating ? theme.dangerBg : theme.accentLight
                        border.color: approvalDialog.isMutating ? theme.danger : theme.accent; border.width: 1
                        Text {
                            id: mutatingLabel; anchors.centerIn: parent
                            text: approvalDialog.isMutating ? "MUTATING" : "READ-ONLY"
                            font.pixelSize: 10; font.bold: true
                            color: approvalDialog.isMutating ? theme.danger : theme.accent; textFormat: Text.PlainText
                        }
                    }
                }

                Text {
                    id: toolNameText; objectName: "toolNameText"; Layout.fillWidth: true
                    text: approvalDialog.currentApproval ? approvalDialog.currentApproval.tool_name : ""
                    font.pixelSize: 14; font.bold: true; color: theme.textPrimary; textFormat: Text.PlainText
                }
                Text {
                    id: actionSummaryText; objectName: "actionSummaryText"; Layout.fillWidth: true
                    text: approvalDialog.currentApproval ? approvalDialog.currentApproval.action_summary : ""
                    font.pixelSize: 12; color: theme.textSecondary; wrapMode: Text.Wrap; textFormat: Text.PlainText
                }
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: Math.min(argsText.implicitHeight + 16, 120)
                    color: theme.bgWindow; radius: 8; border.color: theme.border; border.width: 1; clip: true
                    ScrollView {
                        anchors.fill: parent; anchors.margins: 8
                        Text {
                            id: argsText; objectName: "argsText"; width: parent.width
                            text: approvalDialog.currentApproval ? JSON.stringify(approvalDialog.currentApproval.arguments, null, 2) : "{}"
                            font.family: "Monospace"; font.pixelSize: 11; color: theme.textPrimary
                            wrapMode: Text.WrapAnywhere; textFormat: Text.PlainText
                        }
                    }
                }
                Text {
                    id: countdownLabel; objectName: "countdownLabel"
                    text: approvalDialog.isExpired ? "Approval expired (60s limit)" : "Expires in " + root.approvalRemainingSeconds + "s"
                    font.pixelSize: 12; font.bold: approvalDialog.isExpired
                    color: approvalDialog.isExpired ? theme.danger : theme.textMuted; textFormat: Text.PlainText
                }
                RowLayout {
                    Layout.alignment: Qt.AlignRight; spacing: 10
                    Button {
                        objectName: "approvalRejectButton"; text: "Reject"; implicitWidth: 90; implicitHeight: 34
                        onClicked: { if (typeof orion !== "undefined" && orion !== null) orion.submitApprovalDecision(approvalDialog.currentApprovalId, approvalDialog.currentNonce, false) }
                        background: Rectangle { radius: 8; color: theme.bgHover }
                        contentItem: Text { text: "Reject"; color: theme.textPrimary; font.pixelSize: 13; font.bold: true; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                    Button {
                        objectName: "approvalApproveButton"; text: "Approve Once"; implicitWidth: 120; implicitHeight: 34
                        enabled: !approvalDialog.isExpired && approvalDialog.currentApprovalId.length > 0
                        onClicked: { if (typeof orion !== "undefined" && orion !== null) orion.submitApprovalDecision(approvalDialog.currentApprovalId, approvalDialog.currentNonce, true) }
                        background: Rectangle { radius: 8; color: parent.enabled ? theme.accent : theme.borderStrong }
                        contentItem: Text { text: "Approve Once"; color: "white"; font.pixelSize: 13; font.bold: true; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                }
            }
        }
    }

    // Execution Display Drawer
    Rectangle {
        id: executionDrawer
        objectName: "executionDrawer"
        visible: root.executionPanelVisible
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 18 }
        height: Math.min(340, parent.height * 0.45)
        z: 500
        radius: 16; color: theme.bgSurface; border.width: 1; border.color: theme.borderStrong

        ColumnLayout {
            anchors.fill: parent; anchors.margins: 14; spacing: 8
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                Text { text: "Subprocess Execution (" + root.executionStatus.toUpperCase() + ")"; font.pixelSize: 13; font.bold: true; color: theme.textPrimary; textFormat: Text.PlainText }
                Item { Layout.fillWidth: true }
                Button {
                    objectName: "stopExecutionButton"; visible: root.executionStatus === "running"; enabled: root.executionStatus === "running" && root.executionId !== ""; text: "Stop"
                    implicitHeight: 26; implicitWidth: 64
                    onClicked: { if (typeof orion !== "undefined" && orion !== null && root.executionId !== "") orion.cancelExecution(root.executionId) }
                    background: Rectangle { radius: 6; color: theme.danger }
                    contentItem: Text { text: "Stop"; font.pixelSize: 11; font.bold: true; color: "white"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                }
                Button {
                    objectName: "dismissExecutionButton"; visible: root.executionStatus !== "running"; text: "Dismiss"
                    implicitHeight: 26; implicitWidth: 72
                    onClicked: root.executionPanelVisible = false
                    background: Rectangle { radius: 6; color: theme.bgHover }
                    contentItem: Text { text: "Dismiss"; font.pixelSize: 11; color: theme.textPrimary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                }
            }
            RowLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 8
                ScrollView {
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                    TextArea {
                        objectName: "stdoutDisplay"; readOnly: true; text: root.executionStdout
                        textFormat: TextEdit.PlainText
                        font.family: "Monospace"; font.pixelSize: 11; color: theme.textPrimary
                        background: Rectangle { color: theme.bgSurfaceAlt; radius: 6; border.width: 1; border.color: theme.border }
                    }
                }
                ScrollView {
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                    TextArea {
                        objectName: "stderrDisplay"; readOnly: true; text: root.executionStderr
                        textFormat: TextEdit.PlainText
                        font.family: "Monospace"; font.pixelSize: 11; color: theme.danger
                        background: Rectangle { color: theme.bgSurfaceAlt; radius: 6; border.width: 1; border.color: theme.border }
                    }
                }
            }
        }
    }
}
