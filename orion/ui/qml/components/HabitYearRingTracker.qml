import QtQuick

Item {
    id: tracker
    property var completedDates: [] // ["YYYY-MM-DD", ...]
    property int year: new Date().getFullYear()
    property color habitHue: Theme.habit1
    property string accessibleSummary: ""

    implicitWidth: 260
    implicitHeight: 260
    width: 260
    height: 260

    Accessible.role: Accessible.Graphic
    Accessible.name: accessibleSummary

    onCompletedDatesChanged: ringCanvas.requestPaint()
    onYearChanged: ringCanvas.requestPaint()
    onHabitHueChanged: ringCanvas.requestPaint()
    Connections {
        target: Theme
        function onIsDarkChanged() { ringCanvas.requestPaint() }
    }

    Canvas {
        id: ringCanvas
        anchors.fill: parent
        antialiasing: true

        function daysInMonth(y, m) {
            return new Date(y, m, 0).getDate()
        }

        onPaint: {
            const ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)

            const cx = width / 2
            const cy = height / 2
            const doneSet = {}
            if (tracker.completedDates) {
                for (let i = 0; i < tracker.completedDates.length; i++) {
                    doneSet[tracker.completedDates[i]] = true
                }
            }

            const hueStr = tracker.habitHue.toString()
            const borderStr = Theme.border.toString()

            ctx.lineWidth = 6
            ctx.lineCap = "butt"

            for (let m = 1; m <= 12; m++) {
                const r = 34 + 8 * (m - 1)
                const totalDays = daysInMonth(tracker.year, m)
                const step = (Math.PI * 2) / totalDays
                const gap = Math.min(0.035, step * 0.18)
                const mStr = m < 10 ? ("0" + m) : ("" + m)

                for (let d = 1; d <= totalDays; d++) {
                    const dStr = d < 10 ? ("0" + d) : ("" + d)
                    const iso = tracker.year + "-" + mStr + "-" + dStr
                    const isDone = !!doneSet[iso]

                    const startAngle = -Math.PI / 2 + (d - 1) * step + gap / 2
                    const endAngle = -Math.PI / 2 + d * step - gap / 2

                    ctx.beginPath()
                    ctx.arc(cx, cy, r, startAngle, endAngle, false)
                    ctx.strokeStyle = isDone ? hueStr : borderStr
                    ctx.globalAlpha = isDone ? 1.0 : 0.45
                    ctx.stroke()
                }
            }
            ctx.globalAlpha = 1.0
        }
    }
}
