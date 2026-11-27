import QtQuick

QtObject {
    // Warm stone & sage design system tokens
    readonly property color bgWindow: "#F4F1E9"
    readonly property color bgSidebar: "#E8E5DA"
    readonly property color bgSurface: "#FCFBF8"
    readonly property color bgSurfaceAlt: "#F2F0EA"
    readonly property color bgHover: "#ECE9DF"
    readonly property color bgSelected: "#DFE6DF"

    readonly property color accent: "#4A6B5D"
    readonly property color accentStrong: "#526D5B"
    readonly property color accentLight: "#EEF3EF"

    readonly property color textPrimary: "#303A32"
    readonly property color textSecondary: "#6A6F64"
    readonly property color textMuted: "#85877C"

    readonly property color border: "#E0DDD3"
    readonly property color borderStrong: "#D6D2C7"

    readonly property color danger: "#B3483B"
    readonly property color dangerBg: "#FBECE9"
    readonly property color warning: "#9A6700"
    readonly property color warningBg: "#FFF6E0"

    // Consistent spacing & radii
    readonly property int spaceXs: 4
    readonly property int spaceSm: 8
    readonly property int spaceMd: 12
    readonly property int spaceLg: 16
    readonly property int spaceXl: 24

    readonly property int radiusSm: 8
    readonly property int radiusMd: 12
    readonly property int radiusLg: 18

    // Typography scale
    readonly property int fontXs: 11
    readonly property int fontSm: 12
    readonly property int fontMd: 13
    readonly property int fontLg: 15
    readonly property int fontTitle: 20

    function formatBytes(bytes) {
        if (bytes === undefined || bytes === null || bytes < 0)
            return "—"
        if (bytes < 1024)
            return bytes + " B"
        if (bytes < 1024 * 1024)
            return (bytes / 1024).toFixed(1) + " KB"
        if (bytes < 1024 * 1024 * 1024)
            return (bytes / (1024 * 1024)).toFixed(1) + " MB"
        return (bytes / (1024 * 1024 * 1024)).toFixed(2) + " GB"
    }
}
