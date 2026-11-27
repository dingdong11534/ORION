import QtQuick
import QtQuick.Layouts

Rectangle {
    id: rowItem
    property string title: ""
    property string subtitle: ""
    property bool selected: false
    property alias hovered: ma.containsMouse
    signal clicked()

    radius: Theme.radiusControl
    color: selected ? Theme.window : (ma.containsMouse ? Theme.sidebar : "transparent")
    implicitHeight: Math.max(Theme.controlHeight, col.implicitHeight + Theme.spaceSm * 2)

    Behavior on color { ColorAnimation { duration: Theme.motionMs } }

    Rectangle {
        visible: rowItem.selected
        width: 3
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.topMargin: 4
        anchors.bottomMargin: 4
        radius: 2
        color: Theme.accent
    }

    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        onClicked: rowItem.clicked()
    }

    ColumnLayout {
        id: col
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: Theme.spaceMd
        anchors.rightMargin: Theme.spaceMd
        spacing: 2

        Text {
            Layout.fillWidth: true
            text: rowItem.title
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontSecondary
            font.weight: rowItem.selected ? Font.Medium : Font.Normal
            color: Theme.text
            elide: Text.ElideRight
            textFormat: Text.PlainText
        }

        Text {
            Layout.fillWidth: true
            visible: rowItem.subtitle.length > 0
            text: rowItem.subtitle
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontCaption
            color: Theme.textSecondary
            elide: Text.ElideRight
            textFormat: Text.PlainText
        }
    }
}
