import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: prodPage
    required property var root
    required property var theme

    property string activeSection: "today"  // "today" | "tasks" | "habits"
    property var editingTask: null
    property var editingHabit: null

    function refreshData() {
        if (typeof orion !== "undefined" && orion !== null && root.productivityAvailable) {
            orion.loadProductivityData(
                taskStatusFilter.currentIndex === 0 ? "" : taskStatusFilter.currentText,
                taskPriorityFilter.currentIndex === 0 ? "" : taskPriorityFilter.currentText,
                taskSearchInput.text,
                taskSortCombo.currentValue || "priority_due"
            )
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: theme.spaceLg
        spacing: theme.spaceMd

        // Header & Section Tabs
        RowLayout {
            Layout.fillWidth: true
            spacing: theme.spaceSm

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    text: "Personal Productivity"
                    font.pixelSize: theme.fontLg
                    font.bold: true
                    color: theme.textPrimary
                    textFormat: Text.PlainText
                }
                Text {
                    text: "Manage tasks, track habits, or use natural language directly in Chat."
                    font.pixelSize: theme.fontXs
                    color: theme.textMuted
                    textFormat: Text.PlainText
                }
            }

            RowLayout {
                spacing: 4
                Repeater {
                    model: [
                        { key: "today", label: "Today", obj: "productivityTabToday" },
                        { key: "tasks", label: "Tasks", obj: "productivityTabTasks" },
                        { key: "habits", label: "Habits", obj: "productivityTabHabits" }
                    ]
                    delegate: Button {
                        objectName: modelData.obj
                        text: modelData.label
                        implicitHeight: 30
                        implicitWidth: implicitContentWidth + 20
                        onClicked: {
                            prodPage.activeSection = modelData.key
                            prodPage.refreshData()
                        }
                        background: Rectangle {
                            radius: theme.radiusSm
                            color: prodPage.activeSection === modelData.key ? theme.accent : theme.bgSurface
                            border.width: 1
                            border.color: prodPage.activeSection === modelData.key ? theme.accent : theme.borderStrong
                        }
                        contentItem: Text {
                            text: parent.text
                            font.pixelSize: theme.fontXs
                            font.bold: true
                            color: prodPage.activeSection === modelData.key ? theme.bgSurface : theme.textPrimary
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            textFormat: Text.PlainText
                        }
                    }
                }
            }
        }

        // Error banner
        Rectangle {
            visible: root.productivityError !== ""
            Layout.fillWidth: true
            implicitHeight: errTxt.implicitHeight + 12
            radius: theme.radiusSm
            color: theme.dangerBg
            border.width: 1
            border.color: theme.danger
            RowLayout {
                anchors.fill: parent
                anchors.margins: 6
                Text {
                    id: errTxt
                    Layout.fillWidth: true
                    text: root.productivityError
                    font.pixelSize: theme.fontXs
                    color: theme.danger
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                }
                Button {
                    text: "Dismiss"
                    implicitHeight: 22
                    onClicked: root.productivityError = ""
                }
            }
        }

        // ============================================================
        // SECTION A: TODAY
        // ============================================================
        ScrollView {
            visible: prodPage.activeSection === "today"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            ColumnLayout {
                width: prodPage.width - theme.spaceLg * 2
                spacing: theme.spaceMd

                // Quick-add Task Bar
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: quickAddCol.implicitHeight + 16
                    radius: theme.radiusMd
                    color: theme.bgSurface
                    border.width: 1
                    border.color: theme.border

                    ColumnLayout {
                        id: quickAddCol
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 6

                        Text {
                            text: "Quick Add Task"
                            font.pixelSize: theme.fontXs
                            font.bold: true
                            color: theme.textSecondary
                            textFormat: Text.PlainText
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            TextField {
                                id: quickTaskInput
                                objectName: "quickTaskInput"
                                Layout.fillWidth: true
                                implicitHeight: 32
                                placeholderText: "Task title (e.g., Finish COA Unit 4)..."
                                font.pixelSize: theme.fontSm
                                color: theme.textPrimary
                                placeholderTextColor: theme.textMuted
                                background: Rectangle { radius: 6; color: theme.bgSurfaceAlt; border.width: 1; border.color: theme.borderStrong }
                            }

                            ComboBox {
                                id: quickTaskPriority
                                objectName: "quickTaskPriority"
                                Layout.preferredWidth: 105
                                implicitHeight: 32
                                model: ["medium", "high", "critical", "low"]
                            }

                            TextField {
                                id: quickTaskDueInput
                                objectName: "quickTaskDueInput"
                                Layout.preferredWidth: 130
                                implicitHeight: 32
                                placeholderText: "Due (e.g., today, Friday)"
                                font.pixelSize: theme.fontXs
                                color: theme.textPrimary
                                placeholderTextColor: theme.textMuted
                                background: Rectangle { radius: 6; color: theme.bgSurfaceAlt; border.width: 1; border.color: theme.borderStrong }
                            }

                            Button {
                                objectName: "quickAddTaskButton"
                                text: "Add"
                                implicitHeight: 32
                                implicitWidth: 64
                                onClicked: {
                                    if (quickTaskInput.text.trim().length > 0 && typeof orion !== "undefined" && orion !== null) {
                                        orion.createTask(quickTaskInput.text.trim(), "", quickTaskPriority.currentText, quickTaskDueInput.text.trim())
                                        quickTaskInput.text = ""
                                        quickTaskDueInput.text = ""
                                    }
                                }
                                background: Rectangle { radius: 6; color: theme.accent }
                                contentItem: Text {
                                    text: parent.text; font.pixelSize: theme.fontXs; font.bold: true; color: theme.bgSurface
                                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                                }
                            }
                        }
                    }
                }

                // Today's Habit Check-ins
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: todayHabitsCol.implicitHeight + 18
                    radius: theme.radiusMd
                    color: theme.bgSurface
                    border.width: 1
                    border.color: theme.border

                    ColumnLayout {
                        id: todayHabitsCol
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 6

                        Text {
                            text: "Today's Habit Check-ins (" + root.prodHabits.length + ")"
                            font.pixelSize: theme.fontSm
                            font.bold: true
                            color: theme.textPrimary
                            textFormat: Text.PlainText
                        }

                        Text {
                            visible: root.prodHabits.length === 0
                            text: "No habits yet. Switch to Habits or tell Chat: \"Create a daily reading habit.\""
                            font.pixelSize: theme.fontXs
                            color: theme.textMuted
                            textFormat: Text.PlainText
                        }

                        Repeater {
                            model: root.prodHabits
                            delegate: Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 40
                                radius: 6
                                color: theme.bgSurfaceAlt
                                border.width: 1
                                border.color: modelData.completed_today ? theme.accent : theme.border

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 10
                                    anchors.rightMargin: 8
                                    spacing: 8

                                    Text {
                                        Layout.fillWidth: true
                                        text: modelData.name + " · Streak: " + modelData.current_streak + " · " + modelData.frequency
                                        font.pixelSize: theme.fontXs
                                        font.bold: true
                                        color: theme.textPrimary
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }

                                    Button {
                                        text: modelData.completed_today ? "Done Today ✓" : "Complete"
                                        enabled: !modelData.completed_today
                                        implicitHeight: 26
                                        implicitWidth: implicitContentWidth + 14
                                        onClicked: {
                                            if (typeof orion !== "undefined" && orion !== null)
                                                orion.completeHabit(modelData.id)
                                        }
                                        background: Rectangle {
                                            radius: 5
                                            color: modelData.completed_today ? theme.accentLight : theme.accent
                                        }
                                        contentItem: Text {
                                            text: parent.text
                                            font.pixelSize: 11
                                            font.bold: true
                                            color: modelData.completed_today ? theme.accent : theme.bgSurface
                                            horizontalAlignment: Text.AlignHCenter
                                            verticalAlignment: Text.AlignVCenter
                                            textFormat: Text.PlainText
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // Overdue Tasks
                Rectangle {
                    visible: root.prodOverdueTasks.length > 0
                    Layout.fillWidth: true
                    implicitHeight: overdueCol.implicitHeight + 18
                    radius: theme.radiusMd
                    color: theme.dangerBg
                    border.width: 1
                    border.color: theme.danger

                    ColumnLayout {
                        id: overdueCol
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 6

                        Text {
                            text: "Overdue Tasks (" + root.prodOverdueTasks.length + ")"
                            font.pixelSize: theme.fontSm
                            font.bold: true
                            color: theme.danger
                            textFormat: Text.PlainText
                        }

                        Repeater {
                            model: root.prodOverdueTasks
                            delegate: Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 38
                                radius: 6
                                color: theme.bgSurface
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 10
                                    anchors.rightMargin: 8
                                    Text {
                                        Layout.fillWidth: true
                                        text: "[" + modelData.priority.toUpperCase() + "] " + modelData.title + (modelData.due_display ? (" · Due " + modelData.due_display) : "")
                                        font.pixelSize: theme.fontXs
                                        color: theme.textPrimary
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                    Button {
                                        text: "Complete"
                                        implicitHeight: 24
                                        onClicked: orion.completeTask(modelData.id)
                                    }
                                }
                            }
                        }
                    }
                }

                // Today's Tasks
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: todayTasksCol.implicitHeight + 18
                    radius: theme.radiusMd
                    color: theme.bgSurface
                    border.width: 1
                    border.color: theme.border

                    ColumnLayout {
                        id: todayTasksCol
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 6

                        Text {
                            text: "Today's Tasks (" + root.prodTodayTasks.length + ")"
                            font.pixelSize: theme.fontSm
                            font.bold: true
                            color: theme.textPrimary
                            textFormat: Text.PlainText
                        }

                        Text {
                            visible: root.prodTodayTasks.length === 0
                            text: "No tasks due today."
                            font.pixelSize: theme.fontXs
                            color: theme.textMuted
                            textFormat: Text.PlainText
                        }

                        Repeater {
                            model: root.prodTodayTasks
                            delegate: Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 38
                                radius: 6
                                color: theme.bgSurfaceAlt
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 10
                                    anchors.rightMargin: 8
                                    Text {
                                        Layout.fillWidth: true
                                        text: "[" + modelData.priority.toUpperCase() + "] " + modelData.title + (modelData.due_display ? (" · " + modelData.due_display) : "")
                                        font.pixelSize: theme.fontXs
                                        color: theme.textPrimary
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                    Button {
                                        text: "Complete"
                                        implicitHeight: 24
                                        onClicked: orion.completeTask(modelData.id)
                                    }
                                }
                            }
                        }
                    }
                }

                // Upcoming Tasks
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: upcomingCol.implicitHeight + 18
                    radius: theme.radiusMd
                    color: theme.bgSurface
                    border.width: 1
                    border.color: theme.border

                    ColumnLayout {
                        id: upcomingCol
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 6

                        Text {
                            text: "Upcoming Tasks (" + root.prodUpcomingTasks.length + ")"
                            font.pixelSize: theme.fontSm
                            font.bold: true
                            color: theme.textPrimary
                            textFormat: Text.PlainText
                        }

                        Text {
                            visible: root.prodUpcomingTasks.length === 0
                            text: "No upcoming scheduled tasks."
                            font.pixelSize: theme.fontXs
                            color: theme.textMuted
                            textFormat: Text.PlainText
                        }

                        Repeater {
                            model: root.prodUpcomingTasks
                            delegate: Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 38
                                radius: 6
                                color: theme.bgSurfaceAlt
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 10
                                    anchors.rightMargin: 8
                                    Text {
                                        Layout.fillWidth: true
                                        text: "[" + modelData.priority.toUpperCase() + "] " + modelData.title + (modelData.due_display ? (" · Due " + modelData.due_display) : "")
                                        font.pixelSize: theme.fontXs
                                        color: theme.textPrimary
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                    Button {
                                        text: "Complete"
                                        implicitHeight: 24
                                        onClicked: orion.completeTask(modelData.id)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // ============================================================
        // SECTION B: TASKS
        // ============================================================
        ColumnLayout {
            visible: prodPage.activeSection === "tasks"
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: theme.spaceSm

            RowLayout {
                Layout.fillWidth: true
                spacing: 6

                TextField {
                    id: taskSearchInput
                    objectName: "taskSearchInput"
                    Layout.fillWidth: true
                    implicitHeight: 30
                    placeholderText: "Search tasks..."
                    font.pixelSize: theme.fontXs
                    color: theme.textPrimary
                    placeholderTextColor: theme.textMuted
                    background: Rectangle { radius: 6; color: theme.bgSurface; border.width: 1; border.color: theme.borderStrong }
                    onTextChanged: prodPage.refreshData()
                }

                ComboBox {
                    id: taskStatusFilter
                    objectName: "taskStatusFilter"
                    Layout.preferredWidth: 115
                    implicitHeight: 30
                    model: ["All Status", "todo", "in_progress", "done", "cancelled"]
                    onCurrentIndexChanged: prodPage.refreshData()
                }

                ComboBox {
                    id: taskPriorityFilter
                    objectName: "taskPriorityFilter"
                    Layout.preferredWidth: 115
                    implicitHeight: 30
                    model: ["All Priority", "critical", "high", "medium", "low"]
                    onCurrentIndexChanged: prodPage.refreshData()
                }

                ComboBox {
                    id: taskSortCombo
                    objectName: "taskSortCombo"
                    Layout.preferredWidth: 130
                    implicitHeight: 30
                    textRole: "label"
                    valueRole: "val"
                    model: [
                        { label: "Sort: Priority+Due", val: "priority_due" },
                        { label: "Sort: Deadline", val: "due_at" },
                        { label: "Sort: Priority", val: "priority" },
                        { label: "Sort: Updated", val: "updated_at" }
                    ]
                    onCurrentIndexChanged: prodPage.refreshData()
                }
            }

            ListView {
                objectName: "tasksListView"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 6
                model: root.prodAllTasks

                delegate: Rectangle {
                    width: ListView.view.width
                    implicitHeight: taskRowCol.implicitHeight + 16
                    radius: theme.radiusSm
                    color: theme.bgSurface
                    border.width: 1
                    border.color: theme.border

                    RowLayout {
                        id: taskRowCol
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 8

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Text {
                                Layout.fillWidth: true
                                text: modelData.title
                                font.pixelSize: theme.fontSm
                                font.bold: true
                                font.strikeout: modelData.status === "done"
                                color: modelData.status === "done" ? theme.textMuted : theme.textPrimary
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                            Text {
                                Layout.fillWidth: true
                                text: "Status: " + modelData.status + " · Priority: " + modelData.priority.toUpperCase() + (modelData.due_display ? (" · Due: " + modelData.due_display) : "")
                                font.pixelSize: 11
                                color: theme.textSecondary
                                textFormat: Text.PlainText
                            }
                        }

                        Button {
                            text: modelData.status === "done" ? "Reopen" : "Complete"
                            implicitHeight: 26
                            onClicked: {
                                if (modelData.status === "done") orion.reopenTask(modelData.id)
                                else orion.completeTask(modelData.id)
                            }
                        }
                        Button {
                            text: "Edit"
                            implicitHeight: 26
                            onClicked: prodPage.editingTask = modelData
                        }
                        Button {
                            text: "Archive"
                            implicitHeight: 26
                            onClicked: orion.archiveTask(modelData.id)
                        }
                    }
                }
            }
        }

        // ============================================================
        // SECTION C: HABITS
        // ============================================================
        ColumnLayout {
            visible: prodPage.activeSection === "habits"
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: theme.spaceSm

            // Create Habit Bar
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: addHabitRow.implicitHeight + 14
                radius: theme.radiusSm
                color: theme.bgSurface
                border.width: 1
                border.color: theme.border

                RowLayout {
                    id: addHabitRow
                    anchors.fill: parent
                    anchors.margins: 7
                    spacing: 6

                    TextField {
                        id: newHabitNameInput
                        objectName: "newHabitNameInput"
                        Layout.fillWidth: true
                        implicitHeight: 30
                        placeholderText: "New habit name (e.g., Reading, Workout)..."
                        font.pixelSize: theme.fontXs
                        color: theme.textPrimary
                        placeholderTextColor: theme.textMuted
                        background: Rectangle { radius: 6; color: theme.bgSurfaceAlt; border.width: 1; border.color: theme.borderStrong }
                    }

                    ComboBox {
                        id: newHabitFreqCombo
                        objectName: "newHabitFreqCombo"
                        Layout.preferredWidth: 100
                        implicitHeight: 30
                        model: ["daily", "weekly", "monthly"]
                    }

                    SpinBox {
                        id: newHabitTargetSpin
                        objectName: "newHabitTargetSpin"
                        from: 1
                        to: 31
                        value: 1
                        implicitHeight: 30
                    }

                    Button {
                        objectName: "createHabitButton"
                        text: "+ Create Habit"
                        implicitHeight: 30
                        implicitWidth: implicitContentWidth + 16
                        onClicked: {
                            if (newHabitNameInput.text.trim().length > 0 && typeof orion !== "undefined" && orion !== null) {
                                orion.createHabit(newHabitNameInput.text.trim(), "", newHabitFreqCombo.currentText, newHabitTargetSpin.value)
                                newHabitNameInput.text = ""
                                newHabitTargetSpin.value = 1
                            }
                        }
                        background: Rectangle { radius: 6; color: theme.accent }
                        contentItem: Text {
                            text: parent.text; font.pixelSize: theme.fontXs; font.bold: true; color: theme.bgSurface
                            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText
                        }
                    }
                }
            }

            ListView {
                objectName: "habitsListView"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 8
                model: root.prodHabits

                delegate: Rectangle {
                    width: ListView.view.width
                    implicitHeight: habitCardCol.implicitHeight + 16
                    radius: theme.radiusSm
                    color: theme.bgSurface
                    border.width: 1
                    border.color: theme.border

                    ColumnLayout {
                        id: habitCardCol
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 6

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2
                                Text {
                                    text: modelData.name + " (" + modelData.frequency + ", target " + modelData.target_count + ")"
                                    font.pixelSize: theme.fontSm
                                    font.bold: true
                                    color: theme.textPrimary
                                    textFormat: Text.PlainText
                                }
                                Text {
                                    text: "Current streak: " + modelData.current_streak + " · Longest: " + modelData.longest_streak + " · Rate: " + modelData.completion_rate + "% · Week: " + modelData.weekly_completed + "/" + modelData.weekly_target + " · Month: " + modelData.monthly_completed + "/" + modelData.monthly_target
                                    font.pixelSize: 11
                                    color: theme.textSecondary
                                    wrapMode: Text.Wrap
                                    textFormat: Text.PlainText
                                }
                                Text {
                                    visible: modelData.recent_dates && modelData.recent_dates.length > 0
                                    text: "Recent: " + (modelData.recent_dates || []).join(", ")
                                    font.pixelSize: 10
                                    color: theme.textMuted
                                    elide: Text.ElideRight
                                    textFormat: Text.PlainText
                                }
                            }

                            Button {
                                text: modelData.completed_today ? "Done Today" : "Log Today"
                                enabled: !modelData.completed_today
                                implicitHeight: 26
                                onClicked: orion.completeHabit(modelData.id)
                            }
                            Button {
                                text: "Undo Latest"
                                enabled: modelData.total_completions > 0
                                implicitHeight: 26
                                onClicked: orion.undoHabitCompletion(modelData.id)
                            }
                            Button {
                                text: "Edit"
                                implicitHeight: 26
                                onClicked: prodPage.editingHabit = modelData
                            }
                            Button {
                                text: "Archive"
                                implicitHeight: 26
                                onClicked: orion.archiveHabit(modelData.id)
                            }
                        }
                    }
                }
            }
        }
    }

    // Task Edit Modal
    Rectangle {
        visible: prodPage.editingTask !== null
        anchors.fill: parent
        color: "#66000000"
        z: 950
        MouseArea { anchors.fill: parent; onClicked: prodPage.editingTask = null }

        Rectangle {
            width: Math.min(parent.width - 40, 420)
            height: editTaskCol.implicitHeight + 32
            radius: theme.radiusLg
            color: theme.bgSurface
            anchors.centerIn: parent
            MouseArea { anchors.fill: parent; onClicked: {} }

            ColumnLayout {
                id: editTaskCol
                anchors.fill: parent
                anchors.margins: 16
                spacing: 8

                Text { text: "Edit Task"; font.pixelSize: theme.fontMd; font.bold: true; color: theme.textPrimary }
                TextField {
                    id: editTaskTitle
                    Layout.fillWidth: true
                    text: prodPage.editingTask ? prodPage.editingTask.title : ""
                    placeholderText: "Title"
                }
                TextField {
                    id: editTaskDue
                    Layout.fillWidth: true
                    text: ""
                    placeholderText: "New due date (optional, e.g., tomorrow, Friday)"
                }
                RowLayout {
                    Layout.fillWidth: true
                    ComboBox {
                        id: editTaskPriority
                        Layout.fillWidth: true
                        model: ["critical", "high", "medium", "low"]
                        currentIndex: prodPage.editingTask ? Math.max(0, ["critical", "high", "medium", "low"].indexOf(prodPage.editingTask.priority)) : 2
                    }
                    ComboBox {
                        id: editTaskStatus
                        Layout.fillWidth: true
                        model: ["todo", "in_progress", "done", "cancelled"]
                        currentIndex: prodPage.editingTask ? Math.max(0, ["todo", "in_progress", "done", "cancelled"].indexOf(prodPage.editingTask.status)) : 0
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }
                    Button { text: "Cancel"; onClicked: prodPage.editingTask = null }
                    Button {
                        text: "Save"
                        onClicked: {
                            if (prodPage.editingTask) {
                                orion.updateTask(
                                    prodPage.editingTask.id,
                                    editTaskTitle.text,
                                    prodPage.editingTask.description || "",
                                    editTaskPriority.currentText,
                                    editTaskStatus.currentText,
                                    editTaskDue.text
                                )
                                prodPage.editingTask = null
                            }
                        }
                    }
                }
            }
        }
    }

    // Habit Edit Modal
    Rectangle {
        visible: prodPage.editingHabit !== null
        anchors.fill: parent
        color: "#66000000"
        z: 950
        MouseArea { anchors.fill: parent; onClicked: prodPage.editingHabit = null }

        Rectangle {
            width: Math.min(parent.width - 40, 400)
            height: editHabitCol.implicitHeight + 32
            radius: theme.radiusLg
            color: theme.bgSurface
            anchors.centerIn: parent
            MouseArea { anchors.fill: parent; onClicked: {} }

            ColumnLayout {
                id: editHabitCol
                anchors.fill: parent
                anchors.margins: 16
                spacing: 8

                Text { text: "Edit Habit"; font.pixelSize: theme.fontMd; font.bold: true; color: theme.textPrimary }
                TextField {
                    id: editHabitName
                    Layout.fillWidth: true
                    text: prodPage.editingHabit ? prodPage.editingHabit.name : ""
                }
                RowLayout {
                    Layout.fillWidth: true
                    ComboBox {
                        id: editHabitFreq
                        Layout.fillWidth: true
                        model: ["daily", "weekly", "monthly"]
                        currentIndex: prodPage.editingHabit ? Math.max(0, ["daily", "weekly", "monthly"].indexOf(prodPage.editingHabit.frequency)) : 0
                    }
                    SpinBox {
                        id: editHabitTarget
                        from: 1
                        to: 31
                        value: prodPage.editingHabit ? prodPage.editingHabit.target_count : 1
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }
                    Button { text: "Cancel"; onClicked: prodPage.editingHabit = null }
                    Button {
                        text: "Save"
                        onClicked: {
                            if (prodPage.editingHabit) {
                                orion.updateHabit(
                                    prodPage.editingHabit.id,
                                    editHabitName.text,
                                    prodPage.editingHabit.description || "",
                                    editHabitFreq.currentText,
                                    editHabitTarget.value
                                )
                                prodPage.editingHabit = null
                            }
                        }
                    }
                }
            }
        }
    }
}
