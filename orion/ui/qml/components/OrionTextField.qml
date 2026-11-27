import QtQuick
import QtQuick.Controls

TextField {
    id: field
    implicitHeight: Theme.controlHeight
    font.family: Theme.fontFamily
    font.pixelSize: Theme.fontBody
    color: Theme.text
    placeholderTextColor: Theme.textSecondary
    leftPadding: Theme.spaceMd
    rightPadding: Theme.spaceMd
    opacity: enabled ? 1.0 : 0.5

    background: Rectangle {
        radius: Theme.radiusControl
        color: Theme.surface
        border.width: 1
        border.color: field.activeFocus ? Theme.accent : Theme.border

        Rectangle {
            visible: field.activeFocus
            anchors.fill: parent
            anchors.margins: -3
            radius: Theme.radiusControl + 2
            color: "transparent"
            border.width: 2
            border.color: Theme.accent
        }
    }
}
