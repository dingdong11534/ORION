"""Per-conversation productivity context for unambiguous follow-up resolution."""

from dataclasses import dataclass


@dataclass
class ProductivityConversationContext:
    """Tracks the most recent unambiguous task/habit discussed in a conversation."""

    last_entity_type: str | None = None  # "task" | "habit"
    last_entity_id: str | None = None
    last_entity_label: str | None = None
    pending_confirmation_action_id: str | None = None


class ProductivityContextStore:
    """In-memory per-conversation context registry (keyed by conversation_id)."""

    def __init__(self) -> None:
        self._by_conv: dict[str, ProductivityConversationContext] = {}

    def get(self, conversation_id: str) -> ProductivityConversationContext:
        if conversation_id not in self._by_conv:
            self._by_conv[conversation_id] = ProductivityConversationContext()
        return self._by_conv[conversation_id]

    def set_last_entity(
        self, conversation_id: str, entity_type: str, entity_id: str, entity_label: str
    ) -> None:
        ctx = self.get(conversation_id)
        ctx.last_entity_type = entity_type
        ctx.last_entity_id = entity_id
        ctx.last_entity_label = entity_label

    def set_pending_confirmation(self, conversation_id: str, action_id: str | None) -> None:
        ctx = self.get(conversation_id)
        ctx.pending_confirmation_action_id = action_id
