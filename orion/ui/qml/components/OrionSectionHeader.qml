import QtQuick
import QtQuick.Layouts

RowLayout {
    id: hdr
    property string title: ""
    property string subtitle: ""
    property int count: -1

    Layout.fillWidth: true
    spacing: Theme.spaceSm

    Text {
        text: hdr.title
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontHeading
        font.weight: Font.Medium
        color: Theme.text
        textFormat: Text.PlainText
    }

    Rectangle {
        visible: hdr.count >= 0
        radius: Theme.radiusSm
        color: Theme.sidebar
        border.width: 1
        border.color: Theme.hairline
        implicitWidth: countTxt.implicitWidth + Theme.spaceSm * 2
        implicitHeight: 22

        Text {
            id: countTxt
            anchors.centerIn: parent
            text: String(hdr.count)
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontCaption
            font.weight: Font.Medium
            color: Theme.textSecondary
            textFormat: Text.PlainText
        }
    }

    Text {
        visible: hdr.subtitle.length > 0
        text: hdr.subtitle
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSecondary
        color: Theme.textSecondary
        elide: Text.ElideRight
        Layout.fillWidth: true
        textFormat: Text.PlainText
    }

    Item { Layout.fillWidth: hdr.subtitle.length === 0 }
}
