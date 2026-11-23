import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from orion.application.assistant_service import AssistantService
from orion.infrastructure.fake_model_provider import FakeModelProvider
from orion.ui.qt_bridge import OrionBridge


def main() -> int:
    app = QGuiApplication(sys.argv)
    app.setApplicationName("ORION")
    app.setOrganizationName("ORION")

    # Wire application dependencies
    model_provider = FakeModelProvider(simulated_delay=0.15)
    assistant_service = AssistantService(model_provider=model_provider)
    bridge = OrionBridge(assistant_service=assistant_service)

    # Coordinate safe shutdown when the application event loop is about to quit
    app.aboutToQuit.connect(bridge.shutdown)

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("orion", bridge)

    qml_file = Path(__file__).parent / "ui" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_file.resolve())))

    if not engine.rootObjects():
        print("ERROR: Failed to load ORION interface.")
        return 1

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
