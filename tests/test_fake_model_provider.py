from orion.infrastructure.fake_model_provider import FakeModelProvider


def test_fake_model_provider_default_response():
    provider = FakeModelProvider()
    response = provider.generate_response(prompt="What is the weather?")

    assert 'Echoing message: "What is the weather?"' in response
    assert "ORION simulated mode" in response


def test_fake_model_provider_fixed_response():
    provider = FakeModelProvider(fixed_response="Custom mock reply")
    response = provider.generate_response(prompt="Any query")

    assert response == "Custom mock reply"
