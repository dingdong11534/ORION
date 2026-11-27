import QtQuick
import QtQuick.Controls

Switch {
    id: sw
    implicitHeight: Theme.controlHeightCompact
    opacity: enabled ? 1.0 : 0.5

    indicator: Rectangle {
        implicitWidth: 38
        implicitHeight: 20
        x: sw.leftPadding
        y: (sw.height - height) / 2
        radius: 10
        color: sw.checked ? Theme.accent : Theme.sidebar
        border.width: 1
        border.color: sw.checked ? Theme.accent : Theme.border

        Behavior on color { ColorAnimation { duration: Theme.motionMs } }

        Rectangle {
            x: sw.checked ? parent.width - width - 2 : 2
            anchors.verticalCenter: parent.verticalCenter
            width: 16
            height: 16
            radius: 8
            color: sw.checked ? Theme.onAccent : Theme.textSecondary
            Behavior on x { NumberAnimation { duration: Theme.motionMs } }
        }

        Rectangle {
            visible: sw.activeFocus
            anchors.fill: parent
            anchors.margins: -3
            radius: 12
            color: "transparent"
            border.width: 2
            border.color: Theme.accent
        }
    }

    contentItem: Text {
        text: sw.text
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSecondary
        color: Theme.text
        verticalAlignment: Text.AlignVCenter
        leftPadding: sw.indicator.width + Theme.spaceSm
        textFormat: Text.PlainText
    }
}
