import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Rectangle {
    id: homePage
    required property var root

    color: Theme.window

    readonly property var dash: root.homeDashboard || ({})
    readonly property var counts: dash.counts || ({ due_today: 0, overdue: 0, unscheduled: 0, completed_today: 0, upcoming: 0 })
    readonly property var charts: dash.charts || ({ day_labels: [], weekly_tasks_done: [], daily_activity: [], status_distribution: {}, date_range: "" })

    onVisibleChanged: {
        if (visible && typeof orion !== "undefined" && orion !== null)
            orion.loadHomeDashboard()
    }

    Connections {
        target: orion
        function onProductivityDataLoaded() {
            if (homePage.visible && typeof orion !== "undefined" && orion !== null)
                orion.loadHomeDashboard()
        }
    }

    ScrollView {
        anchors.fill: parent
        anchors.margins: Theme.spaceXl
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: Math.min(parent.width, 1040)
            spacing: Theme.spaceLg

            // Top Greeting & Quick Actions
            OrionCard {
                Layout.fillWidth: true
                implicitHeight: heroCol.implicitHeight + Theme.spaceLg * 2

                ColumnLayout {
                    id: heroCol
                    anchors.fill: parent
                    anchors.margins: Theme.spaceLg
                    spacing: Theme.spaceMd

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spaceMd

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Text {
                                text: homePage.dash.greeting || "Welcome to ORION"
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontPageTitle
                                font.weight: Font.Medium
                                color: Theme.text
                                textFormat: Text.PlainText
                            }
                            Text {
                                text: (homePage.dash.date_display || "") + (homePage.dash.summary ? (" · " + homePage.dash.summary) : "")
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSecondary
                                color: Theme.textSecondary
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                                textFormat: Text.PlainText
                            }
                        }

                        OrionButton {
                            objectName: "homeQuickChatButton"
                            text: "Quick Chat"
                            variant: "primary"
                            onClicked: root.activePage = "chat"
                        }
                    }

                    // Quick Add Task inline bar
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spaceSm
                        OrionTextField {
                            id: quickTaskInput
                            Layout.fillWidth: true
                            placeholderText: "Quick add task for today..."
                        }
                        OrionButton {
                            text: "Add Task"
                            variant: "secondary"
                            compact: true
                            enabled: quickTaskInput.text.trim().length > 0
                            onClicked: {
                                if (typeof orion !== "undefined" && orion !== null) {
                                    orion.createTask(quickTaskInput.text.trim(), "", "medium", "today")
                                    quickTaskInput.text = ""
                                }
                            }
                        }
                    }
                }
            }

            // Today Overview + Habits Row
            GridLayout {
                Layout.fillWidth: true
                columns: homePage.width < 920 ? 1 : 2
                columnSpacing: Theme.spaceLg
                rowSpacing: Theme.spaceLg

                OrionCard {
                    Layout.fillWidth: true
                    implicitHeight: ovCol.implicitHeight + Theme.spaceLg * 2
                    ColumnLayout {
                        id: ovCol
                        anchors.fill: parent
                        anchors.margins: Theme.spaceLg
                        spacing: Theme.spaceSm

                        OrionSectionHeader { title: "Today Overview"; count: homePage.counts.due_today + homePage.counts.overdue }

                        Text {
                            text: "Due today: " + homePage.counts.due_today + " · Overdue: " + homePage.counts.overdue + " · Unscheduled: " + homePage.counts.unscheduled + " · Completed: " + homePage.counts.completed_today
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSecondary
                            color: Theme.textSecondary
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                            textFormat: Text.PlainText
                        }

                        Text {
                            visible: (homePage.dash.due_today_items || []).length === 0 && (homePage.dash.overdue_items || []).length === 0
                            text: "No tasks due today."
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontBody
                            color: Theme.textSecondary
                            textFormat: Text.PlainText
                        }

                        Repeater {
                            model: homePage.dash.overdue_items || []
                            delegate: Text {
                                Layout.fillWidth: true
                                text: "Overdue · " + modelData.title + " (" + modelData.priority.toUpperCase() + ")"
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontBody
                                color: Theme.danger
                                elide: Text.ElideRight
                                textFormat: Text.PlainText
                            }
                        }

                        Repeater {
                            model: homePage.dash.due_today_items || []
                            delegate: Text {
                                Layout.fillWidth: true
                                text: "Today · " + modelData.title + " (" + modelData.priority.toUpperCase() + ")"
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontBody
                                color: Theme.text
                                elide: Text.ElideRight
                                textFormat: Text.PlainText
                            }
                        }
                    }
                }

                OrionCard {
                    Layout.fillWidth: true
                    implicitHeight: habCol.implicitHeight + Theme.spaceLg * 2
                    ColumnLayout {
                        id: habCol
                        anchors.fill: parent
                        anchors.margins: Theme.spaceLg
                        spacing: Theme.spaceSm

                        OrionSectionHeader { title: "Habits & Check-in"; count: (homePage.dash.habits || []).length }

                        Text {
                            visible: (homePage.dash.habits || []).length === 0
                            text: "No habits created yet."
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSecondary
                            color: Theme.textSecondary
                            textFormat: Text.PlainText
                        }

                        Repeater {
                            model: homePage.dash.habits || []
                            delegate: RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.spaceSm
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.name + " · 7d: " + modelData.rate_7d + "% · 30d: " + modelData.rate_30d + "%"
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fontSecondary
                                    color: Theme.text
                                    elide: Text.ElideRight
                                    textFormat: Text.PlainText
                                }
                                OrionButton {
                                    visible: !modelData.completed_today
                                    text: "Check in"
                                    compact: true
                                    variant: "primary"
                                    onClicked: orion.completeHabit(modelData.id)
                                }
                                Text {
                                    visible: modelData.completed_today
                                    text: "Done"
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fontCaption
                                    font.weight: Font.Medium
                                    color: Theme.accent
                                    textFormat: Text.PlainText
                                }
                            }
                        }
                    }
                }
            }

            // Real SQLite Weekly Activity & Task Completion Chart
            OrionCard {
                Layout.fillWidth: true
                implicitHeight: chartCol.implicitHeight + Theme.spaceLg * 2

                ColumnLayout {
                    id: chartCol
                    anchors.fill: parent
                    anchors.margins: Theme.spaceLg
                    spacing: Theme.spaceSm

                    OrionSectionHeader {
                        title: "7-Day Productivity"
                        subtitle: homePage.charts.date_range || ""
                    }

                    Canvas {
                        id: homeBarCanvas
                        Layout.fillWidth: true
                        implicitHeight: 128
                        antialiasing: true

                        Connections {
                            target: homePage
                            function onDashChanged() { homeBarCanvas.requestPaint() }
                        }
                        Connections {
                            target: Theme
                            function onIsDarkChanged() { homeBarCanvas.requestPaint() }
                        }

                        onPaint: {
                            const ctx = getContext("2d")
                            ctx.clearRect(0, 0, width, height)
                            const labels = homePage.charts.day_labels || []
                            const doneVals = homePage.charts.weekly_tasks_done || []
                            const actVals = homePage.charts.daily_activity || []
                            if (labels.length === 0) return

                            let maxV = 1
                            for (let i = 0; i < labels.length; i++) {
                                maxV = Math.max(maxV, doneVals[i] || 0, actVals[i] || 0)
                            }
                            const padL = 16; const padR = 16; const padT = 12; const padB = 24
                            const plotW = Math.max(40, width - padL - padR)
                            const plotH = Math.max(20, height - padT - padB)
                            const slotW = plotW / labels.length
                            const barW = Math.min(18, slotW * 0.28)

                            ctx.font = Theme.fontCaption + "px " + Theme.fontFamily
                            for (let i = 0; i < labels.length; i++) {
                                const cx = padL + i * slotW + slotW / 2
                                const aH = ((actVals[i] || 0) / maxV) * plotH
                                const dH = ((doneVals[i] || 0) / maxV) * plotH

                                ctx.fillStyle = Theme.border.toString()
                                ctx.fillRect(cx - barW - 2, padT + plotH - aH, barW, aH)

                                ctx.fillStyle = Theme.accent.toString()
                                ctx.fillRect(cx + 2, padT + plotH - dH, barW, dH)

                                ctx.fillStyle = Theme.textSecondary.toString()
                                ctx.fillText(labels[i], cx - 10, height - 6)
                            }
                        }
                    }

                    Text {
                        text: "Accent: Tasks completed · Stone: Total activity events"
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontCaption
                        color: Theme.textSecondary
                        textFormat: Text.PlainText
                    }
                }
            }

            // Recent Activity & Recent Conversations/Projects
            GridLayout {
                Layout.fillWidth: true
                columns: homePage.width < 920 ? 1 : 2
                columnSpacing: Theme.spaceLg
                rowSpacing: Theme.spaceLg

                OrionCard {
                    Layout.fillWidth: true
                    implicitHeight: actCol.implicitHeight + Theme.spaceLg * 2
                    ColumnLayout {
                        id: actCol
                        anchors.fill: parent
                        anchors.margins: Theme.spaceLg
                        spacing: Theme.spaceXs
                        OrionSectionHeader { title: "Recent Activity"; count: (homePage.dash.recent_activity || []).length }
                        Text {
                            visible: (homePage.dash.recent_activity || []).length === 0
                            text: "No recorded activity yet."
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSecondary
                            color: Theme.textSecondary
                            textFormat: Text.PlainText
                        }
                        Repeater {
                            model: homePage.dash.recent_activity || []
                            delegate: Text {
                                Layout.fillWidth: true
                                text: modelData.event_type.replace(/_/g, " ") + " · " + modelData.time_display
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSecondary
                                color: Theme.textSecondary
                                elide: Text.ElideRight
                                textFormat: Text.PlainText
                            }
                        }
                    }
                }

                OrionCard {
                    Layout.fillWidth: true
                    implicitHeight: recCol.implicitHeight + Theme.spaceLg * 2
                    ColumnLayout {
                        id: recCol
                        anchors.fill: parent
                        anchors.margins: Theme.spaceLg
                        spacing: Theme.spaceXs
                        OrionSectionHeader { title: "Recent Chats & Projects" }
                        Repeater {
                            model: homePage.dash.projects || []
                            delegate: Text {
                                Layout.fillWidth: true
                                text: "Project · " + modelData.title
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSecondary
                                color: Theme.accent
                                elide: Text.ElideRight
                                textFormat: Text.PlainText
                            }
                        }
                        Repeater {
                            model: homePage.dash.recent_conversations || []
                            delegate: OrionListRow {
                                Layout.fillWidth: true
                                title: modelData.title
                                onClicked: {
                                    root.activePage = "chat"
                                    if (typeof orion !== "undefined" && orion !== null)
                                        orion.selectConversation(modelData.id)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
