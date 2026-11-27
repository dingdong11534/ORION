import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Rectangle {
    id: prodPage
    required property var root

    property string activeTab: "today" // "today" | "tasks" | "habits"
    property string selectedHabitId: ""
    property int selectedHabitIndex: 0
    property var habitStatsData: null

    readonly property int currentYear: new Date().getFullYear()
    readonly property int currentMonth: new Date().getMonth() + 1

    color: Theme.window

    function ensureHabitSelected() {
        if (!root.prodHabits || root.prodHabits.length === 0) {
            selectedHabitId = ""
            habitStatsData = null
            return
        }
        let found = false
        for (let i = 0; i < root.prodHabits.length; i++) {
            if (root.prodHabits[i].id === selectedHabitId) {
                selectedHabitIndex = i
                found = true
                break
            }
        }
        if (!found) {
            selectedHabitId = root.prodHabits[0].id
            selectedHabitIndex = 0
        }
        if (typeof orion !== "undefined" && orion !== null && selectedHabitId.length > 0) {
            orion.loadHabitStats(selectedHabitId, currentYear, currentMonth)
        }
    }

    Connections {
        target: orion
        function onHabitStatsLoaded(payload) {
            if (payload && payload.habit_id === prodPage.selectedHabitId) {
                prodPage.habitStatsData = payload
            }
        }
        function onProductivityDataLoaded() {
            Qt.callLater(prodPage.ensureHabitSelected)
        }
    }

    Component.onCompleted: ensureHabitSelected()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spaceXl
        spacing: Theme.spaceLg

        // Header + Sub-tabs
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceMd

            Text {
                text: prodPage.activeTab === "today" ? "Today" : (prodPage.activeTab === "tasks" ? "Tasks" : "Habits")
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontPageTitle
                font.weight: Font.Medium
                color: Theme.text
                textFormat: Text.PlainText
            }

            Item { Layout.fillWidth: true }

            RowLayout {
                spacing: Theme.spaceXs
                OrionButton {
                    objectName: "prodTabToday"
                    text: "Today"
                    compact: true
                    variant: prodPage.activeTab === "today" ? "primary" : "secondary"
                    onClicked: prodPage.activeTab = "today"
                }
                OrionButton {
                    objectName: "prodTabTasks"
                    text: "Tasks"
                    compact: true
                    variant: prodPage.activeTab === "tasks" ? "primary" : "secondary"
                    onClicked: prodPage.activeTab = "tasks"
                }
                OrionButton {
                    objectName: "prodTabHabits"
                    text: "Habits"
                    compact: true
                    variant: prodPage.activeTab === "habits" ? "primary" : "secondary"
                    onClicked: {
                        prodPage.activeTab = "habits"
                        prodPage.ensureHabitSelected()
                    }
                }
            }
        }

        OrionBanner {
            Layout.fillWidth: true
            message: root.productivityError
            severity: "danger"
            actionLabel: "Dismiss"
            onActionClicked: root.productivityError = ""
        }

        // TAB 1: TODAY
        ScrollView {
            visible: prodPage.activeTab === "today"
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true

            ColumnLayout {
                width: Math.min(parent.width, 960)
                spacing: Theme.spaceLg

                OrionEmptyState {
                    Layout.fillWidth: true
                    visible: root.prodAllTasks.length === 0 && root.prodHabits.length === 0
                    title: "Nothing scheduled yet"
                    subtitle: "Create your first task or habit to see your daily plan here."
                    actionLabel: "Add a task"
                    onActionClicked: prodPage.activeTab = "tasks"
                }

                // Overdue
                OrionCard {
                    Layout.fillWidth: true
                    visible: root.prodOverdueTasks.length > 0
                    implicitHeight: overdueCol.implicitHeight + Theme.spaceLg * 2
                    ColumnLayout {
                        id: overdueCol
                        anchors.fill: parent
                        anchors.margins: Theme.spaceLg
                        spacing: Theme.spaceSm
                        OrionSectionHeader { title: "Overdue"; count: root.prodOverdueTasks.length }
                        Repeater {
                            model: root.prodOverdueTasks
                            delegate: RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.spaceMd
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.title + " · " + modelData.priority.toUpperCase() + (modelData.due_display ? (" · " + modelData.due_display) : "")
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fontBody
                                    color: Theme.danger
                                    wrapMode: Text.Wrap
                                    textFormat: Text.PlainText
                                }
                                OrionButton {
                                    text: "Done"
                                    compact: true
                                    variant: "secondary"
                                    onClicked: orion.completeTask(modelData.id)
                                }
                            }
                        }
                    }
                }

                // Due Today
                OrionCard {
                    Layout.fillWidth: true
                    visible: root.prodAllTasks.length > 0 || root.prodHabits.length > 0
                    implicitHeight: dueTodayCol.implicitHeight + Theme.spaceLg * 2
                    ColumnLayout {
                        id: dueTodayCol
                        anchors.fill: parent
                        anchors.margins: Theme.spaceLg
                        spacing: Theme.spaceSm
                        OrionSectionHeader { title: "Due today"; count: root.prodTodayTasks.length }
                        Text {
                            visible: root.prodTodayTasks.length === 0
                            text: "No tasks due today."
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSecondary
                            color: Theme.textSecondary
                            textFormat: Text.PlainText
                        }
                        Repeater {
                            model: root.prodTodayTasks
                            delegate: RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.spaceMd
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.title + " · " + modelData.priority.toUpperCase() + (modelData.due_display ? (" · " + modelData.due_display) : "")
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fontBody
                                    color: Theme.text
                                    wrapMode: Text.Wrap
                                    textFormat: Text.PlainText
                                }
                                OrionButton {
                                    text: "Done"
                                    compact: true
                                    variant: "primary"
                                    onClicked: orion.completeTask(modelData.id)
                                }
                            }
                        }
                    }
                }

                // Unscheduled
                OrionCard {
                    Layout.fillWidth: true
                    visible: root.prodUnscheduledTasks.length > 0
                    implicitHeight: unschedCol.implicitHeight + Theme.spaceLg * 2
                    ColumnLayout {
                        id: unschedCol
                        anchors.fill: parent
                        anchors.margins: Theme.spaceLg
                        spacing: Theme.spaceSm
                        OrionSectionHeader { title: "Unscheduled"; count: root.prodUnscheduledTasks.length }
                        Repeater {
                            model: root.prodUnscheduledTasks
                            delegate: RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.spaceMd
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.title + " · " + modelData.priority.toUpperCase()
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fontBody
                                    color: Theme.text
                                    wrapMode: Text.Wrap
                                    textFormat: Text.PlainText
                                }
                                OrionButton {
                                    text: "Done"
                                    compact: true
                                    variant: "secondary"
                                    onClicked: orion.completeTask(modelData.id)
                                }
                            }
                        }
                    }
                }

                // Today's Habits
                OrionCard {
                    Layout.fillWidth: true
                    visible: root.prodHabits.length > 0
                    implicitHeight: todayHabitsCol.implicitHeight + Theme.spaceLg * 2
                    ColumnLayout {
                        id: todayHabitsCol
                        anchors.fill: parent
                        anchors.margins: Theme.spaceLg
                        spacing: Theme.spaceMd
                        OrionSectionHeader { title: "Habits today"; count: root.prodHabits.length }
                        Repeater {
                            model: root.prodHabits
                            delegate: RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.spaceMd
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.name + " · Streak " + modelData.current_streak
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fontBody
                                    color: Theme.text
                                    textFormat: Text.PlainText
                                }
                                Text {
                                    visible: modelData.completed_today
                                    text: "Done"
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fontSecondary
                                    font.weight: Font.Medium
                                    color: Theme.accent
                                    textFormat: Text.PlainText
                                }
                                OrionButton {
                                    visible: !modelData.completed_today
                                    text: "Check in"
                                    compact: true
                                    variant: "primary"
                                    onClicked: orion.completeHabit(modelData.id)
                                }
                            }
                        }
                    }
                }
            }
        }

        // TAB 2: TASKS
        ColumnLayout {
            visible: prodPage.activeTab === "tasks"
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.spaceMd

            OrionCard {
                Layout.fillWidth: true
                implicitHeight: newTaskRow.implicitHeight + Theme.spaceLg * 2
                RowLayout {
                    id: newTaskRow
                    anchors.fill: parent
                    anchors.margins: Theme.spaceLg
                    spacing: Theme.spaceSm
                    OrionTextField {
                        id: newTaskTitle
                        Layout.fillWidth: true
                        placeholderText: "New task title..."
                    }
                    OrionTextField {
                        id: newTaskDue
                        Layout.preferredWidth: 160
                        placeholderText: "Due (e.g. today 4pm)"
                    }
                    ComboBox {
                        id: newTaskPriority
                        model: ["medium", "high", "critical", "low"]
                        implicitHeight: Theme.controlHeight
                    }
                    OrionButton {
                        text: "Add Task"
                        variant: "primary"
                        enabled: newTaskTitle.text.trim().length > 0
                        onClicked: {
                            orion.createTask(newTaskTitle.text.trim(), "", newTaskPriority.currentText, newTaskDue.text.trim())
                            newTaskTitle.text = ""
                            newTaskDue.text = ""
                        }
                    }
                }
            }

            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: Theme.spaceSm
                model: root.prodAllTasks
                delegate: OrionCard {
                    width: ListView.view.width
                    implicitHeight: taskRow.implicitHeight + Theme.spaceMd * 2
                    RowLayout {
                        id: taskRow
                        anchors.fill: parent
                        anchors.margins: Theme.spaceMd
                        spacing: Theme.spaceMd
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Text {
                                Layout.fillWidth: true
                                text: modelData.title
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontBody
                                font.weight: Font.Medium
                                color: Theme.text
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                            Text {
                                text: "Priority: " + modelData.priority.toUpperCase() + " · Status: " + modelData.status + (modelData.due_display ? (" · Due: " + modelData.due_display) : "")
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontCaption
                                color: Theme.textSecondary
                                textFormat: Text.PlainText
                            }
                        }
                        OrionButton {
                            text: modelData.status === "done" ? "Reopen" : "Complete"
                            compact: true
                            variant: modelData.status === "done" ? "secondary" : "primary"
                            onClicked: modelData.status === "done" ? orion.reopenTask(modelData.id) : orion.completeTask(modelData.id)
                        }
                        OrionButton {
                            text: "Archive"
                            compact: true
                            variant: "danger"
                            onClicked: orion.archiveTask(modelData.id)
                        }
                    }
                }
            }
        }

        // TAB 3: HABITS + 4 CUSTOM VISUALS
        ColumnLayout {
            visible: prodPage.activeTab === "habits"
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.spaceMd

            OrionEmptyState {
                Layout.fillWidth: true
                visible: root.prodHabits.length === 0
                title: "No habits yet"
                subtitle: "Create your first habit to track streaks, monthly consistency, and your year ring."
                actionLabel: "Create your first habit"
                onActionClicked: newHabitName.forceActiveFocus()
            }

            OrionCard {
                Layout.fillWidth: true
                implicitHeight: newHabitRow.implicitHeight + Theme.spaceMd * 2
                RowLayout {
                    id: newHabitRow
                    anchors.fill: parent
                    anchors.margins: Theme.spaceMd
                    spacing: Theme.spaceSm
                    OrionTextField {
                        id: newHabitName
                        Layout.fillWidth: true
                        placeholderText: "New habit name (e.g. Reading)..."
                    }
                    ComboBox {
                        id: newHabitFreq
                        model: ["daily", "weekly", "monthly"]
                        implicitHeight: Theme.controlHeight
                    }
                    OrionButton {
                        text: "Add Habit"
                        variant: "primary"
                        enabled: newHabitName.text.trim().length > 0
                        onClicked: {
                            orion.createHabit(newHabitName.text.trim(), "", newHabitFreq.currentText, 1)
                            newHabitName.text = ""
                        }
                    }
                }
            }

            GridLayout {
                visible: root.prodHabits.length > 0
                Layout.fillWidth: true
                Layout.fillHeight: true
                columns: prodPage.width < 1000 ? 1 : 2
                columnSpacing: Theme.spaceLg
                rowSpacing: Theme.spaceLg

                // Left column: Habit list + Year ring tracker
                ScrollView {
                    Layout.fillHeight: true
                    Layout.preferredWidth: prodPage.width < 1000 ? prodPage.width - Theme.spaceXl * 2 : 300
                    contentWidth: availableWidth
                    clip: true

                    ColumnLayout {
                        width: parent.width
                        spacing: Theme.spaceMd

                        OrionCard {
                            Layout.fillWidth: true
                            implicitHeight: hListCol.implicitHeight + Theme.spaceMd * 2
                            ColumnLayout {
                                id: hListCol
                                anchors.fill: parent
                                anchors.margins: Theme.spaceMd
                                spacing: Theme.spaceXs
                                Repeater {
                                    model: root.prodHabits
                                    delegate: OrionListRow {
                                        Layout.fillWidth: true
                                        title: modelData.name
                                        subtitle: (modelData.completed_today ? "Done today" : "Pending") + " · Streak " + modelData.current_streak
                                        selected: modelData.id === prodPage.selectedHabitId
                                        onClicked: {
                                            prodPage.selectedHabitId = modelData.id
                                            prodPage.selectedHabitIndex = index
                                            orion.loadHabitStats(modelData.id, prodPage.currentYear, prodPage.currentMonth)
                                        }
                                    }
                                }
                            }
                        }

                        // Year Ring Tracker (260x260)
                        OrionCard {
                            Layout.fillWidth: true
                            implicitHeight: ringCol.implicitHeight + Theme.spaceLg * 2
                            ColumnLayout {
                                id: ringCol
                                anchors.fill: parent
                                anchors.margins: Theme.spaceLg
                                spacing: Theme.spaceSm

                                OrionSectionHeader { title: "Year at a glance" }

                                HabitYearRingTracker {
                                    Layout.alignment: Qt.AlignHCenter
                                    year: prodPage.currentYear
                                    completedDates: prodPage.habitStatsData ? prodPage.habitStatsData.year_completed_dates : []
                                    habitHue: Theme.habitHue(prodPage.selectedHabitIndex)
                                }

                                Text {
                                    Layout.alignment: Qt.AlignHCenter
                                    text: "Inner ring January, outer ring December"
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fontCaption
                                    color: Theme.textSecondary
                                    textFormat: Text.PlainText
                                }
                            }
                        }
                    }
                }

                // Right column: Stats + Donut + Line Chart + Month Grid
                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    contentWidth: availableWidth
                    clip: true

                    ColumnLayout {
                        width: parent.width
                        spacing: Theme.spaceLg

                        // Statistics row + Completion Donut
                        OrionCard {
                            Layout.fillWidth: true
                            implicitHeight: statsRow.implicitHeight + Theme.spaceLg * 2
                            RowLayout {
                                id: statsRow
                                anchors.fill: parent
                                anchors.margins: Theme.spaceLg
                                spacing: Theme.spaceLg

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.spaceXs
                                    readonly property var ms: prodPage.habitStatsData ? prodPage.habitStatsData.month_stats : null
                                    Text {
                                        text: "Current streak: " + (parent.ms ? parent.ms.current_streak : 0) + " days"
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.fontBody
                                        font.weight: Font.Medium
                                        color: Theme.text
                                        textFormat: Text.PlainText
                                    }
                                    Text {
                                        text: "Longest streak: " + (parent.ms ? parent.ms.longest_streak : 0) + " days"
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.fontSecondary
                                        color: Theme.textSecondary
                                        textFormat: Text.PlainText
                                    }
                                    Text {
                                        text: parent.ms ? (parent.ms.completed_dates.length + " of " + parent.ms.elapsed_days + " days this month") : "0 of 0 days this month"
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.fontSecondary
                                        color: Theme.textSecondary
                                        textFormat: Text.PlainText
                                    }
                                    OrionButton {
                                        text: "Check in today"
                                        compact: true
                                        variant: "primary"
                                        enabled: prodPage.selectedHabitId.length > 0
                                        onClicked: orion.completeHabit(prodPage.selectedHabitId)
                                    }
                                }

                                HabitCompletionDonut {
                                    percent: (prodPage.habitStatsData && prodPage.habitStatsData.month_stats)
                                        ? prodPage.habitStatsData.month_stats.completion_percent
                                        : 0
                                }
                            }
                        }

                        // Daily completion line chart
                        OrionCard {
                            Layout.fillWidth: true
                            implicitHeight: chartCol.implicitHeight + Theme.spaceLg * 2
                            ColumnLayout {
                                id: chartCol
                                anchors.fill: parent
                                anchors.margins: Theme.spaceLg
                                spacing: Theme.spaceSm
                                OrionSectionHeader { title: "Daily completion" }
                                HabitDailyLineChart {
                                    Layout.fillWidth: true
                                    dailySeries: prodPage.habitStatsData ? prodPage.habitStatsData.daily_series : []
                                    hasData: !!(prodPage.habitStatsData && prodPage.habitStatsData.year_completed_dates && prodPage.habitStatsData.year_completed_dates.length > 0)
                                }
                            }
                        }

                        // Month grid by week
                        OrionCard {
                            Layout.fillWidth: true
                            implicitHeight: mGridCol.implicitHeight + Theme.spaceLg * 2
                            ColumnLayout {
                                id: mGridCol
                                anchors.fill: parent
                                anchors.margins: Theme.spaceLg
                                spacing: Theme.spaceSm
                                OrionSectionHeader { title: "This month by week" }
                                HabitMonthGrid {
                                    Layout.fillWidth: true
                                    year: prodPage.currentYear
                                    month: prodPage.currentMonth
                                    daysInMonth: (prodPage.habitStatsData && prodPage.habitStatsData.month_stats) ? prodPage.habitStatsData.month_stats.days_in_month : 30
                                    elapsedDays: (prodPage.habitStatsData && prodPage.habitStatsData.month_stats) ? prodPage.habitStatsData.month_stats.elapsed_days : 1
                                    completedDates: (prodPage.habitStatsData && prodPage.habitStatsData.month_stats) ? prodPage.habitStatsData.month_stats.completed_dates : []
                                    habitHue: Theme.habitHue(prodPage.selectedHabitIndex)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
