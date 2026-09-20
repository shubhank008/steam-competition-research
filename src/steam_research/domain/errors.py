"""Adapter-neutral external failure taxonomy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorCategory(StrEnum):
    RETRYABLE = "retryable"
    TERMINAL = "terminal"


class ExternalErrorCode(StrEnum):
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    SERVER = "server"
    AUTHENTICATION = "authentication"
    INVALID_REQUEST = "invalid_request"
    INVALID_RESPONSE = "invalid_response"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


RETRYABLE_CODES = frozenset(
    {
        ExternalErrorCode.CONNECTION,
        ExternalErrorCode.TIMEOUT,
        ExternalErrorCode.RATE_LIMITED,
        ExternalErrorCode.SERVER,
    }
)


@dataclass(frozen=True)
class ExternalError(Exception):
    code: ExternalErrorCode
    message: str
    retry_after_seconds: float | None = None

    @property
    def category(self) -> ErrorCategory:
        return (
            ErrorCategory.RETRYABLE
            if self.code in RETRYABLE_CODES
            else ErrorCategory.TERMINAL
        )

    @property
    def retryable(self) -> bool:
        return self.category is ErrorCategory.RETRYABLE
