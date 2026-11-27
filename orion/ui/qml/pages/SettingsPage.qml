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
            // Personal Memory & Profile
            OrionCard {
                Layout.fillWidth: true
                implicitHeight: memCol.implicitHeight + Theme.spaceLg * 2

                ColumnLayout {
                    id: memCol
                    anchors.fill: parent
                    anchors.margins: Theme.spaceLg
                    spacing: Theme.spaceMd

                    OrionSectionHeader {
                        title: "Personal Memory & Profile"
                        subtitle: "Review, edit, or delete saved facts, projects, and goals"
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spaceSm
                        OrionTextField {
                            id: profNameField
                            Layout.fillWidth: true
                            placeholderText: "Preferred name"
                            text: root.memoryProfile.preferred_name || ""
                        }
                        OrionTextField {
                            id: profTzField
                            Layout.preferredWidth: 160
                            placeholderText: "Timezone"
                            text: root.memoryProfile.timezone || "Asia/Kolkata"
                        }
                        OrionButton {
                            text: "Save Profile"
                            variant: "primary"
                            compact: true
                            onClicked: if (typeof orion !== "undefined" && orion !== null)
                                orion.saveUserProfile(profNameField.text, profTzField.text, "", "")
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spaceSm
                        ComboBox {
                            id: memCatBox
                            model: ["fact", "project", "goal", "routine"]
                            implicitHeight: Theme.controlHeight
                        }
                        OrionTextField {
                            id: memContentField
                            Layout.fillWidth: true
                            placeholderText: "Add a memory (e.g. Preparing for internships)..."
                        }
                        OrionButton {
                            text: "Remember"
                            variant: "secondary"
                            compact: true
                            enabled: memContentField.text.trim().length > 0
                            onClicked: {
                                if (typeof orion !== "undefined" && orion !== null) {
                                    orion.addPersonalMemory(memCatBox.currentText, "", memContentField.text.trim())
                                    memContentField.text = ""
                                }
                            }
                        }
                    }

                    Repeater {
                        model: root.personalMemories
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.spaceSm
                            Text {
                                Layout.fillWidth: true
                                text: "[" + modelData.category + (modelData.status === "stale" ? " · stale" : "") + "] " + modelData.content
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSecondary
                                color: modelData.status === "stale" ? Theme.textSecondary : Theme.text
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                            OrionButton {
                                text: "Stale"
                                variant: "quiet"
                                compact: true
                                visible: modelData.status !== "stale"
                                onClicked: orion.markMemoryStale(modelData.id)
                            }
                            OrionButton {
                                text: "Delete"
                                variant: "danger"
                                compact: true
                                onClicked: orion.deletePersonalMemory(modelData.id)
                            }
                        }
                    }
                }
            }

            // Automation Session & Background Jobs (Milestone 4.14)
            OrionCard {
                Layout.fillWidth: true
                implicitHeight: sessionCol.implicitHeight + Theme.spaceLg * 2

                ColumnLayout {
                    id: sessionCol
                    anchors.fill: parent
                    anchors.margins: Theme.spaceLg
                    spacing: Theme.spaceMd

                    OrionSectionHeader {
                        title: "Automation Session & Background Jobs"
                        subtitle: "Locking ORION never cancels already-authorized background jobs"
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spaceSm
                        Text {
                            objectName: "settingsSessionStateText"
                            Layout.fillWidth: true
                            text: "Session: " + root.sessionState.toUpperCase()
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSecondary
                            color: Theme.text
                            textFormat: Text.PlainText
                        }
                        OrionButton {
                            text: root.sessionState === "unlocked" ? "Lock Session" : "Unlock Session"
                            variant: "secondary"
                            compact: true
                            onClicked: if (typeof orion !== "undefined" && orion !== null) orion.toggleSessionLock()
                        }
                        OrionButton {
                            text: "Hide to Orb"
                            variant: "quiet"
                            compact: true
                            onClicked: if (typeof orion !== "undefined" && orion !== null) orion.setBackgroundMode(true)
                        }
                        OrionButton {
                            text: "Refresh Jobs"
                            variant: "quiet"
                            compact: true
                            onClicked: if (typeof orion !== "undefined" && orion !== null) orion.loadBackgroundJobs()
                        }
                    }

                    Text {
                        visible: root.backgroundJobs.length === 0
                        text: "No background automation jobs recorded."
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontCaption
                        color: Theme.textSecondary
                        textFormat: Text.PlainText
                    }

                    Repeater {
                        model: root.backgroundJobs
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.spaceSm
                            Text {
                                Layout.fillWidth: true
                                text: "[" + modelData.status + "] " + modelData.summary + " (" + modelData.current_step_index + "/" + modelData.total_steps + ")"
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSecondary
                                color: Theme.text
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                            OrionButton {
                                text: "Approve"
                                variant: "primary"
                                compact: true
                                visible: modelData.status === "waiting_for_approval"
                                onClicked: orion.approveBackgroundJob(modelData.id)
                            }
                            OrionButton {
                                text: "Cancel"
                                variant: "danger"
                                compact: true
                                visible: modelData.status === "queued" || modelData.status === "running" || modelData.status === "waiting_for_approval"
                                onClicked: orion.cancelBackgroundJob(modelData.id)
                            }
                        }
                    }
                }
            }
        }
    }
}
