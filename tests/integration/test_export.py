import json
from pathlib import Path
from uuid import uuid4

import pytest

from steam_research.aggregation import aggregate
from steam_research.export import EXPORT_SCHEMA_VERSION, export_parquet
from steam_research.storage import Database


def _project(database: Database) -> str:
    project_id = str(uuid4())
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, "export", "config", "db", "now", "now"),
        )
    return project_id


def test_export_empty_project_round_trips_with_manifest(
    database: Database, tmp_path: Path
) -> None:
    database.migrate()
    project_id = _project(database)
    result = aggregate(database, project_id=project_id)
    exported = export_parquet(
        database,
        project_id=project_id,
        aggregate_run_id=result.run_id,
        output_dir=tmp_path / "parquet",
        chunk_size=1,
    )
    assert exported.row_counts == {
        "reviews": 0,
        "classifications": 0,
        "aspects": 0,
        "aggregates": 0,
        "quotes": 0,
    }
    manifest = json.loads(exported.manifest_path.read_text())
    assert manifest["export_schema_version"] == EXPORT_SCHEMA_VERSION
    assert manifest["lineage"]["aggregate_run_id"] == result.run_id
    import pyarrow.parquet as parquet  # type: ignore[import-untyped]

    assert parquet.read_table(tmp_path / "parquet" / "reviews.parquet").num_rows == 0
    assert parquet.read_table(tmp_path / "parquet" / "aggregates.parquet").num_rows == 0


def test_export_requires_optional_dependency_when_unavailable(
    database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database.migrate()
    project_id = _project(database)
    import steam_research.export as export_module

    monkeypatch.setattr(
        export_module,
        "_pyarrow",
        lambda: (_ for _ in ()).throw(RuntimeError("optional")),
    )
    with pytest.raises(RuntimeError, match="optional"):
        export_parquet(database, project_id=project_id, output_dir=tmp_path)
