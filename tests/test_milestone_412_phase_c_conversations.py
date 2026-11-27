"""Phase C tests: conversation lifecycle, deterministic titles, migration 0009, and composer."""

from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.application.contracts import derive_conversation_title
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)


def test_derive_conversation_title_deterministic_rules():
    assert derive_conversation_title("**Hello** `world`!") == "Hello world!"
    long_msg = (
        "Explain how **DBMS** normalization works in practice for large tables. And more details."
    )
    title = derive_conversation_title(long_msg)
    assert len(title) <= 51  # <= 48 + "..."
    assert title.endswith("...")
    assert "*" not in title
    assert "And more" not in title


def test_alembic_0009_backfills_titles_and_is_reversible(tmp_path: Path):
    db_path = tmp_path / "titles.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0008")

    engine = create_db_engine(db_path)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO conversations (id, title, next_sequence, created_at, updated_at) "
                "VALUES ('conv_with_msg', 'New Conversation', 2, '2026-11-27T00:00:00Z', '2026-11-27T00:00:00Z'), "
                "('conv_empty', 'New Conversation', 0, '2026-11-27T00:00:00Z', '2026-11-27T00:00:00Z')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO messages (id, conversation_id, sequence_number, client_request_id, role, status, content, created_at, updated_at) "
                "VALUES ('msg_1', 'conv_with_msg', 0, 'req_1', 'user', 'completed', '**Explain** B-Tree indexing in SQLite. Extra sentence here.', '2026-11-27T00:00:00Z', '2026-11-27T00:00:00Z')"
            )
        )
    engine.dispose()

    command.upgrade(cfg, "0009")
    engine = create_db_engine(db_path)
    with engine.connect() as conn:
        t1 = conn.execute(
            sa.text("SELECT title FROM conversations WHERE id='conv_with_msg'")
        ).scalar_one()
        t2 = conn.execute(
            sa.text("SELECT title FROM conversations WHERE id='conv_empty'")
        ).scalar_one()
        assert t1 == "Explain B-Tree indexing in SQLite."
        assert t2 == "New Conversation"
    engine.dispose()

    command.downgrade(cfg, "0008")
    command.upgrade(cfg, "0009")


def test_auto_title_and_custom_rename_preserved(tmp_path: Path):
    db_path = tmp_path / "conv.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0009")
    engine = create_db_engine(db_path)
    sf = create_session_factory(engine)
    store = SqliteConversationStore(sf)

    c1 = store.create_conversation("New Conversation")
    assert c1.title == "New Conversation"
    store.begin_request(
        client_request_id="r1",
        user_content="Summarize **Operating Systems** chapter 3.",
        conversation_id=c1.id,
    )
    updated = store.get_conversation(c1.id)
    assert updated is not None
    assert updated.title == "Summarize Operating Systems chapter 3."
    assert updated.title_is_custom is False

    renamed = store.rename_conversation(c1.id, "OS Study Notes")
    assert renamed.title == "OS Study Notes"
    assert renamed.title_is_custom is True

    # Subsequent messages never overwrite custom title
    store.complete_request(
        conversation_id=c1.id,
        client_request_id="r1",
        response_content="Here is the summary.",
    )
    store.begin_request(
        client_request_id="r2",
        user_content="Another completely different question.",
        conversation_id=c1.id,
    )
    final_c = store.get_conversation(c1.id)
    assert final_c is not None and final_c.title == "OS Study Notes"
    engine.dispose()


def test_bridge_new_chat_refocuses_empty_active_conversation(tmp_path: Path):
    from orion.ui.qt_bridge import OrionBridge

    class _StubProvider:
        def generate_response(self, prompt: str, history=None) -> str:
            return "ok"

        def supports_thinking(self) -> bool:
            return False

    db_path = tmp_path / "bridge.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0009")
    engine = create_db_engine(db_path)
    sf = create_session_factory(engine)
    store = SqliteConversationStore(sf)
    settings = SqliteSettingsStore(sf)
    asst = AssistantService(_StubProvider(), store, settings)
    c1 = asst.create_new_conversation("New Conversation")

    bridge = OrionBridge(asst)
    focused = []
    bridge.focusComposerRequested.connect(lambda: focused.append(True))

    bridge.createNewConversation()
    assert len(focused) == 1
    assert len(asst.list_conversations()) == 1
    assert asst.active_conversation_id == c1.id
    engine.dispose()


def test_qml_composer_guards_present():
    chat_qml = Path("orion/ui/qml/pages/ChatPage.qml").read_text(encoding="utf-8")
    main_qml = Path("orion/ui/qml/Main.qml").read_text(encoding="utf-8")
    assert "inputMethodComposing" in chat_qml
    assert "Keys.onEnterPressed" in chat_qml
    assert "root.isSubmitting" in chat_qml
    assert "sendMessageWithScopeAndRequestId" in main_qml
    assert "orion.createNewConversation()" in main_qml
