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

    property var messages: [
        {
            role: "assistant",
            text: "Hello. I'm ORION. Your desktop interface and Python core are connected. The local AI model has not been connected yet."
        }
    ]

    function sendMessage() {
        const message = composer.text.trim()

        if (message.length === 0)
            return

        messages = messages.concat([
            { role: "user", text: message }
        ])

        composer.clear()
        orion.send(message)

        Qt.callLater(function() {
            chatList.positionViewAtEnd()
        })
    }

    Connections {
        target: orion

        function onResponseReady(replyText) {
            root.messages = root.messages.concat([
                { role: "assistant", text: replyText }
            ])

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
                        color: "#77936F"
                    }

                    Text {
                        text: "Desktop core online"
                        color: "#53654F"
                        font.pixelSize: 12
                    }
                }

                Text {
                    text: "Model: Not connected"
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
                        width: 105
                        height: 30
                        radius: 15
                        color: "#E6ECE3"

                        Text {
                            anchors.centerIn: parent
                            text: "CORE ONLINE"
                            font.pixelSize: 10
                            font.bold: true
                            color: "#526D5B"
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
                    model: root.messages

                    delegate: Item {
                        width: chatList.width
                        height: bubble.height + 8

                        Row {
                            width: parent.width
                            layoutDirection: modelData.role === "user"
                                             ? Qt.RightToLeft
                                             : Qt.LeftToRight

                            Rectangle {
                                id: bubble

                                width: Math.min(
                                    chatList.width * 0.78,
                                    Math.max(190, modelData.text.length * 7 + 36)
                                )

                                height: bubbleText.implicitHeight + 24
                                radius: 15

                                color: modelData.role === "user"
                                       ? "#E4EAE1"
                                       : "#F0EEE8"

                                Text {
                                    id: bubbleText

                                    x: 17
                                    y: 12
                                    width: bubble.width - 34

                                    text: modelData.text
                                    wrapMode: Text.WordWrap

                                    color: "#384139"
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
                    color: "#F2F0EA"
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

                            placeholderText: "Message ORION..."
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

                            text: "Send"
                            implicitWidth: 76
                            implicitHeight: 42

                            onClicked: root.sendMessage()

                            background: Rectangle {
                                radius: 12
                                color: sendButton.down
                                       ? "#3C5547"
                                       : "#526D5B"
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
                    text: "ORION is running locally. Model responses are not enabled yet."
                    color: "#A0A094"
                    font.pixelSize: 10
                    horizontalAlignment: Text.AlignHCenter
                }
            }
        }
    }
}
