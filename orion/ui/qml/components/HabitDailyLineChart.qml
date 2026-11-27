import QtQuick

Item {
    id: chartRoot
    property var dailySeries: [] // [{ day: 1, ratio: 0.5 }, ...]
    property bool hasData: false

    implicitHeight: 132

    onDailySeriesChanged: lineCanvas.requestPaint()
    onHasDataChanged: lineCanvas.requestPaint()
    onWidthChanged: lineCanvas.requestPaint()
    Connections {
        target: Theme
        function onIsDarkChanged() { lineCanvas.requestPaint() }
    }

    Text {
        anchors.centerIn: parent
        visible: !chartRoot.hasData || !chartRoot.dailySeries || chartRoot.dailySeries.length < 2
        text: "Not enough data yet"
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSecondary
        color: Theme.textSecondary
        textFormat: Text.PlainText
    }

    Canvas {
        id: lineCanvas
        anchors.fill: parent
        visible: chartRoot.hasData && chartRoot.dailySeries && chartRoot.dailySeries.length >= 2
        antialiasing: true

        onPaint: {
            const ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            const pts = chartRoot.dailySeries || []
            if (pts.length < 2) return

            const leftPad = 42
            const rightPad = 10
            const topPad = 12
            const botPad = 14
            const plotW = Math.max(10, width - leftPad - rightPad)
            const plotH = Math.max(10, height - topPad - botPad)

            // 3 horizontal gridlines at 100%, 50%, 0%
            ctx.lineWidth = 1
            ctx.strokeStyle = Theme.hairline.toString()
            ctx.fillStyle = Theme.textSecondary.toString()
            ctx.font = Theme.fontCaption + "px " + Theme.fontFamily

            const levels = [
                { label: "100%", y: topPad },
                { label: "50%", y: topPad + plotH * 0.5 },
                { label: "0%", y: topPad + plotH }
            ]
            for (let i = 0; i < levels.length; i++) {
                const lv = levels[i]
                ctx.beginPath()
                ctx.moveTo(leftPad, lv.y)
                ctx.lineTo(leftPad + plotW, lv.y)
                ctx.stroke()
                ctx.fillText(lv.label, 2, lv.y + 4)
            }

            // Compute coordinates
            const coords = []
            const n = pts.length
            for (let i = 0; i < n; i++) {
                const x = leftPad + (n === 1 ? 0 : (i / (n - 1)) * plotW)
                const ratio = Math.max(0, Math.min(1, pts[i].ratio || 0))
                const y = topPad + plotH * (1 - ratio)
                coords.push({ x: x, y: y })
            }

            // Area fill at 12% opacity
            const acc = Theme.accent
            ctx.beginPath()
            ctx.moveTo(coords[0].x, topPad + plotH)
            for (let i = 0; i < coords.length; i++) {
                ctx.lineTo(coords[i].x, coords[i].y)
            }
            ctx.lineTo(coords[coords.length - 1].x, topPad + plotH)
            ctx.closePath()
            ctx.fillStyle = Qt.rgba(acc.r, acc.g, acc.b, 0.12).toString()
            ctx.fill()

            // 2px accent polyline
            ctx.beginPath()
            ctx.moveTo(coords[0].x, coords[0].y)
            for (let i = 1; i < coords.length; i++) {
                ctx.lineTo(coords[i].x, coords[i].y)
            }
            ctx.lineWidth = 2
            ctx.strokeStyle = acc.toString()
            ctx.stroke()
        }
    }
}
