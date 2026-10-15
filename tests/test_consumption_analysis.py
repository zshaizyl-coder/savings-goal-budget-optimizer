from __future__ import annotations

from datetime import datetime, timezone
import unittest
from zoneinfo import ZoneInfo

import pandas as pd

from backend.app.analytics.consumption import (
    RefundAllocationPolicy,
    UNALLOCATED_REFUND,
    build_consumption_datasets,
    build_transaction_facts,
    calculate_net_consumption,
)
from backend.app.analytics.forecast_dataset import build_weekly_forecast_dataset
from backend.app.analytics.quality import category_quality_table, weekly_descriptive_statistics
from backend.app.analytics.types import AnalysisTransaction


def local_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)


def tx(
    identifier: int,
    time: str,
    amount: int,
    flow: str = "consumption",
    direction: str = "outflow",
    category: str | None = "food",
    user_category: str | None = None,
    related: int | None = None,
    planning_role: str = "routine",
) -> AnalysisTransaction:
    return AnalysisTransaction(
        id=identifier, occurred_at_utc=local_utc(time), amount_minor=amount,
        direction=direction, flow_type=flow, suggested_category_code=category,
        user_category_code=user_category, related_transaction_id=related,
        planning_role=planning_role,
    )


class ConsumptionAggregationTests(unittest.TestCase):
    def setUp(self):
        self.transactions = [
            tx(1, "2026-01-05 08:00:00", 10000, category="food"),
            tx(2, "2026-01-07 09:00:00", 2000, flow="expense_refund", direction="inflow", category="food", related=1),
            tx(3, "2026-01-09 10:00:00", 50000, flow="transfer", category=None),
            tx(4, "2026-01-11 11:00:00", 30000, flow="balance_transfer", direction="neutral", category=None),
            tx(5, "2026-01-13 12:00:00", 5000, category="other", user_category="shopping"),
            tx(6, "2026-01-18 19:00:00", 3000, category="other"),
        ]
        self.datasets = build_consumption_datasets(self.transactions)

    def test_gross_refund_and_net_are_distinct(self):
        self.assertEqual(int(self.datasets.daily["gross_consumption_minor"].sum()), 18000)
        self.assertEqual(int(self.datasets.daily["refund_minor"].sum()), 2000)
        self.assertEqual(int(self.datasets.daily["net_consumption_minor"].sum()), 16000)

    def test_central_net_consumption_function(self):
        self.assertEqual(calculate_net_consumption(self.transactions), 16000)

    def test_transfer_does_not_enter_core_consumption(self):
        row = self.datasets.facts.loc[self.datasets.facts["transaction_id"] == 3].iloc[0]
        self.assertEqual(row["net_consumption_minor"], 0)
        self.assertEqual(row["consumption_count"], 0)

    def test_top_up_withdrawal_does_not_enter_core_consumption(self):
        row = self.datasets.facts.loc[self.datasets.facts["transaction_id"] == 4].iloc[0]
        self.assertEqual(row["gross_consumption_minor"], 0)
        self.assertEqual(row["refund_minor"], 0)

    def test_daily_series_fills_zero_days(self):
        self.assertEqual(len(self.datasets.daily), 14)
        zero_day = self.datasets.daily.loc[self.datasets.daily["date"].astype(str) == "2026-01-06"].iloc[0]
        self.assertEqual(zero_day["net_consumption_minor"], 0)
        self.assertEqual(zero_day["transaction_count"], 0)

    def test_weekly_aggregation_and_complete_week(self):
        self.assertEqual(len(self.datasets.weekly), 2)
        self.assertTrue(self.datasets.weekly["is_complete_week"].all())
        self.assertEqual(self.datasets.weekly["net_consumption_minor"].tolist(), [8000, 8000])

    def test_weekly_series_keeps_zero_transaction_week(self):
        values = build_consumption_datasets([
            tx(1, "2026-01-05 08:00:00", 100),
            tx(2, "2026-01-25 08:00:00", 200),
        ])
        self.assertEqual(len(values.weekly), 3)
        self.assertEqual(values.weekly.iloc[1]["net_consumption_minor"], 0)
        self.assertEqual(values.weekly.iloc[1]["transaction_count"], 0)

    def test_partial_boundary_weeks_are_marked(self):
        partial = build_consumption_datasets([
            tx(1, "2026-01-07 08:00:00", 100),
            tx(2, "2026-01-15 08:00:00", 100),
        ])
        self.assertFalse(partial.weekly["is_complete_week"].any())

    def test_monthly_aggregation(self):
        self.assertEqual(len(self.datasets.monthly), 1)
        self.assertEqual(self.datasets.monthly.iloc[0]["net_consumption_minor"], 16000)

    def test_monthly_series_keeps_empty_month(self):
        values = build_consumption_datasets([
            tx(1, "2026-01-05 08:00:00", 100),
            tx(2, "2026-03-05 08:00:00", 200),
        ])
        self.assertEqual(values.monthly["year_month"].tolist(), ["2026-01", "2026-02", "2026-03"])
        self.assertEqual(values.monthly.iloc[1]["net_consumption_minor"], 0)

    def test_user_category_overrides_system_category(self):
        facts = self.datasets.facts.set_index("transaction_id")
        self.assertEqual(facts.loc[5, "category_code"], "shopping")

    def test_other_statistics_are_preserved(self):
        quality = category_quality_table(self.datasets.facts, self.datasets.category_weekly, self.datasets.weekly)
        other = quality.loc[quality["category_code"] == "other"].iloc[0]
        self.assertEqual(other["transaction_count"], 1)
        self.assertEqual(other["qualification"], "C")

    def test_total_only_policy_never_assigns_refund_to_food(self):
        facts = build_transaction_facts(self.transactions, refund_policy=RefundAllocationPolicy.TOTAL_ONLY)
        refund = facts.loc[facts["refund_count"] == 1].iloc[0]
        self.assertEqual(refund["category_code"], UNALLOCATED_REFUND)

    def test_reliable_policy_assigns_only_linked_refund(self):
        values = self.transactions + [
            tx(7, "2026-01-14 12:00:00", 500, flow="expense_refund", direction="inflow", category="shopping", related=None)
        ]
        facts = build_transaction_facts(values, refund_policy=RefundAllocationPolicy.RELIABLE_LINKS)
        categories = facts.loc[facts["refund_count"] == 1, "category_code"].tolist()
        self.assertEqual(categories, ["food", UNALLOCATED_REFUND])

    def test_weekly_statistics_use_complete_weeks_only(self):
        stats = weekly_descriptive_statistics(self.datasets.weekly)
        self.assertEqual(stats["complete_weeks"], 2)
        self.assertEqual(stats["mean_minor"], 8000)

    def test_stage1_style_core_formula_reconciles(self):
        # Same formula as stage 1: outflow consumption minus expense refunds;
        # transfers and balance movements contribute zero.
        expected = 10000 - 2000 + 5000 + 3000
        self.assertEqual(calculate_net_consumption(self.transactions), expected)

    def test_irregular_essential_stays_in_actual_but_not_routine_baseline(self):
        values = build_consumption_datasets([
            tx(1, "2026-01-05 08:00:00", 1000, category="food"),
            tx(2, "2026-01-06 08:00:00", 607000, category="education",
               planning_role="irregular_essential"),
        ])
        week = values.weekly.iloc[0]
        self.assertEqual(week["net_consumption_minor"], 608000)
        self.assertEqual(week["routine_net_consumption_minor"], 1000)
        self.assertEqual(week["irregular_essential_minor"], 607000)
        forecast = build_weekly_forecast_dataset(values.weekly, complete_only=False)
        self.assertEqual(forecast.iloc[0]["target_minor"], 1000)


