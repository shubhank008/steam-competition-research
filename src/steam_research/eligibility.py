"""Deterministic, versioned review eligibility decisions."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from steam_research.config.models import FilteringConfig
from steam_research.storage.database import Database

_DECISIONS = {
    "eligible",
    "excluded_empty",
    "excluded_punctuation",
    "excluded_repetition",
    "excluded_ascii_art",
    "excluded_low_information",
}
_WORD_RE = re.compile(r"\b[\w\u0080-\uffff]+\b", re.UNICODE)
_ASCII_ART_CHARS = set("/\\|_-=+*#<>^~()[]{}")


@dataclass(frozen=True)
class EligibilityDecision:
    project_id: str
    appid: int
    recommendationid: str
    source_hash: str
    policy_version: str
    policy_hash: str
    decision: str
    reason: dict[str, Any]
    decided_at: str

    @property
    def eligible(self) -> bool:
        return self.decision == "eligible"


def policy_hash(config: FilteringConfig) -> str:
    """Hash semantic policy values, independent of config formatting."""
    payload = {
        "policy_version": config.policy_version,
        "large_corpus_threshold": config.large_corpus_threshold,
        "short_character_threshold": config.short_character_threshold,
        "short_word_threshold": config.short_word_threshold,
        "repeated_character_ratio": config.repeated_character_ratio,
        "repeated_token_threshold": config.repeated_token_threshold,
        "high_signal_terms": sorted(
            term.casefold() for term in config.high_signal_terms
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _meaningful(text: str) -> str:
    return "".join(char for char in text if not char.isspace())


def _is_punctuation_only(text: str) -> bool:
    meaningful = _meaningful(text)
    return bool(meaningful) and all(
        unicodedata.category(char)[0] in {"P", "S"} for char in meaningful
    )


def _is_repetition(text: str, config: FilteringConfig) -> bool:
    meaningful = _meaningful(text)
    if not meaningful:
        return False
    counts = {char: meaningful.count(char) for char in set(meaningful)}
    if max(counts.values()) / len(meaningful) >= config.repeated_character_ratio:
        return len(meaningful) >= 3
    tokens = [token.casefold() for token in _WORD_RE.findall(text)]
    return (
        bool(tokens)
        and len(tokens) >= config.repeated_token_threshold
        and len(set(tokens)) == 1
    )


def _is_ascii_art(text: str) -> bool:
    meaningful = _meaningful(text)
    if len(meaningful) < 4 or not any(char in _ASCII_ART_CHARS for char in meaningful):
        return False
    letters = sum(char.isalpha() for char in meaningful)
    return letters == 0 or letters / len(meaningful) < 0.2


def _has_signal(text: str, config: FilteringConfig) -> bool:
    folded = text.casefold()
    return any(term.casefold() in folded for term in config.high_signal_terms)


def decide_review(
    *,
    project_id: str,
    appid: int,
    recommendationid: str,
    source_hash: str,
    review_text: str,
    corpus_size: int,
    config: FilteringConfig,
    decided_at: str | None = None,
) -> EligibilityDecision:
    """Classify one review without changing or retaining its source text."""
    if not review_text.strip():
        decision, reason = "excluded_empty", {"rule": "empty"}
    elif _is_ascii_art(review_text):
        decision, reason = "excluded_ascii_art", {"rule": "ascii_art"}
    elif _is_punctuation_only(review_text):
        decision, reason = "excluded_punctuation", {"rule": "punctuation_only"}
    elif _is_repetition(review_text, config):
        decision, reason = "excluded_repetition", {"rule": "repetition"}
    elif (
        corpus_size > config.large_corpus_threshold
        and len(_meaningful(review_text)) < config.short_character_threshold
        and len(_WORD_RE.findall(review_text)) < config.short_word_threshold
        and not _has_signal(review_text, config)
    ):
        decision, reason = (
            "excluded_low_information",
            {"rule": "large_corpus_short_review"},
        )
    else:
        decision, reason = "eligible", {"rule": "default_or_signal_preservation"}
    return EligibilityDecision(
        project_id,
        appid,
        recommendationid,
        source_hash,
        config.policy_version,
        policy_hash(config),
        decision,
        reason,
        decided_at or datetime.now(UTC).isoformat(),
    )


def persist_eligibility(
    database: Database, decisions: list[EligibilityDecision]
) -> None:
    """Persist decisions and obsolete prior policy decisions atomically."""
    if any(decision.decision not in _DECISIONS for decision in decisions):
        raise ValueError("unknown eligibility decision")
    with database.transaction() as connection:
        for decision in decisions:
            key = (decision.project_id, decision.appid, decision.recommendationid)
            connection.execute(
                "UPDATE review_eligibility SET obsolete_at = COALESCE(obsolete_at, ?) "
                "WHERE project_id = ? AND appid = ? AND recommendationid = ? "
                "AND policy_hash <> ? AND obsolete_at IS NULL",
                (decision.decided_at, *key, decision.policy_hash),
            )
            connection.execute(
                "INSERT INTO review_eligibility ("
                "project_id, appid, recommendationid, source_hash, policy_version, "
                "policy_hash, decision, reason_json, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(project_id, appid, recommendationid, policy_hash) "
                "DO UPDATE SET source_hash=excluded.source_hash, "
                "decision=excluded.decision, reason_json=excluded.reason_json, "
                "decided_at=excluded.decided_at, obsolete_at=NULL",
                (
                    *key,
                    decision.source_hash,
                    decision.policy_version,
                    decision.policy_hash,
                    decision.decision,
                    json.dumps(decision.reason, sort_keys=True, separators=(",", ":")),
                    decision.decided_at,
                ),
            )
