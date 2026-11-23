"""Persistence foundation package for ORION."""

from orion.infrastructure.persistence.database import (
    create_db_engine,
    create_session_factory,
    init_database,
)
from orion.infrastructure.persistence.models import (
    ActivityEventModel,
    Base,
    ConversationModel,
    MessageModel,
    UserSettingModel,
    generate_conversation_id,
    generate_event_id,
    generate_message_id,
    generate_request_id,
)
from orion.infrastructure.persistence.paths import (
    ORION_DB_ENV_VAR,
    ensure_secure_directory,
    ensure_secure_file,
    get_database_path,
)
from orion.infrastructure.persistence.types import JsonText, UtcDateTime, utc_now

__all__ = [
    "ORION_DB_ENV_VAR",
    "ActivityEventModel",
    "Base",
    "ConversationModel",
    "JsonText",
    "MessageModel",
    "UserSettingModel",
    "UtcDateTime",
    "create_db_engine",
    "create_session_factory",
    "ensure_secure_directory",
    "ensure_secure_file",
    "generate_conversation_id",
    "generate_event_id",
    "generate_message_id",
    "generate_request_id",
    "get_database_path",
    "init_database",
    "utc_now",
]
