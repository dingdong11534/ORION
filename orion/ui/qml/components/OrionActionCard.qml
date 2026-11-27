import QtQuick
import QtQuick.Layouts

Rectangle {
    id: card
    property string headerText: ""
    property string titleText: ""
    property var changedRows: [] // [{ label: "Priority", oldVal: "Medium", newVal: "High" }]
    property bool failed: false
    property bool undoAvailable: false
    signal undoClicked()

    radius: Theme.radiusCard
    color: Theme.surface
    border.width: 1
    border.color: Theme.hairline
    implicitHeight: col.implicitHeight + Theme.spaceMd * 2

    // 3px accent or danger edge on the left
    Rectangle {
        width: 3
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.topMargin: 1
        anchors.bottomMargin: 1
        radius: 2
        color: card.failed ? Theme.danger : Theme.accent
    }

    ColumnLayout {
        id: col
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: Theme.spaceMd
        anchors.leftMargin: Theme.spaceLg
        anchors.rightMargin: Theme.spaceLg
        spacing: Theme.spaceXs

        Text {
            Layout.fillWidth: true
            text: card.headerText
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontSecondary
            color: card.failed ? Theme.danger : Theme.textSecondary
            elide: Text.ElideRight
            textFormat: Text.PlainText
        }

        Text {
            Layout.fillWidth: true
            visible: card.titleText.length > 0
            text: card.titleText
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontChat
            font.weight: Font.Medium
            color: Theme.text
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }

        Repeater {
            model: card.changedRows
            delegate: RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceSm
                Text {
                    text: modelData.label + ":"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSecondary
                    color: Theme.textSecondary
                    textFormat: Text.PlainText
                }
                Text {
                    text: (modelData.oldVal ? (modelData.oldVal + " to ") : "") + modelData.newVal
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSecondary
                    font.weight: Font.Medium
                    color: Theme.text
                    textFormat: Text.PlainText
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: Theme.spaceXs
            spacing: Theme.spaceMd

            Text {
                text: card.failed ? "Not saved" : "Saved"
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontCaption
                color: card.failed ? Theme.danger : Theme.textSecondary
                textFormat: Text.PlainText
            }

            Item { Layout.fillWidth: true }

            OrionButton {
                visible: card.undoAvailable
                text: "Undo"
                variant: "quiet"
                compact: true
                onClicked: card.undoClicked()
            }
        }
    }
}
