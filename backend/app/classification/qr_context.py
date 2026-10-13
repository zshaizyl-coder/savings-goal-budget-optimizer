from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING

from backend.app.pipeline.category_rules import classify_text_decision

if TYPE_CHECKING:
    from backend.app.classification.evaluation import CategoryReviewGroup


MERCHANT_LIKE = "merchant_like"
PERSONAL_OR_OPAQUE = "personal_or_opaque"
UNKNOWN_ENTITY = "unknown"

_MERCHANT_CUES = re.compile(
    r"有限公司|公司|商行|商贸|超市|便利|百货|店|餐厅|餐馆|饭店|饭馆|"
    r"小吃|快餐|粉店|粥铺|面馆|茶饮|咖啡|"
    r"图文|广告|旅馆|酒店|宾馆|药房|诊所|维修|理发|美发|打印|停车|缴费",
    re.IGNORECASE,
)
_PHONE = re.compile(r"(?:\+?86[- ]?)?1\d{10}")
_PLAIN_PERSON = re.compile(r"^[\u4e00-\u9fffA-Za-z·.\s]{1,12}$")


@dataclass(frozen=True)
class QRContextDecision:
    counterparty_kind: str
    candidate_category: str | None
    confidence_bps: int
    evidence_codes: tuple[str, ...]
    requires_review: bool = True


@dataclass(frozen=True)
class OtherImportanceDecision:
    review_group_id: str
    tier: str
    review_action: str
    budget_treatment: str
    cumulative_amount_share: float


def infer_qr_counterparty_kind(counterparty_hint: str, description_hint: str = "") -> str:
    description = re.sub(r"收款方备注[:：]?二维码收款|二维码收款", "", description_hint or "")
    text = f"{counterparty_hint or ''} {description}".strip(" /|")
    if not text:
        return UNKNOWN_ENTITY
    if _MERCHANT_CUES.search(text):
        return MERCHANT_LIKE
    if "[PHONE]" in text:
        return PERSONAL_OR_OPAQUE
    without_phone = _PHONE.sub("", text).strip(" :丨|-/")
    if _PHONE.search(text) or _PLAIN_PERSON.fullmatch(without_phone):
        return PERSONAL_OR_OPAQUE
    return UNKNOWN_ENTITY


def classify_qr_group(group: CategoryReviewGroup) -> QRContextDecision:
    """Produce a review-only candidate; time is never sufficient evidence."""
    kind = infer_qr_counterparty_kind(group.counterparty_hint, group.description_hint)
    decision = classify_text_decision(
        "qr_payment", group.counterparty_hint, group.description_hint,
    )
    if decision.category_code != "other" and decision.confidence_bps >= 9000:
        return QRContextDecision(
            counterparty_kind=kind,
            candidate_category=decision.category_code,
            confidence_bps=decision.confidence_bps,
            evidence_codes=(decision.evidence_code, "qr_context"),
        )
    return QRContextDecision(
        counterparty_kind=kind,
        candidate_category=None,
        confidence_bps=0,
        evidence_codes=("insufficient_semantics",),
    )


def assign_other_importance(
    groups: list[CategoryReviewGroup],
    *,
    tier_a_cumulative_share: float = 0.80,
    tier_b_cumulative_share: float = 0.95,
    high_frequency_count: int = 5,
) -> list[OtherImportanceDecision]:
    """ABC/Pareto triage without pretending to infer life necessity."""
    if not 0 < tier_a_cumulative_share < tier_b_cumulative_share < 1:
        raise ValueError("invalid_importance_thresholds")
    ordered = sorted(
        groups,
        key=lambda group: (-group.total_amount_minor, -group.transaction_count, group.review_group_id),
    )
    total = sum(group.total_amount_minor for group in ordered)
    cumulative = 0
    decisions = []
    for group in ordered:
        cumulative += group.total_amount_minor
        share = cumulative / total if total else 0.0
        if share <= tier_a_cumulative_share or len(decisions) == 0:
            tier = "A"
        elif share <= tier_b_cumulative_share:
            tier = "B"
        else:
            tier = "C"
        if group.transaction_count >= high_frequency_count and tier == "C":
            tier = "B"

        spread_ratio = (
            (group.maximum_amount_minor - group.minimum_amount_minor) / group.average_amount_minor
            if group.average_amount_minor else 0.0
        )
        if tier == "A":
            action = "human_review"
        elif tier == "B":
            action = "review_if_candidate_or_recurring"
        else:
            action = "keep_pooled_other"

        if group.transaction_count >= high_frequency_count and spread_ratio <= 0.5:
            treatment = "recurring_other_baseline_candidate"
        elif tier == "A" and spread_ratio > 1.0:
            treatment = "contingency_review"
        else:
            treatment = "pooled_other"
        decisions.append(OtherImportanceDecision(
            review_group_id=group.review_group_id,
            tier=tier,
            review_action=action,
            budget_treatment=treatment,
            cumulative_amount_share=share,
        ))
    return decisions
