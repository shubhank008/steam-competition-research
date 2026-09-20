"""Versioned universal and project taxonomy loading and merging."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class TaxonomyError(ValueError):
    """Raised when a taxonomy cannot be validated or merged."""


@dataclass(frozen=True)
class Taxonomy:
    name: str
    version: str
    categories: tuple[dict[str, Any], ...]
    extends: tuple[str, ...]
    content_hash: str

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(category["id"] for category in self.categories)


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise TaxonomyError(f"could not read taxonomy: {path}") from error
    if not isinstance(raw, dict):
        raise TaxonomyError("taxonomy document must be a mapping")
    return raw


def _normalize_category(value: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TaxonomyError(f"category {index} must be a mapping")
    required = {"id", "name", "description"}
    missing = required - value.keys()
    if missing:
        raise TaxonomyError(f"category {index} missing fields: {sorted(missing)}")
    if not all(
        isinstance(value[field], str) and value[field].strip() for field in required
    ):
        raise TaxonomyError(f"category {index} fields must be non-empty strings")
    category = {
        "id": value["id"].strip(),
        "name": value["name"].strip(),
        "description": value["description"].strip(),
    }
    parent = value.get("parent_id")
    if parent is not None:
        if not isinstance(parent, str) or not parent.strip():
            raise TaxonomyError(
                f"category {index} parent_id must be a non-empty string"
            )
        category["parent_id"] = parent.strip()
    for field in ("positive_examples", "negative_examples"):
        examples = value.get(field, [])
        if not isinstance(examples, list) or not all(
            isinstance(item, str) for item in examples
        ):
            raise TaxonomyError(f"category {index} {field} must be a list of strings")
        if examples:
            category[field] = list(examples)
    return category


def _normalized_document(
    name: str,
    version: str,
    extends: tuple[str, ...],
    categories: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    return {
        "extends": list(extends),
        "name": name,
        "version": version,
        "categories": list(categories),
    }


def load_taxonomy(
    path: Path, *, external_parent_ids: frozenset[str] = frozenset()
) -> Taxonomy:
    """Load and validate one taxonomy document."""
    raw = _read(path)
    for field in ("name", "version", "categories"):
        if field not in raw:
            raise TaxonomyError(f"taxonomy missing required field: {field}")
    if not isinstance(raw["name"], str) or not isinstance(raw["version"], str):
        raise TaxonomyError("taxonomy name and version must be strings")
    extends_raw = raw.get("extends", [])
    if not isinstance(extends_raw, list) or not all(
        isinstance(item, str) for item in extends_raw
    ):
        raise TaxonomyError("taxonomy extends must be a list of strings")
    categories_raw = raw["categories"]
    if not isinstance(categories_raw, list):
        raise TaxonomyError("taxonomy categories must be a list")
    categories = tuple(
        _normalize_category(value, index=index)
        for index, value in enumerate(categories_raw)
    )
    ids = [category["id"] for category in categories]
    if len(ids) != len(set(ids)):
        raise TaxonomyError("taxonomy contains duplicate category IDs")
    category_ids = set(ids) | set(external_parent_ids)
    for category in categories:
        parent = category.get("parent_id")
        if parent is not None and parent not in category_ids:
            raise TaxonomyError(f"unknown parent category: {parent}")
    _validate_acyclic(categories)
    normalized = _normalized_document(
        raw["name"], raw["version"], tuple(extends_raw), categories
    )
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    return Taxonomy(
        raw["name"],
        raw["version"],
        categories,
        tuple(extends_raw),
        hashlib.sha256(encoded).hexdigest(),
    )


def _validate_acyclic(categories: tuple[dict[str, Any], ...]) -> None:
    parents = {category["id"]: category.get("parent_id") for category in categories}
    for category_id in parents:
        seen: set[str] = set()
        current: str | None = category_id
        while current is not None:
            if current in seen:
                raise TaxonomyError(
                    f"taxonomy hierarchy contains a cycle at: {current}"
                )
            seen.add(current)
            current = parents[current]


def merge_taxonomies(core: Taxonomy, extension: Taxonomy) -> Taxonomy:
    """Merge a project extension over the universal core without schema changes."""
    if "universal-core" not in extension.extends and extension.name != core.name:
        raise TaxonomyError("project taxonomy must extend universal-core")
    by_id = {category["id"]: category for category in core.categories}
    for category in extension.categories:
        existing = by_id.get(category["id"])
        if existing is not None and existing != category:
            raise TaxonomyError(f"incompatible duplicate category ID: {category['id']}")
        by_id[category["id"]] = category
    categories = tuple(sorted(by_id.values(), key=lambda category: category["id"]))
    _validate_acyclic(categories)
    merged = _normalized_document(
        extension.name, extension.version, extension.extends, categories
    )
    encoded = json.dumps(merged, sort_keys=True, separators=(",", ":")).encode()
    return Taxonomy(
        extension.name,
        extension.version,
        categories,
        extension.extends,
        hashlib.sha256(encoded).hexdigest(),
    )


def load_merged_taxonomy(core_path: Path, project_path: Path | None = None) -> Taxonomy:
    """Load universal core and optionally merge a project extension."""
    core = load_taxonomy(core_path)
    if project_path is None or project_path.resolve() == core_path.resolve():
        return core
    extension = load_taxonomy(project_path, external_parent_ids=core.ids)
    return merge_taxonomies(core, extension)
