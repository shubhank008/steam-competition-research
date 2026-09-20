import json
from uuid import uuid4

from steam_research.aggregation import aggregate, select_evidence
from steam_research.storage import Database


def test_empty_project_aggregate_is_lineaged_with_unknown_rates(
    database: Database,
) -> None:
    database.migrate()
    project_id = str(uuid4())
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, "aggregate", "config", "db", "now", "now"),
        )
    result = aggregate(database, project_id=project_id)
    assert result.source_population == 0
    assert result.classified_population == 0
    assert result.metrics == ()
    row = database.connection.execute(
        "SELECT source_population, classified_population "
        "FROM aggregate_runs WHERE id = ?",
        (result.run_id,),
    ).fetchone()
    assert (row["source_population"], row["classified_population"]) == (0, 0)


def test_evidence_selector_is_bounded_and_reproducible(database: Database) -> None:
    database.migrate()
    project_id = str(uuid4())
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, "evidence", "config", "db", "now", "now"),
        )
    result = aggregate(database, project_id=project_id)
    assert select_evidence(database, aggregate_run_id=result.run_id, limit=2) == ()
    assert json.loads(
        database.connection.execute(
            "SELECT lineage_json FROM aggregate_runs WHERE id = ?", (result.run_id,)
        ).fetchone()[0]
    )["config"] == {"early_minutes": 60, "long_minutes": 600}
