from __future__ import annotations

from datetime import datetime
import re
import unicodedata
from zoneinfo import ZoneInfo

from .types import ClassificationContext
from .qr_context import MERCHANT_LIKE, infer_qr_counterparty_kind


_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_LONG_NUMBER = re.compile(r"(?<!\d)\d{5,}(?!\d)")
_PHONE_LIKE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1\d{10}(?!\d)")
_SPACE = re.compile(r"\s+")
_OPAQUE = re.compile(r"^[\s*/_.\-\d#]+$")


def sanitize_source_text(value: str | None, *, max_length: int = 80) -> str:
    """Remove common direct identifiers before optional cloud processing."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip()
    text = _EMAIL.sub("[EMAIL]", text)
    text = _PHONE_LIKE.sub("[PHONE]", text)
    text = _LONG_NUMBER.sub("[NUMBER]", text)
    text = _SPACE.sub(" ", text).strip()
    if not text or _OPAQUE.fullmatch(text):
        return ""
    return text[:max_length]


def amount_bucket(amount_minor: int) -> str:
    if amount_minor < 1000:
        return "under_10_cny"
    if amount_minor < 3000:
        return "10_to_30_cny"
    if amount_minor < 10000:
        return "30_to_100_cny"
    if amount_minor < 50000:
        return "100_to_500_cny"
    if amount_minor < 200000:
        return "500_to_2000_cny"
    return "at_least_2000_cny"


def time_bucket(value: datetime, timezone_name: str = "Asia/Shanghai") -> str:
    local = value.astimezone(ZoneInfo(timezone_name))
    minute = local.hour * 60 + local.minute
    if 5 * 60 <= minute < 10 * 60:
        return "breakfast_window"
    if 11 * 60 <= minute < 14 * 60 + 30:
        return "lunch_window"
    if 17 * 60 <= minute < 20 * 60:
        return "dinner_window"
    if 20 * 60 <= minute < 24 * 60:
        return "late_evening"
    return "other_time"


def privacy_minimized_features(
    context: ClassificationContext,
    timezone_name: str = "Asia/Shanghai",
) -> dict[str, object]:
    local = context.occurred_at_utc.astimezone(ZoneInfo(timezone_name))
    return {
        "source_type": context.source_type_code,
        "counterparty_hint": sanitize_source_text(context.counterparty_hint),
        "description_hint": sanitize_source_text(context.description_hint),
        "amount_bucket": amount_bucket(context.amount_minor),
        "time_bucket": time_bucket(context.occurred_at_utc, timezone_name),
        "is_weekend": local.weekday() >= 5,
        "recurring_count_bucket": (
            "once" if context.recurring_count <= 1
            else "2_to_4" if context.recurring_count <= 4
            else "5_plus"
        ),
        "counterparty_kind": infer_qr_counterparty_kind(
            sanitize_source_text(context.counterparty_hint),
            sanitize_source_text(context.description_hint),
        ),
        "public_web_evidence": sanitize_source_text(
            context.public_web_evidence, max_length=600,
        ),
    }


def is_cloud_eligible(features: dict[str, object]) -> bool:
    """Default-deny personal QR/group collection data for remote models."""
    source_type = features.get("source_type")
    if source_type == "qr_payment" and features.get("counterparty_kind") != MERCHANT_LIKE:
        return False
    if source_type not in {"merchant_consumption", "qr_payment"}:
        return False
    combined = f"{features.get('counterparty_hint', '')} {features.get('description_hint', '')}".strip()
    return len(combined) >= 2
