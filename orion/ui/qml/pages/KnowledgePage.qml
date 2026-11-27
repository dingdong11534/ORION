import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Rectangle {
    id: knowledgePage
    objectName: "knowledgePanel"
    required property var root
    required property var theme
    required property var knowledgeSourcesModel

    property string librarySearch: ""
    property string formatFilter: "All"
    property string sortBy: "name"
    property string selectedDetailId: ""
    property string directSearchScopeDocId: ""
    property bool showingSearchResults: false
    property var filteredSources: []

    function refreshFilteredSources() {
        const q = librarySearch.trim().toLowerCase()
        const fmt = formatFilter.toLowerCase()
        const out = []
        for (let i = 0; i < knowledgeSourcesModel.count; i++) {
            const item = knowledgeSourcesModel.get(i)
            const name = (item.display_name || "").toLowerCase()
            const path = (item.source_path || "").toLowerCase()
            const ftype = (item.file_type || "").toLowerCase()
            if (q && name.indexOf(q) === -1 && path.indexOf(q) === -1)
                continue
            if (fmt !== "all") {
                if (fmt === "code") {
                    if (["py", "js", "ts", "tsx", "jsx", "go", "rs", "c", "cpp", "java", "sql", "sh", "rb", "php", "cs", "swift", "kt", "vue"].indexOf(ftype) === -1)
                        continue
                } else if (ftype !== fmt) {
                    continue
                }
            }
            out.push({
                id: item.id,
                display_name: item.display_name,
                source_path: item.source_path,
                file_type: item.file_type,
                file_size: item.file_size,
                chunk_count: item.chunk_count,
                ingestion_status: item.ingestion_status,
                indexed_at: item.indexed_at,
                error_message: item.error_message
            })
        }
        out.sort(function(a, b) {
            if (sortBy === "size") return (b.file_size || 0) - (a.file_size || 0)
            if (sortBy === "date") return (b.indexed_at || "").localeCompare(a.indexed_at || "")
            return (a.display_name || "").localeCompare(b.display_name || "")
        })
        filteredSources = out
        if (selectedDetailId) {
            const stillThere = out.some(function(s) { return s.id === selectedDetailId })
            if (!stillThere && out.length > 0)
                selectedDetailId = out[0].id
            else if (!stillThere)
                selectedDetailId = ""
        } else if (out.length > 0) {
            selectedDetailId = out[0].id
        }
    }

    function selectedDetailSource() {
        for (let i = 0; i < knowledgeSourcesModel.count; i++) {
            const s = knowledgeSourcesModel.get(i)
            if (s.id === selectedDetailId) return s
        }
        return null
    }

    function overviewStats() {
        let indexed = 0
        let failed = 0
        let indexing = 0
        let chunks = 0
        let totalBytes = 0
        for (let i = 0; i < knowledgeSourcesModel.count; i++) {
            const s = knowledgeSourcesModel.get(i)
            if (s.ingestion_status === "indexed") {
                indexed++
                chunks += (s.chunk_count || 0)
                totalBytes += (s.file_size || 0)
            } else if (s.ingestion_status === "failed") {
                failed++
            } else if (s.ingestion_status === "indexing") {
                indexing++
            }
        }
        return { indexed: indexed, failed: failed, indexing: indexing, chunks: chunks, totalBytes: totalBytes }
    }

    Connections {
        target: knowledgeSourcesModel
        function onCountChanged() { knowledgePage.refreshFilteredSources() }
        function onDataChanged() { knowledgePage.refreshFilteredSources() }
    }

    Component.onCompleted: refreshFilteredSources()

    radius: theme.radiusLg
    color: theme.bgSurface
    border.width: 1
    border.color: theme.border

    FileDialog {
        id: knowledgeFileDialog
        title: "Import Documents into ORION Knowledge Base"
        fileMode: FileDialog.OpenFiles
        nameFilters: [
            "All supported (*.txt *.md *.markdown *.rst *.log *.py *.js *.ts *.tsx *.jsx *.c *.cpp *.h *.hpp *.java *.go *.rs *.cs *.php *.rb *.swift *.kt *.vue *.sql *.sh *.tex *.ini *.conf *.yaml *.yml *.toml *.json *.xml *.ipynb *.csv *.tsv *.html *.htm *.pdf *.docx *.epub *.pptx *.xlsx *.xlsm *.rtf *.odt *.ods)",
            "Documents (*.pdf *.epub *.docx *.pptx *.xlsx *.xlsm *.odt *.ods *.rtf *.html *.htm)",
            "Text & Data (*.txt *.md *.csv *.tsv *.json *.xml *.ipynb)",
            "All files (*)"
        ]
        onAccepted: {
            if (typeof orion !== "undefined" && orion !== null && selectedFiles.length > 0) {
                orion.importKnowledgeFiles(selectedFiles)
            }
        }
    }

    FolderDialog {
        id: knowledgeFolderDialog
        title: "Import Folder into ORION Knowledge Base"
        onAccepted: {
            if (typeof orion !== "undefined" && orion !== null && selectedFolder) {
                orion.importKnowledgeFolder(selectedFolder)
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: theme.spaceXl
        spacing: theme.spaceMd

        // Header + Overview Metrics
        RowLayout {
            Layout.fillWidth: true
            spacing: theme.spaceMd

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2

                Text {
                    text: "Knowledge Base"
                    font.pixelSize: theme.fontTitle
                    font.bold: true
                    color: theme.textPrimary
                    textFormat: Text.PlainText
                }

                Text {
                    readonly property var st: knowledgePage.overviewStats()
                    text: st.indexed + " indexed · " + st.chunks + " chunks · " + theme.formatBytes(st.totalBytes) + (st.failed > 0 ? (" · " + st.failed + " failed") : "")
                    font.pixelSize: theme.fontSm
                    color: st.failed > 0 ? theme.warning : theme.textMuted
                    textFormat: Text.PlainText
                }
            }

            Button {
                objectName: "importKnowledgeFilesButton"
                text: "+ Import Files"
                enabled: !root.knowledgeBusy
                implicitHeight: 34
                implicitWidth: implicitContentWidth + 20
                onClicked: knowledgeFileDialog.open()
                background: Rectangle { radius: theme.radiusSm; color: theme.accent }
                contentItem: Text {
                    text: parent.text; font.pixelSize: theme.fontSm; font.bold: true; color: theme.bgSurface
                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                }
            }

            Button {
                objectName: "importKnowledgeFolderButton"
                text: "+ Import Folder"
                enabled: !root.knowledgeBusy
                implicitHeight: 34
                implicitWidth: implicitContentWidth + 20
                onClicked: knowledgeFolderDialog.open()
                background: Rectangle { radius: theme.radiusSm; color: theme.bgSurfaceAlt; border.width: 1; border.color: theme.borderStrong }
                contentItem: Text {
                    text: parent.text; font.pixelSize: theme.fontSm; font.bold: true; color: theme.textPrimary
                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                }
            }

            Button {
                objectName: "askKnowledgeCtaButton"
                text: "Ask Knowledge"
                implicitHeight: 34
                implicitWidth: implicitContentWidth + 20
                onClicked: root.startAskKnowledge("", [])
                background: Rectangle { radius: theme.radiusSm; color: theme.accentLight; border.width: 1; border.color: theme.accent }
                contentItem: Text {
                    text: parent.text; font.pixelSize: theme.fontSm; font.bold: true; color: theme.accent
                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                }
            }
        }

        // Active Ingestion / Error Status Banner
        Rectangle {
            visible: root.knowledgeBusy || root.knowledgeStatusNote !== "" || root.knowledgeError !== ""
            Layout.fillWidth: true
            implicitHeight: statusRow.implicitHeight + 14
            radius: theme.radiusSm
            color: root.knowledgeError ? theme.dangerBg : theme.accentLight
            border.width: 1
            border.color: root.knowledgeError ? theme.danger : theme.borderStrong

            RowLayout {
                id: statusRow
                anchors.fill: parent
                anchors.margins: 8
                spacing: theme.spaceSm

                Text {
                    Layout.fillWidth: true
                    text: root.knowledgeError ? root.knowledgeError : (root.knowledgeStatusNote || "Indexing documents...")
                    font.pixelSize: theme.fontXs
                    color: root.knowledgeError ? theme.danger : theme.textPrimary
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                }

                Button {
                    id: cancelKnowledgeButton
                    objectName: "cancelKnowledgeButton"
                    visible: root.knowledgeBusy
                    text: "Cancel"
                    implicitHeight: 26
                    implicitWidth: 68
                    onClicked: {
                        if (typeof orion !== "undefined" && orion !== null)
                            orion.cancelKnowledgeIngestion()
                    }
                    background: Rectangle { radius: 6; color: theme.danger }
                    contentItem: Text {
                        text: parent.text; font.pixelSize: theme.fontXs; font.bold: true; color: theme.bgSurface
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                    }
                }
            }
        }

        // Direct FTS5 Search Bar (Zero LLM calls)
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: ftsRow.implicitHeight + 16
            radius: theme.radiusMd
            color: theme.bgSurfaceAlt
            border.width: 1
            border.color: theme.border

            RowLayout {
                id: ftsRow
                anchors.fill: parent
                anchors.margins: 8
                spacing: theme.spaceSm

                TextField {
                    id: knowledgeDirectSearchInput
                    objectName: "knowledgeDirectSearchInput"
                    Layout.fillWidth: true
                    implicitHeight: 32
                    placeholderText: directSearchScopeDocId ? "Search inside selected document (FTS5)..." : "Search all indexed knowledge (direct FTS5, instant)..."
                    font.pixelSize: theme.fontSm
                    color: theme.textPrimary
                    placeholderTextColor: theme.textMuted
                    background: Rectangle { radius: theme.radiusSm; color: theme.bgSurface; border.width: 1; border.color: theme.borderStrong }
                    onAccepted: searchBtn.clicked()
                }

                Button {
                    visible: knowledgePage.directSearchScopeDocId !== ""
                    text: "All Docs"
                    implicitHeight: 32
                    implicitWidth: implicitContentWidth + 14
                    onClicked: {
                        knowledgePage.directSearchScopeDocId = ""
                        if (knowledgeDirectSearchInput.text.trim()) searchBtn.clicked()
                    }
                    background: Rectangle { radius: theme.radiusSm; color: theme.bgHover }
                    contentItem: Text {
                        text: parent.text; font.pixelSize: theme.fontXs; color: theme.textPrimary
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                    }
                }

                Button {
                    id: searchBtn
                    objectName: "knowledgeSearchButton"
                    text: "Search FTS5"
                    enabled: knowledgeDirectSearchInput.text.trim().length > 0
                    implicitHeight: 32
                    implicitWidth: implicitContentWidth + 18
                    onClicked: {
                        knowledgePage.showingSearchResults = true
                        if (typeof orion !== "undefined" && orion !== null) {
                            orion.searchKnowledgeDirect(knowledgeDirectSearchInput.text, knowledgePage.directSearchScopeDocId, 12)
                        }
                    }
                    background: Rectangle { radius: theme.radiusSm; color: parent.enabled ? theme.accent : theme.borderStrong }
                    contentItem: Text {
                        text: parent.text; font.pixelSize: theme.fontXs; font.bold: true; color: theme.bgSurface
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                    }
                }

                Button {
                    visible: knowledgePage.showingSearchResults
                    text: "Clear Search"
                    implicitHeight: 32
                    implicitWidth: implicitContentWidth + 14
                    onClicked: {
                        knowledgePage.showingSearchResults = false
                        knowledgeDirectSearchInput.text = ""
                        root.knowledgeSearchHits = []
                    }
                    background: Rectangle { radius: theme.radiusSm; color: theme.bgHover }
                    contentItem: Text {
                        text: parent.text; font.pixelSize: theme.fontXs; color: theme.textPrimary
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                    }
                }
            }
        }

        // Direct FTS5 Search Results View (when active)
        Rectangle {
            visible: knowledgePage.showingSearchResults
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: theme.radiusMd
            color: theme.bgSurfaceAlt
            border.width: 1
            border.color: theme.border

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: theme.spaceMd
                spacing: theme.spaceSm

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        Layout.fillWidth: true
                        text: "FTS5 Search Results (" + root.knowledgeSearchHits.length + ")"
                        font.pixelSize: theme.fontMd
                        font.bold: true
                        color: theme.textPrimary
                        textFormat: Text.PlainText
                    }
                    Button {
                        visible: root.knowledgeSearchHits.length > 0
                        text: "Ask ORION about these results"
                        implicitHeight: 28
                        implicitWidth: implicitContentWidth + 16
                        onClicked: root.startAskKnowledge(knowledgeDirectSearchInput.text, [])
                        background: Rectangle { radius: theme.radiusSm; color: theme.accent }
                        contentItem: Text {
                            text: parent.text; font.pixelSize: theme.fontXs; font.bold: true; color: theme.bgSurface
                            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                        }
                    }
                }

                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: theme.spaceSm
                    model: root.knowledgeSearchHits
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    delegate: Rectangle {
                        width: ListView.view.width
                        implicitHeight: hitCol.implicitHeight + 16
                        radius: theme.radiusSm
                        color: theme.bgSurface
                        border.width: 1
                        border.color: theme.border

                        ColumnLayout {
                            id: hitCol
                            anchors.fill: parent
                            anchors.margins: 8
                            spacing: 4

                            RowLayout {
                                Layout.fillWidth: true
                                Text {
                                    Layout.fillWidth: true
                                    text: {
                                        let h = modelData.display_name || "Document"
                                        if (modelData.page_number > 0) h += " · Page " + modelData.page_number
                                        if (modelData.section_title) h += " · " + modelData.section_title
                                        return h
                                    }
                                    font.pixelSize: theme.fontXs
                                    font.bold: true
                                    color: theme.accent
                                    elide: Text.ElideRight
                                    textFormat: Text.PlainText
                                }
                                Button {
                                    text: "Open Reference"
                                    implicitHeight: 22
                                    implicitWidth: implicitContentWidth + 12
                                    onClicked: root.openReferenceModal(modelData)
                                    background: Rectangle { radius: 6; color: theme.bgHover }
                                    contentItem: Text {
                                        text: parent.text; font.pixelSize: 10; color: theme.textPrimary
                                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                                    }
                                }
                            }

                            Text {
                                Layout.fillWidth: true
                                text: modelData.excerpt || ""
                                font.pixelSize: theme.fontSm
                                color: theme.textPrimary
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                        }
                    }
                }

                Text {
                    visible: root.knowledgeSearchHits.length === 0
                    text: "No matching passages found in FTS5 index."
                    font.pixelSize: theme.fontSm
                    color: theme.textMuted
                    textFormat: Text.PlainText
                }
            }
        }

        // Library + Detail Split (responsive side-by-side or stacked)
        GridLayout {
            visible: !knowledgePage.showingSearchResults
            Layout.fillWidth: true
            Layout.fillHeight: true
            columns: knowledgePage.width >= 800 ? 2 : 1
            columnSpacing: theme.spaceMd
            rowSpacing: theme.spaceMd

            // Left: Document Library
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: knowledgePage.width >= 800 ? knowledgePage.width * 0.56 : knowledgePage.width
                radius: theme.radiusMd
                color: theme.bgSurfaceAlt
                border.width: 1
                border.color: theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: theme.spaceMd
                    spacing: theme.spaceSm

                    // Filter & Sort Controls
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: theme.spaceSm

                        TextField {
                            id: libraryFilterInput
                            Layout.fillWidth: true
                            implicitHeight: 30
                            placeholderText: "Filter documents..."
                            font.pixelSize: theme.fontXs
                            color: theme.textPrimary
                            placeholderTextColor: theme.textMuted
                            background: Rectangle { radius: theme.radiusSm; color: theme.bgSurface; border.width: 1; border.color: theme.borderStrong }
                            onTextChanged: {
                                knowledgePage.librarySearch = text
                                knowledgePage.refreshFilteredSources()
                            }
                        }

                        ComboBox {
                            model: ["All", "pdf", "epub", "docx", "pptx", "xlsx", "txt", "md", "html", "code"]
                            implicitWidth: 96
                            onCurrentTextChanged: {
                                knowledgePage.formatFilter = currentText
                                knowledgePage.refreshFilteredSources()
                            }
                        }

                        ComboBox {
                            model: ["Name", "Date", "Size"]
                            implicitWidth: 90
                            onCurrentTextChanged: {
                                knowledgePage.sortBy = currentText.toLowerCase()
                                knowledgePage.refreshFilteredSources()
                            }
                        }
                    }

                    // Multi-select indicator bar
                    RowLayout {
                        visible: root.selectedKnowledgeIds.length > 0
                        Layout.fillWidth: true
                        spacing: theme.spaceSm

                        Text {
                            Layout.fillWidth: true
                            text: root.selectedKnowledgeIds.length + " source(s) selected for Selected Sources mode"
                            font.pixelSize: theme.fontXs
                            color: theme.accent
                            font.bold: true
                            textFormat: Text.PlainText
                        }

                        Button {
                            text: "Ask Selected"
                            implicitHeight: 24
                            implicitWidth: implicitContentWidth + 12
                            onClicked: root.startSelectedSourcesChat()
                            background: Rectangle { radius: 6; color: theme.accent }
                            contentItem: Text {
                                text: parent.text; font.pixelSize: 10; font.bold: true; color: theme.bgSurface
                                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                            }
                        }

                        Button {
                            text: "Clear"
                            implicitHeight: 24
                            implicitWidth: implicitContentWidth + 12
                            onClicked: root.clearKnowledgeScope()
                            background: Rectangle { radius: 6; color: theme.bgHover }
                            contentItem: Text {
                                text: parent.text; font.pixelSize: 10; color: theme.textPrimary
                                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                            }
                        }
                    }

                    // Document List
                    ListView {
                        id: libraryListView
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 6
                        cacheBuffer: 400
                        model: knowledgePage.filteredSources
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                        delegate: Rectangle {
                            width: libraryListView.width
                            implicitHeight: docRow.implicitHeight + 14
                            radius: theme.radiusSm
                            readonly property bool isCurrent: knowledgePage.selectedDetailId === modelData.id
                            color: isCurrent ? theme.bgSelected : theme.bgSurface
                            border.width: 1
                            border.color: isCurrent ? theme.accent : theme.border

                            MouseArea {
                                anchors.fill: parent
                                onClicked: knowledgePage.selectedDetailId = modelData.id
                            }

                            RowLayout {
                                id: docRow
                                anchors.fill: parent
                                anchors.margins: 7
                                spacing: theme.spaceSm

                                CheckBox {
                                    checked: root.isKnowledgeSelected(modelData.id)
                                    onClicked: root.toggleKnowledgeSource(modelData.id)
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 2

                                    Text {
                                        Layout.fillWidth: true
                                        text: modelData.display_name || ""
                                        font.pixelSize: theme.fontSm
                                        font.bold: true
                                        color: theme.textPrimary
                                        elide: Text.ElideMiddle
                                        textFormat: Text.PlainText
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        text: (modelData.file_type || "").toUpperCase() + " · " + theme.formatBytes(modelData.file_size) + " · " + (modelData.chunk_count || 0) + " chunks · " + (modelData.ingestion_status || "")
                                        font.pixelSize: 10
                                        color: modelData.ingestion_status === "failed" ? theme.danger : theme.textMuted
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                }
                            }
                        }
                    }

                    ColumnLayout {
                        visible: knowledgePage.filteredSources.length === 0
                        Layout.fillWidth: true
                        spacing: 6

                        Text {
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignHCenter
                            text: knowledgeSourcesModel.count === 0 ? "Getting Started with ORION Knowledge" : "No documents match the current filter."
                            font.pixelSize: theme.fontMd
                            font.bold: knowledgeSourcesModel.count === 0
                            color: knowledgeSourcesModel.count === 0 ? theme.textPrimary : theme.textMuted
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }

                        Text {
                            visible: knowledgeSourcesModel.count === 0
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignHCenter
                            text: "1. Click '+ Import Files' or '+ Import Folder'.\n2. Wait for indexing to reach 'indexed'.\n3. Choose Ask Knowledge or Selected Sources in Chat.\n4. Ask questions and natural follow-ups.\n5. Click 'Show sources' and 'Inspect' to verify excerpts."
                            font.pixelSize: theme.fontXs
                            color: theme.textSecondary
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }
                    }
                }
            }

            // Right: Document Detail Pane
            Rectangle {
                id: detailPane
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: knowledgePage.width >= 800 ? knowledgePage.width * 0.40 : knowledgePage.width
                radius: theme.radiusMd
                color: theme.bgSurfaceAlt
                border.width: 1
                border.color: theme.border

                property var doc: null
                function refreshDoc() {
                    const nextDoc = knowledgePage.selectedDetailSource()
                    if (doc !== nextDoc) {
                        doc = nextDoc
                        if (doc && doc.file_type === "epub" && typeof orion !== "undefined" && orion !== null) {
                            const docId = doc.id
                            Qt.callLater(function() {
                                orion.loadEpubChapters(docId, "", 0, 50)
                            })
                        }
                    }
                }
                Component.onCompleted: refreshDoc()
                Connections {
                    target: knowledgePage
                    function onSelectedDetailIdChanged() { detailPane.refreshDoc() }
                }
                Connections {
                    target: knowledgeSourcesModel
                    function onCountChanged() { detailPane.refreshDoc() }
                }

                ScrollView {
                    anchors.fill: parent
                    anchors.margins: theme.spaceLg
                    clip: true
                    contentWidth: availableWidth
                    visible: detailPane.doc !== null

                ColumnLayout {
                    width: parent.width
                    spacing: theme.spaceMd

                    Text {
                        Layout.fillWidth: true
                        text: detailPane.doc ? detailPane.doc.display_name : ""
                        font.pixelSize: theme.fontLg
                        font.bold: true
                        color: theme.textPrimary
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 10
                        rowSpacing: 6

                        Text { text: "Format:"; font.pixelSize: theme.fontXs; color: theme.textMuted; textFormat: Text.PlainText }
                        Text { text: detailPane.doc ? (detailPane.doc.file_type || "").toUpperCase() : ""; font.pixelSize: theme.fontXs; font.bold: true; color: theme.textPrimary; textFormat: Text.PlainText }

                        Text { text: "Size:"; font.pixelSize: theme.fontXs; color: theme.textMuted; textFormat: Text.PlainText }
                        Text { text: detailPane.doc ? theme.formatBytes(detailPane.doc.file_size) : ""; font.pixelSize: theme.fontXs; color: theme.textPrimary; textFormat: Text.PlainText }

                        Text { text: "Chunks:"; font.pixelSize: theme.fontXs; color: theme.textMuted; textFormat: Text.PlainText }
                        Text { text: detailPane.doc ? String(detailPane.doc.chunk_count || 0) : "0"; font.pixelSize: theme.fontXs; color: theme.textPrimary; textFormat: Text.PlainText }

                        Text { text: "Status:"; font.pixelSize: theme.fontXs; color: theme.textMuted; textFormat: Text.PlainText }
                        Text {
                            text: detailPane.doc ? (detailPane.doc.ingestion_status || "") : ""
                            font.pixelSize: theme.fontXs; font.bold: true
                            color: (detailPane.doc && detailPane.doc.ingestion_status === "failed") ? theme.danger : theme.accent
                            textFormat: Text.PlainText
                        }

                        Text { text: "Indexed:"; font.pixelSize: theme.fontXs; color: theme.textMuted; textFormat: Text.PlainText }
                        Text {
                            Layout.fillWidth: true
                            text: detailPane.doc ? (detailPane.doc.indexed_at || "") : ""
                            font.pixelSize: theme.fontXs; color: theme.textPrimary; elide: Text.ElideRight; textFormat: Text.PlainText
                        }
                    }

                    // EPUB Book Details, Reading Progress, Policy & Chapter Navigator
                    Rectangle {
                        visible: Boolean(detailPane.doc && detailPane.doc.file_type === "epub")
                        Layout.fillWidth: true
                        implicitHeight: epubCol.implicitHeight + 16
                        radius: theme.radiusSm
                        color: theme.bgSurface
                        border.width: 1
                        border.color: theme.border

                        ColumnLayout {
                            id: epubCol
                            anchors.fill: parent
                            anchors.margins: 8
                            spacing: theme.spaceSm

                            Text {
                                text: "Book & Reading Progress"
                                font.pixelSize: theme.fontSm
                                font.bold: true
                                color: theme.accent
                                textFormat: Text.PlainText
                            }

                            Text {
                                visible: Boolean(detailPane.doc && (detailPane.doc.title || detailPane.doc.author))
                                Layout.fillWidth: true
                                text: (detailPane.doc && detailPane.doc.title ? detailPane.doc.title : "") + (detailPane.doc && detailPane.doc.author ? (" · " + detailPane.doc.author) : "")
                                font.pixelSize: theme.fontXs
                                font.bold: true
                                color: theme.textPrimary
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }

                            Text {
                                Layout.fillWidth: true
                                text: {
                                    if (!detailPane.doc) return ""
                                    let p = "Chapters: " + (detailPane.doc.total_chapters || 0) + " · Whole book searchable"
                                    const dispCh = detailPane.doc.display_reading_chapter > 0 ? detailPane.doc.display_reading_chapter : detailPane.doc.current_reading_position
                                    if (dispCh > 0) p += " · Reading: Ch " + dispCh
                                    return p
                                }
                                font.pixelSize: 10
                                color: theme.textSecondary
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6

                                Text {
                                    text: "Reading Progress:"
                                    font.pixelSize: 10
                                    color: theme.textMuted
                                    textFormat: Text.PlainText
                                }

                                TextField {
                                    id: epubPosInput
                                    objectName: "epubPosInput"
                                    Layout.fillWidth: true
                                    implicitHeight: 30
                                    placeholderText: "Current Ch #"
                                    text: {
                                        if (!detailPane.doc) return ""
                                        const ch = detailPane.doc.display_chapter_number > 0 ? detailPane.doc.display_chapter_number : (detailPane.doc.display_reading_chapter > 0 ? detailPane.doc.display_reading_chapter : detailPane.doc.current_reading_position)
                                        return ch > 0 ? String(ch) : ""
                                    }
                                    font.pixelSize: theme.fontXs
                                    color: theme.textPrimary
                                    placeholderTextColor: theme.textMuted
                                    background: Rectangle { radius: 6; color: theme.bgSurfaceAlt; border.width: 1; border.color: theme.borderStrong }
                                }

                                Button {
                                    objectName: "saveReadingProgressButton"
                                    text: "Save"
                                    implicitHeight: 30
                                    implicitWidth: 56
                                    onClicked: {
                                        if (!detailPane.doc || typeof orion === "undefined" || orion === null) return
                                        const pos = parseInt(epubPosInput.text) || -1
                                        orion.setEpubReadingProgress(detailPane.doc.id, pos, "whole_document", -1, -1)
                                    }
                                    background: Rectangle { radius: 6; color: theme.accent }
                                    contentItem: Text {
                                        text: parent.text; font.pixelSize: theme.fontXs; font.bold: true; color: theme.bgSurface
                                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                                    }
                                }
                            }

                            // Paginated Chapter Navigator
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                TextField {
                                    id: chapterSearchInput
                                    Layout.fillWidth: true
                                    implicitHeight: 28
                                    placeholderText: "Search chapters (" + root.epubChaptersTotal + ")..."
                                    font.pixelSize: 10
                                    color: theme.textPrimary
                                    placeholderTextColor: theme.textMuted
                                    background: Rectangle { radius: 6; color: theme.bgSurfaceAlt; border.width: 1; border.color: theme.borderStrong }
                                    onAccepted: {
                                        if (detailPane.doc && typeof orion !== "undefined" && orion !== null)
                                            orion.loadEpubChapters(detailPane.doc.id, text, 0, 50)
                                    }
                                }
                                Button {
                                    text: "‹"
                                    enabled: root.epubChaptersOffset > 0
                                    implicitHeight: 28; implicitWidth: 26
                                    onClicked: {
                                        if (detailPane.doc && typeof orion !== "undefined" && orion !== null)
                                            orion.loadEpubChapters(detailPane.doc.id, chapterSearchInput.text, Math.max(0, root.epubChaptersOffset - 50), 50)
                                    }
                                }
                                Button {
                                    text: "›"
                                    enabled: root.epubChaptersOffset + 50 < root.epubChaptersTotal
                                    implicitHeight: 28; implicitWidth: 26
                                    onClicked: {
                                        if (detailPane.doc && typeof orion !== "undefined" && orion !== null)
                                            orion.loadEpubChapters(detailPane.doc.id, chapterSearchInput.text, root.epubChaptersOffset + 50, 50)
                                    }
                                }
                            }

                            ListView {
                                Layout.fillWidth: true
                                Layout.preferredHeight: Math.min(contentHeight, 140)
                                clip: true
                                spacing: 2
                                model: (detailPane.doc && root.epubChaptersDocId === detailPane.doc.id) ? root.epubChapters : []
                                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                                delegate: Rectangle {
                                    width: ListView.view.width
                                    implicitHeight: 24
                                    radius: 4
                                    color: (detailPane.doc && detailPane.doc.current_reading_position === modelData.reading_position) ? theme.bgSelected : "transparent"

                                    MouseArea {
                                        anchors.fill: parent
                                        onClicked: {
                                            const chTarget = modelData.display_chapter_number > 0 ? modelData.display_chapter_number : modelData.reading_position
                                            epubPosInput.text = String(chTarget)
                                            if (detailPane.doc && typeof orion !== "undefined" && orion !== null)
                                                orion.setEpubReadingProgress(detailPane.doc.id, chTarget, "whole_document", -1, -1)
                                        }
                                    }

                                    Text {
                                        anchors.fill: parent
                                        anchors.leftMargin: 6
                                        anchors.rightMargin: 6
                                        verticalAlignment: Text.AlignVCenter
                                        text: (modelData.display_chapter_number > 0 ? ("Ch. " + modelData.display_chapter_number + " — ") : "") + (modelData.chapter_title || "")
                                        font.pixelSize: 10
                                        color: theme.textPrimary
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                }
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: "Source Path:\n" + (detailPane.doc ? (detailPane.doc.source_path || "") : "")
                        font.pixelSize: theme.fontXs
                        color: theme.textSecondary
                        wrapMode: Text.WrapAnywhere
                        textFormat: Text.PlainText
                    }

                    Text {
                        visible: Boolean(detailPane.doc && detailPane.doc.error_message)
                        Layout.fillWidth: true
                        text: "Error: " + (detailPane.doc ? detailPane.doc.error_message : "")
                        font.pixelSize: theme.fontXs
                        color: theme.danger
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: theme.spaceSm

                        Button {
                            Layout.fillWidth: true
                            text: "Ask about this document"
                            implicitHeight: 34
                            onClicked: {
                                const d = detailPane.doc
                                if (d) root.startAskKnowledge("", [d.id])
                            }
                            background: Rectangle { radius: theme.radiusSm; color: theme.accent }
                            contentItem: Text {
                                text: parent.text; font.pixelSize: theme.fontSm; font.bold: true; color: theme.bgSurface
                                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                            }
                        }

                        Button {
                            Layout.fillWidth: true
                            text: "Search inside this document"
                            implicitHeight: 32
                            onClicked: {
                                const d = detailPane.doc
                                if (d) {
                                    knowledgePage.directSearchScopeDocId = d.id
                                    knowledgeDirectSearchInput.forceActiveFocus()
                                }
                            }
                            background: Rectangle { radius: theme.radiusSm; color: theme.bgSurface; border.width: 1; border.color: theme.borderStrong }
                            contentItem: Text {
                                text: parent.text; font.pixelSize: theme.fontXs; color: theme.textPrimary
                                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: theme.spaceSm

                            Button {
                                Layout.fillWidth: true
                                text: "Re-index"
                                enabled: !root.knowledgeBusy
                                implicitHeight: 30
                                onClicked: {
                                    const d = detailPane.doc
                                    if (d && typeof orion !== "undefined" && orion !== null)
                                        orion.reindexKnowledgeSource(d.id)
                                }
                                background: Rectangle { radius: theme.radiusSm; color: theme.bgSurface; border.width: 1; border.color: theme.borderStrong }
                                contentItem: Text {
                                    text: parent.text; font.pixelSize: theme.fontXs; color: theme.textPrimary
                                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                                }
                            }

                            Button {
                                Layout.fillWidth: true
                                text: "Remove"
                                enabled: !root.knowledgeBusy
                                implicitHeight: 30
                                onClicked: {
                                    const d = detailPane.doc
                                    if (d && typeof orion !== "undefined" && orion !== null)
                                        orion.removeKnowledgeSource(d.id)
                                }
                                background: Rectangle { radius: theme.radiusSm; color: theme.dangerBg; border.width: 1; border.color: theme.danger }
                                contentItem: Text {
                                    text: parent.text; font.pixelSize: theme.fontXs; font.bold: true; color: theme.danger
                                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                                }
                            }
                        }
                    }

                    Item { Layout.fillHeight: true }
                }
                }

                Text {
                    visible: parent.doc === null
                    anchors.centerIn: parent
                    text: "Select a document to inspect details."
                    font.pixelSize: theme.fontSm
                    color: theme.textMuted
                    textFormat: Text.PlainText
                }
            }
        }
    }
}
