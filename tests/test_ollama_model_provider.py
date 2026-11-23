"""Unit tests for OllamaModelProvider."""

import io
import json
import urllib.error
from typing import Self
from unittest.mock import MagicMock, patch

import pytest

from orion.application.contracts import CancellationToken, ChatMessage
from orion.infrastructure.ollama_model_provider import (
    OllamaAPIError,
    OllamaCancelledError,
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


# ---------------------------------------------------------------------------
# Streaming inference tests (Milestone 3.4)
# ---------------------------------------------------------------------------


class MockStreamingHTTPResponse:
    """Mock streaming HTTP response delivering newline-delimited JSON chunks."""

    def __init__(self, chunks: list[str | dict], status: int = 200) -> None:
        lines: list[bytes] = []
        for c in chunks:
            if isinstance(c, dict):
                lines.append(json.dumps(c).encode("utf-8") + b"\n")
            elif isinstance(c, str):
                lines.append(c.encode("utf-8") + b"\n")
            elif isinstance(c, bytes):
                lines.append(c + b"\n")
        self._lines = lines
        self.status = status
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def read(self) -> bytes:
        return b"".join(self._lines)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True


def test_stream_response_multiple_chunks_and_ordering():
    """Streaming yields chunks in exact incremental order."""
    chunks = [
        {"message": {"role": "assistant", "content": "Chunk 1: Hello"}, "done": False},
        {"message": {"role": "assistant", "content": " "}, "done": False},
        {"message": {"role": "assistant", "content": "Chunk 2: world!"}, "done": False},
        {"message": {"role": "assistant", "content": ""}, "done": False},  # Empty ignored
        {"message": {"role": "assistant", "content": None}, "done": False},
        {"done": True},
    ]
    mock_resp = MockStreamingHTTPResponse(chunks)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = OllamaModelProvider()
        stream = provider.stream_response(prompt="Say hello")
        results = list(stream)

    assert results == ["Chunk 1: Hello", " ", "Chunk 2: world!"]
    assert "".join(results) == "Chunk 1: Hello Chunk 2: world!"


def test_stream_response_payload_structure():
    """stream_response sends stream=True and think=False by default with keep_alive and options."""
    chunks = [
        {"message": {"role": "assistant", "content": "Output"}, "done": True},
    ]
    mock_resp = MockStreamingHTTPResponse(chunks)

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        provider = OllamaModelProvider(
            model_name="qwen3:1.7b",
            num_ctx=4096,
            num_predict=128,
            think=False,
        )
        list(provider.stream_response(prompt="Hello"))

    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["model"] == "qwen3:1.7b"
    assert payload["stream"] is True
    assert payload["think"] is False
    assert payload["keep_alive"] == "30s"
    assert payload["options"]["num_ctx"] == 4096
    assert payload["options"]["num_predict"] == 128


def test_stream_response_think_configurable():
    """stream_response respects think=True when explicitly set."""
    chunks = [
        {"message": {"role": "assistant", "content": "Final output"}, "done": True},
    ]
    mock_resp = MockStreamingHTTPResponse(chunks)

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        provider = OllamaModelProvider(think=True)
        list(provider.stream_response(prompt="Think deeply"))

    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["think"] is True


def test_stream_response_unicode_chunks():
    """stream_response handles multibyte UTF-8 characters across chunks."""
    chunks = [
        {"message": {"role": "assistant", "content": "🌟 Привет, "}, "done": False},
        {"message": {"role": "assistant", "content": "мир! 🌍 🚀"}, "done": True},
    ]
    mock_resp = MockStreamingHTTPResponse(chunks)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = OllamaModelProvider()
        results = list(provider.stream_response(prompt="Unicode test"))

    assert results == ["🌟 Привет, ", "мир! 🌍 🚀"]
    assert "".join(results) == "🌟 Привет, мир! 🌍 🚀"


def test_stream_response_completion_detection_and_premature_end():
    """Stream without done: true record raises OllamaResponseError."""
    chunks = [
        {"message": {"role": "assistant", "content": "Partial output"}, "done": False},
    ]
    mock_resp = MockStreamingHTTPResponse(chunks)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = OllamaModelProvider()
        stream = provider.stream_response(prompt="Incomplete test")
        with pytest.raises(OllamaResponseError) as exc_info:
            list(stream)

    assert "ended prematurely" in str(exc_info.value)


def test_stream_response_malformed_json_raises():
    """Malformed JSON line during streaming raises OllamaResponseError."""
    mock_resp = MockStreamingHTTPResponse(
        [
            '{"message": {"role": "assistant", "content": "Valid"}}\n',
            "INVALID_JSON_CORRUPT_BYTES\n",
        ]
    )

    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = OllamaModelProvider()
        stream = provider.stream_response(prompt="Malformed test")
        with pytest.raises(OllamaResponseError) as exc_info:
            list(stream)

    assert "Malformed JSON chunk" in str(exc_info.value)


def test_stream_response_http_404_model_not_found():
    """HTTP 404 from stream endpoint raises OllamaModelNotFoundError."""
    err_body = io.BytesIO(b'{"error": "model \'missing_model\' not found"}')
    http_err = urllib.error.HTTPError(
        url="http://127.0.0.1:11434/api/chat",
        code=404,
        msg="Not Found",
        hdrs=MagicMock(),
        fp=err_body,
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        provider = OllamaModelProvider(model_name="missing_model")
        with pytest.raises(OllamaModelNotFoundError) as exc_info:
            list(provider.stream_response(prompt="Check 404"))

    assert "missing_model" in str(exc_info.value)


def test_stream_response_connection_failure():
    """URLError during streaming raises OllamaConnectionError."""
    url_err = urllib.error.URLError(reason="Connection refused")

    with patch("urllib.request.urlopen", side_effect=url_err):
        provider = OllamaModelProvider()
        with pytest.raises(OllamaConnectionError) as exc_info:
            list(provider.stream_response(prompt="Check connection err"))

    assert "Unable to connect to Ollama server" in str(exc_info.value)


def test_stream_response_timeout():
    """Timeout during streaming raises OllamaTimeoutError."""
    with patch("urllib.request.urlopen", side_effect=TimeoutError("Read timed out")):
        provider = OllamaModelProvider()
        with pytest.raises(OllamaTimeoutError) as exc_info:
            list(provider.stream_response(prompt="Check timeout"))

    assert "timed out" in str(exc_info.value)


def test_stream_response_empty_final_response():
    """Stream containing only empty content chunks raises OllamaResponseError."""
    chunks = [
        {"message": {"role": "assistant", "content": ""}, "done": False},
        {"message": {"role": "assistant", "content": "   "}, "done": False},
        {"done": True},
    ]
    mock_resp = MockStreamingHTTPResponse(chunks)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = OllamaModelProvider()
        stream = provider.stream_response(prompt="Empty test")
        with pytest.raises(OllamaResponseError) as exc_info:
            list(stream)

    assert "empty streamed response" in str(exc_info.value)


def test_stream_response_closes_underlying_response():
    """Underlying HTTP response is cleanly closed when generator completes."""
    chunks = [
        {"message": {"role": "assistant", "content": "Done"}, "done": True},
    ]
    mock_resp = MockStreamingHTTPResponse(chunks)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = OllamaModelProvider()
        list(provider.stream_response(prompt="Close test"))

    assert mock_resp.closed is True


# ---------------------------------------------------------------------------
# Cooperative Cancellation Tests (Milestone 3.5)
# ---------------------------------------------------------------------------


def test_stream_response_cancellation_before_start():
    """Cancelled token before streaming begins raises OllamaCancelledError immediately."""
    token = CancellationToken()
    token.cancel()

    with patch("urllib.request.urlopen") as mock_urlopen:
        provider = OllamaModelProvider()
        stream = provider.stream_response(prompt="Pre-cancelled test", cancellation_token=token)
        with pytest.raises(OllamaCancelledError) as exc_info:
            next(stream)

    assert "cancelled before starting" in str(exc_info.value).lower()
    mock_urlopen.assert_not_called()


def test_stream_response_cancellation_during_stream():
    """Cancelling token while iterating chunks raises OllamaCancelledError and closes response."""
    chunks = [
        {"message": {"role": "assistant", "content": "Chunk1"}, "done": False},
        {"message": {"role": "assistant", "content": "Chunk2"}, "done": False},
        {"message": {"role": "assistant", "content": "Chunk3"}, "done": False},
    ]
    mock_resp = MockStreamingHTTPResponse(chunks)
    token = CancellationToken()

    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = OllamaModelProvider()
        stream = provider.stream_response(prompt="Mid-stream cancel", cancellation_token=token)

        c1 = next(stream)
        assert c1 == "Chunk1"

        # Cancel token after first chunk
        token.cancel()

        with pytest.raises(OllamaCancelledError) as exc_info:
            next(stream)

    assert "cancelled by user" in str(exc_info.value)
    assert mock_resp.closed is True


def test_stream_response_cancellation_socket_shutdown_and_close():
    """Cancelling token triggers registered socket shutdown and response closure."""
    chunks = [
        {"message": {"role": "assistant", "content": "Chunk1"}, "done": False},
    ]
    mock_resp = MockStreamingHTTPResponse(chunks)
    mock_sock = MagicMock()
    mock_fp = MagicMock()
    mock_fp.raw._sock = mock_sock
    mock_resp.fp = mock_fp

    token = CancellationToken()

    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = OllamaModelProvider()
        stream = provider.stream_response(prompt="Socket shutdown test", cancellation_token=token)
        next(stream)

        # Trigger cancellation
        token.cancel()

    mock_sock.shutdown.assert_called_once()
    assert mock_resp.closed is True


def test_stream_response_cancellation_distinct_from_connection_error():
    """Socket error caused by intentional cancellation is not treated as OllamaConnectionError."""
    token = CancellationToken()

    class InterruptedStreamingHTTPResponse(MockStreamingHTTPResponse):
        def __iter__(self):
            yield (
                json.dumps({"message": {"role": "assistant", "content": "First"}}).encode("utf-8")
                + b"\n"
            )
            # Simulate socket being shut down mid-stream
            token.cancel()
            raise OSError("Socket closed during read")

    mock_resp = InterruptedStreamingHTTPResponse([])

    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = OllamaModelProvider()
        stream = provider.stream_response(prompt="Distinction test", cancellation_token=token)
        assert next(stream) == "First"

        with pytest.raises(OllamaCancelledError) as exc_info:
            next(stream)

    assert "cancelled by user" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Milestone 3.5.2 — _cancel_response_socket helper tests (Part B)
# ---------------------------------------------------------------------------


def test_cancel_response_socket_when_fp_is_none():
    """_cancel_response_socket is a no-op when resp.fp is already None (normal completion)."""
    mock_resp = MagicMock()
    mock_resp.fp = None  # Simulates post-completion state set by _close_conn()
    # Must not raise
    OllamaModelProvider._cancel_response_socket(mock_resp)
    mock_resp.close.assert_called_once()


def test_cancel_response_socket_oserror_on_shutdown_does_not_propagate():
    """OSError during sock.shutdown() is caught silently (concurrent closure race)."""
    mock_sock = MagicMock()
    mock_sock.shutdown.side_effect = OSError(9, "Bad file descriptor")
    mock_fp = MagicMock()
    mock_fp.raw._sock = mock_sock
    mock_resp = MagicMock()
    mock_resp.fp = mock_fp
    # Must not raise
    OllamaModelProvider._cancel_response_socket(mock_resp)
    mock_sock.shutdown.assert_called_once()
    # close() may or may not have been attempted; resp.close() should still be called
    mock_resp.close.assert_called_once()


def test_cancel_response_socket_oserror_on_close_does_not_propagate():
    """OSError during sock.close() is caught silently."""
    mock_sock = MagicMock()
    mock_sock.shutdown.return_value = None
    mock_sock.close.side_effect = OSError(9, "Bad file descriptor")
    mock_fp = MagicMock()
    mock_fp.raw._sock = mock_sock
    mock_resp = MagicMock()
    mock_resp.fp = mock_fp
    # Must not raise
    OllamaModelProvider._cancel_response_socket(mock_resp)
    mock_resp.close.assert_called_once()


def test_cancel_response_socket_not_called_during_normal_stream():
    """The socket helper is NOT called during a normal (non-cancelled) stream completion."""
    chunks = [
        {"message": {"role": "assistant", "content": "Hello"}, "done": False},
        {"message": {"role": "assistant", "content": " world"}, "done": True},
    ]
    mock_resp = MockStreamingHTTPResponse(chunks)
    mock_sock = MagicMock()
    mock_fp = MagicMock()
    mock_fp.raw._sock = mock_sock
    mock_resp.fp = mock_fp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = OllamaModelProvider()
        result = list(provider.stream_response(prompt="Normal stream"))

    assert result == ["Hello", " world"]
    # Socket should NOT have been shut down during normal completion
    mock_sock.shutdown.assert_not_called()


def test_cancel_response_socket_registered_callback_uses_static_method():
    """The cancellation callback registered by stream_response delegates to _cancel_response_socket."""
    chunks = [
        {"message": {"role": "assistant", "content": "Chunk"}, "done": False},
    ]
    mock_resp = MockStreamingHTTPResponse(chunks)
    mock_sock = MagicMock()
    mock_fp = MagicMock()
    mock_fp.raw._sock = mock_sock
    mock_resp.fp = mock_fp

    token = CancellationToken()

    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = OllamaModelProvider()
        stream = provider.stream_response(prompt="Callback test", cancellation_token=token)
        next(stream)  # Advance past urlopen; callback is now registered
        token.cancel()  # Fires the registered _cancel_response_socket callback

    # Socket shutdown and response closure should both have been attempted
    mock_sock.shutdown.assert_called_once()
    assert mock_resp.closed is True
