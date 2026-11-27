import QtQuick
import QtQuick.Layouts

Rectangle {
    id: banner
    property string message: ""
    property string severity: "warning" // "warning" | "danger" | "info"
    property string actionLabel: ""
    signal actionClicked()

    visible: message.length > 0
    radius: Theme.radiusControl
    color: Theme.surface
    border.width: 1
    border.color: severity === "danger" ? Theme.danger : (severity === "warning" ? Theme.warning : Theme.accent)
    implicitHeight: row.implicitHeight + Theme.spaceSm * 2

    RowLayout {
        id: row
        anchors.fill: parent
        anchors.margins: Theme.spaceSm
        anchors.leftMargin: Theme.spaceMd
        anchors.rightMargin: Theme.spaceMd
        spacing: Theme.spaceMd

        Text {
            Layout.fillWidth: true
            text: banner.message
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontSecondary
            color: banner.severity === "danger" ? Theme.danger : Theme.text
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }

        OrionButton {
            visible: banner.actionLabel.length > 0
            text: banner.actionLabel
            variant: "secondary"
            compact: true
            onClicked: banner.actionClicked()
        }
    }
}
