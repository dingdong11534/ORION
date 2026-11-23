"""Application entry point for ORION desktop assistant."""

import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from sqlalchemy import Engine, text

from orion.application.assistant_service import AssistantService
from orion.infrastructure.fake_model_provider import FakeModelProvider
from orion.infrastructure.persistence.database import (
    create_db_engine,
    create_session_factory,
    init_database,
)
from orion.infrastructure.persistence.paths import get_database_path
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)
from orion.ui.qt_bridge import OrionBridge

_EXPECTED_ALEMBIC_REVISION = "0002"


def check_database_migration(
    engine: Engine, expected_revision: str = _EXPECTED_ALEMBIC_REVISION
) -> None:
    """Verify that the database file exists and is migrated to expected revision.

    Does NOT auto-upgrade during normal startup. Raises RuntimeError if unmigrated.
    """
    db_file_str = engine.url.database
    db_file = Path(db_file_str) if db_file_str else None
    if db_file is None or not db_file.exists():
        raise RuntimeError(
            f"Database file not found at '{db_file_str}'.\n"
            "Please initialize and migrate the database using:\n"
            "  .venv/bin/alembic upgrade head"
        )

    with engine.connect() as conn:
        has_version_table = conn.execute(
            text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='alembic_version'")
        ).scalar()
        if not has_version_table:
            raise RuntimeError(
                f"Database at '{db_file_str}' has not been initialized with Alembic.\n"
                "Please run database migrations using:\n"
                "  .venv/bin/alembic upgrade head"
            )
        current_rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        if current_rev != expected_revision:
            raise RuntimeError(
                f"Database at '{db_file_str}' is at revision {current_rev!r}, "
                f"but ORION requires revision {expected_revision!r}.\n"
                "Please run:\n"
                "  .venv/bin/alembic upgrade head"
            )


def main() -> int:
    app = QGuiApplication(sys.argv)
    app.setApplicationName("ORION")
    app.setOrganizationName("ORION")

    # 1. Resolve production database path
    db_path = get_database_path()

    # 2. Create engine & initialize SQLite PRAGMAs
    engine = create_db_engine(db_path)
    init_database(engine, apply_wal=True)

    # 3. Verify that the required Alembic migration has been applied
    try:
        check_database_migration(engine)
    except RuntimeError as exc:
        print(f"ORION STARTUP ERROR:\n{exc}", file=sys.stderr)
        engine.dispose()
        return 1

    # 4. Create session factory
    session_factory = create_session_factory(engine)

    # 5. Construct repository stores
    conversation_store = SqliteConversationStore(session_factory)
    settings_store = SqliteSettingsStore(session_factory)

    # 6. Construct model provider & inject dependencies into AssistantService
    model_provider = FakeModelProvider(simulated_delay=0.15)
    assistant_service = AssistantService(
        model_provider=model_provider,
        conversation_store=conversation_store,
        settings_store=settings_store,
    )

    # 7. Construct OrionBridge
    bridge = OrionBridge(assistant_service=assistant_service)

    # 8. Coordinate safe shutdown: drain workers FIRST, then dispose database engine
    def on_shutdown() -> None:
        bridge.shutdown()
        engine.dispose()

    app.aboutToQuit.connect(on_shutdown)

    # 9. Load QML interface
    qml_engine = QQmlApplicationEngine()
    qml_engine.rootContext().setContextProperty("orion", bridge)

    qml_file = Path(__file__).parent / "ui" / "qml" / "Main.qml"
    qml_engine.load(QUrl.fromLocalFile(str(qml_file.resolve())))

    if not qml_engine.rootObjects():
        print("ERROR: Failed to load ORION interface.", file=sys.stderr)
        return 1

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
