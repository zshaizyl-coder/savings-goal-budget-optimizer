from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import unittest

import pandas as pd

from backend.app.analytics.forecast_dataset import DATASET_VERSION, build_weekly_forecast_dataset
from backend.app.forecasting.evaluation import allocate_weekly_to_months, rolling_backtest
from backend.app.forecasting.models import (
    HistoricalMeanForecaster, HistoricalMedianForecaster, NumericForecast,
    RecentMeanForecaster, RecentMedianForecaster, SimpleExponentialSmoothingForecaster,
)
from backend.app.forecasting.interfaces import ForecastRequest, WeeklyActual
from backend.app.forecasting.service import ProductionWeeklyForecaster


class LastValue:
    name, version = "last", "test"
    def forecast(self, history, horizon):
        return NumericForecast((float(history[-1]),) * horizon, {})


class RecordingForecaster:
    name, version = "recording", "test"
    def __init__(self): self.histories = []
    def forecast(self, history, horizon):
        self.histories.append(tuple(history))
        return NumericForecast((float(history[-1]),) * horizon, {})


class SometimesFail:
    name, version = "sometimes_fail", "test"
    def forecast(self, history, horizon):
        if len(history) == 5: raise RuntimeError("synthetic")
        return NumericForecast((float(history[-1]),) * horizon, {})


class NegativeForecaster:
    name, version = "negative", "test"
    def forecast(self, history, horizon):
        return NumericForecast((-10.0,) * horizon, {})


def weekly_frame() -> pd.DataFrame:
    dates = pd.date_range("2026-01-05", periods=10, freq="W-MON").date
    return pd.DataFrame({
        "week_start": dates, "is_complete_week": True,
        "net_consumption_minor": range(100, 110), "category_net__food": range(10, 20),
    })


