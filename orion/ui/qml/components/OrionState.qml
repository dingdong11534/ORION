import QtQuick

Item {
    id: stateRoot
    required property var root
    required property var chatModel
    required property var conversationsModel
    required property var knowledgeSourcesModel
    required property var workspaceEntriesModel
    required property var pendingApprovalsModel

    Connections {
        target: orion

        function onThemeModeChanged(mode) {
            Theme.themeMode = mode
        }

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
            root.focusComposer()
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
                    referencesExpanded: false,
                    productivityActionJson: ""
                })
            }
            root.isInitializing = false
            root.scrollChatToEnd()
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
                    root.scrollChatToEnd()
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
                    root.scrollChatToEnd()
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
                    chatModel.setProperty(lastIdx, "isError", false)
                    root.scrollChatToEnd()
                    if (typeof orion !== "undefined" && orion !== null && orion.voiceAutoSpeak && orion.voiceEnabled) {
                        orion.speakMessage(replyText)
                    }
                    return
                }
            }
            chatModel.append({
                role: "assistant", text: replyText, status: "completed", isError: false,
                requestId: "", thinkingText: "", thinkingExpanded: false, userCollapsedThinking: false,
                isThinkingActive: false, isAnsweringActive: false, thinkingTruncated: false,
                isRagGrounded: false, referencesJson: "[]", referencesExpanded: false,
                productivityActionJson: ""
            })
            root.scrollChatToEnd()
        }

        function onResponseInterrupted(partialText, requestId) {
            if (chatModel.count > 0) {
                let lastIdx = chatModel.count - 1
                let lastItem = chatModel.get(lastIdx)
                if (lastItem.role === "assistant" && lastItem.status === "pending") {
                    chatModel.setProperty(lastIdx, "isThinkingActive", false)
                    chatModel.setProperty(lastIdx, "isAnsweringActive", false)
                    chatModel.setProperty(lastIdx, "text", partialText || lastItem.text || "")
                    chatModel.setProperty(lastIdx, "status", "interrupted")
                    chatModel.setProperty(lastIdx, "isError", true)
                    root.scrollChatToEnd()
                }
            }
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
                    root.scrollChatToEnd()
                    return
                }
            }
            chatModel.append({
                role: "assistant", text: "Error: " + errorMessage, status: "failed", isError: true,
                requestId: "", thinkingText: "", thinkingExpanded: false, userCollapsedThinking: false,
                isThinkingActive: false, isAnsweringActive: false, thinkingTruncated: false,
                isRagGrounded: false, referencesJson: "[]", referencesExpanded: false,
                productivityActionJson: ""
            })
            root.scrollChatToEnd()
        }

        function onResponseReferences(requestId, refs) {
            if (chatModel.count > 0) {
                let lastIdx = chatModel.count - 1
                let lastItem = chatModel.get(lastIdx)
                if (lastItem.role === "assistant") {
                    chatModel.setProperty(lastIdx, "isRagGrounded", refs && refs.length > 0)
                    chatModel.setProperty(lastIdx, "referencesJson", JSON.stringify(refs || []))
                }
            }
        }

        function onProductivityActionResult(requestId, actionDict) {
            if (chatModel.count > 0) {
                let lastIdx = chatModel.count - 1
                let lastItem = chatModel.get(lastIdx)
                if (lastItem.role === "assistant") {
                    chatModel.setProperty(lastIdx, "productivityActionJson", JSON.stringify(actionDict || {}))
                }
            }
        }

        function onProductivityDataLoaded(payload) {
            root.prodTodayTasks = payload.today_tasks || []
            root.prodOverdueTasks = payload.overdue_tasks || []
            root.prodUpcomingTasks = payload.upcoming_tasks || []
            root.prodUnscheduledTasks = payload.unscheduled_tasks || []
            root.prodAllTasks = payload.all_tasks || []
            root.prodHabits = payload.habits || []
        }

        function onProductivityError(msg) {
            root.productivityError = msg || "Productivity operation failed."
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

        function onConversationKnowledgeContextChanged(mode, docIds) {
            root.selectedKnowledgeIds = docIds || []
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

        function onPassageContextLoaded(docId, chunkIndex, text) {
            root.inspectorDocId = docId
            root.inspectorChunkIndex = chunkIndex
            root.inspectorText = text
            root.openInspectorModal()
        }

        function onKnowledgeSearchResults(query, hits) {
            root.knowledgeSearchHits = hits || []
        }

        function onEpubChaptersLoaded(docId, chapters, offset, total) {
            root.epubChaptersDocId = docId
            root.epubChapters = chapters || []
            root.epubChaptersOffset = offset || 0
            root.epubChaptersTotal = total || 0
        }

        function onKnowledgeError(msg) {
            root.knowledgeError = msg || "Knowledge error"
        }

        function onVoiceTranscriptReady(text) {
            root.insertTranscriptIntoComposer(text)
        }

        function onVoiceError(code, msg) {
            root.voiceErrorMessage = msg || ("Voice error: " + code)
        }

        function onMemoryDataLoaded(payload) {
            root.memoryProfile = payload.profile || ({ preferred_name: "", timezone: "Asia/Kolkata" })
            root.personalMemories = payload.memories || []
        }

        function onHomeDashboardLoaded(payload) {
            root.homeDashboard = payload || ({})
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
    }
}
