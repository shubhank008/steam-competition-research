"""Offline scale benchmark for generated review projects.

Run with ``uv run python tests/benchmarks/scale_benchmark.py``. Runtime
artifacts are created below a temporary directory and removed on exit.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from steam_research.aggregation import aggregate, select_evidence
from steam_research.classification import (
    BatchLimits,
    ClassificationInput,
    estimate_tokens,
    plan_batches,
)
from steam_research.config import FilteringConfig
from steam_research.domain import Run
from steam_research.eligibility import decide_review, persist_eligibility, policy_hash
from steam_research.export import export_parquet
from steam_research.stage1 import STAGE1_PROMPT_VERSION, STAGE1_SCHEMA_VERSION
from steam_research.storage import Database, ReviewRecord, create_run, upsert_reviews
from steam_research.taxonomy import load_taxonomy

_SEED_TAXONOMY = load_taxonomy(Path("config/taxonomies/universal-core.yaml"))
_SEED_FILTERING = FilteringConfig()
_SEED_POLICY_HASH = policy_hash(_SEED_FILTERING)


def _project(database: Database) -> tuple[str, str]:
    project_id = str(uuid4())
    run = Run.create(UUID(project_id), "review_crawl")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, "scale", "config", "db", "now", "now"),
        )
    create_run(database, run)
    return project_id, str(run.id)


def _review(project_id: str, run_id: str, index: int) -> ReviewRecord:
    return ReviewRecord(
        project_id,
        440 + (index % 3),
        str(index),
        ("english", "german", "french")[index % 3],
        index % 4 != 0,
        index % 31,
        index % 7,
        0.5,
        index % 5,
        True,
        False,
        False,
        False,
        None,
        index % 1200,
        index % 2400,
        index % 120,
        None,
        1_700_000_000 + index,
        1_700_000_100 + index,
        f"Generated review {index}",
        "positive" if index % 4 else "negative",
        f"{index:064x}",
        json.dumps(
            {"recommendationid": str(index), "review": f"Generated review {index}"}
        ),
        run_id,
    )


def _classification_measure(size: int) -> dict[str, Any]:
    started = time.perf_counter()
    token_total = 0
    for index in range(size):
        token_total += estimate_tokens(
            ClassificationInput(
                "project",
                440,
                str(index),
                "hash",
                "policy",
                "english",
                True,
                1,
                10,
                1_700_000_000,
                1_700_000_100,
                f"Generated review {index}",
            ).projection()
        )
    elapsed = time.perf_counter() - started
    sample = [
        ClassificationInput(
            "project",
            440,
            str(index),
            "hash",
            "policy",
            "english",
            True,
            1,
            10,
            1_700_000_000,
            1_700_000_100,
            f"Generated review {index}",
        )
        for index in range(min(size, 100_000))
    ]
    batch_started = time.perf_counter()
    batches = plan_batches(sample, BatchLimits())
    batch_elapsed = time.perf_counter() - batch_started
    return {
        "token_estimation_seconds": round(elapsed, 3),
        "estimated_tokens": token_total,
        "batch_plan_sample_size": len(sample),
        "batch_plan_batches": len(batches),
        "batch_plan_seconds": round(batch_elapsed, 3),
    }


def _seed_analytics(
    database: Database, project_id: str, size: int, seed_fraction: float
) -> int:
    """Seed a bounded, deterministic eligible/classified subset for analytics."""
    seed_count = min(100_000, max(0, int(size * seed_fraction)))
    if not seed_count:
        return 0
    step = max(1, size // seed_count)
    selected = database.connection.execute(
        "SELECT * FROM reviews WHERE project_id = ? "
        "AND CAST(recommendationid AS INTEGER) % ? = 0 "
        "ORDER BY CAST(recommendationid AS INTEGER) LIMIT ?",
        (project_id, step, seed_count),
    ).fetchall()
    decisions = [
        decide_review(
            project_id=project_id,
            appid=row["appid"],
            recommendationid=row["recommendationid"],
            source_hash=row["source_hash"],
            review_text=row["review_text"],
            corpus_size=size,
            config=_SEED_FILTERING,
            decided_at="2026-01-01T00:00:00+00:00",
        )
        for row in selected
    ]
    persist_eligibility(database, decisions)
    classification_run_id = str(uuid4())
    batch_id = str(uuid4())
    now = "2026-01-01T00:00:00+00:00"
    prompt_hash = "benchmark-prompt-hash"
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO classification_runs (id, project_id, scope, seed, "
            "source_population, selected_count, prompt_version, prompt_hash, "
            "taxonomy_version, taxonomy_hash, model_policy, schema_version, "
            "policy_hash, status, request_ceiling, token_ceiling, "
            "cost_ceiling_usd, created_at, completed_at) VALUES "
            "(?, ?, 'all', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', "
            "0, 0, 0, ?, ?)",
            (
                classification_run_id,
                project_id,
                seed_count,
                seed_count,
                STAGE1_PROMPT_VERSION,
                prompt_hash,
                _SEED_TAXONOMY.version,
                _SEED_TAXONOMY.content_hash,
                "benchmark-seeded",
                STAGE1_SCHEMA_VERSION,
                _SEED_POLICY_HASH,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO classification_batches "
            "(id, run_id, batch_number, status, input_count, estimated_tokens) "
            "VALUES (?, ?, 0, 'completed', ?, 1)",
            (batch_id, classification_run_id, seed_count),
        )
        taxonomy_id = sorted(_SEED_TAXONOMY.ids)[0]
        for row in selected:
            text = row["review_text"]
            result = {
                "input_id": f"{row['appid']}:{row['recommendationid']}",
                "review_id": row["recommendationid"],
                "is_actionable": True,
                "engagement_posture": "satisfied"
                if row["voted_up"]
                else "frustrated_but_engaged",
                "overall_sentiment": "positive" if row["voted_up"] else "negative",
                "sentiment_intensity": 3,
                "aspects": [
                    {
                        "taxonomy_id": taxonomy_id,
                        "sentiment": "positive" if row["voted_up"] else "negative",
                        "intensity": 3,
                        "evidence": text,
                        "feature_request": None,
                        "confidence": 0.8,
                    }
                ],
                "use_cases": [],
                "self_reported_abandonment": {
                    "mentioned": False,
                    "statement_type": None,
                    "evidence": None,
                    "verified": False,
                },
                "summary": "Generated benchmark classification.",
            }
            connection.execute(
                "INSERT INTO review_classifications "
                "(run_id, batch_id, project_id, appid, recommendationid, source_hash, "
                "eligibility_policy_hash, prompt_version, prompt_hash, "
                "taxonomy_version, taxonomy_hash, model_policy, schema_version, "
                "status, result_json, inclusion_reason, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'success', ?, "
                "'eligible', ?, ?)",
                (
                    classification_run_id,
                    batch_id,
                    project_id,
                    row["appid"],
                    row["recommendationid"],
                    row["source_hash"],
                    _SEED_POLICY_HASH,
                    STAGE1_PROMPT_VERSION,
                    prompt_hash,
                    _SEED_TAXONOMY.version,
                    _SEED_TAXONOMY.content_hash,
                    "benchmark-seeded",
                    STAGE1_SCHEMA_VERSION,
                    json.dumps(result, sort_keys=True),
                    now,
                    now,
                ),
            )
    return seed_count


def _db_bytes(path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in (
            path,
            path.with_name(path.name + "-wal"),
            path.with_name(path.name + "-shm"),
        )
        if candidate.exists()
    )


def benchmark(
    size: int,
    chunk_size: int,
    include_export: bool,
    seed_fraction: float = 0.01,
) -> dict[str, Any]:
    if not 0 <= seed_fraction <= 1:
        raise ValueError("seed-fraction must be between zero and one")
    with tempfile.TemporaryDirectory(prefix=f"steam-scale-{size}-") as directory:
        root = Path(directory)
        db_path = root / "project.sqlite3"
        with Database(db_path) as database:
            database.migrate()
            project_id, run_id = _project(database)
            started = time.perf_counter()
            for start in range(0, size, chunk_size):
                upsert_reviews(
                    database,
                    [
                        _review(project_id, run_id, index)
                        for index in range(start, min(start + chunk_size, size))
                    ],
                )
            ingest_seconds = time.perf_counter() - started
            query_started = time.perf_counter()
            query_rows = database.connection.execute(
                "SELECT language, voted_up, COUNT(*) FROM reviews "
                "WHERE project_id = ? GROUP BY language, voted_up "
                "ORDER BY language, voted_up",
                (project_id,),
            ).fetchall()
            query_seconds = time.perf_counter() - query_started
            seeded_count = _seed_analytics(database, project_id, size, seed_fraction)
            aggregate_started = time.perf_counter()
            aggregate_result = aggregate(database, project_id=project_id)
            aggregate_seconds = time.perf_counter() - aggregate_started
            evidence_started = time.perf_counter()
            evidence = select_evidence(
                database, aggregate_run_id=aggregate_result.run_id
            )
            evidence_seconds = time.perf_counter() - evidence_started
            export_seconds: float | None = None
            export_rows: dict[str, int] | None = None
            export_error: str | None = None
            if include_export:
                try:
                    export_started = time.perf_counter()
                    exported = export_parquet(
                        database,
                        project_id=project_id,
                        output_dir=root / "parquet",
                        chunk_size=chunk_size,
                    )
                    export_seconds = time.perf_counter() - export_started
                    export_rows = exported.row_counts
                except RuntimeError as error:
                    export_error = str(error)
            database.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            page_size = database.connection.execute("PRAGMA page_size").fetchone()[0]
            page_count = database.connection.execute("PRAGMA page_count").fetchone()[0]
        result = {
            "size": size,
            "chunk_size": chunk_size,
            "database_bytes": _db_bytes(db_path),
            "sqlite_pages": page_count,
            "sqlite_page_size": page_size,
            "ingest_seconds": round(ingest_seconds, 3),
            "ingest_reviews_per_second": round(size / ingest_seconds, 1),
            "query_seconds": round(query_seconds, 3),
            "query_groups": len(query_rows),
            "seed_fraction": seed_fraction,
            "seeded_classification_count": seeded_count,
            "aggregate_seconds": round(aggregate_seconds, 3),
            "aggregate_source_population": aggregate_result.source_population,
            "aggregate_classified_population": aggregate_result.classified_population,
            "evidence_seconds": round(evidence_seconds, 3),
            "evidence_rows": len(evidence),
            "classification": _classification_measure(size),
            "export_seconds": round(export_seconds, 3)
            if export_seconds is not None
            else None,
            "export_rows": export_rows,
            "export_error": export_error,
            "peak_rss_mb": round(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1
            ),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, action="append", dest="sizes")
    parser.add_argument("--chunk-size", type=int, default=1_000)
    parser.add_argument(
        "--seed-fraction",
        type=float,
        default=0.01,
        help="fraction of generated reviews to seed for analytical paths (max 100k)",
    )
    parser.add_argument("--no-export", action="store_true")
    args = parser.parse_args()
    sizes = args.sizes or [100_000, 1_000_000]
    if (
        any(size < 1 for size in sizes)
        or args.chunk_size < 1
        or not 0 <= args.seed_fraction <= 1
    ):
        parser.error("size and chunk-size must be positive; seed-fraction must be 0..1")
    print(
        json.dumps(
            {
                "python": sys.version,
                "results": [
                    benchmark(
                        size,
                        args.chunk_size,
                        not args.no_export,
                        args.seed_fraction,
                    )
                    for size in sizes
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
