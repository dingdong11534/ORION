pragma Singleton
import QtQuick

QtObject {
    id: theme

    // "light" | "dark" | "system"
    property string themeMode: "light"

    readonly property bool isDark: {
        if (themeMode === "dark")
            return true
        if (themeMode === "light")
            return false
        if (typeof Qt.styleHints !== "undefined" && Qt.styleHints !== null)
            return Qt.styleHints.colorScheme === Qt.Dark
        return false
    }

    // Palette tokens
    readonly property color window: isDark ? "#1D1C19" : "#F6F3ED"
    readonly property color surface: isDark ? "#252420" : "#FCFAF6"
    readonly property color sidebar: isDark ? "#181714" : "#EEEAE1"
    readonly property color border: isDark ? "#787368" : "#8F897D"
    readonly property color hairline: isDark ? "#2F2D28" : "#E8E3D8"
    readonly property color text: isDark ? "#ECE8DF" : "#2A2824"
    readonly property color textSecondary: isDark ? "#A9A395" : "#6A655B"
    readonly property color accent: isDark ? "#7FB08F" : "#4F7A5E"
    readonly property color accentHover: isDark ? "#6FA180" : "#436950"
    readonly property color accentPressed: isDark ? "#619272" : "#385943"
    readonly property color onAccent: isDark ? "#10201A" : "#FFFFFF"
    readonly property color danger: isDark ? "#E07A6F" : "#B4493F"
    readonly property color warning: isDark ? "#D9A441" : "#94641A"
    readonly property color userBubble: isDark ? "#2F2D28" : "#E9E4D8"

    // 4 Habit hues
    readonly property color habit1: isDark ? "#7FB08F" : "#4F7A5E"
    readonly property color habit2: isDark ? "#D69AA3" : "#C07A84"
    readonly property color habit3: isDark ? "#AC9FD0" : "#8C7BB0"
    readonly property color habit4: isDark ? "#D9A441" : "#B8893A"

    function habitHue(index) {
        const idx = ((index % 4) + 4) % 4
        if (idx === 0) return habit1
        if (idx === 1) return habit2
        if (idx === 2) return habit3
        return habit4
    }

    function habitTint(index) {
        const c = habitHue(index)
        return Qt.rgba(c.r, c.g, c.b, 0.12)
    }

    // Typography (minimum 12px everywhere, regular & medium weights only)
    readonly property string fontFamily: Qt.application.font.family
    readonly property int fontPageTitle: 22
    readonly property int fontHeading: 18
    readonly property int fontChat: 15
    readonly property real chatLineHeight: 1.45
    readonly property int fontBody: 14
    readonly property int fontSecondary: 13
    readonly property int fontCaption: 12

    // Spacing scale: 4, 8, 12, 16, 24, 32, 40
    readonly property int spaceXs: 4
    readonly property int spaceSm: 8
    readonly property int spaceMd: 12
    readonly property int spaceLg: 16
    readonly property int spaceXl: 24
    readonly property int space2xl: 32
    readonly property int space3xl: 40

    // Radii
    readonly property int radiusSm: 4
    readonly property int radiusControl: 6
    readonly property int radiusCard: 10

    // Control heights
    readonly property int controlHeight: 32
    readonly property int controlHeightCompact: 28
    readonly property int motionMs: 150

    // Compatibility aliases for KnowledgePage / WorkspacePage
    readonly property color bgWindow: window
    readonly property color bgSidebar: sidebar
    readonly property color bgSurface: surface
    readonly property color bgSurfaceAlt: sidebar
    readonly property color bgHover: sidebar
    readonly property color bgSelected: isDark ? "#2E3B33" : "#DFE6DF"
    readonly property color accentStrong: accentHover
    readonly property color accentLight: isDark ? "#233229" : "#EEF3EF"
    readonly property color textPrimary: text
    readonly property color textMuted: textSecondary
    readonly property color borderStrong: border
    readonly property color dangerBg: isDark ? "#3A2220" : "#FBECE9"
    readonly property color warningBg: isDark ? "#382D18" : "#FFF6E0"
    readonly property int radiusLg: radiusCard
    readonly property int radiusMd: radiusCard
    readonly property int fontXs: fontCaption
    readonly property int fontSm: fontCaption
    readonly property int fontMd: fontSecondary
    readonly property int fontLg: fontChat
    readonly property int fontTitle: fontPageTitle

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
