from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


ALLOWED_CATEGORY_CODES = frozenset({
    "food", "shopping", "transport", "entertainment", "housing",
    "life_services", "health", "education", "communications",
    "human_relations", "other",
})


@dataclass(frozen=True)
class ClassificationContext:
    """Transient source context. Text fields must never be persisted or logged."""

    transaction_id: int
    source_type_code: str
    occurred_at_utc: datetime
    amount_minor: int
    counterparty_hint: str = ""
    description_hint: str = ""
    recurring_count: int = 1
    # Transient public-web snippets. Never persisted or logged.
    public_web_evidence: str = ""


@dataclass(frozen=True)
class ClassificationResult:
    category_code: str
    confidence_bps: int
    evidence_codes: tuple[str, ...]
    requires_review: bool = True