class ForecastDatasetTests(unittest.TestCase):
    def setUp(self):
        starts = pd.date_range("2026-01-05", periods=8, freq="W-MON").date
        self.weekly = pd.DataFrame({
            "week_start": list(reversed(starts)),
            "is_complete_week": True,
            "net_consumption_minor": list(reversed([100, 200, 300, 400, 500, 600, 700, 800])),
            "category_net__food": list(reversed([10, 20, 30, 40, 50, 60, 70, 80])),
        })

    def test_output_is_sorted(self):
        result = build_weekly_forecast_dataset(self.weekly)
        self.assertTrue(result["week_start"].is_monotonic_increasing)

    def test_lag_features_use_prior_weeks(self):
        result = build_weekly_forecast_dataset(self.weekly)
        self.assertEqual(result.loc[4, "lag_1"], 400)
        self.assertEqual(result.loc[4, "lag_4"], 100)

    def test_rolling_features_use_only_prior_weeks(self):
        result = build_weekly_forecast_dataset(self.weekly)
        self.assertEqual(result.loc[4, "rolling_mean_2"], 350)
        self.assertEqual(result.loc[4, "rolling_mean_4"], 250)

    def test_current_target_change_does_not_change_current_features(self):
        baseline = build_weekly_forecast_dataset(self.weekly)
        changed = self.weekly.copy()
        target_week = baseline.loc[5, "week_start"]
        changed.loc[changed["week_start"] == target_week, "net_consumption_minor"] = 999999
        updated = build_weekly_forecast_dataset(changed)
        feature_columns = ["lag_1", "lag_2", "lag_3", "lag_4", "rolling_mean_2", "rolling_mean_4", "rolling_std_4"]
        self.assertEqual(baseline.loc[5, feature_columns].tolist(), updated.loc[5, feature_columns].tolist())

    def test_category_target_uses_same_generator(self):
        result = build_weekly_forecast_dataset(self.weekly, target_category_code="food")
        self.assertEqual(result.loc[4, "target_minor"], 50)
        self.assertEqual(result.loc[4, "lag_1"], 40)


if __name__ == "__main__":
    unittest.main()
