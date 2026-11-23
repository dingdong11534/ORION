# ORION

ORION is a local-first personal desktop AI assistant built with Python 3.12, PySide6, and QML.

## System Requirements

- **Operating System**: Ubuntu 24.04+ LTS (tested on Ubuntu 26.04 LTS, GNOME Wayland)
- **Python**: 3.12 or higher
- **Memory**: 8 GB RAM target
- **Graphics**: Intel integrated graphics or equivalent

## Architecture Overview

ORION follows a decoupled, layered architecture:

- **`orion.ui`**: QML presentation layer and thread-safe Qt bridge (`OrionBridge`).
- **`orion.application`**: Core orchestration (`AssistantService`), immutable Pydantic contracts (`ChatMessage`, `ChatRequest`), and abstraction ports (`ModelProvider`). Free of GUI and OS dependencies.
- **`orion.infrastructure`**: Concurrency management (`Worker` / `WorkerSignals` on `QThreadPool`) and adapters such as `FakeModelProvider`.

## Setup and Installation

1. Create and activate a Python 3.12 virtual environment:
   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   ```

2. Install the package in editable mode with development dependencies:
   ```bash
   pip install -e ".[dev]"
   ```

## Running the Application

Launch the desktop interface:
```bash
python -m orion.main
```
Or via the entry point command:
```bash
orion
```

## Running Tests and Linting

Run test suite:
```bash
pytest
```

Run linter and formatting checks:
```bash
ruff check .
ruff format --check .
```

## Current Status

- **Milestone 1**: Foundational layered architecture with a responsive QML interface, reactive `ListModel` conversation view, background worker execution on dedicated `QThreadPool`, hardened worker lifecycle / shutdown safety, and a simulated model provider (`FakeModelProvider`).
- **Milestone 2.1 (Database Foundation)**: SQLite persistence foundation implemented under `orion.infrastructure.persistence`:
  - Path resolution conforming to Linux XDG Base Directory specification (`$XDG_DATA_HOME/orion/orion.db` or `~/.local/share/orion/orion.db`), with directory permissions `0700` and database file permissions `0600`.
  - Configurable database path override via `ORION_DB_PATH` environment variable.
  - Custom `UtcDateTime` SQLAlchemy TypeDecorator storing UTC ISO 8601 strings and returning timezone-aware `datetime` objects.
  - SQLite configuration enforcing PRAGMA foreign keys (`ON`), busy timeout (5000 ms), write-ahead logging (`journal_mode=WAL`), and `synchronous=NORMAL`.
  - SQLAlchemy ORM models: `ConversationModel`, `MessageModel`, `ActivityEventModel`, and `UserSettingModel` with strict check constraints and JSON validation.
  - Alembic migration environment and baseline migration `0001_initial_persistence`.
  - *Note*: Repository interfaces and integration of persistence into `AssistantService` and the UI workflow are scheduled for subsequent milestones (Milestone 2.2 and 2.3). The application currently runs with in-memory state and `FakeModelProvider`.

## Database Administration & Migrations

Alembic manages database schema migrations.

Apply database migrations to the latest revision:
```bash
alembic upgrade head
```

Inspect current migration status:
```bash
alembic current
```

Roll back one revision:
```bash
alembic downgrade -1
```

Override database path for development or testing:
```bash
export ORION_DB_PATH="/tmp/test_orion.db"
alembic upgrade head
```

Run persistence unit tests:
```bash
pytest tests/test_paths.py tests/test_types.py tests/test_database.py tests/test_models.py tests/test_migrations.py
```
