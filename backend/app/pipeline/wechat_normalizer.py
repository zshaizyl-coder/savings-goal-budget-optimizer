from __future__ import annotations

from dataclasses import dataclass

from backend.app.domain.enums import Direction, PaymentSource
from backend.app.parsers.wechat import WechatSourceTransaction
from backend.app.pipeline.category_rules import CATEGORY_RULE_VERSION, classify_text
from backend.app.pipeline.dto import StandardTransactionDraft
from backend.app.pipeline.fingerprints import stable_hmac
from backend.app.pipeline.issues import DataIssue, IssueSeverity
from backend.app.pipeline.money import MoneyParseError, parse_cny_to_minor
from backend.app.pipeline.time_utils import TimeParseError, parse_source_datetime
from backend.app.pipeline.wechat_rules import normalize_direction, normalize_source_type, normalize_status


@dataclass(frozen=True)
class NormalizeResult:
    transaction: StandardTransactionDraft | None
    issues: tuple[DataIssue, ...]


class WechatTransactionNormalizer:
    def __init__(self, fingerprint_secret: bytes, timezone_name: str = "Asia/Shanghai"):
        self.secret = fingerprint_secret
        self.timezone_name = timezone_name

    def normalize(self, row: WechatSourceTransaction) -> NormalizeResult:
        issues: list[DataIssue] = []
        try:
            amount_minor = parse_cny_to_minor(row.source_amount)
        except MoneyParseError as exc:
            return NormalizeResult(None, (DataIssue(IssueSeverity.ROW_ERROR, str(exc), row.source_row_number, "amount"),))
        try:
            occurred_at = parse_source_datetime(row.source_transaction_time, self.timezone_name)
        except TimeParseError as exc:
            return NormalizeResult(None, (DataIssue(IssueSeverity.ROW_ERROR, str(exc), row.source_row_number, "transaction_time"),))
        direction = normalize_direction(row.source_direction)
        if direction is None:
            return NormalizeResult(None, (DataIssue(IssueSeverity.ROW_ERROR, "direction_unknown", row.source_row_number, "direction"),))
        source_type_code, flow_type, unknown_type = normalize_source_type(row.source_transaction_type, direction)
        if unknown_type:
            issues.append(DataIssue(IssueSeverity.WARNING, "flow_type_unknown", row.source_row_number, "transaction_type"))
        status = normalize_status(row.source_status)
        if status.value == "unknown":
            issues.append(DataIssue(IssueSeverity.WARNING, "status_unknown", row.source_row_number, "status"))
        category, reason = classify_text(
            source_type_code,
            row.source_transaction_type,
            row.source_counterparty,
            row.source_description,
        )
        identity = row.source_transaction_id or "|".join((
            str(row.source_transaction_time), str(row.source_amount), row.source_direction,
            row.source_transaction_type, row.source_merchant_order_id,
            stable_hmac(self.secret, "counterparty", row.source_counterparty) if row.source_counterparty else "",
        ))
        source_fingerprint = stable_hmac(self.secret, "wechat-record", identity)
        counterparty_fp = stable_hmac(self.secret, "counterparty", row.source_counterparty) if row.source_counterparty else None
        account_fp = stable_hmac(self.secret, "payment-method", row.source_payment_method) if row.source_payment_method else None
        original_category = row.source_transaction_type if source_type_code in {
            "merchant_consumption", "qr_payment", "group_collection", "donation", "qr_receipt",
            "transfer", "transfer_refund", "red_packet", "red_packet_refund", "balance_top_up",
            "balance_withdrawal", "other",
        } else "退款"
        return NormalizeResult(StandardTransactionDraft(
            source_row_number=row.source_row_number,
            source=PaymentSource.WECHAT,
            source_record_fingerprint=source_fingerprint,
            occurred_at_utc=occurred_at,
            amount_minor=amount_minor,
            direction=direction,
            flow_type=flow_type,
            normalized_status=status,
            source_type_code=source_type_code,
            suggested_category_code=category,
            classification_version=CATEGORY_RULE_VERSION,
            original_category=original_category,
            counterparty_fingerprint=counterparty_fp,
            source_account_fingerprint=account_fp,
        ), tuple(issues))
