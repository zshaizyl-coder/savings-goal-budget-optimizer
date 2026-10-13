from __future__ import annotations

from datetime import date, timedelta
from dataclasses import replace
from types import SimpleNamespace
import unittest

from backend.app.domain.contracts import (
    BudgetConstraintSpec, FinancialSnapshotSpec, IncomeAssumptionSpec,
    SavingsGoalSpec, WeeklyPredictionItem,
)
from backend.app.domain.enums import AdjustmentMode, BudgetStrategy
from backend.app.optimization.baselines import build_category_natural_baselines
from backend.app.optimization.feasibility import SavingsGoalFeasibilityEvaluator
from backend.app.optimization.interfaces import BudgetOptimizationRequest, FeasibilityRequest
from backend.app.optimization.optimizer import (
    HistoricalShareOptimizer, PersonalizedBudgetOptimizer, ProportionalReductionOptimizer,
)


START = date(2026, 1, 5)
CATEGORIES = {"food": 10_000, "shopping": 8_000, "housing": 5_000, "other": 3_000}


def make_inputs(*, weeks_count=4, target=122_000, current=100_000, weekly_income=30_000,
                mode=AdjustmentMode.BALANCED, other_amount=3_000, constraints_override=None):
    weeks = tuple(START + timedelta(days=7 * index) for index in range(weeks_count))
    goal = SavingsGoalSpec(1, 1, 1, START, weeks[-1] + timedelta(days=6), target)
    snapshot = FinancialSnapshotSpec(1, 1, START, current)
    income = () if weekly_income == 0 else (
        IncomeAssumptionSpec(1, "weekly", weekly_income, START, weeks[-1] + timedelta(days=6)),
    )
    specs = {
        "food": (6_000, None, False, 2_500),
        "shopping": (2_000, None, False, 10_000),
        "housing": (5_000, 5_000, True, 0),
        "other": (1_000, None, False, 5_000),
    }
    specs.update(constraints_override or {})
    constraints = tuple(
        BudgetConstraintSpec(1, category, minimum, maximum, fixed, adjustability,
                             START, weeks[-1], 1)
        for category, (minimum, maximum, fixed, adjustability) in specs.items()
    )
    natural = dict(CATEGORIES); natural["other"] = other_amount
    predictions = []
    for week in weeks:
        for category, amount in natural.items():
            predictions.append(WeeklyPredictionItem(week, category, amount))
        predictions.append(WeeklyPredictionItem(week, "total", sum(natural.values()),
                                                 lower_minor=10_000, upper_minor=40_000))
    feasibility_request = FeasibilityRequest(goal, snapshot, income, tuple(predictions), constraints, weeks)
    feasibility = SavingsGoalFeasibilityEvaluator().evaluate(feasibility_request)
    optimization = BudgetOptimizationRequest(goal, snapshot, income, tuple(predictions), constraints,
                                               feasibility, mode, weeks)
    return feasibility_request, optimization


