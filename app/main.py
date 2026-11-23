import sys
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine


class OrionBridge(QObject):
    responseReady = Signal(str)

    @Slot(str)
    def send(self, message: str) -> None:
        message = message.strip()

        if not message:
            return

        # Temporary response until the Ollama adapter is implemented.
        self.responseReady.emit(
            "Message received by the ORION Python core. "
            "Local model integration is our next milestone."
        )


def main() -> int:
    app = QGuiApplication(sys.argv)
    app.setApplicationName("ORION")
    app.setOrganizationName("ORION")

    engine = QQmlApplicationEngine()
    bridge = OrionBridge()

    engine.rootContext().setContextProperty("orion", bridge)

    qml_file = Path(__file__).parent / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_file.resolve())))

    if not engine.rootObjects():
        print("ERROR: Failed to load ORION interface.")
        return 1

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
