"""Unit tests for OllamaModelProvider."""

import io
import json
import urllib.error
from typing import Self
from unittest.mock import MagicMock, patch

import pytest

from orion.application.contracts import ChatMessage
from orion.infrastructure.ollama_model_provider import (
    OllamaAPIError,
    OllamaConnectionError,
    OllamaModelNotFoundError,
    OllamaModelProvider,
    OllamaProviderError,
    OllamaResponseError,
    OllamaTimeoutError,
)


class MockHTTPResponse:
    """Mock HTTP response returned by urllib.request.urlopen."""

    def __init__(self, data: bytes, status: int = 200) -> None:
        self.data = data
        self.status = status

    def read(self) -> bytes:
        return self.data

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


# ---------------------------------------------------------------------------
# Successful inference & request structure
# ---------------------------------------------------------------------------


def test_successful_response():
    """Provider sends request and extracts response content correctly."""
    resp_payload = {
        "model": "qwen2.5:1.5b",
        "message": {"role": "assistant", "content": "Hello! How can I help you today?"},
        "done": True,
    }
    mock_resp = MockHTTPResponse(json.dumps(resp_payload).encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = OllamaModelProvider()
        result = provider.generate_response(prompt="Hello")

    assert result == "Hello! How can I help you today?"


def test_correct_endpoint_and_http_method():
    """Provider uses POST /api/chat and sets correct headers."""
    resp_payload = {
        "message": {"role": "assistant", "content": "Response"},
    }
    mock_resp = MockHTTPResponse(json.dumps(resp_payload).encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        provider = OllamaModelProvider(base_url="http://10.0.0.5:11434/")
        provider.generate_response(prompt="Test")

    assert mock_urlopen.call_count == 1
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "http://10.0.0.5:11434/api/chat"
    assert req.get_method() == "POST"
    assert "application/json" in req.headers["Content-type"]


def test_correct_model_name():
    """Configured model name is sent in request payload."""
    resp_payload = {"message": {"role": "assistant", "content": "Done"}}
    mock_resp = MockHTTPResponse(json.dumps(resp_payload).encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        provider = OllamaModelProvider(model_name="qwen3:1.7b")
        provider.generate_response(prompt="Test model name")

    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["model"] == "qwen3:1.7b"


def test_correct_history_ordering():
    """Chat messages in history are preserved in exact chronological order."""
    resp_payload = {"message": {"role": "assistant", "content": "Final reply"}}
    mock_resp = MockHTTPResponse(json.dumps(resp_payload).encode("utf-8"))

    history = [
        ChatMessage(role="user", content="Turn 1 User"),
        ChatMessage(role="assistant", content="Turn 1 Assistant"),
        ChatMessage(role="user", content="Turn 2 User"),
        ChatMessage(role="assistant", content="Turn 2 Assistant"),
    ]

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        provider = OllamaModelProvider()
        provider.generate_response(prompt="Turn 3 User", history=history)

    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data.decode("utf-8"))
    messages = payload["messages"]

    assert len(messages) == 5
    assert messages[0] == {"role": "user", "content": "Turn 1 User"}
    assert messages[1] == {"role": "assistant", "content": "Turn 1 Assistant"}
    assert messages[2] == {"role": "user", "content": "Turn 2 User"}
    assert messages[3] == {"role": "assistant", "content": "Turn 2 Assistant"}
    assert messages[4] == {"role": "user", "content": "Turn 3 User"}


def test_current_prompt_included_exactly_once():
    """When history is empty, messages contains only the prompt."""
    resp_payload = {"message": {"role": "assistant", "content": "Single reply"}}
    mock_resp = MockHTTPResponse(json.dumps(resp_payload).encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        provider = OllamaModelProvider()
        provider.generate_response(prompt="Single prompt", history=None)

    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data.decode("utf-8"))
    assert len(payload["messages"]) == 1
    assert payload["messages"][0] == {"role": "user", "content": "Single prompt"}


def test_correct_non_streaming_payload():
    """stream is set to False and think is set to False by default in payload."""
    resp_payload = {"message": {"role": "assistant", "content": "Non-streamed"}}
    mock_resp = MockHTTPResponse(json.dumps(resp_payload).encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        provider = OllamaModelProvider()
        provider.generate_response(prompt="Query")

    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["keep_alive"] == "30s"


def test_default_think_parameter_is_false():
    """Default provider configuration sends think=False."""
    resp_payload = {"message": {"role": "assistant", "content": "Default think"}}
    mock_resp = MockHTTPResponse(json.dumps(resp_payload).encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        provider = OllamaModelProvider()
        assert provider.think is False
        provider.generate_response(prompt="Test think default")

    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["think"] is False


def test_explicit_think_parameter_true():
    """Explicit think=True sends think: true in the request payload."""
    resp_payload = {"message": {"role": "assistant", "content": "Thinking response"}}
    mock_resp = MockHTTPResponse(json.dumps(resp_payload).encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        provider = OllamaModelProvider(think=True)
        assert provider.think is True
        provider.generate_response(prompt="Think enabled")

    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["think"] is True


def test_qwen25_provider_remains_compatible():
    """Qwen2.5 model provider continues to function with default think=False."""
    resp_payload = {"message": {"role": "assistant", "content": "Qwen2.5 reply"}}
    mock_resp = MockHTTPResponse(json.dumps(resp_payload).encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        provider = OllamaModelProvider(model_name="qwen2.5:1.5b")
        reply = provider.generate_response(prompt="2 + 2")

    assert reply == "Qwen2.5 reply"
    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["model"] == "qwen2.5:1.5b"
    assert payload["think"] is False


def test_correct_context_and_prediction_limits():
    """Configured num_ctx and num_predict options are included in payload."""
    resp_payload = {"message": {"role": "assistant", "content": "Bounded"}}
    mock_resp = MockHTTPResponse(json.dumps(resp_payload).encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        provider = OllamaModelProvider(num_ctx=4096, num_predict=512)
        provider.generate_response(prompt="Options query")

    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["options"]["num_ctx"] == 4096
    assert payload["options"]["num_predict"] == 512


def test_unicode_prompt_and_response_handling():
    """UTF-8 unicode characters in prompt and response are handled without corruption."""
    unicode_prompt = "こんにちは世界！ 🚀 ñañó café"
    unicode_reply = "こんにちは！ ✨ 了解しました。"

    resp_payload = {"message": {"role": "assistant", "content": unicode_reply}}
    mock_resp = MockHTTPResponse(json.dumps(resp_payload).encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        provider = OllamaModelProvider()
        reply = provider.generate_response(prompt=unicode_prompt)

    assert reply == unicode_reply
    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["messages"][0]["content"] == unicode_prompt


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


def test_connection_refused():
    """Connection refused raises OllamaConnectionError with helpful guidance."""
    exc = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    with patch("urllib.request.urlopen", side_effect=exc):
        provider = OllamaModelProvider(base_url="http://127.0.0.1:11434")
        with pytest.raises(OllamaConnectionError) as exc_info:
            provider.generate_response(prompt="Hello")

    assert "Unable to connect" in str(exc_info.value)
    assert "ollama serve" in str(exc_info.value)
    assert isinstance(exc_info.value, OllamaProviderError)


def test_general_network_failure():
    """Unreachable host or general network error raises OllamaConnectionError."""
    exc = urllib.error.URLError("No route to host")

    with patch("urllib.request.urlopen", side_effect=exc):
        provider = OllamaModelProvider()
        with pytest.raises(OllamaConnectionError) as exc_info:
            provider.generate_response(prompt="Hello")

    assert "Network failure" in str(exc_info.value)


def test_timeout():
    """Socket timeout raises OllamaTimeoutError."""
    exc = urllib.error.URLError(TimeoutError("timed out"))

    with patch("urllib.request.urlopen", side_effect=exc):
        provider = OllamaModelProvider(timeout=10.0)
        with pytest.raises(OllamaTimeoutError) as exc_info:
            provider.generate_response(prompt="Long query")

    assert "timed out after 10.0s" in str(exc_info.value)


def test_direct_timeout_error():
    """Direct TimeoutError from urlopen raises OllamaTimeoutError."""
    with patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out")):
        provider = OllamaModelProvider(timeout=5.0)
        with pytest.raises(OllamaTimeoutError) as exc_info:
            provider.generate_response(prompt="Slow model")

    assert "timed out after 5.0s" in str(exc_info.value)


def test_model_not_found_http_response():
    """HTTP 404 with model-not-found body raises OllamaModelNotFoundError."""
    err_body = b'{"error":"model \'nonexistent:latest\' not found, try pulling it first"}'
    fp = io.BytesIO(err_body)
    http_err = urllib.error.HTTPError(
        url="http://127.0.0.1:11434/api/chat",
        code=404,
        msg="Not Found",
        hdrs=MagicMock(),
        fp=fp,
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        provider = OllamaModelProvider(model_name="nonexistent:latest")
        with pytest.raises(OllamaModelNotFoundError) as exc_info:
            provider.generate_response(prompt="Hello")

    assert "not found on Ollama server" in str(exc_info.value)
    assert "ollama pull" in str(exc_info.value)


def test_http_404_other_resource():
    """HTTP 404 for an unrelated path raises OllamaAPIError, not OllamaModelNotFoundError."""
    err_body = b'{"error":"404 page not found"}'
    fp = io.BytesIO(err_body)
    http_err = urllib.error.HTTPError(
        url="http://127.0.0.1:11434/api/wrong",
        code=404,
        msg="Not Found",
        hdrs=MagicMock(),
        fp=fp,
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        provider = OllamaModelProvider()
        with pytest.raises(OllamaAPIError) as exc_info:
            provider.generate_response(prompt="Hello")

    assert exc_info.value.status_code == 404
    assert not isinstance(exc_info.value, OllamaModelNotFoundError)


def test_other_http_error():
    """HTTP 500 or other status codes raise OllamaAPIError with status code."""
    err_body = b'{"error":"internal server error: CUDA out of memory"}'
    fp = io.BytesIO(err_body)
    http_err = urllib.error.HTTPError(
        url="http://127.0.0.1:11434/api/chat",
        code=500,
        msg="Internal Server Error",
        hdrs=MagicMock(),
        fp=fp,
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        provider = OllamaModelProvider()
        with pytest.raises(OllamaAPIError) as exc_info:
            provider.generate_response(prompt="Hello")

    assert exc_info.value.status_code == 500
    assert "HTTP 500" in str(exc_info.value)
    assert "CUDA out of memory" in str(exc_info.value)


def test_malformed_json():
    """Non-JSON response raises OllamaResponseError."""
    mock_resp = MockHTTPResponse(b"<html>502 Bad Gateway</html>")

    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = OllamaModelProvider()
        with pytest.raises(OllamaResponseError) as exc_info:
            provider.generate_response(prompt="Hello")

    assert "Malformed JSON" in str(exc_info.value)


def test_missing_message_field():
    """Response missing message key raises OllamaResponseError."""
    mock_resp = MockHTTPResponse(b'{"done": true}')

    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = OllamaModelProvider()
        with pytest.raises(OllamaResponseError) as exc_info:
            provider.generate_response(prompt="Hello")

    assert "missing 'message' object" in str(exc_info.value)


def test_missing_content_field():
    """Response message missing content field raises OllamaResponseError."""
    mock_resp = MockHTTPResponse(b'{"message": {"role": "assistant"}}')

    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = OllamaModelProvider()
        with pytest.raises(OllamaResponseError) as exc_info:
            provider.generate_response(prompt="Hello")

    assert "missing 'content' field" in str(exc_info.value)


def test_empty_assistant_response():
    """Response message with empty or whitespace-only content raises OllamaResponseError."""
    mock_resp = MockHTTPResponse(b'{"message": {"role": "assistant", "content": "   "}}')

    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = OllamaModelProvider()
        with pytest.raises(OllamaResponseError) as exc_info:
            provider.generate_response(prompt="Hello")

    assert "empty assistant response" in str(exc_info.value)
