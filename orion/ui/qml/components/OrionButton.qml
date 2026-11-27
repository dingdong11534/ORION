import QtQuick
import QtQuick.Controls

Button {
    id: btn
    property string variant: "secondary" // "primary" | "secondary" | "quiet" | "danger"
    property bool compact: false
    property bool loading: false

    implicitHeight: compact ? Theme.controlHeightCompact : Theme.controlHeight
    implicitWidth: Math.max(compact ? 64 : 76, contentRow.implicitWidth + Theme.spaceLg * 2)
    enabled: !loading
    opacity: enabled ? 1.0 : 0.5

    background: Rectangle {
        radius: Theme.radiusControl
        color: {
            if (btn.variant === "primary") {
                if (btn.down) return Theme.accentPressed
                if (btn.hovered) return Theme.accentHover
                return Theme.accent
            }
            if (btn.variant === "danger") {
                return (btn.down || btn.hovered) ? Theme.sidebar : Theme.surface
            }
            if (btn.variant === "quiet") {
                return (btn.down || btn.hovered) ? Theme.sidebar : "transparent"
            }
            return (btn.down || btn.hovered) ? Theme.sidebar : Theme.surface
        }
        border.width: (btn.variant === "secondary" || btn.variant === "danger") ? 1 : 0
        border.color: btn.variant === "danger" ? Theme.danger : Theme.border

        Behavior on color { ColorAnimation { duration: Theme.motionMs } }

        // 2px accent focus ring, 1px offset
        Rectangle {
            visible: btn.activeFocus
            anchors.fill: parent
            anchors.margins: -3
            radius: Theme.radiusControl + 2
            color: "transparent"
            border.width: 2
            border.color: Theme.accent
        }
    }

    contentItem: Item {
        id: contentRow
        implicitWidth: labelText.implicitWidth
        implicitHeight: labelText.implicitHeight

        Text {
            id: labelText
            anchors.centerIn: parent
            text: btn.loading ? "Working" : btn.text
            font.family: Theme.fontFamily
            font.pixelSize: btn.compact ? Theme.fontSecondary : Theme.fontBody
            font.weight: Font.Medium
            color: {
                if (btn.variant === "primary") return Theme.onAccent
                if (btn.variant === "danger") return Theme.danger
                return Theme.text
            }
            textFormat: Text.PlainText
        }
    }
}