class Stage7BudgetTests(unittest.TestCase):
    def test_01_normal_goal_is_feasible(self):
        self.assertTrue(make_inputs()[0] and make_inputs()[1].feasibility.feasible)

    def test_02_loose_goal_keeps_natural_budget(self):
        request = make_inputs(target=100_000)[1]
        result = PersonalizedBudgetOptimizer().optimize(request)
        self.assertEqual(result.recommended_spending_minor, result.natural_spending_minor)

    def test_03_boundary_goal_is_feasible(self):
        self.assertTrue(make_inputs(target=164_000)[1].feasibility.feasible)

    def test_04_impossible_goal_is_rejected(self):
        result = make_inputs(target=170_000)[1].feasibility
        self.assertFalse(result.feasible)
        self.assertEqual(result.reason_code, "INFEASIBLE_GOAL")

    def test_05_current_savings_above_goal(self):
        request = make_inputs(target=90_000, current=200_000, weekly_income=0)[1]
        self.assertTrue(request.feasibility.feasible)

    def test_06_zero_income_is_supported(self):
        request = make_inputs(target=40_000, weekly_income=0)[1]
        self.assertTrue(request.feasibility.feasible)

    def test_07_fixed_expense_is_unchanged(self):
        result = PersonalizedBudgetOptimizer().optimize(make_inputs()[1])
        self.assertEqual({item.recommended_budget_minor for item in result.items if item.category_code == "housing"}, {5_000})

    def test_08_minimum_budget_is_enforced(self):
        result = PersonalizedBudgetOptimizer().optimize(make_inputs(target=160_000, mode=AdjustmentMode.GOAL_PRIORITY)[1])
        self.assertTrue(all(item.recommended_budget_minor >= item.minimum_constraint_minor for item in result.items))

    def test_09_non_adjustable_category_is_unchanged(self):
        override = {"food": (10_000, 10_000, True, 0)}
        result = PersonalizedBudgetOptimizer().optimize(make_inputs(constraints_override=override)[1])
        self.assertEqual({item.recommended_budget_minor for item in result.items if item.category_code == "food"}, {10_000})

    def test_10_other_is_independent_budget_bucket(self):
        result = PersonalizedBudgetOptimizer().optimize(make_inputs()[1])
        self.assertEqual(len([item for item in result.items if item.category_code == "other"]), 4)

    def test_11_target_first_is_supported(self):
        result = PersonalizedBudgetOptimizer().optimize(make_inputs(mode=AdjustmentMode.GOAL_PRIORITY)[1])
        self.assertTrue(result.feasible)
        self.assertEqual(result.optimization_mode, AdjustmentMode.GOAL_PRIORITY)

    def test_12_balanced_is_supported(self):
        result = PersonalizedBudgetOptimizer().optimize(make_inputs(mode=AdjustmentMode.BALANCED)[1])
        self.assertTrue(result.feasible)

    def test_13_lifestyle_first_is_supported(self):
        result = PersonalizedBudgetOptimizer().optimize(make_inputs(mode=AdjustmentMode.LIFE_STABILITY)[1])
        self.assertTrue(result.feasible)

    def test_14_modes_produce_real_differences(self):
        results = [PersonalizedBudgetOptimizer().optimize(make_inputs(mode=mode)[1]) for mode in (
            AdjustmentMode.GOAL_PRIORITY, AdjustmentMode.BALANCED, AdjustmentMode.LIFE_STABILITY,
        )]
        signatures = {(result.recommended_spending_minor,
                       tuple(item.recommended_budget_minor for item in result.items)) for result in results}
        self.assertEqual(len(signatures), 3)

    def test_15_all_budgets_are_non_negative(self):
        result = PersonalizedBudgetOptimizer().optimize(make_inputs(target=160_000, mode=AdjustmentMode.GOAL_PRIORITY)[1])
        self.assertTrue(all(item.recommended_budget_minor >= 0 for item in result.items))

    def test_16_minimum_violation_count_is_zero(self):
        result = PersonalizedBudgetOptimizer().optimize(make_inputs()[1])
        self.assertEqual(result.minimum_violation_count, 0)

    def test_17_final_savings_meets_target(self):
        request = make_inputs()[1]; result = PersonalizedBudgetOptimizer().optimize(request)
        self.assertGreaterEqual(result.expected_final_savings_minor, request.goal.target_savings_minor)

    def test_18_money_conservation(self):
        request = make_inputs()[1]; result = PersonalizedBudgetOptimizer().optimize(request)
        funds = request.financial_snapshot.available_savings_minor + 4 * 30_000
        self.assertEqual(result.expected_final_savings_minor + result.recommended_spending_minor, funds)

    def test_19_solver_failure_is_distinct(self):
        failed = lambda *args, **kwargs: SimpleNamespace(success=False)
        result = PersonalizedBudgetOptimizer(solver=failed).optimize(make_inputs()[1])
        self.assertFalse(result.feasible)
        self.assertEqual(result.infeasibility.reason_code, "SOLVER_FAILURE")

    def test_20_infeasible_advice_is_correct(self):
        result = make_inputs(target=170_000)[1].feasibility
        self.assertEqual(result.maximum_achievable_target_minor, 164_000)
        self.assertEqual(result.required_additional_income_minor, 6_000)

    def test_21_proportional_baseline(self):
        result = ProportionalReductionOptimizer().optimize(make_inputs()[1])
        self.assertTrue(result.feasible); self.assertEqual(result.strategy, BudgetStrategy.PROPORTIONAL)

    def test_22_historical_share_baseline(self):
        result = HistoricalShareOptimizer().optimize(make_inputs()[1])
        self.assertTrue(result.feasible); self.assertEqual(result.strategy, BudgetStrategy.HISTORICAL_SHARE)

    def test_23_different_time_horizons(self):
        short = PersonalizedBudgetOptimizer().optimize(make_inputs(weeks_count=4)[1])
        long = PersonalizedBudgetOptimizer().optimize(make_inputs(weeks_count=8, target=190_000)[1])
        self.assertEqual(len(long.items), len(short.items) * 2)

    def test_24_monthly_summary_conserves_budget(self):
        result = PersonalizedBudgetOptimizer().optimize(make_inputs(weeks_count=8, target=190_000)[1])
        self.assertEqual(sum(value for _month, value in result.monthly_budget_minor), result.recommended_spending_minor)

    def test_25_pressure_scenario_sets_medium_risk(self):
        result = PersonalizedBudgetOptimizer().optimize(make_inputs(target=100_000)[1])
        self.assertEqual(result.risk_level, "MEDIUM_RISK")

    def test_26_category_profile_reconciles_total_and_keeps_other(self):
        profile = build_category_natural_baselines(
            {"food": [10, 20], "shopping": [20, 40], "other": [5, 5]}, 100,
            model_overrides={"food": 25},
        )
        self.assertEqual(sum(profile.values()), 100)
        self.assertIn("other", profile)

    def test_27_high_other_scenario_does_not_delete_other(self):
        result = PersonalizedBudgetOptimizer().optimize(make_inputs(other_amount=10_000, target=122_000)[1])
        self.assertTrue(any(item.category_code == "other" and item.recommended_budget_minor > 0 for item in result.items))

    def test_28_baselines_respect_user_maximum(self):
        override = {"shopping": (2_000, 4_000, False, 10_000)}
        request = make_inputs(target=100_000, constraints_override=override)[1]
        for optimizer in (ProportionalReductionOptimizer(), HistoricalShareOptimizer()):
            result = optimizer.optimize(request)
            self.assertTrue(all(item.recommended_budget_minor <= 4_000
                                for item in result.items if item.category_code == "shopping"))

    def test_29_sparse_category_history_is_not_erased(self):
        profile = build_category_natural_baselines(
            {"food": [100, 100, 100], "other": [0, 0, 30]}, 400,
            model_overrides={"food": 300},
        )
        self.assertEqual(profile, {"food": 300, "other": 100})

    def test_30_smoothing_reduces_unnecessary_weekly_changes(self):
        request = make_inputs(target=122_000)[1]
        amounts = dict(zip(request.planning_weeks, (14_000, 6_000, 14_000, 6_000)))
        predictions = tuple(
            replace(item, predicted_minor=amounts[item.week_start])
            if item.target_key == "food" else item
            for item in request.predictions
        )
        request = replace(request, predictions=predictions)
        unsmoothed = PersonalizedBudgetOptimizer(smoothing_override=0).optimize(request)
        smoothed = PersonalizedBudgetOptimizer(smoothing_override=5).optimize(request)
        self.assertLess(smoothed.weekly_volatility_minor, unsmoothed.weekly_volatility_minor)

    def test_31_mode_infeasibility_keeps_actionable_advice(self):
        result = PersonalizedBudgetOptimizer().optimize(
            make_inputs(target=160_000, mode=AdjustmentMode.LIFE_STABILITY)[1]
        )
        self.assertEqual(result.infeasibility.reason_code, "MODE_CONSTRAINT_INFEASIBLE")
        self.assertEqual(result.infeasibility.maximum_achievable_target_minor, 135_200)
        self.assertEqual(result.infeasibility.required_additional_income_minor, 24_800)


if __name__ == "__main__":
    unittest.main()
