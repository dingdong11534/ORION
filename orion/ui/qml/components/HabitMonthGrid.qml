import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: gridRoot
    property int year: new Date().getFullYear()
    property int month: new Date().getMonth() + 1
    property int daysInMonth: 30
    property int elapsedDays: 30
    property var completedDates: [] // ["YYYY-MM-DD", ...]
    property color habitHue: Theme.habit1

    spacing: Theme.spaceSm

    function isDone(dayNum) {
        if (!completedDates) return false
        const mStr = month < 10 ? ("0" + month) : ("" + month)
        const dStr = dayNum < 10 ? ("0" + dayNum) : ("" + dayNum)
        const iso = year + "-" + mStr + "-" + dStr
        return completedDates.indexOf(iso) !== -1
    }

    function weekCount() {
        return Math.ceil(Math.max(1, daysInMonth) / 7)
    }

    Flow {
        Layout.fillWidth: true
        spacing: Theme.spaceSm

        Repeater {
            model: gridRoot.weekCount()

            delegate: ColumnLayout {
                id: weekCol
                readonly property int weekIndex: index
                readonly property int startDay: weekIndex * 7 + 1
                readonly property int endDay: Math.min(gridRoot.daysInMonth, (weekIndex + 1) * 7)
                readonly property int daySpan: Math.max(0, endDay - startDay + 1)

                spacing: Theme.spaceXs

                Text {
                    text: "Week " + (weekCol.weekIndex + 1)
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontCaption
                    color: Theme.textSecondary
                    textFormat: Text.PlainText
                }

                Rectangle {
                    radius: Theme.radiusSm
                    color: Theme.habitTint(weekCol.weekIndex) // week 5 reuses hue 0 via modulo 4
                    implicitWidth: weekRow.implicitWidth + 8
                    implicitHeight: weekRow.implicitHeight + 8

                    Row {
                        id: weekRow
                        anchors.centerIn: parent
                        spacing: 2

                        Repeater {
                            model: weekCol.daySpan

                            delegate: Rectangle {
                                readonly property int dayNum: weekCol.startDay + index
                                readonly property bool done: gridRoot.isDone(dayNum)
                                readonly property bool future: dayNum > gridRoot.elapsedDays

                                width: 15
                                height: 15
                                radius: Theme.radiusSm
                                color: done ? gridRoot.habitHue : "transparent"
                                border.width: done ? 0 : 1
                                border.color: Theme.border
                                opacity: future && !done ? 0.4 : 1.0
                            }
                        }
                    }
                }
            }
        }
    }

    Text {
        text: "Filled: done · Outlined: missed · Faint: upcoming"
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontCaption
        color: Theme.textSecondary
        textFormat: Text.PlainText
    }
}
