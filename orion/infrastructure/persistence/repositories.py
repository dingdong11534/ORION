"""Concrete SQLAlchemy repository implementations for ORION.

Design rules
------------
- One Session per public operation; created and closed inside each method.
- One short transaction per write operation (open → mutate → commit → close).
- No transaction is ever held open during model generation.
- No Session object is stored on a repository or exposed to callers.
- No ORM object is returned to the caller; all returns are immutable Pydantic models.
- Sessionmaker (factory) is shared across threads; individual Sessions are not.

SQLite sequence allocation
--------------------------
SQLite 3.46.1 supports `UPDATE … RETURNING`. We use:

    UPDATE conversations
    SET next_sequence = next_sequence + 2
    WHERE id = :cid
    RETURNING next_sequence

This atomically increments next_sequence by 2 and returns the **new** value.
The old value (first sequence number for the user message) is new_seq - 2.
Concurrent requests always read distinct old values because each UPDATE holds
a write lock for the duration of that statement.

Retry on SQLITE_BUSY
--------------------
When a write operation fails with OperationalError("database is locked"),
we retry up to _MAX_RETRIES times with exponential back-off.  The retry
only occurs *before* any write has been committed; it is never triggered
after a successful commit, so duplicates cannot be introduced.
"""

import json
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from orion.application.contracts import (
    ActivityEvent,
    ConversationSummary,
    MessageStatus,
    PersistedMessage,
    RequestRecord,
    RequestStartResult,
)
from orion.application.ports import (
    ConversationNotFoundError,
    InvalidStateTransitionError,
    PersistenceError,
    PersistenceIntegrityError,
    RequestNotFoundError,
)
from orion.infrastructure.persistence.models import (
    ActivityEventModel,
    ConversationModel,
    MessageModel,
    UserSettingModel,
    generate_conversation_id,
    generate_event_id,
)
from orion.infrastructure.persistence.types import utc_now

log = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_SLEEP = 0.05  # 50 ms initial back-off

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def _msg_to_contract(m: MessageModel) -> PersistedMessage:
    return PersistedMessage(
        id=m.id,
        conversation_id=m.conversation_id,
        client_request_id=m.client_request_id,
        sequence_number=m.sequence_number,
        role=m.role,  # type: ignore[arg-type]
        status=MessageStatus(m.status),
        content=m.content,
        error_message=m.error_message,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def _conv_to_summary(c: ConversationModel) -> ConversationSummary:
    return ConversationSummary(
        id=c.id,
        title=c.title,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _evt_to_contract(e: ActivityEventModel) -> ActivityEvent:
    payload = e.payload_json if isinstance(e.payload_json, dict) else {}
    return ActivityEvent(
        id=e.id,
        event_type=e.event_type,
        aggregate_type=e.aggregate_type,
        aggregate_id=e.aggregate_id,
        payload=payload,
        created_at=e.created_at,
    )


# ---------------------------------------------------------------------------
# Event factory helpers — payloads contain metadata only, not message content
# ---------------------------------------------------------------------------


def _evt_conversation_created(conv_id: str, title: str) -> ActivityEventModel:
    return ActivityEventModel(
        id=generate_event_id(),
        event_type="conversation.created",
        aggregate_type="conversation",
        aggregate_id=conv_id,
        payload_json={"title": title},
    )


def _evt_conversation_deleted(conv_id: str, title: str) -> ActivityEventModel:
    return ActivityEventModel(
        id=generate_event_id(),
        event_type="conversation.deleted",
        aggregate_type="conversation",
        aggregate_id=conv_id,
        payload_json={"title": title},
    )


def _evt_message_created(msg: MessageModel) -> ActivityEventModel:
    return ActivityEventModel(
        id=generate_event_id(),
        event_type="message.created",
        aggregate_type="message",
        aggregate_id=msg.id,
        payload_json={
            "conversation_id": msg.conversation_id,
            "role": msg.role,
            "status": msg.status,
            "sequence_number": msg.sequence_number,
        },
    )


def _evt_response_completed(msg: MessageModel) -> ActivityEventModel:
    return ActivityEventModel(
        id=generate_event_id(),
        event_type="assistant.response.completed",
        aggregate_type="message",
        aggregate_id=msg.id,
        payload_json={
            "conversation_id": msg.conversation_id,
            "sequence_number": msg.sequence_number,
        },
    )


def _evt_response_failed(msg: MessageModel) -> ActivityEventModel:
    return ActivityEventModel(
        id=generate_event_id(),
        event_type="assistant.response.failed",
        aggregate_type="message",
        aggregate_id=msg.id,
        payload_json={
            "conversation_id": msg.conversation_id,
            "sequence_number": msg.sequence_number,
        },
    )


def _evt_response_interrupted(msg: MessageModel) -> ActivityEventModel:
    return ActivityEventModel(
        id=generate_event_id(),
        event_type="assistant.response.interrupted",
        aggregate_type="message",
        aggregate_id=msg.id,
        payload_json={
            "conversation_id": msg.conversation_id,
            "sequence_number": msg.sequence_number,
        },
    )


def _evt_settings_updated(key: str) -> ActivityEventModel:
    return ActivityEventModel(
        id=generate_event_id(),
        event_type="settings.updated",
        aggregate_type="settings",
        aggregate_id=None,
        payload_json={"key": key},
    )


# ---------------------------------------------------------------------------
# Retry wrapper for SQLITE_BUSY
# ---------------------------------------------------------------------------


def _is_sqlite_busy_or_locked(exc: OperationalError) -> bool:
    """Inspect OperationalError to determine if it is SQLITE_BUSY or SQLITE_LOCKED.

    Uses sqlite_errorcode and sqlite_errorname when available, falling back
    to string matching on DBAPI error messages.
    """
    orig = getattr(exc, "orig", None)
    if orig is not None:
        code = getattr(orig, "sqlite_errorcode", None)
        if isinstance(code, int) and (code & 0xFF) in (5, 6):
            return True
        name = getattr(orig, "sqlite_errorname", "")
        if name and ("SQLITE_BUSY" in name or "SQLITE_LOCKED" in name):
            return True
    msg = str(exc).lower()
    return "database is locked" in msg or "database table is locked" in msg or "sqlite_busy" in msg


def _with_busy_retry[T](operation: Callable[[], T]) -> T:
    """Execute operation, retrying on SQLITE_BUSY/SQLITE_LOCKED up to _MAX_RETRIES times.

    The callable must be idempotent up to the point of a successful commit;
    we never retry after a commit. Unrelated OperationalErrors and IntegrityErrors
    are never retried and are re-raised immediately.
    """
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return operation()
        except OperationalError as exc:
            if _is_sqlite_busy_or_locked(exc) and attempt < _MAX_RETRIES:
                sleep_time = _RETRY_BASE_SLEEP * (2**attempt)
                log.warning(
                    "SQLite busy/locked (attempt %d/%d); retrying in %.3fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    sleep_time,
                )
                time.sleep(sleep_time)
                continue
            raise PersistenceError(f"Database operation failed: {exc}") from exc
        except IntegrityError as exc:
            raise PersistenceError(f"Database integrity error: {exc}") from exc


# ---------------------------------------------------------------------------
# SqliteConversationStore
# ---------------------------------------------------------------------------


class SqliteConversationStore:
    """Concrete implementation of ConversationStore backed by SQLite/SQLAlchemy."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def create_conversation(self, title: str = "New Conversation") -> ConversationSummary:
        """Create a new conversation."""
        title = title.strip()
        if not title:
            raise ValueError("Conversation title cannot be blank.")

        def _run() -> ConversationSummary:
            with self._factory() as session:
                conv = ConversationModel(
                    id=generate_conversation_id(),
                    title=title,
                )
                session.add(conv)
                evt = _evt_conversation_created(conv.id, title)
                session.add(evt)
                session.commit()
                session.refresh(conv)
                return _conv_to_summary(conv)

        return _with_busy_retry(_run)

    def get_conversation(self, conversation_id: str) -> ConversationSummary | None:
        """Return ConversationSummary for conversation_id, or None if absent."""

        def _run() -> ConversationSummary | None:
            with self._factory() as session:
                conv = session.get(ConversationModel, conversation_id)
                if conv is None:
                    return None
                return _conv_to_summary(conv)

        return _with_busy_retry(_run)

    def list_conversations(self, limit: int = 100) -> Sequence[ConversationSummary]:
        """Return conversations ordered by updated_at descending."""

        def _run() -> list[ConversationSummary]:
            with self._factory() as session:
                rows = (
                    session.execute(
                        select(ConversationModel)
                        .order_by(ConversationModel.updated_at.desc())
                        .limit(limit)
                    )
                    .scalars()
                    .all()
                )
                return [_conv_to_summary(c) for c in rows]

        return _with_busy_retry(_run)

    def delete_conversation(self, conversation_id: str) -> None:
        """Delete a conversation, cascade-delete messages, preserve activity events."""

        def _run() -> None:
            with self._factory() as session:
                conv = session.get(ConversationModel, conversation_id)
                if conv is None:
                    raise ConversationNotFoundError(f"Conversation {conversation_id!r} not found.")
                # Record deletion event BEFORE deleting (preserves conv metadata)
                evt = _evt_conversation_deleted(conv.id, conv.title)
                session.add(evt)

                # Clear active_conversation_id setting if it points here
                setting = session.get(UserSettingModel, "active_conversation_id")
                if setting is not None:
                    raw = setting.value_json
                    stored_id = raw if isinstance(raw, str) else None
                    if stored_id == conversation_id:
                        session.delete(setting)

                session.delete(conv)
                session.commit()

        return _with_busy_retry(_run)

    # ------------------------------------------------------------------
    # Request lifecycle
    # ------------------------------------------------------------------

    def begin_request(
        self,
        client_request_id: str,
        user_content: str,
        conversation_id: str | None = None,
    ) -> RequestStartResult:
        """Start a request turn, handling idempotency."""
        user_content = user_content.strip()
        if not user_content:
            raise ValueError("user_content cannot be blank.")
        client_request_id = client_request_id.strip()
        if not client_request_id:
            raise ValueError("client_request_id cannot be blank.")

        def _run() -> RequestStartResult:
            with self._factory() as session:
                # --- Idempotency check ---
                existing = self._find_request_messages(session, conversation_id, client_request_id)
                if existing is not None:
                    user_msg, asst_msg = existing
                    if user_msg is None or asst_msg is None:
                        raise PersistenceIntegrityError(
                            f"Request {client_request_id!r} is structurally incomplete "
                            f"(missing user or assistant message)."
                        )
                    if user_msg.content != user_content:
                        raise PersistenceIntegrityError(
                            f"Request {client_request_id!r} already exists with different user content."
                        )
                    return RequestStartResult(
                        created=False,
                        request=RequestRecord(
                            conversation_id=user_msg.conversation_id,
                            client_request_id=client_request_id,
                            user_message=_msg_to_contract(user_msg),
                            assistant_message=_msg_to_contract(asst_msg),
                        ),
                    )

                # --- Resolve / create conversation ---
                if conversation_id is None:
                    conv = ConversationModel(id=generate_conversation_id())
                    session.add(conv)
                    session.flush()  # assigns id, initialises next_sequence=0
                    conv_evt = _evt_conversation_created(conv.id, conv.title)
                    session.add(conv_evt)
                    cid = conv.id
                else:
                    cid = conversation_id
                    conv = session.get(ConversationModel, cid)
                    if conv is None:
                        raise ConversationNotFoundError(f"Conversation {cid!r} not found.")

                try:
                    now = utc_now()

                    # --- Allocate two sequence numbers atomically AND update conversation updated_at ---
                    result = session.execute(
                        update(ConversationModel)
                        .where(ConversationModel.id == cid)
                        .values(
                            next_sequence=ConversationModel.next_sequence + 2,
                            updated_at=now,
                        )
                        .returning(ConversationModel.next_sequence)
                    ).fetchone()
                    if result is None:
                        raise PersistenceError(
                            f"Failed to allocate sequence numbers for conversation {cid!r}."
                        )
                    new_seq: int = result[0]
                    user_seq = new_seq - 2  # first allocated slot
                    asst_seq = new_seq - 1  # second allocated slot

                    # --- Persist user message ---
                    user_msg_model = MessageModel(
                        conversation_id=cid,
                        sequence_number=user_seq,
                        client_request_id=client_request_id,
                        role="user",
                        status="completed",
                        content=user_content,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(user_msg_model)
                    session.flush()

                    # --- Persist pending assistant message ---
                    asst_msg_model = MessageModel(
                        conversation_id=cid,
                        sequence_number=asst_seq,
                        client_request_id=client_request_id,
                        role="assistant",
                        status="pending",
                        content=None,
                        error_message=None,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(asst_msg_model)
                    session.flush()

                    # --- Activity events ---
                    session.add(_evt_message_created(user_msg_model))
                    session.add(_evt_message_created(asst_msg_model))

                    session.commit()
                    session.refresh(user_msg_model)
                    session.refresh(asst_msg_model)

                    return RequestStartResult(
                        created=True,
                        request=RequestRecord(
                            conversation_id=cid,
                            client_request_id=client_request_id,
                            user_message=_msg_to_contract(user_msg_model),
                            assistant_message=_msg_to_contract(asst_msg_model),
                        ),
                    )
                except IntegrityError as exc:
                    session.rollback()
                    err_str = str(exc).lower()
                    if "client_request_id" in err_str or "unique" in err_str:
                        existing = self._find_request_messages(
                            session, conversation_id, client_request_id
                        )
                        if existing is not None:
                            user_m, asst_m = existing
                            if user_m is not None and asst_m is not None:
                                if user_m.content != user_content:
                                    raise PersistenceIntegrityError(
                                        f"Request {client_request_id!r} already exists with different user content."
                                    )
                                return RequestStartResult(
                                    created=False,
                                    request=RequestRecord(
                                        conversation_id=user_m.conversation_id,
                                        client_request_id=client_request_id,
                                        user_message=_msg_to_contract(user_m),
                                        assistant_message=_msg_to_contract(asst_m),
                                    ),
                                )
                    raise PersistenceError(f"Database integrity error: {exc}") from exc

        return _with_busy_retry(_run)

    def _find_request_messages(
        self,
        session: Session,
        conversation_id: str | None,
        client_request_id: str,
    ) -> tuple[MessageModel | None, MessageModel | None] | None:
        """Return (user_msg, asst_msg) if any message for this request exists, else None.

        If conversation_id is provided and the existing request belongs to a different
        conversation, raises PersistenceIntegrityError.
        """
        q = select(MessageModel).where(MessageModel.client_request_id == client_request_id)
        rows = session.execute(q).scalars().all()
        if not rows:
            return None

        # Check conversation affinity
        existing_conv_id = rows[0].conversation_id
        if conversation_id is not None and existing_conv_id != conversation_id:
            raise PersistenceIntegrityError(
                f"Request {client_request_id!r} already belongs to conversation "
                f"{existing_conv_id!r}, cannot be used with conversation {conversation_id!r}."
            )

        by_role: dict[str, MessageModel] = {}
        for r in rows:
            by_role[r.role] = r
        return by_role.get("user"), by_role.get("assistant")

    def get_request(
        self,
        conversation_id: str,
        client_request_id: str,
    ) -> RequestRecord | None:
        """Return RequestRecord(user_message, assistant_message) or None."""

        def _run() -> RequestRecord | None:
            with self._factory() as session:
                result = self._find_request_messages(session, conversation_id, client_request_id)
                if result is None:
                    return None
                user_m, asst_m = result
                if user_m is None or asst_m is None:
                    return None
                return RequestRecord(
                    conversation_id=user_m.conversation_id,
                    client_request_id=client_request_id,
                    user_message=_msg_to_contract(user_m),
                    assistant_message=_msg_to_contract(asst_m),
                )

        return _with_busy_retry(_run)

    def load_history(
        self,
        conversation_id: str,
        limit: int = 100,
    ) -> Sequence[PersistedMessage]:
        """Return the most recent messages in ascending sequence_number order."""

        def _run() -> list[PersistedMessage]:
            with self._factory() as session:
                # Subquery: take the N most-recent by sequence_number descending
                subq = (
                    select(MessageModel)
                    .where(MessageModel.conversation_id == conversation_id)
                    .order_by(MessageModel.sequence_number.desc())
                    .limit(limit)
                    .subquery()
                )
                rows = (
                    session.execute(
                        select(MessageModel)
                        .join(subq, MessageModel.id == subq.c.id)
                        .order_by(MessageModel.sequence_number.asc())
                    )
                    .scalars()
                    .all()
                )
                return [_msg_to_contract(m) for m in rows]

        return _with_busy_retry(_run)

    def complete_request(
        self,
        conversation_id: str,
        client_request_id: str,
        response_content: str,
    ) -> PersistedMessage:
        """Mark the assistant message completed. Returns updated PersistedMessage."""
        response_content = response_content.strip()
        if not response_content:
            raise ValueError("response_content cannot be blank.")

        def _run() -> PersistedMessage:
            with self._factory() as session:
                asst = self._get_assistant_message(session, conversation_id, client_request_id)
                if asst is None:
                    raise RequestNotFoundError(
                        f"Assistant message for request {client_request_id!r} not found."
                    )
                if asst.status == "completed":
                    # Idempotency: same content → return existing
                    if asst.content == response_content:
                        return _msg_to_contract(asst)
                    raise InvalidStateTransitionError(
                        "Assistant message already completed with different content."
                    )
                if asst.status != "pending":
                    raise InvalidStateTransitionError(
                        f"Cannot complete a message in state {asst.status!r}."
                    )
                now = utc_now()
                asst.status = "completed"
                asst.content = response_content
                asst.updated_at = now
                session.execute(
                    update(ConversationModel)
                    .where(ConversationModel.id == conversation_id)
                    .values(updated_at=now)
                )
                session.add(_evt_response_completed(asst))
                session.commit()
                session.refresh(asst)
                return _msg_to_contract(asst)

        return _with_busy_retry(_run)

    def fail_request(
        self,
        conversation_id: str,
        client_request_id: str,
        error_message: str,
        partial_content: str | None = None,
    ) -> PersistedMessage:
        """Mark the assistant message failed."""
        error_message = error_message.strip()
        if not error_message:
            raise ValueError("error_message cannot be blank.")

        def _run() -> PersistedMessage:
            with self._factory() as session:
                asst = self._get_assistant_message(session, conversation_id, client_request_id)
                if asst is None:
                    raise RequestNotFoundError(
                        f"Assistant message for request {client_request_id!r} not found."
                    )
                cleaned_partial = (
                    partial_content.strip() if partial_content and partial_content.strip() else None
                )
                if asst.status == "failed":
                    # Idempotency: same error and partial content -> return existing
                    if asst.error_message == error_message and asst.content == cleaned_partial:
                        return _msg_to_contract(asst)
                    raise InvalidStateTransitionError(
                        "Assistant message already failed with different error or content."
                    )
                if asst.status != "pending":
                    raise InvalidStateTransitionError(
                        f"Cannot fail a message in state {asst.status!r}."
                    )
                now = utc_now()
                asst.status = "failed"
                asst.error_message = error_message
                asst.content = cleaned_partial
                asst.updated_at = now
                session.execute(
                    update(ConversationModel)
                    .where(ConversationModel.id == conversation_id)
                    .values(updated_at=now)
                )
                session.add(_evt_response_failed(asst))
                session.commit()
                session.refresh(asst)
                return _msg_to_contract(asst)

        return _with_busy_retry(_run)

    def interrupt_request(
        self,
        conversation_id: str,
        client_request_id: str,
        partial_content: str | None = None,
        reason: str | None = None,
    ) -> PersistedMessage:
        """Mark the assistant message interrupted."""
        cleaned_partial = (
            partial_content.strip() if partial_content and partial_content.strip() else None
        )
        cleaned_reason = (
            reason.strip() if reason and reason.strip() else "Generation cancelled by user."
        )

        def _run() -> PersistedMessage:
            with self._factory() as session:
                asst = self._get_assistant_message(session, conversation_id, client_request_id)
                if asst is None:
                    raise RequestNotFoundError(
                        f"Assistant message for request {client_request_id!r} not found."
                    )
                if asst.status == "interrupted":
                    # Idempotency: same reason and partial content -> return existing
                    if asst.error_message == cleaned_reason and asst.content == cleaned_partial:
                        return _msg_to_contract(asst)
                    raise InvalidStateTransitionError(
                        "Assistant message already interrupted with different reason or content."
                    )
                if asst.status != "pending":
                    raise InvalidStateTransitionError(
                        f"Cannot interrupt a message in state {asst.status!r}."
                    )
                now = utc_now()
                asst.status = "interrupted"
                asst.error_message = cleaned_reason
                asst.content = cleaned_partial
                asst.updated_at = now
                session.execute(
                    update(ConversationModel)
                    .where(ConversationModel.id == conversation_id)
                    .values(updated_at=now)
                )
                session.add(_evt_response_interrupted(asst))
                session.commit()
                session.refresh(asst)
                return _msg_to_contract(asst)

        return _with_busy_retry(_run)

    def _get_assistant_message(
        self, session: Session, conversation_id: str, client_request_id: str
    ) -> MessageModel | None:
        return session.scalar(
            select(MessageModel).where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.client_request_id == client_request_id,
                MessageModel.role == "assistant",
            )
        )

    def load_activity(
        self,
        limit: int = 100,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
    ) -> Sequence[ActivityEvent]:
        """Return activity events ascending by created_at."""

        def _run() -> list[ActivityEvent]:
            with self._factory() as session:
                q = select(ActivityEventModel).order_by(ActivityEventModel.created_at.asc())
                if aggregate_type is not None:
                    q = q.where(ActivityEventModel.aggregate_type == aggregate_type)
                if aggregate_id is not None:
                    q = q.where(ActivityEventModel.aggregate_id == aggregate_id)
                q = q.limit(limit)
                rows = session.execute(q).scalars().all()
                return [_evt_to_contract(e) for e in rows]

        return _with_busy_retry(_run)


# ---------------------------------------------------------------------------
# SqliteSettingsStore
# ---------------------------------------------------------------------------


class SqliteSettingsStore:
    """Concrete implementation of SettingsStore backed by SQLite/SQLAlchemy."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    def get_setting(self, key: str) -> Any:
        """Return the Python value for key, or None if absent."""

        def _run() -> Any:
            with self._factory() as session:
                row = session.get(UserSettingModel, key)
                if row is None:
                    return None
                return row.value_json

        return _with_busy_retry(_run)

    def set_setting(self, key: str, value: Any) -> None:
        """Persist key=value. Value must be JSON-serializable."""
        key = key.strip()
        if not key:
            raise ValueError("Setting key cannot be blank.")
        # Validate JSON-serialisability before hitting the database
        try:
            json.dumps(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Setting value is not JSON-serializable: {exc}") from exc

        def _run() -> None:
            with self._factory() as session:
                row = session.get(UserSettingModel, key)
                if row is None:
                    row = UserSettingModel(key=key, value_json=value)
                    session.add(row)
                else:
                    row.value_json = value
                    row.updated_at = utc_now()
                session.add(_evt_settings_updated(key))
                session.commit()

        _with_busy_retry(_run)

    def delete_setting(self, key: str) -> None:
        """Remove a setting (no-op if absent)."""

        def _run() -> None:
            with self._factory() as session:
                row = session.get(UserSettingModel, key)
                if row is not None:
                    session.delete(row)
                    session.commit()

        _with_busy_retry(_run)
