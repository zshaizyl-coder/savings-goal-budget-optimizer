from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AnalysisTransaction:
    """Framework-independent transaction view consumed by analysis code."""

    id: int
    occurred_at_utc: datetime
    amount_minor: int
    direction: str
    flow_type: str
    source_type_code: str | None = None
    suggested_category_code: str | None = None
    user_category_code: str | None = None
    related_transaction_id: int | None = None
    is_abnormal: bool = False

    @property
    def effective_category_code(self) -> str:
        return self.user_category_code or self.suggested_category_code or "other"
