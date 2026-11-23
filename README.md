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

## Current Status (Milestone 1)

Milestone 1 establishes the foundational layered architecture with a responsive QML interface, reactive `ListModel` conversation view, background worker execution, and a simulated model provider (`FakeModelProvider`). Local model (Ollama) and persistence (SQLite/Alembic) integration will follow in subsequent milestones.
