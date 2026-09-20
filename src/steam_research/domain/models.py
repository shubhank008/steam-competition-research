"""Framework-independent domain values, results, and workflow state."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeVar
from uuid import UUID, uuid4


class DomainError(ValueError):
    """Raised when a domain value or state transition is invalid."""


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UnitStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.CANCELLED}
)
TERMINAL_UNIT_STATUSES = frozenset(
    {UnitStatus.COMPLETED, UnitStatus.FAILED, UnitStatus.CANCELLED}
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class SourceHash:
    """A validated lowercase SHA-256 digest for source lineage."""

    value: str

    def __post_init__(self) -> None:
        if len(self.value) != 64 or any(
            char not in "0123456789abcdef" for char in self.value
        ):
            raise DomainError("source hash must be a lowercase SHA-256 digest")


@dataclass(frozen=True)
class AppId:
    value: int

    def __post_init__(self) -> None:
        if self.value <= 0:
            raise DomainError("Steam app ID must be positive")


@dataclass(frozen=True)
class Run:
    id: UUID
    project_id: UUID
    run_type: str
    status: RunStatus = RunStatus.PENDING
    configuration: dict[str, object] | None = None
    code_version: str = "unknown"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_summary: str | None = None

    @classmethod
    def create(
        cls, project_id: UUID, run_type: str, *, code_version: str = "unknown"
    ) -> Run:
        if not run_type.strip():
            raise DomainError("run type must not be empty")
        return cls(uuid4(), project_id, run_type, code_version=code_version)

    def transition(
        self,
        status: RunStatus,
        *,
        at: datetime | None = None,
        error_summary: str | None = None,
    ) -> Run:
        allowed = {
            RunStatus.PENDING: {RunStatus.RUNNING, RunStatus.CANCELLED},
            RunStatus.RUNNING: TERMINAL_RUN_STATUSES,
        }
        if status not in allowed.get(self.status, set()):
            raise DomainError(f"illegal run transition: {self.status} -> {status}")
        timestamp = _timestamp(at or utc_now())
        started_at = self.started_at
        if status is RunStatus.RUNNING:
            started_at = timestamp
        completed_at = timestamp if status in TERMINAL_RUN_STATUSES else None
        return replace(
            self,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            error_summary=error_summary,
        )


@dataclass(frozen=True)
class RunUnit:
    id: UUID
    run_id: UUID
    key: str
    status: UnitStatus = UnitStatus.PENDING
    attempt: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_summary: str | None = None

    @classmethod
    def create(cls, run_id: UUID, key: str) -> RunUnit:
        if not key.strip():
            raise DomainError("run unit key must not be empty")
        return cls(uuid4(), run_id, key)

    def transition(
        self,
        status: UnitStatus,
        *,
        at: datetime | None = None,
        error_summary: str | None = None,
    ) -> RunUnit:
        allowed = {
            UnitStatus.PENDING: {UnitStatus.RUNNING, UnitStatus.CANCELLED},
            UnitStatus.RUNNING: TERMINAL_UNIT_STATUSES,
        }
        if status not in allowed.get(self.status, set()):
            raise DomainError(f"illegal run-unit transition: {self.status} -> {status}")
        timestamp = _timestamp(at or utc_now())
        started_at = timestamp if status is UnitStatus.RUNNING else self.started_at
        completed_at = timestamp if status in TERMINAL_UNIT_STATUSES else None
        attempt = self.attempt + 1 if status is UnitStatus.RUNNING else self.attempt
        return replace(
            self,
            status=status,
            attempt=attempt,
            started_at=started_at,
            completed_at=completed_at,
            error_summary=error_summary,
        )


T = TypeVar("T")


@dataclass(frozen=True)
class Result[T]:
    """Explicit success/failure result for deterministic domain services."""

    value: T | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.error is None):
            raise DomainError("result must contain exactly one of value or error")

    @property
    def is_success(self) -> bool:
        return self.error is None

    @classmethod
    def ok(cls, value: T) -> Result[T]:
        return cls(value=value)

    @classmethod
    def failure(cls, error: str) -> Result[T]:
        return cls(error=error)
