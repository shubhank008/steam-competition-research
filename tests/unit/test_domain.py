"""Unit tests for framework-independent domain contracts."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from steam_research.domain import (
    AppId,
    DomainError,
    ErrorCategory,
    ExternalError,
    ExternalErrorCode,
    Result,
    Run,
    RunStatus,
    RunUnit,
    SourceHash,
    UnitStatus,
)


@pytest.mark.parametrize(
    ("initial", "next_status"),
    [
        (RunStatus.PENDING, RunStatus.COMPLETED),
        (RunStatus.RUNNING, RunStatus.PENDING),
    ],
)
def test_illegal_run_transitions_are_rejected(
    initial: RunStatus, next_status: RunStatus
) -> None:
    run = Run.create(uuid4(), "review_crawl")
    if initial is RunStatus.RUNNING:
        run = run.transition(initial)
    elif initial is not RunStatus.PENDING:
        run = run.transition(initial)
    with pytest.raises(DomainError, match="illegal run transition"):
        run.transition(next_status)


def test_terminal_unit_cannot_transition_again() -> None:
    unit = RunUnit.create(uuid4(), "store").transition(UnitStatus.RUNNING)
    unit = unit.transition(UnitStatus.FAILED)
    with pytest.raises(DomainError, match="illegal run-unit transition"):
        unit.transition(UnitStatus.RUNNING)


def test_run_and_unit_transitions_record_utc_times_and_attempts() -> None:
    at = datetime(2026, 1, 1, tzinfo=UTC)
    run = Run.create(uuid4(), "review_crawl").transition(RunStatus.RUNNING, at=at)
    run = run.transition(RunStatus.PARTIAL, at=at)
    unit = RunUnit.create(run.id, "positive")
    unit = unit.transition(UnitStatus.RUNNING, at=at).transition(
        UnitStatus.COMPLETED, at=at
    )
    assert run.completed_at == at
    assert unit.attempt == 1
    assert unit.completed_at == at


def test_domain_values_and_results_validate() -> None:
    assert AppId(10).value == 10
    assert SourceHash("a" * 64).value == "a" * 64
    assert Result.ok("value").is_success
    assert not Result.failure("failed").is_success
    with pytest.raises(DomainError):
        AppId(0)
    with pytest.raises(DomainError):
        SourceHash("invalid")


@pytest.mark.parametrize(
    ("code", "category"),
    [
        (ExternalErrorCode.TIMEOUT, ErrorCategory.RETRYABLE),
        (ExternalErrorCode.RATE_LIMITED, ErrorCategory.RETRYABLE),
        (ExternalErrorCode.INVALID_RESPONSE, ErrorCategory.TERMINAL),
        (ExternalErrorCode.AUTHENTICATION, ErrorCategory.TERMINAL),
    ],
)
def test_external_error_classification_is_adapter_independent(
    code: ExternalErrorCode, category: ErrorCategory
) -> None:
    error = ExternalError(code, "sanitized message")
    assert error.category is category
    assert error.retryable is (category is ErrorCategory.RETRYABLE)
