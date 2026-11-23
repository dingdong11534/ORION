"""Ollama model provider implementation for local LLM inference."""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from typing import Any

from orion.application.contracts import ChatMessage

logger = logging.getLogger(__name__)


class OllamaProviderError(Exception):
    """Base exception for all Ollama model provider errors."""


class OllamaConnectionError(OllamaProviderError):
    """Raised when unable to connect to the Ollama server."""


class OllamaModelNotFoundError(OllamaProviderError):
    """Raised when the specified model is not found on the Ollama server."""

    def __init__(self, message: str, status_code: int = 404) -> None:
        super().__init__(message)
        self.status_code = status_code


class OllamaTimeoutError(OllamaProviderError):
    """Raised when an Ollama request times out."""


class OllamaResponseError(OllamaProviderError):
    """Raised when the Ollama server returns an unexpected, malformed, or empty response."""


class OllamaAPIError(OllamaProviderError):
    """Raised when the Ollama server returns an unhandled HTTP error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OllamaModelProvider:
    """Model provider communicating with a local Ollama server via REST API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model_name: str = "qwen2.5:1.5b",
        timeout: float = 300.0,
        num_ctx: int = 2048,
        num_predict: int = 256,
        think: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name.strip()
        self.timeout = timeout
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.think = think

    def generate_response(
        self,
        prompt: str,
        history: Sequence[ChatMessage] | None = None,
    ) -> str:
        """Generate a text response for the given prompt and conversation history."""
        messages: list[dict[str, str]] = []

        if history:
            for msg in history:
                messages.append(
                    {
                        "role": msg.role,
                        "content": msg.content,
                    }
                )

        messages.append(
            {
                "role": "user",
                "content": prompt,
            }
        )

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
            "think": self.think,
            "keep_alive": "30s",
            "options": {
                "num_ctx": self.num_ctx,
                "num_predict": self.num_predict,
            },
        }

        endpoint = f"{self.base_url}/api/chat"
        json_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            endpoint,
            data=json_data,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw_bytes = resp.read()
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except OSError:
                pass

            err_detail = err_body
            try:
                parsed = json.loads(err_body)
                if isinstance(parsed, dict) and "error" in parsed:
                    err_detail = str(parsed["error"])
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

            if exc.code == 404:
                lower_body = err_detail.lower()
                if "model" in lower_body and (
                    "not found" in lower_body or "does not exist" in lower_body
                ):
                    raise OllamaModelNotFoundError(
                        f"Model {self.model_name!r} not found on Ollama server at {self.base_url}. "
                        "Please verify the model name or pull it using 'ollama pull <model>'."
                    ) from exc
                raise OllamaAPIError(
                    f"Ollama API resource not found (HTTP 404): {err_detail or 'Not Found'}",
                    status_code=404,
                ) from exc

            raise OllamaAPIError(
                f"Ollama API request failed with HTTP {exc.code}: {err_detail or exc.reason}",
                status_code=exc.code,
            ) from exc

        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise OllamaTimeoutError(
                    f"Inference request to Ollama server at {self.base_url} timed out after {self.timeout}s."
                ) from exc
            if (
                isinstance(reason, ConnectionRefusedError)
                or "connection refused" in str(reason).lower()
            ):
                raise OllamaConnectionError(
                    f"Unable to connect to Ollama server at {self.base_url}. "
                    "Please ensure Ollama is running ('ollama serve')."
                ) from exc
            raise OllamaConnectionError(
                f"Network failure communicating with Ollama server at {self.base_url}: {reason}"
            ) from exc

        except TimeoutError as exc:
            raise OllamaTimeoutError(
                f"Inference request to Ollama server at {self.base_url} timed out after {self.timeout}s."
            ) from exc

        except OSError as exc:
            raise OllamaConnectionError(
                f"System connection error while communicating with Ollama server at {self.base_url}: {exc}"
            ) from exc

        try:
            resp_str = raw_bytes.decode("utf-8")
            data = json.loads(resp_str)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OllamaResponseError(
                "Malformed JSON response received from Ollama server."
            ) from exc

        if not isinstance(data, dict):
            raise OllamaResponseError("Ollama API returned an unexpected non-object JSON response.")

        message_obj = data.get("message")
        if not isinstance(message_obj, dict):
            raise OllamaResponseError("Ollama API response missing 'message' object.")

        content = message_obj.get("content")
        if content is None:
            raise OllamaResponseError("Ollama API response message missing 'content' field.")

        if not isinstance(content, str) or not content.strip():
            raise OllamaResponseError("Ollama API returned an empty assistant response.")

        return content
