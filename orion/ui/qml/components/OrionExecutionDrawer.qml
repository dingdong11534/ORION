import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: executionDrawer
    objectName: "executionDrawer"
    required property var root

    visible: root.executionPanelVisible
    anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 18 }
    height: Math.min(340, parent.height * 0.45)
    z: 500
    radius: Theme.radiusCard
    color: Theme.surface
    border.width: 1
    border.color: Theme.border

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 14; spacing: 8
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            Text { text: "Subprocess Execution (" + root.executionStatus.toUpperCase() + ")"; font.pixelSize: 13; font.weight: Font.Medium; color: Theme.text; textFormat: Text.PlainText }
            Item { Layout.fillWidth: true }
            OrionButton {
                objectName: "stopExecutionButton"; visible: root.executionStatus === "running"; enabled: root.executionStatus === "running" && root.executionId !== ""; text: "Stop"; variant: "danger"; compact: true
                onClicked: { if (typeof orion !== "undefined" && orion !== null && root.executionId !== "") orion.cancelExecution(root.executionId) }
            }
            OrionButton {
                objectName: "dismissExecutionButton"; visible: root.executionStatus !== "running"; text: "Dismiss"; variant: "secondary"; compact: true
                onClicked: root.executionPanelVisible = false
            }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 8
            ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                TextArea {
                    objectName: "stdoutDisplay"; readOnly: true; text: root.executionStdout
                    textFormat: TextEdit.PlainText
                    font.family: "Monospace"; font.pixelSize: 12; color: Theme.text
                    background: Rectangle { color: Theme.sidebar; radius: 6; border.width: 1; border.color: Theme.hairline }
                }
            }
            ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                TextArea {
                    objectName: "stderrDisplay"; readOnly: true; text: root.executionStderr
                    textFormat: TextEdit.PlainText
                    font.family: "Monospace"; font.pixelSize: 12; color: Theme.danger
                    background: Rectangle { color: Theme.sidebar; radius: 6; border.width: 1; border.color: Theme.hairline }
                }
            }
        }
    }
}
