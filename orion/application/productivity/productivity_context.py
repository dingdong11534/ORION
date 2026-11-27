"""Per-conversation productivity context for unambiguous follow-up resolution."""

from dataclasses import dataclass, field
from typing import Protocol

CONTEXT_TURN_EXPIRY = 5
MAX_LIST_IDS = 10


@dataclass
class ProductivityConversationContext:
    """Tracks the most recent unambiguous task/habit/list discussed in a conversation."""

    last_entity_type: str | None = None  # "task" | "habit"
    last_entity_id: str | None = None
    last_entity_label: str | None = None
    last_task_id: str | None = None
    last_habit_id: str | None = None
    last_list_ids: list[str] = field(default_factory=list)
    last_list_type: str | None = None
    user_turn_count: int = 0
    last_task_turn: int | None = None
    last_habit_turn: int | None = None
    last_list_turn: int | None = None
    pending_confirmation_action_id: str | None = None


class ProductivityContextPort(Protocol):
    """Port for per-conversation productivity context (in-memory or SQLite-backed)."""

    def get(self, conversation_id: str) -> ProductivityConversationContext: ...

    def advance_user_turn(self, conversation_id: str) -> int: ...

    def set_last_entity(
        self, conversation_id: str, entity_type: str, entity_id: str, entity_label: str
    ) -> None: ...

    def set_last_list(
        self, conversation_id: str, entity_type: str, entity_ids: list[str]
    ) -> None: ...

    def resolve_reference(
        self,
        conversation_id: str,
        entity_type: str,
        *,
        ordinal_index: int | None = None,
    ) -> str | None: ...

    def set_pending_confirmation(self, conversation_id: str, action_id: str | None) -> None: ...


class ProductivityContextStore:
    """In-memory per-conversation context registry (keyed by conversation_id)."""

    def __init__(self) -> None:
        self._by_conv: dict[str, ProductivityConversationContext] = {}

    def get(self, conversation_id: str) -> ProductivityConversationContext:
        if conversation_id not in self._by_conv:
            self._by_conv[conversation_id] = ProductivityConversationContext()
        return self._by_conv[conversation_id]

    def advance_user_turn(self, conversation_id: str) -> int:
        ctx = self.get(conversation_id)
        ctx.user_turn_count += 1
        return ctx.user_turn_count

    def set_last_entity(
        self, conversation_id: str, entity_type: str, entity_id: str, entity_label: str
    ) -> None:
        ctx = self.get(conversation_id)
        ctx.last_entity_type = entity_type
        ctx.last_entity_id = entity_id
        ctx.last_entity_label = entity_label
        if entity_type == "task":
            ctx.last_task_id = entity_id
            ctx.last_task_turn = ctx.user_turn_count
        elif entity_type == "habit":
            ctx.last_habit_id = entity_id
            ctx.last_habit_turn = ctx.user_turn_count

    def set_last_list(self, conversation_id: str, entity_type: str, entity_ids: list[str]) -> None:
        ctx = self.get(conversation_id)
        ctx.last_list_ids = list(entity_ids[:MAX_LIST_IDS])
        ctx.last_list_type = entity_type
        ctx.last_list_turn = ctx.user_turn_count

    def resolve_reference(
        self,
        conversation_id: str,
        entity_type: str,
        *,
        ordinal_index: int | None = None,
    ) -> str | None:
        ctx = self.get(conversation_id)
        if ordinal_index is not None:
            if (
                ctx.last_list_type == entity_type
                and ctx.last_list_turn is not None
                and (ctx.user_turn_count - ctx.last_list_turn) <= CONTEXT_TURN_EXPIRY
                and 1 <= ordinal_index <= len(ctx.last_list_ids)
            ):
                return ctx.last_list_ids[ordinal_index - 1]
            return None
        if entity_type == "task":
            if (
                ctx.last_task_id
                and ctx.last_task_turn is not None
                and (ctx.user_turn_count - ctx.last_task_turn) <= CONTEXT_TURN_EXPIRY
            ):
                return ctx.last_task_id
            return None
        if entity_type == "habit":
            if (
                ctx.last_habit_id
                and ctx.last_habit_turn is not None
                and (ctx.user_turn_count - ctx.last_habit_turn) <= CONTEXT_TURN_EXPIRY
            ):
                return ctx.last_habit_id
            return None
        return None

    def set_pending_confirmation(self, conversation_id: str, action_id: str | None) -> None:
        ctx = self.get(conversation_id)
        ctx.pending_confirmation_action_id = action_id
