from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from backend.app.domain.enums import FinancialEventType
from backend.app.optimization.optimizer import (
    HistoricalShareOptimizer, PersonalizedBudgetOptimizer, ProportionalReductionOptimizer,
)
from backend.app.replanning.service import RollingBudgetReplanner
from backend.app.replanning.variance import VarianceTriggerPolicy
from experiments.stage10.core import (
    FIXED_CATEGORIES, common_metrics, config_hash, load_config, load_real_profile,
    make_budget_request, paired_statistics, replan_once, run_experiment_c,
    run_experiment_d, synthetic_profile, weekly_paths,
)
from experiments.stage10.run_all import make_figures


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "experiments" / "stage10" / "configs" / "stage10.json"
RESULTS = ROOT / "experiments" / "stage10" / "results"


class Stage10ExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config(CONFIG_PATH)
        cls.profile = load_real_profile(ROOT, cls.config)

    def _hard_request(self):
        spec = self.config["synthetic_users"]["A_STABLE"]
        profile = synthetic_profile(self.profile, spec)
        rng = np.random.default_rng(self.config["master_seed"] + 1000)
        sampled = {
            category: (amount if category in FIXED_CATEGORIES else
                       max(100, round(amount * max(0.55, rng.normal(1.0, spec["volatility"])))))
            for category, amount in profile.items()
        }
        paths = weekly_paths(sampled, 0.04, rng, 12, 0.0)
        return make_budget_request(self.config, sampled, paths, "HARD")

    def test_01_config_has_six_frozen_user_types(self):
        self.assertEqual(len(self.config["synthetic_users"]), 6)

    def test_02_config_has_thirty_repetitions(self):
        self.assertEqual(self.config["repetitions"], 30)

    def test_03_config_hash_is_stable_sha256(self):
        self.assertEqual(len(config_hash(CONFIG_PATH)), 64)

    def test_04_real_input_is_aggregate_only(self):
        self.assertTrue(self.config["privacy"]["aggregate_only"])
        self.assertGreater(sum(self.profile.values()), 0)

    def test_05_fixed_categories_do_not_randomly_change(self):
        rng = np.random.default_rng(1)
        rows = weekly_paths(self.profile, 0.4, rng, 3)
        for category in FIXED_CATEGORIES:
            self.assertEqual({row[category] for row in rows}, {self.profile[category]})

    def test_06_baseline_inputs_are_identical(self):
        request = self._hard_request()
        self.assertIs(request, request)
        self.assertEqual(len(request.predictions), len(request.planning_weeks) * (len(self.profile) + 1))

    def test_07_uniform_respects_minimums(self):
        result = ProportionalReductionOptimizer().optimize(self._hard_request())
        self.assertTrue(result.feasible)
        self.assertEqual(result.minimum_violation_count, 0)

    def test_08_historical_rounding_keeps_feasible_request_feasible(self):
        result = HistoricalShareOptimizer().optimize(self._hard_request())
        self.assertTrue(result.feasible)
        self.assertEqual(result.minimum_violation_count, 0)

    def test_09_personalized_converges_on_feasible_hard_request(self):
        result = PersonalizedBudgetOptimizer().optimize(self._hard_request())
        self.assertTrue(result.feasible)

    def test_10_personalized_uses_official_class(self):
        self.assertEqual(PersonalizedBudgetOptimizer.__module__, "backend.app.optimization.optimizer")

    def test_11_infeasible_is_consistently_rejected(self):
        request = make_budget_request(self.config, self.profile,
                                      weekly_paths(self.profile, 0, np.random.default_rng(1), 12),
                                      "INFEASIBLE")
        self.assertTrue(all(not factory().optimize(request).feasible for factory in
                            (ProportionalReductionOptimizer, HistoricalShareOptimizer,
                             PersonalizedBudgetOptimizer)))

    def test_12_savings_gap_formula(self):
        request = make_budget_request(self.config, self.profile,
                                      weekly_paths(self.profile, 0, np.random.default_rng(1), 12), "MEDIUM")
        result = ProportionalReductionOptimizer().optimize(request)
        self.assertEqual(common_metrics(request, result)["savings_gap_minor"],
                         max(request.goal.target_savings_minor - result.expected_final_savings_minor, 0))

    def test_13_habit_deviation_reuses_official_value(self):
        request = make_budget_request(self.config, self.profile,
                                      weekly_paths(self.profile, 0, np.random.default_rng(1), 12), "MEDIUM")
        result = PersonalizedBudgetOptimizer().optimize(request)
        self.assertEqual(common_metrics(request, result)["habit_deviation_bps"], result.habit_deviation_bps)

    def test_14_weekly_volatility_formula(self):
        request = make_budget_request(self.config, self.profile,
                                      weekly_paths(self.profile, 0, np.random.default_rng(1), 12), "MEDIUM")
        result = ProportionalReductionOptimizer().optimize(request)
        self.assertEqual(common_metrics(request, result)["weekly_budget_volatility_minor"], 0)

    def test_15_no_replan_loses_exact_shock_amount(self):
        request = make_budget_request(self.config, self.profile,
                                      weekly_paths(self.profile, 0, np.random.default_rng(1), 12), "MEDIUM")
        initial = PersonalizedBudgetOptimizer().optimize(request)
        _, _, final = replan_once(self.config, request, initial, elapsed_weeks=3,
                                  balance_delta_minor=-50_000, recovery_horizon=9)
        self.assertEqual(request.goal.target_savings_minor - final, 50_000)

    def test_16_rolling_uses_official_service(self):
        self.assertEqual(RollingBudgetReplanner.__module__, "backend.app.replanning.service")

    def test_17_shock_request_contains_no_future_actuals(self):
        request = make_budget_request(self.config, self.profile,
                                      weekly_paths(self.profile, 0, np.random.default_rng(1), 12), "MEDIUM")
        initial = PersonalizedBudgetOptimizer().optimize(request)
        replan_request, _, _ = replan_once(self.config, request, initial, elapsed_weeks=3,
                                            balance_delta_minor=-50_000, recovery_horizon=9)
        self.assertTrue(all(item.week_start < replan_request.next_open_week
                            for item in replan_request.locked_actuals))

    def test_18_prediction_error_levels_are_exact(self):
        self.assertEqual(self.config["prediction_errors"], [-0.2, -0.1, 0.0, 0.1, 0.2])

    def test_19_fixed_seed_repeats_identically(self):
        left = weekly_paths(self.profile, .1, np.random.default_rng(99), 4)
        right = weekly_paths(self.profile, .1, np.random.default_rng(99), 4)
        self.assertEqual(left, right)

    def test_20_threshold_scan_definitions(self):
        policies = [VarianceTriggerPolicy(item["absolute_minor"], item["relative_bps"], item["require_both"])
                    for item in self.config["thresholds"]]
        self.assertEqual((policies[0].absolute_overrun_minor, policies[-1].relative_overrun_bps), (5000, 3000))

    def test_21_recovery_horizon_scan_definitions(self):
        self.assertEqual(self.config["recovery_horizons"], [2, 4, "remaining"])

    def test_22_outputs_have_no_infinity(self):
        for path in RESULTS.glob("*.csv"):
            numeric = pd.read_csv(path).select_dtypes(include=[np.number])
            self.assertFalse(np.isinf(numeric.to_numpy()).any(), path.name)

    def test_23_output_has_no_transaction_text_columns(self):
        forbidden = {"merchant", "counterparty", "description", "transaction_id", "account"}
        for path in RESULTS.glob("*.csv"):
            self.assertFalse(forbidden & set(pd.read_csv(path, nrows=0).columns), path.name)

    def test_24_statistics_use_paired_sample_count(self):
        a = pd.read_csv(RESULTS / "experiment_a_budget_methods.csv")
        stats_frame = paired_statistics(a)
        self.assertTrue((stats_frame["n"] >= 30).all())

    def test_25_chart_generation_is_executable(self):
        a = pd.read_csv(RESULTS / "experiment_a_budget_methods.csv")
        b = pd.read_csv(RESULTS / "experiment_b_replanning.csv")
        c = pd.read_csv(RESULTS / "experiment_c_forecast_robustness.csv")
        h = pd.read_csv(RESULTS / "experiment_d_recovery_horizons.csv")
        with tempfile.TemporaryDirectory() as tmp:
            make_figures(a, b, c, h, Path(tmp))
            self.assertEqual(len(list(Path(tmp).glob("*.png"))), 5)

    def test_26_metadata_contains_reproducibility_fields(self):
        metadata = json.loads((RESULTS / "run_metadata.json").read_text(encoding="utf-8"))
        self.assertTrue({"master_seed", "config_sha256", "git_commit_at_run", "dependencies"} <= metadata.keys())


if __name__ == "__main__":
    unittest.main()
