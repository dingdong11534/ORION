import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Rectangle {
    id: settingsPage
    required property var root

    color: Theme.window

    ScrollView {
        anchors.fill: parent
        anchors.margins: Theme.spaceXl
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: Math.min(parent.width, 720)
            spacing: Theme.spaceXl

            Text {
                text: "Settings"
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontPageTitle
                font.weight: Font.Medium
                color: Theme.text
                textFormat: Text.PlainText
            }

            // Appearance / Theme
            OrionCard {
                Layout.fillWidth: true
                implicitHeight: themeCol.implicitHeight + Theme.spaceLg * 2

                ColumnLayout {
                    id: themeCol
                    anchors.fill: parent
                    anchors.margins: Theme.spaceLg
                    spacing: Theme.spaceMd

                    OrionSectionHeader {
                        title: "Appearance"
                        subtitle: "Applies immediately across all pages"
                    }

                    RowLayout {
                        spacing: Theme.spaceSm

                        OrionButton {
                            objectName: "themeLightButton"
                            text: "Light"
                            variant: Theme.themeMode === "light" ? "primary" : "secondary"
                            onClicked: {
                                Theme.themeMode = "light"
                                if (typeof orion !== "undefined" && orion !== null)
                                    orion.setThemeMode("light")
                            }
                        }

                        OrionButton {
                            objectName: "themeDarkButton"
                            text: "Dark"
                            variant: Theme.themeMode === "dark" ? "primary" : "secondary"
                            onClicked: {
                                Theme.themeMode = "dark"
                                if (typeof orion !== "undefined" && orion !== null)
                                    orion.setThemeMode("dark")
                            }
                        }

                        OrionButton {
                            objectName: "themeSystemButton"
                            text: "System"
                            variant: Theme.themeMode === "system" ? "primary" : "secondary"
                            onClicked: {
                                Theme.themeMode = "system"
                                if (typeof orion !== "undefined" && orion !== null)
                                    orion.setThemeMode("system")
                            }
                        }
                    }
                }
            }

            // Offline Voice
            OrionCard {
                Layout.fillWidth: true
                implicitHeight: voiceCol.implicitHeight + Theme.spaceLg * 2

                ColumnLayout {
                    id: voiceCol
                    anchors.fill: parent
                    anchors.margins: Theme.spaceLg
                    spacing: Theme.spaceMd

                    OrionSectionHeader {
                        title: "Offline Voice"
                        subtitle: "100% local speech-to-text (whisper.cpp) and text-to-speech (Piper)"
                    }

                    OrionToggle {
                        objectName: "voiceEnabledToggle"
                        text: "Enable offline voice controls"
                        checked: (typeof orion !== "undefined" && orion !== null) ? orion.voiceEnabled : false
                        enabled: (typeof orion !== "undefined" && orion !== null) && orion.voiceAvailable
                        onToggled: {
                            if (typeof orion !== "undefined" && orion !== null)
                                orion.setVoiceSettings(checked, orion.voiceAutoSpeak)
                        }
                    }

                    OrionToggle {
                        objectName: "voiceAutoSpeakToggle"
                        text: "Speak assistant responses automatically"
                        checked: (typeof orion !== "undefined" && orion !== null) ? orion.voiceAutoSpeak : false
                        enabled: (typeof orion !== "undefined" && orion !== null) && orion.voiceEnabled
                        onToggled: {
                            if (typeof orion !== "undefined" && orion !== null)
                                orion.setVoiceSettings(orion.voiceEnabled, checked)
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        height: 1
                        color: Theme.hairline
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spaceMd

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Theme.spaceXs

                            readonly property var statusObj: (typeof orion !== "undefined" && orion !== null)
                                ? orion.voiceAssetStatus
                                : ({ stt_ready: false, tts_ready: false, mic_available: false, audio_backend: "none", fix_hint: "" })

                            Text {
                                text: "Speech-to-Text Model: " + (parent.statusObj.stt_ready ? "Ready (tiny.en)" : "Missing")
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSecondary
                                color: Theme.text
                                textFormat: Text.PlainText
                            }
                            Text {
                                text: "Text-to-Speech Voice: " + (parent.statusObj.tts_ready ? "Ready (en_US-lessac-medium)" : "Missing")
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSecondary
                                color: Theme.text
                                textFormat: Text.PlainText
                            }
                            Text {
                                text: "Microphone (" + (parent.statusObj.audio_backend || "none") + "): " + (parent.statusObj.mic_available ? "Available" : "Unavailable")
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSecondary
                                color: Theme.text
                                textFormat: Text.PlainText
                            }
                            Text {
                                visible: (parent.statusObj.fix_hint || "").length > 0
                                Layout.fillWidth: true
                                text: parent.statusObj.fix_hint || ""
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontCaption
                                color: Theme.warning
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                        }

                        OrionButton {
                            text: "Refresh"
                            variant: "secondary"
                            compact: true
                            onClicked: {
                                if (typeof orion !== "undefined" && orion !== null)
                                    orion.refreshVoiceStatus()
                            }
                        }
                    }
                }
            }
        }
    }
}
