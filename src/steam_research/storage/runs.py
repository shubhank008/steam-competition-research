"""Persistence and status views for command runs and child units."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from steam_research.domain import Run, RunStatus, RunUnit, UnitStatus
from steam_research.storage import Database


@dataclass(frozen=True)
class RunStatusView:
    id: str
    run_type: str
    status: str
    units: list[dict[str, Any]]
    error_summary: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def sanitized_manifest(value: dict[str, Any]) -> dict[str, Any]:
    """Copy a manifest while removing values under secret-looking keys."""
    secret_words = ("key", "token", "secret", "password", "credential")

    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for key, child in item.items():
                if any(word in str(key).lower() for word in secret_words):
                    continue
                result[str(key)] = clean(child)
            return result
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        return _json_value(item)

    result = clean(value)
    return result if isinstance(result, dict) else {}


def create_run(
    database: Database,
    run: Run,
    manifest: dict[str, Any] | None = None,
) -> None:
    """Insert a pending run with a deterministic sanitized manifest."""
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO runs "
            "(id, project_id, run_type, status, configuration_json, code_version) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(run.id),
                str(run.project_id),
                run.run_type,
                run.status.value,
                json.dumps(sanitized_manifest(manifest or {}), sort_keys=True),
                run.code_version,
            ),
        )


def add_unit(database: Database, unit: RunUnit) -> None:
    """Insert one pending child work unit."""
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO run_units (id, run_id, unit_key, status, attempt) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(unit.id), str(unit.run_id), unit.key, unit.status.value, unit.attempt),
        )


def transition_run(
    database: Database,
    run_id: UUID,
    status: RunStatus,
    *,
    at: datetime | None = None,
    error_summary: str | None = None,
) -> Run:
    """Apply a domain-validated run transition atomically."""
    row = database.connection.execute(
        "SELECT id, project_id, run_type, status, configuration_json, code_version, "
        "started_at, completed_at, error_summary FROM runs WHERE id = ?",
        (str(run_id),),
    ).fetchone()
    if row is None:
        raise ValueError(f"run not found: {run_id}")
    current = Run(
        UUID(str(row[0])),
        UUID(str(row[1])),
        str(row[2]),
        RunStatus(str(row[3])),
        json.loads(str(row[4])),
        str(row[5]),
        datetime.fromisoformat(row[6]) if row[6] else None,
        datetime.fromisoformat(row[7]) if row[7] else None,
        row[8],
    )
    updated = current.transition(status, at=at, error_summary=error_summary)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE runs SET status = ?, started_at = ?, completed_at = ?, "
            "error_summary = ? WHERE id = ?",
            (
                updated.status.value,
                updated.started_at.isoformat() if updated.started_at else None,
                updated.completed_at.isoformat() if updated.completed_at else None,
                updated.error_summary,
                str(run_id),
            ),
        )
    return updated


def transition_unit(
    database: Database,
    unit_id: UUID,
    status: UnitStatus,
    *,
    at: datetime | None = None,
    error_summary: str | None = None,
) -> RunUnit:
    """Apply a domain-validated child-unit transition atomically."""
    row = database.connection.execute(
        "SELECT id, run_id, unit_key, status, attempt, started_at, completed_at, "
        "error_summary FROM run_units WHERE id = ?",
        (str(unit_id),),
    ).fetchone()
    if row is None:
        raise ValueError(f"run unit not found: {unit_id}")
    current = RunUnit(
        UUID(str(row[0])),
        UUID(str(row[1])),
        str(row[2]),
        UnitStatus(str(row[3])),
        int(row[4]),
        datetime.fromisoformat(row[5]) if row[5] else None,
        datetime.fromisoformat(row[6]) if row[6] else None,
        row[7],
    )
    updated = current.transition(status, at=at, error_summary=error_summary)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE run_units SET status = ?, attempt = ?, started_at = ?, "
            "completed_at = ?, error_summary = ? WHERE id = ?",
            (
                updated.status.value,
                updated.attempt,
                updated.started_at.isoformat() if updated.started_at else None,
                updated.completed_at.isoformat() if updated.completed_at else None,
                updated.error_summary,
                str(unit_id),
            ),
        )
    return updated


def status_view(database: Database) -> list[RunStatusView]:
    """Return recent runs and their child-unit statuses."""
    runs = database.connection.execute(
        "SELECT id, run_type, status, error_summary FROM runs ORDER BY rowid DESC"
    ).fetchall()
    result: list[RunStatusView] = []
    for row in runs:
        units = database.connection.execute(
            "SELECT unit_key, status, attempt, error_summary FROM run_units "
            "WHERE run_id = ? ORDER BY rowid",
            (row[0],),
        ).fetchall()
        result.append(
            RunStatusView(
                str(row[0]),
                str(row[1]),
                str(row[2]),
                [dict(unit) for unit in units],
                row[3],
            )
        )
    return result


def status_json(database: Database) -> str:
    """Serialize status using the stable machine-readable view shape."""
    return json.dumps([asdict(view) for view in status_view(database)], sort_keys=True)
