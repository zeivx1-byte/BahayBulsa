"""Exercise the real Groq SDK with synthetic HTTP responses and no network."""

import json
from dataclasses import replace

import httpx
import pytest
from groq import Groq

from config import settings
from model.ai_service import (
    GROQ_BASE_URL, GroqAIService, InvalidModelResponseError, ModelConfigurationError,
    ModelRateLimitError, ModelTimeoutError, ModelUnavailableError,
)
from model.budgeting_ai import ANSWER_SCHEMA


MESSAGES = [{"role": "user", "content": "Everything in this household example is fictional."}]


def ask(service, model="openai/gpt-oss-20b"):
    return service.chat(model=model, messages=MESSAGES, schema=ANSWER_SCHEMA,
                        temperature=0, max_completion_tokens=900)


def mock_service(handler):
    transport = httpx.MockTransport(handler)
    client = Groq(api_key="synthetic-test-key", base_url=GROQ_BASE_URL, max_retries=0,
                  http_client=httpx.Client(transport=transport))
    return GroqAIService(settings, client)


def completion(content="{\"summary\":\"Example\"}", finish_reason="stop"):
    return {"id": "synthetic", "model": "openai/gpt-oss-20b", "created": 0, "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}


def test_default_model_uses_official_https_and_strict_schema():
    def handle(request):
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer synthetic-test-key"
        body = json.loads(request.content)
        assert body["model"] == "openai/gpt-oss-20b"
        assert body["messages"] == MESSAGES
        assert body["max_completion_tokens"] == 900
        response_format = body["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["strict"] is True
        schema = response_format["json_schema"]["schema"]
        for obj in (schema, *schema["$defs"].values()):
            assert set(obj["required"]) == set(obj["properties"])
            assert obj["additionalProperties"] is False
        return httpx.Response(200, json=completion())

    with mock_service(handle) as service:
        answer = ask(service)
    assert json.loads(answer.content) == {"summary": "Example"}
    assert (answer.input_tokens, answer.output_tokens) == (20, 10)


def test_alternate_model_uses_supported_json_object_mode():
    def handle(request):
        body = json.loads(request.content)
        assert body["model"] == "other-groq-model"
        assert body["response_format"] == {"type": "json_object"}
        assert "JSON object" in body["messages"][0]["content"]
        assert "reasoning_effort" not in body
        return httpx.Response(200, json=completion())

    with mock_service(handle) as service:
        ask(service, model="other-groq-model")


@pytest.mark.parametrize(("status", "exception"), [
    (401, ModelConfigurationError), (403, ModelConfigurationError), (404, ModelConfigurationError),
    (429, ModelRateLimitError), (503, ModelUnavailableError), (504, ModelTimeoutError),
])
def test_upstream_error_is_sanitized_without_retry(status, exception):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "private provider content"}},
                              headers={"Retry-After": "12"})

    with mock_service(handle) as service, pytest.raises(exception) as error:
        ask(service)
    assert len(calls) == 1
    assert "private provider content" not in str(error.value)
    if status == 429:
        assert error.value.retry_after == "12"


@pytest.mark.parametrize(("upstream", "exception"), [
    (httpx.ConnectError, ModelUnavailableError), (httpx.ReadTimeout, ModelTimeoutError),
])
def test_network_errors(upstream, exception):
    def handle(request):
        raise upstream("private network details", request=request)

    with mock_service(handle) as service, pytest.raises(exception) as error:
        ask(service)
    assert "private network details" not in str(error.value)


@pytest.mark.parametrize("reply", [completion(None), completion(" "), completion("{}", "length"), {"choices": []}])
def test_empty_or_truncated_completion_is_rejected(reply):
    with mock_service(lambda _: httpx.Response(200, json=reply)) as service:
        with pytest.raises(InvalidModelResponseError):
            ask(service)


def test_success_with_malformed_json_body_is_rejected():
    response = httpx.Response(200, headers={"Content-Type": "application/json"}, content=b"{")
    with mock_service(lambda _: response) as service, pytest.raises(InvalidModelResponseError):
        ask(service)


def test_missing_key_does_not_open_a_client():
    with GroqAIService(replace(settings, groq_api_key="")) as service:
        with pytest.raises(ModelConfigurationError, match="GROQ_API_KEY"):
            ask(service)
