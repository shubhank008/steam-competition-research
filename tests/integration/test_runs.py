"""Integration tests for persisted run state and status views."""

import json
from uuid import UUID, uuid4

import pytest

from steam_research.domain import DomainError, Run, RunStatus, RunUnit, UnitStatus
from steam_research.storage import (
    Database,
    add_unit,
    create_run,
    status_json,
    transition_run,
    transition_unit,
)


def _project(database: Database) -> UUID:
    project_id = uuid4()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects (id, name, config_path, database_path, created_at, "
            "updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(project_id), "test", "config", "db", "now", "now"),
        )
    return project_id


def test_run_units_and_sanitized_json_status(database: Database) -> None:
    database.migrate()
    run = Run.create(_project(database), "review_crawl")
    create_run(database, run, {"model": "x", "api_key": "secret"})
    unit = RunUnit.create(run.id, "positive")
    add_unit(database, unit)
    transition_run(database, run.id, RunStatus.RUNNING)
    transition_unit(database, unit.id, UnitStatus.RUNNING)
    transition_unit(database, unit.id, UnitStatus.FAILED, error_summary="timeout")
    payload = json.loads(status_json(database))
    assert payload[0]["status"] == "running"
    assert payload[0]["units"][0]["status"] == "failed"
    assert "secret" not in json.dumps(payload)
    manifest = database.connection.execute(
        "SELECT configuration_json FROM runs WHERE id = ?", (str(run.id),)
    ).fetchone()[0]
    assert "api_key" not in manifest


def test_illegal_persisted_transition_is_rejected_without_mutation(
    database: Database,
) -> None:
    database.migrate()
    run = Run.create(_project(database), "classification")
    create_run(database, run)
    with pytest.raises(DomainError):
        transition_run(database, run.id, RunStatus.COMPLETED)
    assert (
        database.connection.execute(
            "SELECT status FROM runs WHERE id = ?", (str(run.id),)
        ).fetchone()[0]
        == "pending"
    )
