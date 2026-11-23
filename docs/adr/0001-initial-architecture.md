# ADR 0001: Initial Architecture

Status: Accepted

## Decision

Build ORION as a modular, local-first desktop application.

Use a single Python application initially, with PySide6/QML for the
interface and separately defined adapters for external capabilities.

Keep permission enforcement deterministic and outside the language model.

Use SQLite and migrations for persistence. Use Ollama for local inference
and Agent Desktop as an external desktop accessibility adapter.

## Consequences

- Lower operational complexity for the first release.
- No separate backend server is needed initially.
- Components can later be exposed through a versioned API.
- External tools can be replaced without rewriting application logic.
- Operating-system actions remain subject to explicit policy checks.
