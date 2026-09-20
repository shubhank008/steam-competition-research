import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import pytest

from steam_research.config.models import Secret
from steam_research.llm import (
    GenerationMessage,
    OpenCodeGoConfig,
    OpenCodeGoProvider,
    ProviderCapabilities,
    ProviderContractError,
    ProviderError,
    StructuredGenerationRequest,
    StructuredMode,
    TransportResponse,
)


@dataclass
class FakeTransport:
    responses: list[object]

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResponse:
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return cast(TransportResponse, item)


def request(
    mode: StructuredMode = StructuredMode.NATIVE_SCHEMA,
) -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        model="model-id",
        messages=(GenerationMessage("user", "Return JSON."),),
        schema_name="result",
        schema={"type": "object"},
        mode=mode,
    )


def response(content: str = '{"ok":true}') -> TransportResponse:
    return TransportResponse(
        200,
        json.dumps(
            {
                "model": "model-id",
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 4,
                    "total_tokens": 7,
                },
            }
        ).encode(),
        {"x-request-id": "req-safe"},
    )


def provider(transport: FakeTransport, **kwargs: Any) -> OpenCodeGoProvider:
    return OpenCodeGoProvider(
        OpenCodeGoConfig(
            model="model-id",
            api_key=Secret("secret-value"),
            retry_backoff_seconds=0,
            **kwargs,
        ),
        transport,
    )


def test_native_schema_capability_is_used() -> None:
    transport = FakeTransport([response()])
    result = provider(
        transport, capabilities=ProviderCapabilities(native_schema=True)
    ).generate_structured(request())
    assert result.usage.total_tokens == 7
    assert transport.responses == []


def test_provider_without_native_schema_falls_back_to_json_mode() -> None:
    transport = FakeTransport([response()])
    result = provider(transport).generate_structured(request())
    assert result.mode is StructuredMode.JSON_MODE


def test_retryable_rate_limit_is_bounded() -> None:
    rate_limited = TransportResponse(429, b"secret server body", {})
    transport = FakeTransport([rate_limited, response()])
    result = provider(transport, max_retries=1).generate_structured(
        request(StructuredMode.JSON_MODE)
    )
    assert result.model == "model-id"


def test_invalid_response_is_classified_without_body_or_key() -> None:
    transport = FakeTransport([TransportResponse(200, b"not-json", {})])
    with pytest.raises(ProviderError, match="invalid structured response") as error:
        provider(transport).generate_structured(request())
    assert "secret-value" not in str(error.value)
    assert "not-json" not in str(error.value)


def test_configuration_rejects_insecure_endpoint_and_missing_key() -> None:
    with pytest.raises(ProviderContractError):
        provider(FakeTransport([]), base_url="http://localhost/v1")
    with pytest.raises(ProviderContractError):
        OpenCodeGoConfig(model="model-id", api_key=Secret(""))


def test_capability_without_any_structured_mode_fails() -> None:
    transport = FakeTransport([response()])
    configured = provider(
        transport,
        capabilities=ProviderCapabilities(native_schema=False, json_mode=False),
    )
    with pytest.raises(ProviderContractError, match="structured JSON"):
        configured.generate_structured(request())


def test_configuration_rejects_url_userinfo_and_query() -> None:
    for base_url in (
        "https://user:password@example.test/v1",
        "https://example.test/v1?api_key=leak",
        "https://example.test/v1#fragment",
    ):
        with pytest.raises(ProviderContractError):
            provider(FakeTransport([]), base_url=base_url)