class Stage6ForecastTests(unittest.TestCase):
    def test_01_rolling_split_order_is_monotonic(self):
        result = rolling_backtest(range(1, 11), LastValue(), horizon=1, initial_train_weeks=4)
        self.assertEqual([p.origin_index for p in result.points], list(range(3, 9)))

    def test_02_training_never_contains_target_or_future(self):
        model = RecordingForecaster()
        result = rolling_backtest(range(1, 9), model, horizon=1, initial_train_weeks=4)
        for history, point in zip(model.histories, result.points, strict=True):
            self.assertEqual(len(history), point.origin_index + 1)
            self.assertNotIn(point.actual_minor, history)

    def test_03_lag_feature_has_no_current_week_leakage(self):
        original = build_weekly_forecast_dataset(weekly_frame())
        changed = weekly_frame(); changed.loc[5, "net_consumption_minor"] = 9999
        rebuilt = build_weekly_forecast_dataset(changed)
        self.assertEqual(original.loc[5, "lag_1"], rebuilt.loc[5, "lag_1"])

    def test_04_rolling_feature_has_no_current_week_leakage(self):
        original = build_weekly_forecast_dataset(weekly_frame())
        changed = weekly_frame(); changed.loc[5, "net_consumption_minor"] = 9999
        rebuilt = build_weekly_forecast_dataset(changed)
        self.assertEqual(original.loc[5, "rolling_mean_4"], rebuilt.loc[5, "rolling_mean_4"])

    def test_05_all_required_baselines_are_correct(self):
        history = [1, 2, 3, 4, 100]
        self.assertEqual(HistoricalMeanForecaster().forecast(history, 1).raw_predictions[0], 22)
        self.assertEqual(HistoricalMedianForecaster().forecast(history, 1).raw_predictions[0], 3)
        self.assertEqual(RecentMeanForecaster(2).forecast(history, 1).raw_predictions[0], 52)
        self.assertEqual(RecentMeanForecaster(4).forecast(history, 1).raw_predictions[0], 27.25)
        self.assertEqual(RecentMedianForecaster(4).forecast(history, 1).raw_predictions[0], 3.5)

    def test_06_mae_is_correct(self):
        metric = rolling_backtest([10, 20, 30, 40, 50], LastValue(), horizon=1, initial_train_weeks=3).metrics
        self.assertEqual(metric.mae_minor, 10)

    def test_07_rmse_is_correct(self):
        metric = rolling_backtest([10, 20, 30, 40, 50], LastValue(), horizon=1, initial_train_weeks=3).metrics
        self.assertEqual(metric.rmse_minor, 10)

    def test_08_wape_is_correct(self):
        metric = rolling_backtest([10, 20, 30, 40, 50], LastValue(), horizon=1, initial_train_weeks=3).metrics
        self.assertEqual(metric.wape_bps, 2222)

    def test_09_smape_is_correct(self):
        metric = rolling_backtest([10, 20, 30, 40, 50], LastValue(), horizon=1, initial_train_weeks=3).metrics
        self.assertEqual(metric.smape_bps, 2540)

    def test_10_mape_is_none_when_any_actual_is_zero(self):
        metric = rolling_backtest([1, 2, 3, 0, 5], LastValue(), horizon=1, initial_train_weeks=3).metrics
        self.assertIsNone(metric.mape_bps)

    def test_11_one_week_prediction_length(self):
        self.assertEqual(len(SimpleExponentialSmoothingForecaster().forecast(range(1, 13), 1).raw_predictions), 1)

    def test_12_two_week_prediction_length(self):
        self.assertEqual(len(SimpleExponentialSmoothingForecaster().forecast(range(1, 13), 2).raw_predictions), 2)

    def test_13_four_week_prediction_length(self):
        self.assertEqual(len(SimpleExponentialSmoothingForecaster().forecast(range(1, 13), 4).raw_predictions), 4)

    def test_14_single_model_failure_does_not_abort_experiment(self):
        result = rolling_backtest(range(1, 10), SometimesFail(), horizon=1, initial_train_weeks=4)
        self.assertEqual(result.metrics.failure_count, 1)
        self.assertGreater(result.metrics.sample_count, 0)

    def test_15_negative_prediction_is_recorded_then_clamped(self):
        result = rolling_backtest(range(1, 10), NegativeForecaster(), horizon=1, initial_train_weeks=4)
        self.assertTrue(all(point.raw_prediction_minor < 0 for point in result.points))
        self.assertTrue(all(point.prediction_minor == 0 for point in result.points))
        self.assertEqual(result.metrics.negative_prediction_count, result.metrics.sample_count)

    def test_16_interval_structure_uses_prior_errors(self):
        result = rolling_backtest(range(1, 15), LastValue(), horizon=1, initial_train_weeks=4)
        self.assertIsNone(result.points[0].interval80_lower_minor)
        self.assertIsNotNone(result.points[-1].interval80_lower_minor)
        self.assertLessEqual(result.points[-1].interval80_lower_minor, result.points[-1].interval80_upper_minor)

    def test_17_experiment_is_reproducible(self):
        first = rolling_backtest(range(1, 15), LastValue(), horizon=2, initial_train_weeks=5)
        second = rolling_backtest(range(1, 15), LastValue(), horizon=2, initial_train_weeks=5)
        self.assertEqual(first, second)

    def test_18_dataset_version_is_frozen(self):
        result = build_weekly_forecast_dataset(weekly_frame())
        self.assertEqual(DATASET_VERSION, "weekly-forecast-v2-routine")
        self.assertEqual(set(result.dataset_version), {DATASET_VERSION})

    def test_19_month_allocation_conserves_cross_month_amount(self):
        result = allocate_weekly_to_months([date(2026, 3, 30)], [701])
        self.assertEqual(result, {"2026-03": 201, "2026-04": 500})
        self.assertEqual(sum(result.values()), 701)

    def test_20_backtest_output_has_no_transaction_text_fields(self):
        result = rolling_backtest(range(1, 12), LastValue(), horizon=1, initial_train_weeks=4)
        keys = set(asdict(result.points[0]))
        self.assertTrue(keys.isdisjoint({"merchant", "description", "counterparty", "account"}))

    def test_21_production_adapter_keeps_contract_and_mondays(self):
        history = tuple(
            WeeklyActual(date(2026, 1, 5) + timedelta(days=7 * index), "total", 100 + index, True, False)
            for index in range(8)
        )
        request = ForecastRequest(
            history=history, horizon_weeks=2, target_keys=("total",),
            data_cutoff_utc=datetime(2026, 3, 1, tzinfo=timezone.utc),
            model_name="historical_mean", model_version="total-weekly-forecast-v1",
            parameters={"absolute_errors_minor": [10, 20, 30]},
        )
        result = ProductionWeeklyForecaster().forecast(request)
        self.assertEqual(len(result.items), 2)
        self.assertTrue(all(item.week_start.weekday() == 0 for item in result.items))
        self.assertTrue(all(item.lower_minor is not None for item in result.items))


if __name__ == "__main__":
    unittest.main()
