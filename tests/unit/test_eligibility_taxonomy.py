"""Deterministic filtering and taxonomy contract tests."""

from pathlib import Path

import pytest

from steam_research.config import FilteringConfig
from steam_research.eligibility import EligibilityDecision, decide_review, policy_hash
from steam_research.taxonomy import TaxonomyError, load_merged_taxonomy, load_taxonomy


def _decision(text: str, *, corpus_size: int = 1) -> EligibilityDecision:
    return decide_review(
        project_id="project",
        appid=10,
        recommendationid="review",
        source_hash="a" * 64,
        review_text=text,
        corpus_size=corpus_size,
        config=FilteringConfig(),
        decided_at="2026-01-01T00:00:00+00:00",
    )


@pytest.mark.parametrize("text", ["", "   \n\t", "!!!...", "！？。。"])
def test_obvious_noise_is_excluded(text: str) -> None:
    assert _decision(text).decision in {
        "excluded_empty",
        "excluded_punctuation",
    }


def test_repetition_and_ascii_art_are_excluded() -> None:
    assert _decision("ha ha ha ha").decision == "excluded_repetition"
    assert _decision("/\\|_-=+*#").decision == "excluded_ascii_art"


def test_short_multilingual_signal_is_retained() -> None:
    assert _decision("crash").eligible
    assert _decision("ошибка", corpus_size=20_000).eligible
    assert _decision("lag", corpus_size=20_000).eligible


def test_large_corpus_threshold_is_deterministic() -> None:
    assert _decision("ok", corpus_size=10_000).eligible
    assert _decision("ok", corpus_size=10_001).decision == "excluded_low_information"
    assert policy_hash(FilteringConfig()) == policy_hash(FilteringConfig())


def test_taxonomy_hash_ignores_formatting(tmp_path: Path) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    body = (
        "version: '1'\nname: universal-core\ncategories:\n"
        "  - id: use_case\n    name: Use case\n"
        "    description: Context or goal.\n"
    )
    first.write_text(body, encoding="utf-8")
    second.write_text("\n" + body.replace("Use case", "  Use case  "), encoding="utf-8")
    assert load_taxonomy(first).content_hash == load_taxonomy(second).content_hash


def test_taxonomy_rejects_missing_fields_duplicate_ids_and_bad_parent(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.yaml"
    missing.write_text(
        "version: '1'\nname: x\ncategories:\n  - id: x\n", encoding="utf-8"
    )
    with pytest.raises(TaxonomyError, match="missing fields"):
        load_taxonomy(missing)
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(
        "version: '1'\nname: x\ncategories:\n"
        "  - {id: x, name: X, description: one}\n"
        "  - {id: x, name: X, description: two}\n",
        encoding="utf-8",
    )
    with pytest.raises(TaxonomyError, match="duplicate"):
        load_taxonomy(duplicate)
    child = tmp_path / "child.yaml"
    child.write_text(
        "version: '1'\nname: x\ncategories:\n"
        "  - {id: x, parent_id: missing, name: X, description: one}\n",
        encoding="utf-8",
    )
    with pytest.raises(TaxonomyError, match="unknown parent"):
        load_taxonomy(child)


def test_taxonomy_merges_extension_and_rejects_incompatible_override(
    tmp_path: Path,
) -> None:
    core = tmp_path / "core.yaml"
    extension = tmp_path / "extension.yaml"
    core.write_text(
        "version: '1'\nname: universal-core\ncategories:\n"
        "  - {id: use_case, name: Use case, description: Context}\n",
        encoding="utf-8",
    )
    extension.write_text(
        "version: '1'\nname: project\nextends: [universal-core]\ncategories:\n"
        "  - {id: custom, name: Custom, description: Extra}\n",
        encoding="utf-8",
    )
    merged = load_merged_taxonomy(core, extension)
    assert merged.ids == {"use_case", "custom"}
    extension.write_text(
        "version: '1'\nname: project\nextends: [universal-core]\ncategories:\n"
        "  - {id: use_case, name: Changed, description: Incompatible}\n",
        encoding="utf-8",
    )
    with pytest.raises(TaxonomyError, match="incompatible"):
        load_merged_taxonomy(core, extension)
