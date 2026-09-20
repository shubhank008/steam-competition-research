"""Explicit, local-only fixture boundaries for deterministic CLI tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from steam_research.llm import (
    GenerationProvider,
    ProviderCapabilities,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    Usage,
)
from steam_research.steam import (
    FetchedDocument,
    Review,
    ReviewPage,
    ReviewPageRequest,
    StorePageRequest,
)
from steam_research.steam.contracts import source_hash


@dataclass(frozen=True)
class OfflineFixture:
    reviews: Mapping[int, tuple[Review, ...]]
    store_names: Mapping[int, str]

    @classmethod
    def load(cls, path: Path) -> OfflineFixture:
        path = path.expanduser()
        if path.is_symlink() or not path.is_file():
            raise ValueError("offline fixture must be a regular local file")
        if path.stat().st_size > 10_000_000:
            raise ValueError("offline fixture exceeds the 10 MB local limit")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("offline fixture is not readable JSON") from error
        if not isinstance(data, dict) or not isinstance(data.get("apps"), list):
            raise ValueError("offline fixture must contain an apps array")
        reviews: dict[int, tuple[Review, ...]] = {}
        names: dict[int, str] = {}
        for app in data["apps"]:
            if not isinstance(app, dict) or not isinstance(app.get("appid"), int):
                raise ValueError("offline fixture app entries require an integer appid")
            appid = app["appid"]
            names[appid] = str(app.get("name", f"Fixture {appid}"))
            values: list[Review] = []
            for index, item in enumerate(app.get("reviews", []), start=1):
                if not isinstance(item, dict):
                    raise ValueError("offline fixture reviews must be objects")
                text = str(item.get("review", "Fixture review"))
                raw = json.dumps(
                    {
                        "recommendationid": str(
                            item.get("recommendationid", f"{appid}-{index}")
                        ),
                        "language": str(item.get("language", "english")),
                        "review": text,
                        "voted_up": bool(item.get("voted_up", True)),
                        "timestamp_created": int(
                            item.get("timestamp_created", 1_700_000_000 + index)
                        ),
                        "timestamp_updated": int(
                            item.get("timestamp_updated", 1_700_000_000 + index)
                        ),
                    },
                    sort_keys=True,
                )
                values.append(
                    Review(
                        str(item.get("recommendationid", f"{appid}-{index}")),
                        str(item.get("language", "english")),
                        text,
                        bool(item.get("voted_up", True)),
                        int(item.get("votes_up", 1)),
                        0,
                        int(item.get("timestamp_created", 1_700_000_000 + index)),
                        int(item.get("timestamp_updated", 1_700_000_000 + index)),
                        True,
                        False,
                        False,
                        False,
                        raw,
                        source_hash(raw),
                    )
                )
            reviews[appid] = tuple(values)
        return cls(reviews, names)


class FixtureReviewSource:
    def __init__(self, fixture: OfflineFixture) -> None:
        self.fixture = fixture

    def fetch_page(self, request: ReviewPageRequest) -> ReviewPage:
        values = tuple(
            review
            for review in self.fixture.reviews.get(request.appid.value, ())
            if review.voted_up == (request.review_type == "positive")
        )
        return ReviewPage(
            request.appid,
            request.review_type,
            f"fixture-{request.review_type}-done",
            values,
            request.query_params(),
        )


class FixtureStoreFetcher:
    def __init__(self, fixture: OfflineFixture) -> None:
        self.fixture = fixture

    def fetch(self, request: StorePageRequest) -> FetchedDocument:
        name = self.fixture.store_names.get(
            request.appid.value, f"Fixture {request.appid.value}"
        )
        body = f'<html><meta property="og:title" content="{name}"></html>'.encode()
        return FetchedDocument(
            request.appid,
            200,
            f"fixture://store/{request.appid.value}",
            body,
            {},
            "offline-fixture",
            "2026-01-01T00:00:00+00:00",
        )

    def fetch_structured(self, request: StorePageRequest) -> Mapping[str, Any]:
        return {
            "name": self.fixture.store_names.get(
                request.appid.value, f"Fixture {request.appid.value}"
            )
        }


class FixtureProvider:
    capabilities = ProviderCapabilities(json_mode=True, usage=True)

    def generate_structured(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse:
        if request.schema_name == "stage1-v1":
            user = request.messages[-1].content
            start = "UNTRUSTED_REVIEW_DATA_START\n"
            end = "\nUNTRUSTED_REVIEW_DATA_END"
            raw = json.loads(user.split(start, 1)[1].split(end, 1)[0])
            output = []
            for item in raw:
                text = str(item["review_text"])
                output.append(
                    {
                        "input_id": item["input_id"],
                        "review_id": item["review_id"],
                        "is_actionable": True,
                        "engagement_posture": "satisfied"
                        if item["voted_up"]
                        else "frustrated_but_engaged",
                        "overall_sentiment": "positive"
                        if item["voted_up"]
                        else "negative",
                        "sentiment_intensity": 3,
                        "aspects": [
                            {
                                "taxonomy_id": "technical.stability",
                                "sentiment": "positive"
                                if item["voted_up"]
                                else "negative",
                                "intensity": 3,
                                "evidence": text[: min(20, len(text))],
                                "feature_request": None,
                                "confidence": 0.8,
                            }
                        ],
                        "use_cases": [],
                        "self_reported_abandonment": {
                            "mentioned": False,
                            "verified": False,
                            "statement_type": None,
                        },
                        "summary": "Fixture classification",
                    }
                )
            content = json.dumps(output)
        else:
            payload = json.loads(request.messages[-1].content)
            evidence = payload["evidence_payload"].get("evidence", [])
            metrics = payload["evidence_payload"].get("metrics", [])
            recommendation = []
            if metrics or evidence:
                recommendation = [
                    {
                        "rank": 1,
                        "title": "Improve reliability",
                        "action": "Test stability improvements",
                        "impact": "high",
                        "effort": "medium",
                        "evidence_metric_ids": [metrics[0]["metric_id"]]
                        if metrics
                        else [],
                        "evidence_review_ids": [evidence[0]["review_id"]]
                        if evidence
                        else [],
                        "validation_step": "Run a bounded fixture check",
                        "confidence": "medium",
                        "counterevidence": [],
                    }
                ]
            content = json.dumps(
                {
                    "schema_version": "stage2-v1",
                    "status": "complete",
                    "executive_direction": ["Prioritize reliable experiences"],
                    "recommendations": recommendation,
                    "risks_and_counterevidence": [],
                    "coverage_and_caveats": ["Fixture evidence is bounded."],
                    "competitor_grid": payload["evidence_payload"].get(
                        "competitors", []
                    ),
                }
            )
        return StructuredGenerationResponse(
            content, Usage(total_tokens=10), "offline-fixture"
        )


def load_fixture(
    path: Path,
) -> tuple[FixtureReviewSource, FixtureStoreFetcher, GenerationProvider]:
    fixture = OfflineFixture.load(path)
    return FixtureReviewSource(fixture), FixtureStoreFetcher(fixture), FixtureProvider()
