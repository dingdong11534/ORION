from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from orion.application.contracts import ChatMessage, ChatRequest, MessageRole


def test_valid_chat_message_creation():
    msg = ChatMessage(role=MessageRole.USER.value, content="Hello ORION")
    assert msg.id is not None
    assert len(msg.id) > 0
    assert msg.role == "user"
    assert msg.content == "Hello ORION"
    assert isinstance(msg.timestamp, datetime)
    assert msg.timestamp.tzinfo is not None
    assert msg.timestamp.tzinfo == UTC


def test_valid_chat_request():
    req = ChatRequest(content="  Valid query  ")
    assert req.content == "Valid query"


@pytest.mark.parametrize("blank_content", ["", "   ", "\t", "\n\n   \t"])
def test_chat_message_rejects_blank_content(blank_content: str):
    with pytest.raises(ValidationError) as exc_info:
        ChatMessage(role=MessageRole.USER.value, content=blank_content)
    assert "Message content cannot be blank" in str(exc_info.value)


@pytest.mark.parametrize("blank_content", ["", "   ", "\t", "\r\n"])
def test_chat_request_rejects_blank_content(blank_content: str):
    with pytest.raises(ValidationError) as exc_info:
        ChatRequest(content=blank_content)
    assert "Message content cannot be blank" in str(exc_info.value)


def test_chat_message_rejects_naive_timestamp():
    naive_dt = datetime(2026, 1, 1, 12, 0, 0)
    with pytest.raises(ValidationError) as exc_info:
        ChatMessage(
            role=MessageRole.ASSISTANT.value,
            content="Testing timestamp",
            timestamp=naive_dt,
        )
    assert "Timestamp must be timezone-aware" in str(exc_info.value)


def test_chat_message_is_immutable():
    msg = ChatMessage(role=MessageRole.USER.value, content="Immutable test")
    with pytest.raises(ValidationError):
        msg.content = "Changed content"  # type: ignore[misc]
