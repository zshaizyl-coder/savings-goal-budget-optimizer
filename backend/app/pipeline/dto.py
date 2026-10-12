from dataclasses import dataclass
from datetime import datetime

from backend.app.domain.enums import Direction, FlowType, NormalizedStatus, PaymentSource


@dataclass(frozen=True)
class StandardTransactionDraft:
    source_row_number: int
    source: PaymentSource
    source_record_fingerprint: str
    occurred_at_utc: datetime
    amount_minor: int
    direction: Direction
    flow_type: FlowType
    normalized_status: NormalizedStatus
    source_type_code: str
    suggested_category_code: str
    classification_version: str
    original_category: str | None = None
    counterparty_fingerprint: str | None = None
    source_account_fingerprint: str | None = None
    is_abnormal: bool = False
