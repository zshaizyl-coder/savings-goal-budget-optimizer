from __future__ import annotations

from datetime import datetime, timezone
import unittest

from backend.app.classification.evaluation import CategoryReviewGroup
from backend.app.classification.features import is_cloud_eligible, privacy_minimized_features
from backend.app.classification.qr_context import (
    MERCHANT_LIKE,
    PERSONAL_OR_OPAQUE,
    assign_other_importance,
    classify_qr_group,
    infer_qr_counterparty_kind,
)
from backend.app.classification.types import ClassificationContext
from backend.app.pipeline.category_rules import classify_text


def group(
    group_id: str,
    amount_minor: int,
    count: int = 1,
    *,
    counterparty: str = "模拟对象",
    description: str = "二维码收款",
    minimum: int | None = None,
    maximum: int | None = None,
) -> CategoryReviewGroup:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    average = round(amount_minor / count)
    return CategoryReviewGroup(
        review_group_id=group_id,
        counterparty_fingerprint=group_id * 4,
        representative_transaction_id=1,
        source_types="qr_payment",
        transaction_count=count,
        total_amount_minor=amount_minor,
        average_amount_minor=average,
        minimum_amount_minor=minimum if minimum is not None else average,
        maximum_amount_minor=maximum if maximum is not None else average,
        first_transaction_at=now,
        last_transaction_at=now,
        weekend_ratio=0.0,
        dominant_time_bucket="dinner_window",
        counterparty_hint=counterparty,
        description_hint=description,
        cloud_eligible=False,
        sample_priority=1,
        sample_reason="top_amount",
        in_validation_sample=True,
    )


class QRContextTests(unittest.TestCase):
    def test_real_format_generic_qr_semantics_are_classified(self):
        cases = {
            "振杰云吞店": "food",
            "潮潮记潮汕汤粉": "food",
            "海南海鲜焖面": "food",
            "工艺校园图文店": "life_services",
            "顺昌旅馆": "housing",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(classify_text("qr_payment", text)[0], expected)

    def test_merchant_like_and_personal_qr_are_separated(self):
        self.assertEqual(infer_qr_counterparty_kind("振杰云吞店", "二维码收款"), MERCHANT_LIKE)
        self.assertEqual(infer_qr_counterparty_kind("张某", "收款方备注:二维码收款"), PERSONAL_OR_OPAQUE)
        self.assertEqual(infer_qr_counterparty_kind("[PHONE]", "二维码收款"), PERSONAL_OR_OPAQUE)

    def test_merchant_like_qr_can_enter_optional_cloud_queue(self):
        context = ClassificationContext(
            transaction_id=1,
            source_type_code="qr_payment",
            occurred_at_utc=datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
            amount_minor=1500,
            counterparty_hint="模拟餐饮店",
            description_hint="二维码收款",
        )
        self.assertTrue(is_cloud_eligible(privacy_minimized_features(context)))

    def test_personal_qr_stays_out_of_cloud_queue(self):
        context = ClassificationContext(
            transaction_id=1,
            source_type_code="qr_payment",
            occurred_at_utc=datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
            amount_minor=1500,
            counterparty_hint="张某",
            description_hint="二维码收款",
        )
        self.assertFalse(is_cloud_eligible(privacy_minimized_features(context)))

    def test_meal_time_without_semantics_is_rejected(self):
        decision = classify_qr_group(group("a", 1500, counterparty="张某"))
        self.assertIsNone(decision.candidate_category)
        self.assertEqual(decision.evidence_codes, ("insufficient_semantics",))

    def test_strong_qr_semantics_produce_review_only_candidate(self):
        decision = classify_qr_group(group("b", 1500, counterparty="振杰云吞店"))
        self.assertEqual(decision.candidate_category, "food")
        self.assertTrue(decision.requires_review)


class OtherImportanceTests(unittest.TestCase):
    def test_abc_tiers_prioritize_amount_without_dropping_frequent_groups(self):
        groups = [
            group("a", 8000),
            group("b", 1200),
            group("c", 500),
            group("d", 300, count=6),
        ]
        decisions = {item.review_group_id: item for item in assign_other_importance(groups)}
        self.assertEqual(decisions["a"].tier, "A")
        self.assertEqual(decisions["d"].tier, "B")
        self.assertEqual(decisions["d"].budget_treatment, "recurring_other_baseline_candidate")

    def test_invalid_tier_thresholds_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_importance_thresholds"):
            assign_other_importance([group("a", 100)], tier_a_cumulative_share=0.95, tier_b_cumulative_share=0.80)


if __name__ == "__main__":
    unittest.main()
