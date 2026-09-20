"""OpenAI-compatible structured generation adapter for OpenCode Go."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

from steam_research.config.models import Secret
from steam_research.llm.contracts import (
    JsonTransport,
    ProviderCapabilities,
    ProviderContractError,
    ProviderError,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    StructuredMode,
    TransportResponse,
    Usage,
)

OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/v1"


@dataclass(frozen=True)
class OpenCodeGoConfig:
    model: str
    api_key: Secret
    base_url: str = OPENCODE_GO_BASE_URL
    timeout_seconds: float = 60.0
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0
    capabilities: ProviderCapabilities = ProviderCapabilities()

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.model.strip() or not self.api_key.value.strip():
            raise ProviderContractError("OpenCode Go model and API key are required")
        if not self.base_url.startswith("https://") or self.base_url.endswith("/"):
            raise ProviderContractError(
                "provider base URL must be an HTTPS URL without a trailing slash"
            )
        if (
            self.timeout_seconds <= 0
            or self.max_retries < 0
            or self.retry_backoff_seconds < 0
        ):
            raise ProviderContractError(
                "provider timeout, retries, and backoff are invalid"
            )


class OpenCodeGoProvider:
    def __init__(self, config: OpenCodeGoConfig, transport: JsonTransport) -> None:
        config.validate()
        self._config = config
        self._transport = transport

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._config.capabilities

    def generate_structured(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse:
        mode = self.capabilities.choose_mode(request.mode)
        payload: dict[str, object] = {
            "model": request.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if mode is StructuredMode.NATIVE_SCHEMA:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "schema": dict(request.schema),
                    "strict": True,
                },
            }
        else:
            payload["response_format"] = {"type": "json_object"}

        last_error: ProviderError | None = None
        for attempt in range(self._config.max_retries + 1):
            try:
                response = self._transport.post(
                    f"{self._config.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._config.api_key.value}",
                        "Content-Type": "application/json",
                    },
                    payload=payload,
                    timeout=self._config.timeout_seconds,
                )
            except TimeoutError:
                last_error = ProviderError("timeout", "provider request timed out")
            except OSError:
                last_error = ProviderError("connection", "provider connection failed")
            else:
                last_error = _http_error(response)
                if last_error is None:
                    return _parse_response(response, mode)
            if not last_error.retryable or attempt >= self._config.max_retries:
                break
            delay = last_error.retry_after_seconds
            if delay is None:
                delay = self._config.retry_backoff_seconds * (2**attempt)
            time.sleep(delay)
        assert last_error is not None
        raise last_error


def _http_error(response: Any) -> ProviderError | None:
    retry_after = _retry_after(response.headers.get("retry-after"))
    if 200 <= response.status_code < 300:
        return None
    code = {
        401: "authentication",
        403: "authentication",
        404: "not_found",
        408: "timeout",
        429: "rate_limited",
    }.get(
        response.status_code,
        "server" if response.status_code >= 500 else "invalid_request",
    )
    return ProviderError(
        code, f"provider request failed ({code})", retry_after_seconds=retry_after
    )


def _parse_response(
    response: TransportResponse, mode: StructuredMode
) -> StructuredGenerationResponse:
    try:
        payload = json.loads(response.body)
        choice = payload["choices"][0]["message"]["content"]
        model = payload["model"]
    except (
        KeyError,
        IndexError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise ProviderError(
            "invalid_response", "provider returned an invalid structured response"
        ) from error
    data = payload.get("usage", {})
    usage = Usage(
        _int_or_none(data.get("prompt_tokens")),
        _int_or_none(data.get("completion_tokens")),
        _int_or_none(data.get("total_tokens")),
    )
    if not isinstance(choice, str) or not isinstance(model, str):
        raise ProviderError(
            "invalid_response", "provider returned invalid response fields"
        )
    return StructuredGenerationResponse(
        choice, usage, model, response.headers.get("x-request-id"), mode
    )


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            return max(0.0, (date - date.now(date.tzinfo)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
