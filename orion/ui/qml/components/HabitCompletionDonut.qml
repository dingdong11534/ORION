import QtQuick

Item {
    id: donut
    property int percent: 0

    implicitWidth: 88
    implicitHeight: 88
    width: 88
    height: 88

    onPercentChanged: donutCanvas.requestPaint()
    Connections {
        target: Theme
        function onIsDarkChanged() { donutCanvas.requestPaint() }
    }

    Canvas {
        id: donutCanvas
        anchors.fill: parent
        antialiasing: true

        onPaint: {
            const ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            const cx = width / 2
            const cy = height / 2
            const r = (Math.min(width, height) - 12) / 2
            const pct = Math.max(0, Math.min(100, donut.percent))

            ctx.lineWidth = 10
            ctx.lineCap = "round"

            // Track
            ctx.beginPath()
            ctx.arc(cx, cy, r, 0, Math.PI * 2, false)
            ctx.strokeStyle = Theme.border.toString()
            ctx.globalAlpha = 0.45
            ctx.stroke()

            // Progress arc
            if (pct > 0) {
                const startA = -Math.PI / 2
                const endA = startA + (Math.PI * 2) * (pct / 100.0)
                ctx.beginPath()
                ctx.arc(cx, cy, r, startA, endA, false)
                ctx.strokeStyle = Theme.accent.toString()
                ctx.globalAlpha = 1.0
                ctx.stroke()
            }
            ctx.globalAlpha = 1.0
        }
    }

    Text {
        anchors.centerIn: parent
        text: Math.max(0, Math.min(100, donut.percent)) + "%"
        font.family: Theme.fontFamily
        font.pixelSize: 16
        font.weight: Font.Medium
        color: Theme.text
        textFormat: Text.PlainText
    }
}
