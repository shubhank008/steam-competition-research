"""Versioned, bounded analytical exports from canonical SQLite storage."""

# SQL projections are intentionally readable multiline strings.
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from steam_research.storage import Database

EXPORT_SCHEMA_VERSION = "parquet-v1"

_DATASETS: dict[str, tuple[tuple[str, str], ...]] = {
    "reviews": (
        ("project_id", "string"),
        ("appid", "int64"),
        ("recommendationid", "string"),
        ("language", "string"),
        ("voted_up", "bool"),
        ("votes_up", "int64"),
        ("votes_funny", "int64"),
        ("weighted_vote_score", "double"),
        ("comment_count", "int64"),
        ("steam_purchase", "bool"),
        ("received_for_free", "bool"),
        ("refunded", "bool"),
        ("written_during_early_access", "bool"),
        ("primarily_steam_deck", "bool"),
        ("playtime_at_review", "int64"),
        ("playtime_forever", "int64"),
        ("playtime_last_two_weeks", "int64"),
        ("last_played", "int64"),
        ("timestamp_created", "int64"),
        ("timestamp_updated", "int64"),
        ("review_text", "string"),
        ("source_review_type", "string"),
        ("source_hash", "string"),
        ("eligibility_decision", "string"),
        ("eligibility_policy_hash", "string"),
    ),
    "classifications": (
        ("project_id", "string"),
        ("appid", "int64"),
        ("recommendationid", "string"),
        ("source_hash", "string"),
        ("run_id", "string"),
        ("prompt_version", "string"),
        ("taxonomy_version", "string"),
        ("schema_version", "string"),
        ("model_policy", "string"),
        ("overall_sentiment", "string"),
        ("engagement_posture", "string"),
        ("actionable", "bool"),
        ("self_reported_abandonment", "bool"),
    ),
    "aspects": (
        ("project_id", "string"),
        ("appid", "int64"),
        ("recommendationid", "string"),
        ("source_hash", "string"),
        ("run_id", "string"),
        ("taxonomy_id", "string"),
        ("sentiment", "string"),
        ("confidence", "double"),
        ("evidence", "string"),
    ),
    "aggregates": (
        ("run_id", "string"),
        ("metric_id", "string"),
        ("metric_name", "string"),
        ("dimensions_json", "string"),
        ("numerator", "int64"),
        ("denominator", "int64"),
        ("value", "double"),
        ("population_definition", "string"),
        ("coverage_json", "string"),
        ("caveats_json", "string"),
    ),
    "quotes": (
        ("run_id", "string"),
        ("selection_id", "string"),
        ("project_id", "string"),
        ("appid", "int64"),
        ("recommendationid", "string"),
        ("taxonomy_id", "string"),
        ("evidence", "string"),
        ("selection_reason", "string"),
        ("score", "double"),
        ("direction", "string"),
        ("language", "string"),
        ("source_hash", "string"),
    ),
}


@dataclass(frozen=True)
class ExportResult:
    output_dir: Path
    datasets: tuple[str, ...]
    row_counts: dict[str, int]
    manifest_path: Path


def _pyarrow() -> Any:
    try:
        import pyarrow as pa  # type: ignore[import-untyped]
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ImportError as error:
        raise RuntimeError(
            "Parquet export requires the optional dependency; "
            "install with `uv sync --extra parquet`"
        ) from error
    return pa, pq


def _lineage(
    database: Database, project_id: str, aggregate_run_id: str | None
) -> dict[str, Any]:
    runs = database.connection.execute(
        "SELECT id, config_hash, source_population, classified_population, lineage_json "
        "FROM aggregate_runs WHERE project_id = ? AND status = 'completed' "
        "ORDER BY created_at, id",
        (project_id,),
    ).fetchall()
    if aggregate_run_id is not None and not any(
        row["id"] == aggregate_run_id for row in runs
    ):
        raise ValueError("aggregate run does not belong to project or is incomplete")
    selected = aggregate_run_id or (runs[-1]["id"] if runs else None)
    selected_row = next((row for row in runs if row["id"] == selected), None)
    classification_runs: list[str] = []
    if selected_row is not None:
        classification_runs = json.loads(selected_row["lineage_json"]).get(
            "classification_run_ids", []
        )
    return {
        "project_id": project_id,
        "aggregate_run_id": selected,
        "aggregate_config_hash": selected_row["config_hash"] if selected_row else None,
        "classification_run_ids": sorted(classification_runs),
        "sqlite_schema_versions": [
            row[0]
            for row in database.connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ],
    }


