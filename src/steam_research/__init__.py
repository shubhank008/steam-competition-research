"""Steam competitor research application."""

__version__ = "0.1.0"
from steam_research.aggregation import (
    AggregateResult,
    Evidence,
    Metric,
    aggregate,
    select_evidence,
)

__all__ = ["AggregateResult", "Evidence", "Metric", "aggregate", "select_evidence"]
