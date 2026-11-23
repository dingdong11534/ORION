import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root

    visible: true
    width: 1100
    height: 720
    minimumWidth: 800
    minimumHeight: 550

    title: "ORION"
    color: "#F4F1E9"

    readonly property bool busy: (typeof orion !== "undefined" && orion !== null) ? orion.isBusy : false

    ListModel {
        id: chatModel

        ListElement {
            role: "assistant"
            text: "Hello. I'm ORION. Your desktop interface and Python core are connected.\n(Simulated mode: local AI model not connected yet)"
            isError: false
        }
    }

    function sendMessage() {
        const message = composer.text.trim()

        if (message.length === 0 || root.busy)
            return

        chatModel.append({
            role: "user",
            text: message,
            isError: false
        })

        composer.clear()
        composer.forceActiveFocus()
        orion.sendMessage(message)

        Qt.callLater(function() {
            chatList.positionViewAtEnd()
        })
    }

    Connections {
        target: orion

        function onResponseReady(replyText) {
            chatModel.append({
                role: "assistant",
                text: replyText,
                isError: false
            })

            Qt.callLater(function() {
                chatList.positionViewAtEnd()
            })
        }

        function onErrorOccurred(errorMessage) {
            chatModel.append({
                role: "assistant",
                text: "Error: " + errorMessage,
                isError: true
            })

            Qt.callLater(function() {
                chatList.positionViewAtEnd()
            })
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 18

        // Sidebar
        Rectangle {
            Layout.preferredWidth: 235
            Layout.fillHeight: true

            radius: 22
            color: "#E8E5DA"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 22
                spacing: 18

                RowLayout {
                    spacing: 12

                    Rectangle {
                        Layout.preferredWidth: 38
                        Layout.preferredHeight: 38
                        radius: 12
                        color: "#526D5B"

                        Text {
                            anchors.centerIn: parent
                            text: "O"
                            color: "white"
                            font.pixelSize: 23
                            font.bold: true
                        }
                    }

                    ColumnLayout {
                        spacing: 0

                        Text {
                            text: "ORION"
                            font.pixelSize: 19
                            font.bold: true
                            color: "#303A32"
                        }

                        Text {
                            text: "PERSONAL ASSISTANT"
                            font.pixelSize: 9
                            font.letterSpacing: 1.2
                            color: "#74796E"
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    height: 1
                    color: "#D3D0C5"
                }

                Text {
                    text: "WORKSPACE"
                    font.pixelSize: 10
                    font.bold: true
                    font.letterSpacing: 1.5
                    color: "#85877C"
                }

                Rectangle {
                    Layout.fillWidth: true
                    height: 42
                    radius: 11
                    color: "#D9E0D5"

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 13
                        spacing: 11

                        Text {
                            text: "◉"
                            color: "#526D5B"
                            font.pixelSize: 17
                        }

                        Text {
                            text: "Assistant"
                            color: "#394B3E"
                            font.pixelSize: 14
                            font.bold: true
                        }
                    }
                }

                Text {
                    text: "○   Memory"
                    color: "#696E65"
                    font.pixelSize: 14
                }

                Text {
                    text: "○   Activity"
                    color: "#696E65"
                    font.pixelSize: 14
                }

                Text {
                    text: "○   Projects"
                    color: "#696E65"
                    font.pixelSize: 14
                }

                Item {
                    Layout.fillHeight: true
                }

                Rectangle {
                    Layout.fillWidth: true
                    height: 1
                    color: "#D3D0C5"
                }

                Text {
                    text: "SYSTEM STATUS"
                    font.pixelSize: 10
                    font.bold: true
                    font.letterSpacing: 1.2
                    color: "#85877C"
                }

                RowLayout {
                    spacing: 8

                    Rectangle {
                        width: 8
                        height: 8
                        radius: 4
                        color: root.busy ? "#D48B38" : "#77936F"
                    }

                    Text {
                        text: root.busy ? "Processing request..." : "Desktop core online"
                        color: root.busy ? "#8A5B28" : "#53654F"
                        font.pixelSize: 12
                    }
                }

                Text {
                    text: root.busy ? "Model: Generating (Simulated)..." : "Model: Simulated (FakeModel)"
                    color: "#85877C"
                    font.pixelSize: 11
                }
            }
        }

        // Main workspace
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true

            radius: 22
            color: "#FCFBF8"
            border.width: 1
            border.color: "#EAE7DF"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 26
                spacing: 18

                // Header
                RowLayout {
                    Layout.fillWidth: true

                    ColumnLayout {
                        spacing: 4

                        Text {
                            text: "Assistant"
                            font.pixelSize: 25
                            font.bold: true
                            color: "#303A32"
                        }

                        Text {
                            text: "Your personal workspace"
                            font.pixelSize: 13
                            color: "#898B80"
                        }
                    }

                    Item {
                        Layout.fillWidth: true
                    }

                    Rectangle {
                        width: root.busy ? 118 : 105
                        height: 30
                        radius: 15
                        color: root.busy ? "#F6EEDD" : "#E6ECE3"

                        Text {
                            anchors.centerIn: parent
                            text: root.busy ? "THINKING..." : "CORE ONLINE"
                            font.pixelSize: 10
                            font.bold: true
                            color: root.busy ? "#8A5B28" : "#526D5B"
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    height: 1
                    color: "#ECEAE3"
                }

                // Conversation
                ListView {
                    id: chatList

                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    clip: true
                    spacing: 12
                    model: chatModel

                    delegate: Item {
                        width: chatList.width
                        height: bubble.height + 8

                        Row {
                            width: parent.width
                            layoutDirection: model.role === "user"
                                             ? Qt.RightToLeft
                                             : Qt.LeftToRight

                            Rectangle {
                                id: bubble

                                width: Math.min(
                                    chatList.width * 0.78,
                                    Math.max(120, bubbleText.implicitWidth + 34)
                                )

                                height: bubbleText.implicitHeight + 24
                                radius: 15

                                color: model.isError
                                       ? "#FCE8E6"
                                       : (model.role === "user" ? "#E4EAE1" : "#F0EEE8")
                                border.width: model.isError ? 1 : 0
                                border.color: model.isError ? "#E5A9A4" : "transparent"

                                Text {
                                    id: bubbleText

                                    x: 17
                                    y: 12
                                    width: Math.min(implicitWidth, chatList.width * 0.78 - 34)

                                    text: model.text
                                    wrapMode: Text.Wrap

                                    color: model.isError ? "#9C2B27" : "#384139"
                                    font.pixelSize: 14
                                    lineHeight: 1.35
                                }
                            }
                        }
                    }

                    ScrollBar.vertical: ScrollBar {}
                }

                // Message composer
                Rectangle {
                    Layout.fillWidth: true
                    height: 62

                    radius: 16
                    color: root.busy ? "#EBE9E3" : "#F2F0EA"
                    border.width: 1
                    border.color: "#E6E3D9"

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 15
                        anchors.rightMargin: 9
                        spacing: 10

                        TextField {
                            id: composer

                            Layout.fillWidth: true
                            enabled: !root.busy

                            placeholderText: root.busy ? "ORION is generating a response..." : "Message ORION..."
                            placeholderTextColor: "#999B90"

                            color: "#303A32"
                            font.pixelSize: 14

                            background: Rectangle {
                                color: "transparent"
                            }

                            selectByMouse: true

                            onAccepted: root.sendMessage()
                        }

                        Button {
                            id: sendButton

                            enabled: !root.busy && composer.text.trim().length > 0
                            text: root.busy ? "Wait..." : "Send"
                            implicitWidth: 76
                            implicitHeight: 42

                            onClicked: root.sendMessage()

                            background: Rectangle {
                                radius: 12
                                color: !sendButton.enabled
                                       ? "#A4B0A7"
                                       : (sendButton.down ? "#3C5547" : "#526D5B")
                            }

                            contentItem: Text {
                                text: sendButton.text
                                color: "white"
                                font.pixelSize: 13
                                font.bold: true
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: "ORION is running locally in simulated mode. Local AI model integration is the next milestone."
                    color: "#A0A094"
                    font.pixelSize: 10
                    horizontalAlignment: Text.AlignHCenter
                }
            }
        }
    }
}
