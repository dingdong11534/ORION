import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: workspacePage
    objectName: "workspaceBrowserPanel"
    required property var root
    required property var theme
    required property var workspaceEntriesModel

    property string entryFilter: ""
    property bool showGoToPath: false
    property var navHistory: [""]
    property int navIndex: 0
    property bool navigatingHistory: false
    property var knownFolders: [""]
    property var sortedFilteredEntries: []

    function navigateTo(relPath, recordHistory) {
        const clean = (relPath || "").replace(/^\/+|\/+$/g, "")
        if (recordHistory !== false && !navigatingHistory) {
            if (navHistory[navIndex] !== clean) {
                const next = navHistory.slice(0, navIndex + 1)
                next.push(clean)
                navHistory = next
                navIndex = next.length - 1
            }
        }
        if (typeof orion !== "undefined" && orion !== null) {
            orion.listWorkspaceDirectory(clean)
        }
    }

    function goBack() {
        if (navIndex > 0) {
            navIndex -= 1
            navigatingHistory = true
            navigateTo(navHistory[navIndex], false)
            navigatingHistory = false
        }
    }

    function goForward() {
        if (navIndex < navHistory.length - 1) {
            navIndex += 1
            navigatingHistory = true
            navigateTo(navHistory[navIndex], false)
            navigatingHistory = false
        }
    }

    function goParent() {
        if (!root.workspaceCurrentPath) return
        const parts = root.workspaceCurrentPath.split("/").filter(function(p) { return p.length > 0 })
        parts.pop()
        navigateTo(parts.join("/"), true)
    }

    function rebuildEntries() {
        const q = entryFilter.trim().toLowerCase()
        const dirs = []
        const files = []
        const folderSet = { "": true }
        for (let k = 0; k < knownFolders.length; k++) {
            folderSet[knownFolders[k]] = true
        }
        if (root.workspaceCurrentPath) folderSet[root.workspaceCurrentPath] = true

        for (let i = 0; i < workspaceEntriesModel.count; i++) {
            const e = workspaceEntriesModel.get(i)
            const name = e.name || ""
            const isDir = e.entry_type === "directory"
            if (isDir && !e.is_excluded) {
                const fullSub = root.workspaceCurrentPath ? (root.workspaceCurrentPath + "/" + name) : name
                folderSet[fullSub] = true
            }
            if (q && name.toLowerCase().indexOf(q) === -1)
                continue
            const row = {
                name: name,
                entry_type: e.entry_type,
                size_bytes: e.size_bytes,
                is_excluded: Boolean(e.is_excluded)
            }
            if (isDir) dirs.push(row)
            else files.push(row)
        }
        dirs.sort(function(a, b) { return a.name.localeCompare(b.name) })
        files.sort(function(a, b) { return a.name.localeCompare(b.name) })
        sortedFilteredEntries = dirs.concat(files)
        knownFolders = Object.keys(folderSet).sort()
    }

    function breadcrumbParts() {
        const res = [{ label: "Root", path: "" }]
        if (!root.workspaceCurrentPath) return res
        const segs = root.workspaceCurrentPath.split("/").filter(function(p) { return p.length > 0 })
        let acc = ""
        for (let i = 0; i < segs.length; i++) {
            acc = acc ? (acc + "/" + segs[i]) : segs[i]
            res.push({ label: segs[i], path: acc })
        }
        return res
    }

    function isSelectedFileInKnowledge() {
        if (!root.selectedWorkspaceFile) return false
        const targetName = root.selectedWorkspaceFile.split("/").pop()
        for (let i = 0; i < root.knowledgeSourcesModelRef.count; i++) {
            const s = root.knowledgeSourcesModelRef.get(i)
            if (s.display_name === targetName && (s.source_path || "").endsWith(root.selectedWorkspaceFile))
                return true
        }
        return false
    }

    Connections {
        target: workspaceEntriesModel
        function onCountChanged() { workspacePage.rebuildEntries() }
        function onDataChanged() { workspacePage.rebuildEntries() }
    }

    Component.onCompleted: rebuildEntries()

    radius: theme.radiusLg
    color: theme.bgSurface
    border.width: 1
    border.color: theme.border

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: theme.spaceXl
        spacing: theme.spaceMd

        // Header + Root
        RowLayout {
            Layout.fillWidth: true
            spacing: theme.spaceMd

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2

                Text {
                    text: "Workspace Explorer"
                    font.pixelSize: theme.fontTitle
                    font.bold: true
                    color: theme.textPrimary
                    textFormat: Text.PlainText
                }

                Text {
                    objectName: "workspaceRootLabel"
                    Layout.fillWidth: true
                    text: root.workspaceRoot ? ("Configured Root: " + root.workspaceRoot) : "Workspace root not configured"
                    font.pixelSize: theme.fontXs
                    color: theme.textMuted
                    elide: Text.ElideMiddle
                    textFormat: Text.PlainText
                }
            }

            Button {
                text: workspacePage.showGoToPath ? "Hide Go To" : "Go to path"
                implicitHeight: 30
                implicitWidth: implicitContentWidth + 16
                onClicked: workspacePage.showGoToPath = !workspacePage.showGoToPath
                background: Rectangle { radius: theme.radiusSm; color: theme.bgSurfaceAlt; border.width: 1; border.color: theme.borderStrong }
                contentItem: Text {
                    text: parent.text; font.pixelSize: theme.fontXs; color: theme.textPrimary
                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                }
            }
        }

        // Toolbar: Back, Forward, Parent, Refresh, Breadcrumbs, Filter
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: navBar.implicitHeight + 12
            radius: theme.radiusMd
            color: theme.bgSurfaceAlt
            border.width: 1
            border.color: theme.border

            RowLayout {
                id: navBar
                anchors.fill: parent
                anchors.margins: 6
                spacing: 6

                Button {
                    objectName: "workspaceBackButton"
                    text: "‹"
                    enabled: !root.workspaceToolBusy && workspacePage.navIndex > 0
                    implicitHeight: 30
                    implicitWidth: 32
                    onClicked: workspacePage.goBack()
                    background: Rectangle { radius: 6; color: parent.enabled ? theme.bgSurface : "transparent"; border.width: 1; border.color: theme.border }
                    contentItem: Text { text: parent.text; font.pixelSize: 15; color: parent.enabled ? theme.textPrimary : theme.textMuted; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                }

                Button {
                    objectName: "workspaceForwardButton"
                    text: "›"
                    enabled: !root.workspaceToolBusy && workspacePage.navIndex < workspacePage.navHistory.length - 1
                    implicitHeight: 30
                    implicitWidth: 32
                    onClicked: workspacePage.goForward()
                    background: Rectangle { radius: 6; color: parent.enabled ? theme.bgSurface : "transparent"; border.width: 1; border.color: theme.border }
                    contentItem: Text { text: parent.text; font.pixelSize: 15; color: parent.enabled ? theme.textPrimary : theme.textMuted; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                }

                Button {
                    id: workspaceUpButton
                    objectName: "workspaceUpButton"
                    text: "Up"
                    enabled: !root.workspaceToolBusy && root.workspaceCurrentPath !== ""
                    implicitHeight: 30
                    implicitWidth: 46
                    onClicked: workspacePage.goParent()
                    background: Rectangle { radius: 6; color: parent.enabled ? theme.bgSurface : "transparent"; border.width: 1; border.color: theme.border }
                    contentItem: Text { text: parent.text; font.pixelSize: theme.fontXs; color: parent.enabled ? theme.textPrimary : theme.textMuted; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                }

                Button {
                    objectName: "workspaceRefreshButton"
                    text: "Refresh"
                    enabled: !root.workspaceToolBusy
                    implicitHeight: 30
                    implicitWidth: implicitContentWidth + 14
                    onClicked: workspacePage.navigateTo(root.workspaceCurrentPath, false)
                    background: Rectangle { radius: 6; color: theme.bgSurface; border.width: 1; border.color: theme.border }
                    contentItem: Text { text: parent.text; font.pixelSize: theme.fontXs; color: theme.textPrimary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                }

                // Breadcrumbs
                ScrollView {
                    Layout.fillWidth: true
                    implicitHeight: 30
                    clip: true
                    ScrollBar.vertical.policy: ScrollBar.AlwaysOff

                    RowLayout {
                        spacing: 2
                        anchors.verticalCenter: parent.verticalCenter

                        Repeater {
                            model: workspacePage.breadcrumbParts()
                            delegate: RowLayout {
                                spacing: 2
                                Text { visible: index > 0; text: "/"; font.pixelSize: theme.fontXs; color: theme.textMuted }
                                Button {
                                    text: modelData.label
                                    implicitHeight: 26
                                    implicitWidth: implicitContentWidth + 12
                                    onClicked: workspacePage.navigateTo(modelData.path, true)
                                    background: Rectangle {
                                        radius: 6
                                        color: modelData.path === root.workspaceCurrentPath ? theme.bgSelected : "transparent"
                                    }
                                    contentItem: Text {
                                        text: parent.text
                                        font.pixelSize: theme.fontXs
                                        font.bold: modelData.path === root.workspaceCurrentPath
                                        color: theme.textPrimary
                                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                                        textFormat: Text.PlainText
                                    }
                                }
                            }
                        }
                    }
                }

                TextField {
                    id: workspaceFilterInput
                    objectName: "workspaceFilterInput"
                    Layout.preferredWidth: 170
                    implicitHeight: 30
                    placeholderText: "Filter folder..."
                    font.pixelSize: theme.fontXs
                    color: theme.textPrimary
                    placeholderTextColor: theme.textMuted
                    background: Rectangle { radius: 6; color: theme.bgSurface; border.width: 1; border.color: theme.borderStrong }
                    onTextChanged: {
                        workspacePage.entryFilter = text
                        workspacePage.rebuildEntries()
                    }
                }
            }
        }

        // Optional Advanced "Go to path" Row (strictly validated)
        RowLayout {
            visible: workspacePage.showGoToPath
            Layout.fillWidth: true
            spacing: theme.spaceSm

            TextField {
                id: workspacePathInput
                objectName: "workspacePathInput"
                Layout.fillWidth: true
                placeholderText: "Relative path inside workspace (e.g., orion/ui)"
                font.pixelSize: theme.fontXs
                onAccepted: goPathBtn.clicked()
            }

            Button {
                id: goPathBtn
                text: "Open Path"
                enabled: !root.workspaceToolBusy
                implicitHeight: 30
                implicitWidth: implicitContentWidth + 16
                onClicked: workspacePage.navigateTo(workspacePathInput.text.trim(), true)
                background: Rectangle { radius: theme.radiusSm; color: theme.accent }
                contentItem: Text {
                    text: parent.text; font.pixelSize: theme.fontXs; font.bold: true; color: theme.bgSurface
                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                }
            }
        }

        // Error Banner
        Rectangle {
            id: workspaceErrorBanner
            objectName: "workspaceErrorBanner"
            visible: root.workspaceError !== ""
            Layout.fillWidth: true
            implicitHeight: errTxt.implicitHeight + 14
            radius: theme.radiusSm
            color: theme.dangerBg
            border.width: 1
            border.color: theme.danger

            Text {
                id: errTxt
                anchors.fill: parent
                anchors.margins: 7
                text: root.workspaceError
                font.pixelSize: theme.fontXs
                color: theme.danger
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }
        }

        // Responsive Two/Three-Pane Explorer Body
        GridLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            columns: workspacePage.width >= 820 ? 3 : 1
            columnSpacing: theme.spaceMd
            rowSpacing: theme.spaceMd

            // Left Pane: Directory Navigation Tree
            Rectangle {
                visible: workspacePage.width >= 720
                Layout.fillHeight: true
                Layout.preferredWidth: 200
                Layout.fillWidth: workspacePage.width < 820
                radius: theme.radiusMd
                color: theme.bgSurfaceAlt
                border.width: 1
                border.color: theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: theme.spaceSm
                    spacing: 6

                    Text {
                        text: "Folders"
                        font.pixelSize: theme.fontXs
                        font.bold: true
                        color: theme.textMuted
                        textFormat: Text.PlainText
                    }

                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 2
                        model: workspacePage.knownFolders
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                        delegate: Rectangle {
                            width: ListView.view.width
                            implicitHeight: 28
                            radius: 6
                            readonly property bool isCur: modelData === root.workspaceCurrentPath
                            color: isCur ? theme.bgSelected : "transparent"

                            MouseArea {
                                anchors.fill: parent
                                onClicked: workspacePage.navigateTo(modelData, true)
                            }

                            Text {
                                anchors.fill: parent
                                anchors.leftMargin: 8 + (modelData === "" ? 0 : (modelData.split("/").length * 8))
                                anchors.rightMargin: 6
                                verticalAlignment: Text.AlignVCenter
                                text: modelData === "" ? "Workspace Root" : modelData.split("/").pop()
                                font.pixelSize: theme.fontXs
                                font.bold: isCur
                                color: isCur ? theme.accent : theme.textPrimary
                                elide: Text.ElideRight
                                textFormat: Text.PlainText
                            }
                        }
                    }
                }
            }

            // Main Pane: Directory & File Listing
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: workspacePage.width >= 820 ? workspacePage.width * 0.42 : workspacePage.width
                radius: theme.radiusMd
                color: theme.bgSurfaceAlt
                border.width: 1
                border.color: theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: theme.spaceSm
                    spacing: 6

                    Text {
                        visible: root.workspaceToolBusy
                        text: "Loading workspace directory..."
                        font.pixelSize: theme.fontXs
                        color: theme.accent
                        textFormat: Text.PlainText
                    }

                    ListView {
                        id: workspaceListView
                        objectName: "workspaceListView"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 4
                        model: workspacePage.sortedFilteredEntries
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                        delegate: Rectangle {
                            width: workspaceListView.width
                            implicitHeight: 34
                            radius: 6
                            readonly property string relEntry: root.workspaceCurrentPath ? (root.workspaceCurrentPath + "/" + modelData.name) : modelData.name
                            readonly property bool isSel: modelData.entry_type === "file" && root.selectedWorkspaceFile === relEntry
                            color: isSel ? theme.bgSelected : theme.bgSurface
                            border.width: 1
                            border.color: isSel ? theme.accent : theme.border

                            MouseArea {
                                anchors.fill: parent
                                onClicked: {
                                    if (modelData.is_excluded) return
                                    if (modelData.entry_type === "directory") {
                                        workspacePage.navigateTo(relEntry, true)
                                    } else {
                                        root.selectedWorkspaceFile = relEntry
                                    }
                                }
                                onDoubleClicked: {
                                    if (modelData.is_excluded) return
                                    if (modelData.entry_type === "directory") {
                                        workspacePage.navigateTo(relEntry, true)
                                    } else if (typeof orion !== "undefined" && orion !== null) {
                                        root.selectedWorkspaceFile = relEntry
                                        orion.readWorkspaceFile(relEntry)
                                    }
                                }
                            }

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 10
                                anchors.rightMargin: 10
                                spacing: 8

                                Text {
                                    text: modelData.entry_type === "directory" ? "DIR" : "FILE"
                                    font.pixelSize: 9
                                    font.bold: true
                                    color: modelData.entry_type === "directory" ? theme.accent : theme.textMuted
                                    textFormat: Text.PlainText
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.name
                                    font.pixelSize: theme.fontSm
                                    font.bold: modelData.entry_type === "directory"
                                    color: modelData.is_excluded ? theme.textMuted : theme.textPrimary
                                    elide: Text.ElideMiddle
                                    textFormat: Text.PlainText
                                }

                                Text {
                                    visible: modelData.entry_type === "file" && modelData.size_bytes >= 0
                                    text: theme.formatBytes(modelData.size_bytes)
                                    font.pixelSize: 10
                                    color: theme.textMuted
                                    textFormat: Text.PlainText
                                }
                            }
                        }
                    }

                    Text {
                        visible: !root.workspaceToolBusy && workspacePage.sortedFilteredEntries.length === 0
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        text: workspacePage.entryFilter ? "No matching entries in this folder." : "This folder is empty."
                        font.pixelSize: theme.fontSm
                        color: theme.textMuted
                        textFormat: Text.PlainText
                    }
                }
            }

            // Right/Bottom Pane: Selected File Details + Content Viewer + Add to Knowledge
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: workspacePage.width >= 820 ? workspacePage.width * 0.34 : workspacePage.width
                clip: true
                radius: theme.radiusMd
                color: theme.bgSurfaceAlt
                border.width: 1
                border.color: theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: theme.spaceMd
                    spacing: theme.spaceSm

                    Text {
                        Layout.fillWidth: true
                        text: root.selectedWorkspaceFile ? root.selectedWorkspaceFile : "Select a file to inspect"
                        font.pixelSize: theme.fontSm
                        font.bold: true
                        color: theme.textPrimary
                        elide: Text.ElideMiddle
                        textFormat: Text.PlainText
                    }

                    Flow {
                        visible: root.selectedWorkspaceFile !== ""
                        Layout.fillWidth: true
                        spacing: theme.spaceSm

                        Button {
                            objectName: "workspaceReadFileButton"
                            text: "Read File"
                            enabled: !root.workspaceToolBusy
                            implicitHeight: 30
                            implicitWidth: implicitContentWidth + 16
                            onClicked: {
                                if (typeof orion !== "undefined" && orion !== null)
                                    orion.readWorkspaceFile(root.selectedWorkspaceFile)
                            }
                            background: Rectangle { radius: theme.radiusSm; color: theme.accent }
                            contentItem: Text {
                                text: parent.text; font.pixelSize: theme.fontXs; font.bold: true; color: theme.bgSurface
                                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                            }
                        }

                        Button {
                            objectName: "workspaceAddToKnowledgeButton"
                            visible: root.knowledgeAvailable
                            text: workspacePage.isSelectedFileInKnowledge() ? "Indexed in Knowledge" : "Add to Knowledge"
                            enabled: !root.knowledgeBusy
                            implicitHeight: 30
                            implicitWidth: implicitContentWidth + 16
                            onClicked: {
                                if (typeof orion !== "undefined" && orion !== null)
                                    orion.addWorkspaceFileToKnowledge(root.selectedWorkspaceFile)
                            }
                            background: Rectangle {
                                radius: theme.radiusSm
                                color: workspacePage.isSelectedFileInKnowledge() ? theme.accentLight : theme.bgSurface
                                border.width: 1
                                border.color: theme.accent
                            }
                            contentItem: Text {
                                text: parent.text; font.pixelSize: theme.fontXs; font.bold: true; color: theme.accent
                                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                            }
                        }

                        Button {
                            visible: root.lastWorkspaceAddedDocId !== ""
                            text: "Open in Knowledge"
                            implicitHeight: 30
                            implicitWidth: implicitContentWidth + 14
                            onClicked: root.openKnowledgePage()
                            background: Rectangle { radius: theme.radiusSm; color: theme.bgHover }
                            contentItem: Text {
                                text: parent.text; font.pixelSize: theme.fontXs; color: theme.textPrimary
                                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                            }
                        }
                    }

                    // Untrusted Workspace Data Badge
                    Rectangle {
                        id: untrustedDataBadge
                        objectName: "untrustedDataBadge"
                        visible: root.workspaceResultPath !== ""
                        Layout.fillWidth: true
                        implicitHeight: 24
                        radius: 6
                        color: theme.warningBg
                        border.width: 1
                        border.color: theme.warning

                        Text {
                            anchors.fill: parent
                            anchors.leftMargin: 6
                            anchors.rightMargin: 6
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            text: "UNTRUSTED WORKSPACE DATA · READ-ONLY" + (root.workspaceResultTruncated ? " (TRUNCATED)" : "")
                            font.pixelSize: 10
                            font.bold: true
                            color: theme.warning
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }
                    }

                    ScrollView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true

                        TextArea {
                            id: workspaceFileContentText
                            objectName: "workspaceFileContentText"
                            readOnly: true
                            selectByMouse: true
                            text: root.workspaceResultContent
                            font.family: "monospace"
                            font.pixelSize: 11
                            color: theme.textPrimary
                            wrapMode: TextEdit.Wrap
                            background: Rectangle { color: theme.bgSurface; radius: 6; border.width: 1; border.color: theme.border }
                        }
                    }
                }
            }
        }
    }
}
