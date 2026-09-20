"""Regression checks for the offline scale benchmark harness."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_MODULE_SPEC = spec_from_file_location(
    "scale_benchmark", Path(__file__).with_name("scale_benchmark.py")
)
assert _MODULE_SPEC and _MODULE_SPEC.loader
_MODULE = module_from_spec(_MODULE_SPEC)
_MODULE_SPEC.loader.exec_module(_MODULE)
benchmark = _MODULE.benchmark


def test_small_generated_project_exercises_bounded_paths() -> None:
    result = benchmark(25, chunk_size=7, include_export=False)

    assert result["size"] == 25
    assert result["database_bytes"] > 0
    assert result["aggregate_source_population"] == 0
    assert result["aggregate_classified_population"] == 0
    assert result["evidence_rows"] == 0
    assert result["query_groups"] == 6
    assert result["classification"]["batch_plan_sample_size"] == 25
    assert result["export_seconds"] is None
    assert not list(Path(".").glob("steam-scale-*/project.sqlite3"))


def test_seeded_project_exercises_aggregate_and_evidence_paths() -> None:
    result = benchmark(100, chunk_size=17, include_export=False, seed_fraction=0.2)

    assert result["seeded_classification_count"] == 20
    assert result["aggregate_source_population"] == 20
    assert result["aggregate_classified_population"] == 20
    assert result["evidence_rows"] > 0
    assert result["aggregate_seconds"] >= 0
    assert result["evidence_seconds"] >= 0
