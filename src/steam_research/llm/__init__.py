"""Provider-independent LLM contracts and adapters."""

from steam_research.llm.contracts import (
    GenerationMessage,
    GenerationProvider,
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
from steam_research.llm.opencode import OpenCodeGoConfig, OpenCodeGoProvider

__all__ = [
    "GenerationMessage",
    "GenerationProvider",
    "JsonTransport",
    "OpenCodeGoConfig",
    "OpenCodeGoProvider",
    "ProviderCapabilities",
    "ProviderContractError",
    "ProviderError",
    "StructuredGenerationRequest",
    "StructuredGenerationResponse",
    "StructuredMode",
    "TransportResponse",
    "Usage",
]
