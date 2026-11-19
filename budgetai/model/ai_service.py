"""Backend-only Groq transport for the household budgeting assistant."""

import json
import re
from dataclasses import dataclass
from json import JSONDecodeError

from groq import (
    APIConnectionError, APIError, APIResponseValidationError, APIStatusError, APITimeoutError,
    AuthenticationError, Groq, NotFoundError, PermissionDeniedError, RateLimitError,
)

from config import Settings


GROQ_BASE_URL = "https://api.groq.com"
STRICT_MODELS = frozenset({"openai/gpt-oss-20b", "openai/gpt-oss-120b"})
_IDENTIFIER = re.compile(
    r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b|\bhttps?://[^\s]+|"
    r"(?<!\w)(?:\+63[ -]?9|09)(?:[ -]?\d){9}(?!\w)|"
    r"\b(?:account|card)\s*(?:number|no\.?|#)\s*[:#]?\s*\d(?:[ -]?\d){5,}\b",
    re.IGNORECASE,
)


def redact_identifiers(text: str) -> str:
    """Remove common contact and account identifiers from provider-bound text."""
    return _IDENTIFIER.sub("[private detail]", text)


class BudgetAIError(Exception):
    """A user-facing AI failure without upstream request or response details."""


class ModelConfigurationError(BudgetAIError):
    """The API key or selected model requires attention."""


class ModelUnavailableError(BudgetAIError):
    """Groq or its network connection is unavailable."""


class ModelTimeoutError(BudgetAIError):
    """Groq did not answer within the request timeout."""


class ModelRateLimitError(BudgetAIError):
    """The provider has reached a usage limit."""

    def __init__(self, message: str, retry_after: str | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class InvalidModelResponseError(BudgetAIError):
    """Groq returned an empty, incomplete, or invalid reply."""

    def __init__(self, message: str, *, finish_reason: str | None = None):
        super().__init__(message)
        self.finish_reason = finish_reason


@dataclass(frozen=True)
class ModelReply:
    content: str
    finish_reason: str = "stop"
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_seconds: float | None = None
    prompt_seconds: float | None = None
    completion_seconds: float | None = None


def _strict_schema(schema: dict) -> dict:
    """Use the strict decoder's basic types and validate additional constraints locally."""
    result = {key: schema[key] for key in ("type", "$ref", "enum") if key in schema}
    if "anyOf" in schema:
        result["anyOf"] = [_strict_schema(item) for item in schema["anyOf"]]
    if "items" in schema:
        result["items"] = _strict_schema(schema["items"])
    if "$defs" in schema:
        result["$defs"] = {key: _strict_schema(value) for key, value in schema["$defs"].items()}
    if "properties" in schema:
        properties = {key: _strict_schema(value) for key, value in schema["properties"].items()}
        result.update(properties=properties, required=list(properties), additionalProperties=False)
    return result


def _provider_seconds(value: object) -> float | None:
    return value if isinstance(value, (int, float)) and 0 <= value <= 1_000_000 else None


class GroqAIService:
    """Send structured chat requests to Groq."""

    def __init__(self, settings: Settings, client: Groq | None = None):
        self.settings = settings
        self._client = client

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _get_client(self) -> Groq:
        if self._client is None:
            if not self.settings.groq_api_key:
                raise ModelConfigurationError("Set GROQ_API_KEY in the Python environment to use BudgetAI.")
            self._client = Groq(
                api_key=self.settings.groq_api_key,
                base_url=GROQ_BASE_URL,
                timeout=self.settings.timeout_seconds,
                max_retries=0,
            )
        return self._client

    def chat(
        self, *, model: str, messages: list[dict[str, str]], schema: dict,
        temperature: float, max_completion_tokens: int,
    ) -> ModelReply:
        """Send a non-streamed completion and return the usable answer and usage."""
        params = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_completion_tokens,
        }
        structured_schema = _strict_schema(schema)
        if model in STRICT_MODELS:
            params.update(include_reasoning=False, reasoning_effort="low")
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "budgetai_reply", "schema": structured_schema, "strict": True},
            }
        else:
            params["response_format"] = {"type": "json_object"}
            schema_instruction = "Reply with a JSON object matching this schema: " + json.dumps(
                structured_schema, ensure_ascii=False, separators=(",", ":"),
            )
            params["messages"] = [{"role": "system", "content": schema_instruction}, *messages]

        try:
            completion = self._get_client().chat.completions.create(**params)
        except RateLimitError as exc:
            retry_after = exc.response.headers.get("retry-after", "").strip()
            if not (retry_after.isascii() and retry_after.isdecimal() and len(retry_after) <= 9):
                retry_after = None
            raise ModelRateLimitError(
                "Groq has reached its usage limit. Wait before sending another AI request.", retry_after,
            ) from None
        except (AuthenticationError, PermissionDeniedError):
            raise ModelConfigurationError("Groq rejected access. Check GROQ_API_KEY and the key's permissions.") from None
        except NotFoundError:
            raise ModelConfigurationError("The Groq model is unavailable. Check BUDGETAI_MODEL.") from None
        except APITimeoutError:
            raise ModelTimeoutError("Groq did not answer in time. Try again shortly.") from None
        except APIConnectionError:
            raise ModelUnavailableError("Could not reach Groq. Check your internet connection and try again.") from None
        except (APIResponseValidationError, JSONDecodeError, UnicodeDecodeError):
            raise InvalidModelResponseError("Groq returned an invalid reply.") from None
        except APIStatusError as exc:
            if exc.status_code in {408, 504}:
                raise ModelTimeoutError("Groq did not answer in time. Try again shortly.") from None
            if exc.status_code >= 500:
                raise ModelUnavailableError("Groq is temporarily unavailable. Try again shortly.") from None
            if isinstance(exc.body, dict):
                details = exc.body.get("error", exc.body)
                code = details.get("code") if isinstance(details, dict) else None
                if code in ("json_validate_failed", "failed_generation"):
                    raise InvalidModelResponseError("Groq returned an invalid structured reply.") from None
                if code in ("model_decommissioned", "model_not_found"):
                    raise ModelConfigurationError("The Groq model is unavailable. Check BUDGETAI_MODEL.") from None
            raise BudgetAIError("Groq could not complete the AI request.") from None
        except APIError:
            raise BudgetAIError("Groq could not complete the AI request.") from None

        choices = getattr(completion, "choices", None)
        if not isinstance(choices, list) or not choices:
            raise InvalidModelResponseError("Groq returned an empty reply.")
        choice = choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        content = getattr(getattr(choice, "message", None), "content", None)
        if finish_reason == "length":
            raise InvalidModelResponseError("Groq returned an incomplete reply.", finish_reason="length")
        if finish_reason != "stop" or not isinstance(content, str) or not content.strip():
            raise InvalidModelResponseError("Groq returned an empty or incomplete reply.")
        usage = getattr(completion, "usage", None)
        return ModelReply(
            content=content.strip(),
            finish_reason=finish_reason,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            total_seconds=_provider_seconds(getattr(usage, "total_time", None)),
            prompt_seconds=_provider_seconds(getattr(usage, "prompt_time", None)),
            completion_seconds=_provider_seconds(getattr(usage, "completion_time", None)),
        )
