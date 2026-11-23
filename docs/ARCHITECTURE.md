# ORION — Initial Architecture

## Product direction

ORION is a local-first personal AI assistant.
The first target is a native Ubuntu desktop application.
Android is a later client, not part of the initial release.

## Architecture

Desktop UI
    |
Application services
    |
Domain logic and permission policy
    |
Adapters
    |-- SQLite database
    |-- Ollama local model
    |-- Agent Desktop CLI
    |-- Terminal process manager

The LLM does not directly execute operating-system actions.
Every action passes through an application service and policy check.

## Initial technology choices

- Python 3.12: application logic
- PySide6 and QML: native desktop interface
- Pydantic: validated data contracts
- SQLite: persistent local storage
- SQLAlchemy: database access
- Alembic: schema migrations
- Ollama: local model runtime
- Agent Desktop: desktop accessibility adapter
- subprocess and PTY: controlled terminal execution
- pytest: tests
- Ruff: linting

## Security boundaries

- The LLM cannot bypass the permission engine.
- File and web content is treated as untrusted input.
- Commands must pass deterministic policy checks.
- Destructive operations require explicit approval.
- No unrestricted shell=True execution.
- Desktop automation is exposed through a restricted adapter.
- Project-level configuration cannot grant permissions.

## Initial implementation scope

1. Native application window.
2. Application service layer.
3. Persistent SQLite database.
4. Local model adapter.
5. Read-only desktop observation.
6. Controlled terminal execution.
7. Conversation history and activity records.

## Deferred

- Android application
- Wake-word detection
- Floating desktop overlay
- Multi-agent orchestration
- Vector database
- Message broker
- Plugin marketplace
- Remote laptop access
