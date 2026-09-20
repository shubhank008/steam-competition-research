"""Framework-independent structured generation contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class StructuredMode(StrEnum):
    NATIVE_SCHEMA = "native_schema"
    JSON_MODE = "json_mode"


class ProviderContractError(ValueError):
    """Raised when provider configuration or a structured response is invalid."""


@dataclass(frozen=True)
class ProviderCapabilities:
    native_schema: bool = False
    json_mode: bool = True
    usage: bool = True
    max_context_tokens: int | None = None

    def choose_mode(self, requested: StructuredMode) -> StructuredMode:
        if requested is StructuredMode.NATIVE_SCHEMA and not self.native_schema:
            if self.json_mode:
                return StructuredMode.JSON_MODE
            raise ProviderContractError("provider cannot produce structured JSON")
        if requested is StructuredMode.JSON_MODE and not self.json_mode:
            if self.native_schema:
                return StructuredMode.NATIVE_SCHEMA
            raise ProviderContractError("provider cannot produce structured JSON")
        return requested


@dataclass(frozen=True)
class GenerationMessage:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant"}:
            raise ProviderContractError("message role is unsupported")
        if not self.content:
            raise ProviderContractError("message content must not be empty")


@dataclass(frozen=True)
class StructuredGenerationRequest:
    model: str
    messages: tuple[GenerationMessage, ...]
    schema_name: str
    schema: Mapping[str, object]
    mode: StructuredMode = StructuredMode.NATIVE_SCHEMA
    temperature: float = 0.0
    max_output_tokens: int = 4096
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model.strip() or not self.schema_name.strip():
            raise ProviderContractError("model and schema name must not be empty")
        if not self.messages:
            raise ProviderContractError("at least one generation message is required")
        if not 0 <= self.temperature <= 2:
            raise ProviderContractError("temperature must be between 0 and 2")
        if self.max_output_tokens <= 0:
            raise ProviderContractError("max output tokens must be positive")


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None


@dataclass(frozen=True)
class StructuredGenerationResponse:
    content: str
    usage: Usage
    model: str
    request_id: str | None = None
    mode: StructuredMode = StructuredMode.JSON_MODE

    def __post_init__(self) -> None:
        if not self.content.strip() or not self.model.strip():
            raise ProviderContractError(
                "provider response content and model are required"
            )


class GenerationProvider(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    def generate_structured(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse: ...


@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


class JsonTransport(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResponse: ...


class ProviderError(RuntimeError):
    """A safe, classified provider failure with no secret-bearing detail."""

    def __init__(
        self, code: str, message: str, *, retry_after_seconds: float | None = None
    ):
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds

    @property
    def retryable(self) -> bool:
        return self.code in {"connection", "timeout", "rate_limited", "server"}
