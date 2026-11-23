"""Custom SQLAlchemy types and time utilities for timezone-aware UTC persistence."""

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, Text, TypeDecorator
from sqlalchemy.engine.interfaces import Dialect


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime with microsecond precision."""
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """SQLAlchemy TypeDecorator storing timezone-aware datetimes as fixed-format ISO 8601 UTC strings.

    Format: YYYY-MM-DDTHH:MM:SS.ffffffZ (27 characters).
    Always serializes to UTC and restores timezone-aware datetime objects with datetime.UTC.
    Rejects naive datetimes to guarantee deterministic timestamps across threads and timezones.
    """

    impl = String(27)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None

        if not isinstance(value, datetime):
            raise TypeError(f"Expected datetime object, got {type(value).__name__}: {value!r}")

        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError(f"UtcDateTime requires timezone-aware datetime, got naive: {value!r}")

        # Normalize to UTC
        utc_dt = value.astimezone(UTC)
        return f"{utc_dt.strftime('%Y-%m-%dT%H:%M:%S')}.{utc_dt.microsecond:06d}Z"

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None

        if isinstance(value, datetime):
            if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)

        if not isinstance(value, str):
            raise TypeError(
                f"Expected string timestamp from database, got {type(value).__name__}: {value!r}"
            )

        val_to_parse = value
        if val_to_parse.endswith("Z"):
            val_to_parse = val_to_parse[:-1] + "+00:00"

        try:
            parsed = datetime.fromisoformat(val_to_parse)
        except ValueError as exc:
            raise ValueError(f"Invalid ISO 8601 timestamp stored in database: {value!r}") from exc

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


class JsonText(TypeDecorator[Any]):
    """SQLAlchemy TypeDecorator storing Python values as JSON text in a TEXT column.

    This is preferred over SQLAlchemy's JSON type for SQLite because it stores
    values as plain TEXT, making SQLite's json_valid() and json_type() CHECK
    constraints effective at the database level.

    - Serializes Python dicts, lists, and scalars to compact JSON strings.
    - Deserializes stored JSON text back to Python objects on load.
    - Rejects values that are not JSON-serializable on bind.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        try:
            return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"Value is not JSON-serializable: {value!r}") from exc

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"Invalid JSON stored in database: {value!r}") from exc
