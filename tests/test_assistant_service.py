import pytest
from pydantic import ValidationError

from orion.application.assistant_service import AssistantService, AssistantServiceError
from orion.infrastructure.fake_model_provider import FakeModelProvider


class CrashingModelProvider:
    """Mock provider that always raises an error."""

    def generate_response(self, prompt: str, history=None) -> str:
        raise ConnectionResetError("Connection to inference runtime lost.")


def test_assistant_service_successful_response_flow():
    provider = FakeModelProvider()
    service = AssistantService(model_provider=provider)

    assert len(service.get_history()) == 0

    reply = service.send_message("Plan my schedule")

    assert reply.role == "assistant"
    assert 'Echoing message: "Plan my schedule"' in reply.content

    history = service.get_history()
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[0].content == "Plan my schedule"
    assert history[1].role == "assistant"
    assert history[1].content == reply.content


@pytest.mark.parametrize("blank_input", ["", "   ", "\t\t"])
def test_assistant_service_rejects_blank_message(blank_input: str):
    provider = FakeModelProvider()
    service = AssistantService(model_provider=provider)

    with pytest.raises(ValidationError):
        service.send_message(blank_input)

    assert len(service.get_history()) == 0


def test_assistant_service_handles_model_provider_failure():
    service = AssistantService(model_provider=CrashingModelProvider())

    with pytest.raises(AssistantServiceError) as exc_info:
        service.send_message("Generate report")

    assert "Model provider generation failed" in str(exc_info.value)
    assert "Connection to inference runtime lost." in str(exc_info.value)

    # History should contain the user message that was attempted
    history = service.get_history()
    assert len(history) == 1
    assert history[0].role == "user"
    assert history[0].content == "Generate report"
