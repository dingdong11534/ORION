import QtQuick
import QtQuick.Layouts

Rectangle {
    id: empty
    property string title: "Nothing here yet"
    property string subtitle: ""
    property string actionLabel: ""
    signal actionClicked()

    radius: Theme.radiusCard
    color: Theme.surface
    border.width: 1
    border.color: Theme.hairline
    implicitHeight: col.implicitHeight + Theme.space2xl * 2

    ColumnLayout {
        id: col
        anchors.centerIn: parent
        width: Math.min(parent.width - Theme.spaceXl * 2, 420)
        spacing: Theme.spaceMd

        Text {
            Layout.fillWidth: true
            text: empty.title
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontHeading
            font.weight: Font.Medium
            color: Theme.text
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }

        Text {
            Layout.fillWidth: true
            visible: empty.subtitle.length > 0
            text: empty.subtitle
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontBody
            color: Theme.textSecondary
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }

        OrionButton {
            Layout.alignment: Qt.AlignHCenter
            visible: empty.actionLabel.length > 0
            text: empty.actionLabel
            variant: "primary"
            onClicked: empty.actionClicked()
        }
    }
}
