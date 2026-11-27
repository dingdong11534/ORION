import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: approvalDialog
    objectName: "approvalDialog"
    required property var root
    required property var pendingApprovalsModel

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

    Timer {
        interval: 1000
        repeat: true
        running: pendingApprovalsModel.count > 0
        onTriggered: { if (root.approvalRemainingSeconds > 0) root.approvalRemainingSeconds -= 1 }
    }

    Rectangle {
        width: Math.min(parent.width - 40, 520)
        implicitHeight: approvalContentColumn.implicitHeight + 40
        radius: Theme.radiusCard
        color: Theme.surface
        anchors.centerIn: parent

        ColumnLayout {
            id: approvalContentColumn
            anchors.fill: parent
            anchors.margins: 20
            spacing: 12

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text { text: "Action Approval Required"; font.pixelSize: 17; font.weight: Font.Medium; color: Theme.text; textFormat: Text.PlainText }
                Item { Layout.fillWidth: true }
                Rectangle {
                    implicitWidth: mutatingLabel.implicitWidth + 12; implicitHeight: 22; radius: 6
                    color: approvalDialog.isMutating ? Theme.dangerBg : Theme.accentLight
                    border.color: approvalDialog.isMutating ? Theme.danger : Theme.accent; border.width: 1
                    Text {
                        id: mutatingLabel; anchors.centerIn: parent
                        text: approvalDialog.isMutating ? "MUTATING" : "READ-ONLY"
                        font.pixelSize: 12; font.weight: Font.Medium
                        color: approvalDialog.isMutating ? Theme.danger : Theme.accent; textFormat: Text.PlainText
                    }
                }
            }

            Text {
                id: toolNameText; objectName: "toolNameText"; Layout.fillWidth: true
                text: approvalDialog.currentApproval ? approvalDialog.currentApproval.tool_name : ""
                font.pixelSize: 14; font.weight: Font.Medium; color: Theme.text; textFormat: Text.PlainText
            }
            Text {
                id: actionSummaryText; objectName: "actionSummaryText"; Layout.fillWidth: true
                text: approvalDialog.currentApproval ? approvalDialog.currentApproval.action_summary : ""
                font.pixelSize: 12; color: Theme.textSecondary; wrapMode: Text.Wrap; textFormat: Text.PlainText
            }
            Rectangle {
                Layout.fillWidth: true; implicitHeight: Math.min(argsText.implicitHeight + 16, 120)
                color: Theme.window; radius: 8; border.color: Theme.border; border.width: 1; clip: true
                ScrollView {
                    anchors.fill: parent; anchors.margins: 8
                    Text {
                        id: argsText; objectName: "argsText"; width: parent.width
                        text: approvalDialog.currentApproval ? JSON.stringify(approvalDialog.currentApproval.arguments, null, 2) : "{}"
                        font.family: "Monospace"; font.pixelSize: 12; color: Theme.text
                        wrapMode: Text.WrapAnywhere; textFormat: Text.PlainText
                    }
                }
            }
            Text {
                id: countdownLabel; objectName: "countdownLabel"
                text: approvalDialog.isExpired ? "Approval expired (60s limit)" : "Expires in " + root.approvalRemainingSeconds + "s"
                font.pixelSize: 12
                color: approvalDialog.isExpired ? Theme.danger : Theme.textSecondary; textFormat: Text.PlainText
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight; spacing: 10
                OrionButton {
                    objectName: "approvalRejectButton"; text: "Reject"; variant: "secondary"
                    onClicked: { if (typeof orion !== "undefined" && orion !== null) orion.submitApprovalDecision(approvalDialog.currentApprovalId, approvalDialog.currentNonce, false) }
                }
                OrionButton {
                    objectName: "approvalApproveButton"; text: "Approve Once"; variant: "primary"
                    enabled: !approvalDialog.isExpired && approvalDialog.currentApprovalId.length > 0
                    onClicked: { if (typeof orion !== "undefined" && orion !== null) orion.submitApprovalDecision(approvalDialog.currentApprovalId, approvalDialog.currentNonce, true) }
                }
            }
        }
    }
}
