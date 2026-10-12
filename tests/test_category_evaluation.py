from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from backend.app.classification.evaluation import (
    CategoryReviewItem,
    build_review_groups,
    evaluate_category_predictions,
)


def item(
    transaction_id: int,
    fingerprint: str,
    amount_minor: int,
    *,
    source_type: str = "merchant_consumption",
    day: int = 0,
) -> CategoryReviewItem:
    return CategoryReviewItem(
        transaction_id=transaction_id,
        counterparty_fingerprint=fingerprint,
        source_type_code=source_type,
        occurred_at_utc=datetime(2026, 1, 5, 10, tzinfo=timezone.utc) + timedelta(days=day),
        amount_minor=amount_minor,
        counterparty_hint=f"模拟商户{fingerprint}",
        description_hint="模拟商品",
    )


class ReviewQueueTests(unittest.TestCase):
    def test_transactions_are_grouped_by_anonymous_counterparty(self):
        groups = build_review_groups([
            item(1, "a" * 64, 100),
            item(2, "a" * 64, 200, day=1),
            item(3, "b" * 64, 500),
        ], top_amount_n=1, high_frequency_n=0, low_amount_n=0, random_n=0)
        self.assertEqual(len(groups), 2)
        grouped = next(group for group in groups if group.review_group_id == "a" * 12)
        self.assertEqual(grouped.transaction_count, 2)
        self.assertEqual(grouped.total_amount_minor, 300)
        self.assertEqual(grouped.representative_transaction_id, 1)

    def test_top_amount_groups_are_always_first_priority(self):
        groups = build_review_groups([
            item(index, str(index) * 64, index * 100)
            for index in range(1, 7)
        ], top_amount_n=2, high_frequency_n=1, low_amount_n=1, random_n=1)
        top = [group for group in groups if group.sample_reason == "top_amount"]
        self.assertEqual({group.total_amount_minor for group in top}, {500, 600})
        self.assertTrue(all(group.sample_priority == 1 for group in top))

    def test_representative_sampling_is_deduplicated_and_deterministic(self):
        items = []
        transaction_id = 1
        for group_number in range(1, 21):
            for repeat in range(1 + group_number % 4):
                items.append(item(transaction_id, f"{group_number:064d}", group_number * 10, day=repeat))
                transaction_id += 1
        first = build_review_groups(items, top_amount_n=3, high_frequency_n=3, low_amount_n=3, random_n=3)
        second = build_review_groups(items, top_amount_n=3, high_frequency_n=3, low_amount_n=3, random_n=3)
        first_sample = [(row.review_group_id, row.sample_reason) for row in first if row.in_validation_sample]
        second_sample = [(row.review_group_id, row.sample_reason) for row in second if row.in_validation_sample]
        self.assertEqual(first_sample, second_sample)
        self.assertEqual(len(first_sample), len({group_id for group_id, _reason in first_sample}))
        self.assertEqual(len(first_sample), 12)

    def test_personal_qr_group_is_not_cloud_eligible(self):
        groups = build_review_groups([
            item(1, "q" * 64, 1000, source_type="qr_payment"),
        ], top_amount_n=1, high_frequency_n=0, low_amount_n=0, random_n=0)
        self.assertFalse(groups[0].cloud_eligible)


class EvaluationMetricTests(unittest.TestCase):
    def test_unreviewed_rows_do_not_create_fake_accuracy(self):
        metrics = evaluate_category_predictions([
            {"model_candidate": "food", "confirmed_category": "", "total_amount_minor": 1000},
        ], prediction_field="model_candidate")
        self.assertEqual(metrics["status"], "awaiting_human_labels")
        self.assertIsNone(metrics["accuracy"])

    def test_accuracy_confusion_and_amount_weighting_use_human_labels(self):
        metrics = evaluate_category_predictions([
            {"model_candidate": "food", "confirmed_category": "food", "total_amount_minor": 100},
            {"model_candidate": "food", "confirmed_category": "shopping", "total_amount_minor": 900},
        ], prediction_field="model_candidate")
        self.assertEqual(metrics["accuracy"], 0.5)
        self.assertEqual(metrics["amount_weighted_accuracy"], 0.1)
        self.assertEqual(metrics["confusion"]["shopping"], {"food": 1})

    def test_invalid_human_label_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_confirmed_category"):
            evaluate_category_predictions([
                {"rule_candidate": "other", "confirmed_category": "餐饮店", "total_amount_minor": 100},
            ], prediction_field="rule_candidate")


if __name__ == "__main__":
    unittest.main()
