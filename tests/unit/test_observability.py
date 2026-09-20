import json
from pathlib import Path
from uuid import uuid4

import pytest

from steam_research.domain import Run
from steam_research.observability import Diagnostic, diagnostic_json, redact_text
from steam_research.offline_fixture import OfflineFixture
from steam_research.storage import Database, create_run, record_diagnostic, status_json


def test_redaction_removes_credentials_and_keeps_operational_fields() -> None:
    text = redact_text(
        "https://alice:pw@example.test/x Authorization: Bearer abc "
        "API_KEY=secret review text",
        secrets=("secret",),
    )
    assert "alice" not in text and "pw" not in text
    assert "abc" not in text and "secret" not in text
    assert "review text" in text


def test_diagnostic_json_excludes_review_and_prompt_payloads() -> None:
    payload = diagnostic_json(
        Diagnostic(
            "run", "classification", app_id=42, result_count=3, message="invalid output"
        )
    )
    assert json.loads(payload)["result_count"] == 3
    assert "review" not in payload.lower()
    assert "prompt" not in payload.lower()


def test_status_contains_bounded_diagnostic_without_secret_or_source_text(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "status.sqlite3")
    database.migrate()
    project_id = uuid4()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects (id, name, config_path, database_path, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(project_id), "project", "config", "db", "now", "now"),
        )
    run = Run.create(project_id, "classification")
    create_run(database, run)
    record_diagnostic(
        database,
        Diagnostic(
            str(run.id),
            "classification",
            app_id=7,
            attempt=1,
            duration_ms=12,
            result_count=2,
            error_classification="validation",
            message="secret review text",
        ),
    )
    payload = status_json(database)
    assert '"result_count": 2' in payload
    assert "secret" not in payload
    assert "review text" in payload


def test_fixture_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "fixture.json"
    target.write_text('{"apps": []}', encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="regular local file"):
        OfflineFixture.load(link)