def _queries(
    project_id: str, aggregate_run_id: str | None
) -> dict[str, tuple[str, tuple[Any, ...]]]:
    base = (project_id,)
    classification = """
        SELECT * FROM (
            SELECT c.*, json_extract(c.result_json, '$.overall_sentiment') AS overall_sentiment,
                   json_extract(c.result_json, '$.engagement_posture') AS engagement_posture,
                   json_extract(c.result_json, '$.actionable') AS actionable,
                   json_extract(c.result_json, '$.self_reported_abandonment.mentioned')
                       AS self_reported_abandonment,
                   row_number() OVER (PARTITION BY c.project_id, c.appid, c.recommendationid
                                      ORDER BY c.updated_at DESC, c.run_id DESC) AS rn
            FROM review_classifications c
            JOIN reviews r USING (project_id, appid, recommendationid)
            JOIN review_eligibility e USING (project_id, appid, recommendationid)
            WHERE c.project_id = ? AND c.status = 'success' AND c.source_hash = r.source_hash
              AND e.obsolete_at IS NULL AND e.decision = 'eligible'
        ) WHERE rn = 1 ORDER BY appid, recommendationid
    """
    if aggregate_run_id is not None:
        aggregate_filter = "WHERE run_id = ?"
        aggregate_args: tuple[Any, ...] = (aggregate_run_id,)
    else:
        aggregate_filter = (
            "WHERE run_id IN (SELECT id FROM aggregate_runs WHERE project_id = ?)"
        )
        aggregate_args = base
    return {
        "reviews": (
            """
            SELECT r.*, e.decision AS eligibility_decision, e.policy_hash AS eligibility_policy_hash
            FROM reviews r LEFT JOIN review_eligibility e
              ON e.project_id=r.project_id AND e.appid=r.appid
             AND e.recommendationid=r.recommendationid AND e.obsolete_at IS NULL
            WHERE r.project_id=? ORDER BY r.appid, r.recommendationid
        """,
            base,
        ),
        "classifications": (classification, base),
        "aspects": (
            """
            SELECT c.project_id, c.appid, c.recommendationid, c.source_hash, c.run_id,
                   json_extract(aspect.value, '$.taxonomy_id') AS taxonomy_id,
                   json_extract(aspect.value, '$.sentiment') AS sentiment,
                   json_extract(aspect.value, '$.confidence') AS confidence,
                   json_extract(aspect.value, '$.evidence') AS evidence
            FROM review_classifications c JOIN reviews r USING (project_id, appid, recommendationid)
            JOIN review_eligibility e USING (project_id, appid, recommendationid)
            JOIN json_each(c.result_json, '$.aspects') aspect
            WHERE c.project_id=? AND c.status='success' AND c.source_hash=r.source_hash
              AND e.obsolete_at IS NULL AND e.decision='eligible'
            ORDER BY c.appid, c.recommendationid, taxonomy_id
        """,
            base,
        ),
        "aggregates": (
            f"SELECT * FROM aggregate_metrics {aggregate_filter} ORDER BY run_id, metric_id",
            aggregate_args,
        ),
        "quotes": (
            f"SELECT * FROM quote_selections {aggregate_filter} ORDER BY run_id, selection_id",
            aggregate_args,
        ),
    }


def export_parquet(
    database: Database,
    *,
    project_id: str,
    output_dir: Path,
    aggregate_run_id: str | None = None,
    chunk_size: int = 1000,
) -> ExportResult:
    """Write deterministic Parquet datasets without loading a full corpus."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    pa, pq = _pyarrow()
    output_dir.mkdir(parents=True, exist_ok=True)
    lineage = _lineage(database, project_id, aggregate_run_id)
    lineage_hash = hashlib.sha256(
        json.dumps(lineage, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    queries = _queries(project_id, lineage["aggregate_run_id"])
    counts: dict[str, int] = {}
    for dataset, columns in _DATASETS.items():
        arrow_types = {
            "string": pa.string,
            "int64": pa.int64,
            "double": pa.float64,
            "bool": pa.bool_,
        }
        schema = pa.schema(
            [
                pa.field(name, arrow_types[type_name](), nullable=True)
                for name, type_name in columns
            ],
            metadata={
                b"export_schema_version": EXPORT_SCHEMA_VERSION.encode(),
                b"dataset": dataset.encode(),
                b"lineage_hash": lineage_hash.encode(),
            },
        )
        path = output_dir / f"{dataset}.parquet"
        writer = pq.ParquetWriter(path, schema, compression="zstd")
        count = 0
        try:
            cursor = database.connection.execute(*queries[dataset])
            while True:
                rows = cursor.fetchmany(chunk_size)
                if not rows:
                    break
                records = []
                for row in rows:
                    record = {}
                    for name, type_name in columns:
                        value = row[name]
                        record[name] = (
                            bool(value)
                            if type_name == "bool" and value is not None
                            else value
                        )
                    records.append(record)
                table = pa.Table.from_pylist(records, schema=schema)
                writer.write_table(table)
                count += len(records)
        finally:
            writer.close()
        counts[dataset] = count
    manifest = {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "project_id": project_id,
        "lineage": lineage,
        "lineage_hash": lineage_hash,
        "datasets": {
            name: {"path": f"{name}.parquet", "rows": counts[name]}
            for name in _DATASETS
        },
        "partition_policy": "one deterministic file per dataset; row groups are bounded by chunk_size",
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return ExportResult(output_dir, tuple(_DATASETS), counts, manifest_path)
