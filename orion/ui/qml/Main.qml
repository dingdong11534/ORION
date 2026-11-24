import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    objectName: "mainWindow"

    visible: true
    width: 1100
    height: 720
    minimumWidth: 800
    minimumHeight: 550

    title: "ORION"
    color: "#F4F1E9"

    readonly property bool busy: (typeof orion !== "undefined" && orion !== null) ? orion.isBusy : false
    property string activeConversationId: (typeof orion !== "undefined" && orion !== null) ? orion.activeConversationId : ""
    property bool isInitializing: true

    property string pendingDeleteId: ""
    property string pendingDeleteTitle: ""

    ListModel {
        id: conversationsModel
        objectName: "conversationsModel"
    }

    ListModel {
        id: chatModel
        objectName: "chatModel"
    }

    ListModel {
        id: pendingApprovalsModel
        objectName: "pendingApprovalsModel"
    }

    property int approvalRemainingSeconds: 60

    // ---- Execution display state ----
    property string executionId: ""
    property string executionCommand: ""
    property var    executionArgs: []
    property string executionCwd: ""
    property string executionStatus: ""   // "running" | "completed" | "failed" | "cancelled" | "timed_out"
    property string executionStdout: ""
    property string executionStderr: ""
    property int    executionExitCode: -1
    property double executionDurationMs: 0
    property double executionElapsedMs: 0
    property bool   executionTruncated: false
    property string executionError: ""
    property bool   executionPanelVisible: false
    readonly property int maxExecutionDisplayStreamChars: 131072

    Timer {
        id: executionElapsedTimer
        interval: 100
        repeat: true
        running: root.executionStatus === "running"
        onTriggered: {
            root.executionElapsedMs += 100
        }
    }

    function getActiveTitle() {
        if (!root.activeConversationId)
            return "New Chat"
        for (let i = 0; i < conversationsModel.count; i++) {
            if (conversationsModel.get(i).id === root.activeConversationId)
                return conversationsModel.get(i).title
        }
        return "Assistant"
    }

    function sendMessage() {
        const message = composer.text.trim()

        if (message.length === 0 || root.busy)
            return

        chatModel.append({
            role: "user",
            text: message,
            status: "completed",
            isError: false
        })

        // Placeholder assistant message for incremental streaming output
        chatModel.append({
            role: "assistant",
            text: "",
            status: "pending",
            isError: false
        })

        composer.clear()
        composer.forceActiveFocus()
        orion.sendMessage(message)

        Qt.callLater(function() {
            chatList.positionViewAtEnd()
        })
    }

    function openDeleteConfirmation(convId, title) {
        root.pendingDeleteId = convId
        root.pendingDeleteTitle = title
        deleteDialog.visible = true
    }

    Component.onCompleted: {
        if (typeof orion !== "undefined" && orion !== null) {
            orion.loadInitialState()
        } else {
            root.isInitializing = false
        }
    }

    Connections {
        target: orion

        function onConversationsLoaded(convs, activeId) {
            conversationsModel.clear()
            for (let i = 0; i < convs.length; i++) {
                conversationsModel.append(convs[i])
            }
            root.activeConversationId = activeId
        }

        function onActiveConversationChanged(convId) {
            root.activeConversationId = convId
        }

        function onHistoryLoaded(messages) {
            chatModel.clear()
            for (let i = 0; i < messages.length; i++) {
                const msg = messages[i]
                let isErr = (msg.status === "failed" || msg.status === "interrupted")
                let displayText = msg.content
                if (!displayText && msg.error_message) {
                    displayText = msg.error_message
                }
                if (!displayText && msg.status === "interrupted") {
                    displayText = "Request was interrupted. A retry must use a new client_request_id."
                }
                chatModel.append({
                    role: msg.role,
                    text: displayText || "",
                    status: msg.status,
                    isError: isErr
                })
            }
            root.isInitializing = false
            Qt.callLater(function() {
                chatList.positionViewAtEnd()
            })
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
                if (rem > 0) {
                    root.executionStdout = root.executionStdout + (text.length > rem ? text.slice(0, rem) : text)
                }
                if (text.length > rem) {
                    root.executionTruncated = true
                }
            } else {
                const rem = root.maxExecutionDisplayStreamChars - root.executionStderr.length
                if (rem > 0) {
                    root.executionStderr = root.executionStderr + (text.length > rem ? text.slice(0, rem) : text)
                }
                if (text.length > rem) {
                    root.executionTruncated = true
                }
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

        function onResponseChunk(chunk, requestId) {
            if (chatModel.count > 0) {
                let lastIdx = chatModel.count - 1
                let lastItem = chatModel.get(lastIdx)
                if (lastItem.role === "assistant" && !lastItem.isError && lastItem.status !== "completed") {
                    let currentText = lastItem.text || ""
                    chatModel.setProperty(lastIdx, "text", currentText + chunk)
                    chatList.positionViewAtEnd()
                    return
                }
            }
        }

        function onResponseReady(replyText) {
            if (chatModel.count > 0) {
                let lastIdx = chatModel.count - 1
                let lastItem = chatModel.get(lastIdx)
                if (lastItem.role === "assistant" && !lastItem.isError && lastItem.status !== "completed") {
                    chatModel.setProperty(lastIdx, "text", replyText)
                    chatModel.setProperty(lastIdx, "status", "completed")
                    Qt.callLater(function() {
                        chatList.positionViewAtEnd()
                    })
                    return
                }
            }

            chatModel.append({
                role: "assistant",
                text: replyText,
                status: "completed",
                isError: false
            })

            Qt.callLater(function() {
                chatList.positionViewAtEnd()
            })
        }

        function onResponseInterrupted(partialText, requestId) {
            if (chatModel.count > 0) {
                let lastIdx = chatModel.count - 1
                let lastItem = chatModel.get(lastIdx)
                if (lastItem.role === "assistant" && (lastItem.status === "pending" || !lastItem.isError)) {
                    let displayText = partialText && partialText.length > 0 ? partialText : "Request was interrupted. A retry must use a new client_request_id."
                    chatModel.setProperty(lastIdx, "text", displayText)
                    chatModel.setProperty(lastIdx, "status", "interrupted")
                    chatModel.setProperty(lastIdx, "isError", true)
                    Qt.callLater(function() {
                        chatList.positionViewAtEnd()
                    })
                    return
                }
            }

            chatModel.append({
                role: "assistant",
                text: partialText && partialText.length > 0 ? partialText : "Request was interrupted. A retry must use a new client_request_id.",
                status: "interrupted",
                isError: true
            })

            Qt.callLater(function() {
                chatList.positionViewAtEnd()
            })
        }

        function onErrorOccurred(errorMessage) {
            if (chatModel.count > 0) {
                let lastIdx = chatModel.count - 1
                let lastItem = chatModel.get(lastIdx)
                if (lastItem.role === "assistant" && (lastItem.status === "pending" || !lastItem.isError)) {
                    let errorText = "Error: " + errorMessage
                    if (lastItem.text && lastItem.text.length > 0) {
                        errorText = lastItem.text + "\n\n[Error: " + errorMessage + "]"
                    }
                    chatModel.setProperty(lastIdx, "text", errorText)
                    chatModel.setProperty(lastIdx, "status", "failed")
                    chatModel.setProperty(lastIdx, "isError", true)
                    Qt.callLater(function() {
                        chatList.positionViewAtEnd()
                    })
                    return
                }
            }

            chatModel.append({
                role: "assistant",
                text: "Error: " + errorMessage,
                status: "failed",
                isError: true
            })

            Qt.callLater(function() {
                chatList.positionViewAtEnd()
            })
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 18

        // ==========================================
        // Sidebar: Real Conversation Navigator
        // ==========================================
        Rectangle {
            Layout.preferredWidth: 250
            Layout.fillHeight: true

            radius: 22
            color: "#E8E5DA"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 14

                // App Brand
                RowLayout {
                    spacing: 12

                    Rectangle {
                        Layout.preferredWidth: 36
                        Layout.preferredHeight: 36
                        radius: 11
                        color: "#526D5B"

                        Text {
                            anchors.centerIn: parent
                            text: "O"
                            color: "white"
                            font.pixelSize: 21
                            font.bold: true
                        }
                    }

                    ColumnLayout {
                        spacing: 0

                        Text {
                            text: "ORION"
                            font.pixelSize: 18
                            font.bold: true
                            color: "#303A32"
                        }

                        Text {
                            text: "PERSONAL ASSISTANT"
                            font.pixelSize: 8
                            font.letterSpacing: 1.2
                            color: "#74796E"
                        }
                    }
                }

                // New Chat Button
                Button {
                    id: newChatButton
                    objectName: "newChatButton"
                    Layout.fillWidth: true
                    implicitHeight: 38
                    enabled: !root.busy

                    onClicked: {
                        if (typeof orion !== "undefined" && orion !== null) {
                            orion.createNewConversation()
                        }
                    }

                    background: Rectangle {
                        radius: 10
                        color: !newChatButton.enabled
                               ? "#B8BFB6"
                               : (newChatButton.down ? "#3C5547" : "#526D5B")
                    }

                    contentItem: RowLayout {
                        spacing: 8
                        Item { Layout.fillWidth: true }
                        Text {
                            text: "+  New Chat"
                            color: "white"
                            font.pixelSize: 13
                            font.bold: true
                        }
                        Item { Layout.fillWidth: true }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    height: 1
                    color: "#D3D0C5"
                }

                // Section title
                Text {
                    text: "CONVERSATIONS"
                    font.pixelSize: 10
                    font.bold: true
                    font.letterSpacing: 1.5
                    color: "#85877C"
                }

                // Scrollable Conversation List
                ListView {
                    id: convList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 4
                    model: conversationsModel

                    delegate: Rectangle {
                        id: convItem
                        width: convList.width
                        height: 38
                        radius: 9

                        readonly property bool isSelected: model.id === root.activeConversationId
                        readonly property bool isHovered: itemMouseArea.containsMouse

                        color: isSelected ? "#D9E0D5" : (isHovered ? "#E1DDD2" : "transparent")
                        border.width: isSelected ? 1 : 0
                        border.color: isSelected ? "#C6CEBF" : "transparent"

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            anchors.rightMargin: 8
                            spacing: 8

                            Text {
                                text: "💬"
                                font.pixelSize: 12
                                opacity: isSelected ? 0.9 : 0.5
                            }

                            Text {
                                Layout.fillWidth: true
                                text: model.title
                                elide: Text.ElideRight
                                font.pixelSize: 13
                                font.bold: isSelected
                                color: isSelected ? "#2E3B30" : "#555A51"
                            }

                            // Delete button (visible on hover or when selected)
                            Rectangle {
                                id: deleteBtn
                                width: 22
                                height: 22
                                radius: 6
                                visible: isHovered || isSelected
                                color: deleteMouseArea.containsMouse ? "#FCE8E6" : "transparent"

                                Text {
                                    anchors.centerIn: parent
                                    text: "✕"
                                    font.pixelSize: 11
                                    color: deleteMouseArea.containsMouse ? "#C53929" : "#898B80"
                                }

                                MouseArea {
                                    id: deleteMouseArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    enabled: !root.busy
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        root.openDeleteConfirmation(model.id, model.title)
                                    }
                                }
                            }
                        }

                        MouseArea {
                            id: itemMouseArea
                            anchors.fill: parent
                            hoverEnabled: true
                            enabled: !root.busy
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (model.id !== root.activeConversationId) {
                                    orion.selectConversation(model.id)
                                }
                            }
                        }
                    }

                    // Empty state when no conversations exist
                    Text {
                        anchors.centerIn: parent
                        text: "No conversations yet"
                        font.pixelSize: 12
                        color: "#9C9E94"
                        visible: conversationsModel.count === 0 && !root.isInitializing
                    }

                    ScrollBar.vertical: ScrollBar {
                        policy: ScrollBar.AsNeeded
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    height: 1
                    color: "#D3D0C5"
                }

                Text {
                    text: "SYSTEM STATUS"
                    font.pixelSize: 10
                    font.bold: true
                    font.letterSpacing: 1.2
                    color: "#85877C"
                }

                RowLayout {
                    spacing: 8

                    Rectangle {
                        width: 8
                        height: 8
                        radius: 4
                        color: root.busy ? "#D48B38" : "#77936F"
                    }

                    Text {
                        text: root.busy ? "Processing request..." : "Desktop core online"
                        color: root.busy ? "#8A5B28" : "#53654F"
                        font.pixelSize: 12
                    }
                }

                Text {
                    text: root.busy ? "Model: Generating (Simulated)..." : "Model: Simulated (FakeModel)"
                    color: "#85877C"
                    font.pixelSize: 11
                }
            }
        }

        // ==========================================
        // Main Workspace: Chat Panel & Input
        // ==========================================
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true

            radius: 22
            color: "#FCFBF8"
            border.width: 1
            border.color: "#EAE7DF"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 26
                spacing: 16

                // Header
                RowLayout {
                    Layout.fillWidth: true

                    ColumnLayout {
                        spacing: 4

                        Text {
                            text: root.getActiveTitle()
                            font.pixelSize: 22
                            font.bold: true
                            color: "#303A32"
                            elide: Text.ElideRight
                        }

                        Text {
                            text: root.activeConversationId ? "Active persistent session" : "Start a new conversation"
                            font.pixelSize: 12
                            color: "#898B80"
                        }
                    }

                    Item {
                        Layout.fillWidth: true
                    }

                    Rectangle {
                        width: root.busy ? 118 : 105
                        height: 30
                        radius: 15
                        color: root.busy ? "#F6EEDD" : "#E6ECE3"

                        Text {
                            anchors.centerIn: parent
                            text: root.busy ? "THINKING..." : "CORE ONLINE"
                            font.pixelSize: 10
                            font.bold: true
                            color: root.busy ? "#8A5B28" : "#526D5B"
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    height: 1
                    color: "#ECEAE3"
                }

                // Message Area (Loading, Empty, or Message List)
                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    // 1. Initial Loading State
                    ColumnLayout {
                        anchors.centerIn: parent
                        spacing: 12
                        visible: root.isInitializing

                        Rectangle {
                            Layout.alignment: Qt.AlignHCenter
                            width: 36
                            height: 36
                            radius: 18
                            color: "#EAE7DF"

                            Text {
                                anchors.centerIn: parent
                                text: "⏳"
                                font.pixelSize: 16
                            }
                        }

                        Text {
                            Layout.alignment: Qt.AlignHCenter
                            text: "Loading conversations..."
                            color: "#898B80"
                            font.pixelSize: 14
                        }
                    }

                    // 2. Empty Workspace State (No active messages)
                    ColumnLayout {
                        anchors.centerIn: parent
                        spacing: 14
                        visible: !root.isInitializing && chatModel.count === 0

                        Rectangle {
                            Layout.alignment: Qt.AlignHCenter
                            width: 48
                            height: 48
                            radius: 24
                            color: "#E8EFE5"

                            Text {
                                anchors.centerIn: parent
                                text: "✦"
                                font.pixelSize: 22
                                color: "#526D5B"
                            }
                        }

                        Text {
                            Layout.alignment: Qt.AlignHCenter
                            text: "How can I help you today?"
                            font.pixelSize: 19
                            font.bold: true
                            color: "#303A32"
                        }

                        Text {
                            Layout.alignment: Qt.AlignHCenter
                            text: "Type a message below or start a new conversation."
                            font.pixelSize: 13
                            color: "#898B80"
                        }
                    }

                    // 3. Active Chat ListView
                    ListView {
                        id: chatList
                        anchors.fill: parent
                        clip: true
                        spacing: 12
                        model: chatModel
                        visible: !root.isInitializing && chatModel.count > 0

                        delegate: Item {
                            width: chatList.width
                            height: bubble.height + 8

                            Row {
                                width: parent.width
                                layoutDirection: model.role === "user"
                                                 ? Qt.RightToLeft
                                                 : Qt.LeftToRight

                                Rectangle {
                                    id: bubble

                                    width: Math.min(
                                        chatList.width * 0.78,
                                        Math.max(120, bubbleContent.implicitWidth + 34)
                                    )
                                    height: bubbleContent.implicitHeight + 24
                                    radius: 15

                                    color: {
                                        if (model.status === "failed") return "#FCE8E6"
                                        if (model.status === "interrupted") return "#FFF3E0"
                                        return model.role === "user" ? "#E4EAE1" : "#F0EEE8"
                                    }
                                    border.width: model.isError ? 1 : 0
                                    border.color: {
                                        if (model.status === "failed") return "#E5A9A4"
                                        if (model.status === "interrupted") return "#FFE0B2"
                                        return "transparent"
                                    }

                                    ColumnLayout {
                                        id: bubbleContent
                                        x: 17
                                        y: 12
                                        width: Math.min(implicitWidth, chatList.width * 0.78 - 34)
                                        spacing: 4

                                        // Status badge for failed or interrupted messages
                                        Text {
                                            visible: model.isError
                                            text: model.status === "interrupted" ? "INTERRUPTED" : "FAILED"
                                            font.pixelSize: 10
                                            font.bold: true
                                            color: model.status === "interrupted" ? "#D97706" : "#C53929"
                                        }

                                        Text {
                                            id: bubbleText
                                            Layout.fillWidth: true
                                            text: (model.status === "pending" && (!model.text || model.text.length === 0)) ? "..." : model.text
                                            opacity: (model.status === "pending" && (!model.text || model.text.length === 0)) ? 0.6 : 1.0
                                            wrapMode: Text.Wrap
                                            color: {
                                                if (model.status === "failed") return "#9C2B27"
                                                if (model.status === "interrupted") return "#92400E"
                                                return "#384139"
                                            }
                                            font.pixelSize: 14
                                            lineHeight: 1.35
                                        }
                                    }
                                }
                            }
                        }

                        ScrollBar.vertical: ScrollBar {
                            policy: ScrollBar.AsNeeded
                        }
                    }
                }

                // Message composer
                Rectangle {
                    Layout.fillWidth: true
                    height: 62

                    radius: 16
                    color: root.busy ? "#EBE9E3" : "#F2F0EA"
                    border.width: 1
                    border.color: "#E6E3D9"

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 15
                        anchors.rightMargin: 9
                        spacing: 10

                        TextField {
                            id: composer
                            objectName: "composer"

                            Layout.fillWidth: true
                            enabled: !root.busy

                            placeholderText: root.busy ? "ORION is generating a response..." : "Message ORION..."
                            placeholderTextColor: "#999B90"

                            color: "#303A32"
                            font.pixelSize: 14

                            background: Rectangle {
                                color: "transparent"
                            }

                            selectByMouse: true
                            onAccepted: root.sendMessage()
                        }

                        Button {
                            id: stopButton
                            objectName: "stopButton"

                            visible: root.busy
                            enabled: root.busy
                            text: "Stop"
                            implicitWidth: 76
                            implicitHeight: 42

                            onClicked: {
                                if (typeof orion !== "undefined" && orion !== null) {
                                    orion.stopGeneration()
                                }
                            }

                            background: Rectangle {
                                radius: 12
                                color: stopButton.down ? "#8A3828" : "#C53929"
                            }

                            contentItem: Text {
                                text: stopButton.text
                                color: "white"
                                font.pixelSize: 13
                                font.bold: true
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                        }

                        Button {
                            id: sendButton
                            objectName: "sendButton"

                            visible: !root.busy
                            enabled: !root.busy && composer.text.trim().length > 0
                            text: "Send"
                            implicitWidth: 76
                            implicitHeight: 42

                            onClicked: root.sendMessage()

                            background: Rectangle {
                                radius: 12
                                color: !sendButton.enabled
                                       ? "#A4B0A7"
                                       : (sendButton.down ? "#3C5547" : "#526D5B")
                            }

                            contentItem: Text {
                                text: sendButton.text
                                color: "white"
                                font.pixelSize: 13
                                font.bold: true
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: "ORION is running locally in simulated mode. Local AI model integration is the next milestone."
                    color: "#A0A094"
                    font.pixelSize: 10
                    horizontalAlignment: Text.AlignHCenter
                }
            }
        }
    }

    // ==========================================
    // Delete Confirmation Modal Dialog
    // ==========================================
    Rectangle {
        id: deleteDialog
        objectName: "deleteDialog"
        visible: false
        anchors.fill: parent
        color: "#66000000"
        z: 999

        // Consume mouse clicks outside dialog card
        MouseArea {
            anchors.fill: parent
            onClicked: {}
        }

        Rectangle {
            width: 380
            height: 180
            radius: 18
            color: "#FCFBF8"
            anchors.centerIn: parent

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 22
                spacing: 14

                Text {
                    text: "Delete Conversation"
                    font.pixelSize: 17
                    font.bold: true
                    color: "#303A32"
                }

                Text {
                    Layout.fillWidth: true
                    text: "Are you sure you want to delete \"" + root.pendingDeleteTitle + "\"? All messages in this conversation will be permanently removed."
                    font.pixelSize: 13
                    color: "#666B62"
                    wrapMode: Text.Wrap
                }

                Item {
                    Layout.fillHeight: true
                }

                RowLayout {
                    Layout.alignment: Qt.AlignRight
                    spacing: 10

                    Button {
                        text: "Cancel"
                        implicitWidth: 80
                        implicitHeight: 36
                        onClicked: {
                            deleteDialog.visible = false
                        }

                        background: Rectangle {
                            radius: 9
                            color: "#EBE8DF"
                        }

                        contentItem: Text {
                            text: "Cancel"
                            color: "#4A5247"
                            font.pixelSize: 13
                            font.bold: true
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                    }

                    Button {
                        text: "Delete"
                        implicitWidth: 80
                        implicitHeight: 36
                        onClicked: {
                            deleteDialog.visible = false
                            if (typeof orion !== "undefined" && orion !== null) {
                                orion.deleteConversation(root.pendingDeleteId)
                            }
                        }

                        background: Rectangle {
                            radius: 9
                            color: "#D9534F"
                        }

                        contentItem: Text {
                            text: "Delete"
                            color: "white"
                            font.pixelSize: 13
                            font.bold: true
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                    }
                }
            }
        }
    }

    // ==========================================
    // Tool Execution Approval Modal Dialog
    // ==========================================
    Timer {
        id: approvalTimer
        interval: 1000
        repeat: true
        running: pendingApprovalsModel.count > 0
        onTriggered: {
            if (root.approvalRemainingSeconds > 0) {
                root.approvalRemainingSeconds -= 1
            }
        }
    }

    Rectangle {
        id: approvalDialog
        objectName: "approvalDialog"
        visible: pendingApprovalsModel.count > 0
        anchors.fill: parent
        color: "#66000000"
        z: 1000

        // Consume mouse clicks outside dialog card
        MouseArea {
            anchors.fill: parent
            onClicked: {}
        }

        readonly property var currentApproval: pendingApprovalsModel.count > 0 ? pendingApprovalsModel.get(0) : null
        readonly property string currentApprovalId: currentApproval ? currentApproval.approval_id : ""
        readonly property string currentNonce: currentApproval ? currentApproval.nonce : ""
        readonly property bool isMutating: currentApproval ? currentApproval.is_mutating : false
        readonly property bool isExpired: root.approvalRemainingSeconds <= 0

        Rectangle {
            width: Math.min(parent.width - 40, 520)
            implicitHeight: approvalContentColumn.implicitHeight + 40
            radius: 18
            color: "#FCFBF8"
            anchors.centerIn: parent

            ColumnLayout {
                id: approvalContentColumn
                anchors.fill: parent
                anchors.margins: 20
                spacing: 12

                // Header with Title and Mutating Badge
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Text {
                        text: "Action Approval Required"
                        font.pixelSize: 17
                        font.bold: true
                        color: "#303A32"
                        textFormat: Text.PlainText
                    }

                    Item { Layout.fillWidth: true }

                    // Mutating Badge
                    Rectangle {
                        implicitWidth: mutatingLabel.implicitWidth + 12
                        implicitHeight: 22
                        radius: 6
                        color: approvalDialog.isMutating ? "#FBEAE8" : "#EBF3EE"
                        border.color: approvalDialog.isMutating ? "#D9534F" : "#526D5B"
                        border.width: 1

                        Text {
                            id: mutatingLabel
                            anchors.centerIn: parent
                            text: approvalDialog.isMutating ? "MUTATING" : "READ-ONLY"
                            font.pixelSize: 10
                            font.bold: true
                            color: approvalDialog.isMutating ? "#D9534F" : "#526D5B"
                            textFormat: Text.PlainText
                        }
                    }
                }

                // Multiple approvals indicator if count > 1
                Text {
                    visible: pendingApprovalsModel.count > 1
                    Layout.fillWidth: true
                    text: "Pending approval 1 of " + pendingApprovalsModel.count
                    font.pixelSize: 11
                    color: "#88887E"
                    textFormat: Text.PlainText
                }

                // Tool Name and Description
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4

                    Text {
                        id: toolNameText
                        objectName: "toolNameText"
                        Layout.fillWidth: true
                        text: approvalDialog.currentApproval ? approvalDialog.currentApproval.tool_name : ""
                        font.pixelSize: 14
                        font.bold: true
                        color: "#2C342E"
                        textFormat: Text.PlainText
                    }

                    Text {
                        id: actionSummaryText
                        objectName: "actionSummaryText"
                        Layout.fillWidth: true
                        text: approvalDialog.currentApproval ? approvalDialog.currentApproval.action_summary : ""
                        font.pixelSize: 12
                        color: "#5B6358"
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }
                }

                // Arguments Preview
                Text {
                    text: "Validated Arguments:"
                    font.pixelSize: 12
                    font.bold: true
                    color: "#4A5247"
                    textFormat: Text.PlainText
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: Math.min(argsText.implicitHeight + 16, 120)
                    color: "#F4F1E9"
                    radius: 8
                    border.color: "#E0DDD3"
                    border.width: 1
                    clip: true

                    ScrollView {
                        anchors.fill: parent
                        anchors.margins: 8

                        Text {
                            id: argsText
                            objectName: "argsText"
                            width: parent.width
                            text: approvalDialog.currentApproval ? JSON.stringify(approvalDialog.currentApproval.arguments, null, 2) : "{}"
                            font.family: "Monospace"
                            font.pixelSize: 11
                            color: "#303A32"
                            wrapMode: Text.WrapAnywhere
                            textFormat: Text.PlainText
                        }
                    }
                }

                // Expiration Countdown
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6

                    Text {
                        id: countdownLabel
                        objectName: "countdownLabel"
                        text: approvalDialog.isExpired ? "Approval expired (60s limit)" : "Expires in " + root.approvalRemainingSeconds + "s"
                        font.pixelSize: 12
                        font.bold: approvalDialog.isExpired
                        color: approvalDialog.isExpired ? "#D9534F" : "#74796E"
                        textFormat: Text.PlainText
                    }
                }

                Item { Layout.fillHeight: true }

                // Actions: Reject and Approve Once
                RowLayout {
                    Layout.alignment: Qt.AlignRight
                    spacing: 10

                    Button {
                        id: rejectButton
                        objectName: "approvalRejectButton"
                        text: "Reject"
                        implicitWidth: 90
                        implicitHeight: 36
                        onClicked: {
                            if (typeof orion !== "undefined" && orion !== null) {
                                orion.submitApprovalDecision(
                                    approvalDialog.currentApprovalId,
                                    approvalDialog.currentNonce,
                                    false
                                )
                            }
                        }

                        background: Rectangle {
                            radius: 9
                            color: "#EBE8DF"
                        }

                        contentItem: Text {
                            text: "Reject"
                            color: "#4A5247"
                            font.pixelSize: 13
                            font.bold: true
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                    }

                    Button {
                        id: approveButton
                        objectName: "approvalApproveButton"
                        text: "Approve Once"
                        implicitWidth: 120
                        implicitHeight: 36
                        enabled: !approvalDialog.isExpired && approvalDialog.currentApprovalId.length > 0
                        opacity: enabled ? 1.0 : 0.5
                        onClicked: {
                            if (typeof orion !== "undefined" && orion !== null) {
                                orion.submitApprovalDecision(
                                    approvalDialog.currentApprovalId,
                                    approvalDialog.currentNonce,
                                    true
                                )
                            }
                        }

                        background: Rectangle {
                            radius: 9
                            color: approveButton.enabled ? "#526D5B" : "#A0A094"
                        }

                        contentItem: Text {
                            text: "Approve Once"
                            color: "white"
                            font.pixelSize: 13
                            font.bold: true
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                    }
                }
            }
        }
    }

    // ==========================================
    // Execution Display Drawer
    // ==========================================
    Rectangle {
        id: executionDrawer
        objectName: "executionDrawer"
        visible: root.executionPanelVisible
        anchors {
            left: parent.left
            right: parent.right
            bottom: parent.bottom
            margins: 18
        }
        height: Math.min(360, parent.height * 0.45)
        z: 500

        radius: 16
        color: "#FCFBF8"
        border.width: 1
        border.color: "#D6D3CA"

        Column {
            anchors.fill: parent
            anchors.margins: 14
            spacing: 8

            // Header row
            Row {
                width: parent.width
                spacing: 8

                Text {
                    text: "Subprocess Execution"
                    font.pixelSize: 13
                    font.bold: true
                    color: "#303A32"
                    textFormat: Text.PlainText
                    verticalAlignment: Text.AlignVCenter
                    height: 28
                }

                // Status badge
                Rectangle {
                    width: statusBadge.implicitWidth + 12
                    height: 22
                    radius: 5
                    anchors.verticalCenter: parent.verticalCenter
                    color: {
                        const s = root.executionStatus
                        if (s === "running") return "#F6EEDD"
                        if (s === "completed") return "#EBF3EE"
                        if (s === "cancelled") return "#F6F0E0"
                        if (s === "timed_out") return "#FEF3C7"
                        return "#FBEAE8"
                    }
                    border.color: {
                        const s = root.executionStatus
                        if (s === "running") return "#C9A74E"
                        if (s === "completed") return "#526D5B"
                        if (s === "cancelled") return "#A08040"
                        if (s === "timed_out") return "#D97706"
                        return "#D9534F"
                    }
                    border.width: 1

                    Text {
                        id: statusBadge
                        anchors.centerIn: parent
                        text: root.executionStatus.toUpperCase()
                        font.pixelSize: 10
                        font.bold: true
                        color: {
                            const s = root.executionStatus
                            if (s === "running") return "#C9A74E"
                            if (s === "completed") return "#526D5B"
                            if (s === "cancelled") return "#A08040"
                            if (s === "timed_out") return "#D97706"
                            return "#D9534F"
                        }
                        textFormat: Text.PlainText
                    }
                }

                // Truncation badge
                Rectangle {
                    visible: root.executionTruncated
                    width: truncLabel.implicitWidth + 12
                    height: 22
                    radius: 5
                    anchors.verticalCenter: parent.verticalCenter
                    color: "#FBEAE8"
                    border.color: "#D9534F"
                    border.width: 1
                    Text {
                        id: truncLabel
                        anchors.centerIn: parent
                        text: "TRUNCATED"
                        font.pixelSize: 10
                        font.bold: true
                        color: "#D9534F"
                        textFormat: Text.PlainText
                    }
                }

                Item { width: parent.width - 220; height: 1 }

                // Duration / live elapsed label
                Text {
                    visible: root.executionStatus === "running" || root.executionDurationMs > 0
                    text: root.executionStatus === "running"
                        ? (root.executionElapsedMs / 1000).toFixed(1) + "s"
                        : (root.executionDurationMs / 1000).toFixed(2) + "s"
                    font.pixelSize: 11
                    color: "#889080"
                    textFormat: Text.PlainText
                    verticalAlignment: Text.AlignVCenter
                    height: 28
                }

                // Cancel button (only shown while running)
                Rectangle {
                    id: stopExecutionButton
                    objectName: "stopExecutionButton"
                    visible: root.executionStatus === "running"
                    enabled: root.executionStatus === "running" && root.executionId !== ""
                    width: cancelExecBtn.implicitWidth + 20
                    height: 26
                    radius: 8
                    color: "#FBEAE8"
                    border.color: "#D9534F"
                    border.width: 1
                    anchors.verticalCenter: parent.verticalCenter

                    Text {
                        id: cancelExecBtn
                        anchors.centerIn: parent
                        text: "Stop"
                        font.pixelSize: 11
                        font.bold: true
                        color: "#D9534F"
                        textFormat: Text.PlainText
                    }

                    MouseArea {
                        anchors.fill: parent
                        enabled: stopExecutionButton.enabled
                        onClicked: {
                            if (typeof orion !== "undefined" && orion !== null && root.executionId !== "") {
                                orion.cancelExecution(root.executionId)
                            }
                        }
                    }
                }

                // Dismiss button
                Rectangle {
                    id: dismissExecutionButton
                    objectName: "dismissExecutionButton"
                    visible: root.executionStatus !== "running"
                    width: dismissBtn.implicitWidth + 20
                    height: 26
                    radius: 8
                    color: "#EBE9E3"
                    border.color: "#CCC9C0"
                    border.width: 1
                    anchors.verticalCenter: parent.verticalCenter

                    Text {
                        id: dismissBtn
                        anchors.centerIn: parent
                        text: "Dismiss"
                        font.pixelSize: 11
                        color: "#666860"
                        textFormat: Text.PlainText
                    }

                    MouseArea {
                        anchors.fill: parent
                        onClicked: {
                            root.executionPanelVisible = false
                            root.executionId = ""
                        }
                    }
                }
            }

            // Command line display
            Text {
                width: parent.width
                text: root.executionCommand + (root.executionArgs.length > 0 ? " " + root.executionArgs.join(" ") : "")
                font.pixelSize: 11
                font.family: "monospace"
                color: "#555750"
                textFormat: Text.PlainText
                elide: Text.ElideRight
            }

            // CWD
            Text {
                width: parent.width
                text: "cwd: " + root.executionCwd
                font.pixelSize: 10
                color: "#888B80"
                textFormat: Text.PlainText
                elide: Text.ElideLeft
            }

            // Error message
            Text {
                visible: root.executionError !== "" && root.executionStatus !== "running"
                width: parent.width
                text: root.executionError
                font.pixelSize: 11
                color: "#D9534F"
                wrapMode: Text.WordWrap
                textFormat: Text.PlainText
            }

            // stdout / stderr split
            Row {
                width: parent.width
                height: executionDrawer.height - 130
                spacing: 8

                // stdout
                Rectangle {
                    width: (parent.width - 8) / 2
                    height: parent.height
                    radius: 8
                    color: "#F4F2EB"
                    border.width: 1
                    border.color: "#E0DDD5"

                    Column {
                        anchors.fill: parent
                        anchors.margins: 6
                        spacing: 4

                        Text {
                            text: "stdout"
                            font.pixelSize: 10
                            font.bold: true
                            color: "#7A7E70"
                            textFormat: Text.PlainText
                        }

                        ScrollView {
                            width: parent.width
                            height: parent.height - 20
                            clip: true
                            ScrollBar.vertical.policy: ScrollBar.AsNeeded
                            ScrollBar.horizontal.policy: ScrollBar.AsNeeded

                            TextArea {
                                objectName: "stdoutDisplay"
                                text: root.executionStdout
                                readOnly: true
                                wrapMode: TextArea.NoWrap
                                font.pixelSize: 11
                                font.family: "monospace"
                                color: "#303A32"
                                background: null
                                textFormat: TextEdit.PlainText
                            }
                        }
                    }
                }

                // stderr
                Rectangle {
                    width: (parent.width - 8) / 2
                    height: parent.height
                    radius: 8
                    color: root.executionStderr !== "" ? "#FDF8F4" : "#F4F2EB"
                    border.width: 1
                    border.color: root.executionStderr !== "" ? "#F0D8C8" : "#E0DDD5"

                    Column {
                        anchors.fill: parent
                        anchors.margins: 6
                        spacing: 4

                        Text {
                            text: "stderr"
                            font.pixelSize: 10
                            font.bold: true
                            color: root.executionStderr !== "" ? "#C07050" : "#7A7E70"
                            textFormat: Text.PlainText
                        }

                        ScrollView {
                            width: parent.width
                            height: parent.height - 20
                            clip: true
                            ScrollBar.vertical.policy: ScrollBar.AsNeeded
                            ScrollBar.horizontal.policy: ScrollBar.AsNeeded

                            TextArea {
                                objectName: "stderrDisplay"
                                text: root.executionStderr
                                readOnly: true
                                wrapMode: TextArea.NoWrap
                                font.pixelSize: 11
                                font.family: "monospace"
                                color: root.executionStderr !== "" ? "#8B3A20" : "#303A32"
                                background: null
                                textFormat: TextEdit.PlainText
                            }
                        }
                    }
                }
            }
        }
    }
}
