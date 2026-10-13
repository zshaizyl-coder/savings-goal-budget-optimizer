from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select

from backend.app.db.session import create_session_factory, create_sqlite_engine
from backend.app.domain.contracts import (
    BudgetConstraintSpec, BudgetPlanItem, BudgetPlanVersion, FinancialEventSpec,
    FinancialSnapshotSpec, IncomeAssumptionSpec, SavingsGoalSpec, WeeklyPredictionItem,
)
from backend.app.domain.enums import (
    AdjustmentMode, BudgetStrategy, FinancialEventType, ImpactScope, ReplanTriggerSource,
)
from backend.app.forecasting.interfaces import WeeklyActual
from backend.app.models import (
    BudgetPlan, BudgetPlanEvent, BudgetPlanItemModel, Category, FinancialEvent,
    FinancialSnapshot, SavingGoal, User,
)
from backend.app.replanning import (
    PersistReplanCommand, ReplanPersistenceService, RollingBudgetReplanner,
    RollingForecastUpdater, VarianceTriggerPolicy, calculate_weekly_variance,
    choose_replan_trigger, model_reevaluation_due, no_replan_expected_savings,
)


ROOT = Path(__file__).resolve().parents[1]
START = date(2026, 1, 5)
CATEGORIES = {"food": (10_000, 9_000), "shopping": (8_000, 7_000),
              "housing": (5_000, 5_000), "other": (3_000, 3_000)}


def previous_plan(plan_id=10, version=1, parent=None) -> BudgetPlanVersion:
    items = tuple(
        BudgetPlanItem(
            START + timedelta(days=7 * week), category, natural, budget,
            budget - natural, {"food": 6_000, "shopping": 2_000,
                               "housing": 5_000, "other": 1_000}[category],
            5_000 if category == "housing" else None,
            {"food": 2_500, "shopping": 10_000, "housing": 0, "other": 5_000}[category],
        )
        for week in range(8) for category, (natural, budget) in CATEGORIES.items()
    )
    return BudgetPlanVersion(
        plan_id, 1, 1, version, BudgetStrategy.PERSONALIZED, AdjustmentMode.BALANCED,
        START, START + timedelta(days=49), True, items, parent_plan_id=parent,
        expected_final_savings_minor=708_000, snapshot_id=1,
        risk_level="MEDIUM_RISK", target_date_snapshot=START + timedelta(days=55),
    )


def event(event_id: int, event_type: FinancialEventType, amount: int | None = None,
          goal_id: int = 1) -> FinancialEventSpec:
    scope = ImpactScope.ACTUAL_SPEND
    if event_type in {FinancialEventType.EXTRA_INCOME, FinancialEventType.INCOME_DECREASE,
                      FinancialEventType.INCOME_INCREASE}:
        scope = ImpactScope.FUTURE_INCOME
    elif event_type in {FinancialEventType.GOAL_AMOUNT_CHANGE, FinancialEventType.GOAL_DATE_CHANGE}:
        scope = ImpactScope.GOAL
    elif event_type == FinancialEventType.BUDGET_CONSTRAINT_CHANGE:
        scope = ImpactScope.CONSTRAINT
    return FinancialEventSpec(event_id, 1, goal_id, event_type, amount,
                              START + timedelta(days=10), scope, "SIMULATED_EVENT")


def make_request(*, shock=0, extra=0, weekly_income=50_000, target=650_000,
                 mode=AdjustmentMode.GOAL_PRIORITY, recovery=6, total_weeks=8,
                 constraints_override=None, events=(), current_override=None,
                 plan=None) -> object:
    plan = plan or previous_plan()
    next_open = START + timedelta(days=14)
    weeks = tuple(START + timedelta(days=7 * index) for index in range(2, total_weeks))
    goal = SavingsGoalSpec(
        1, 1, 1, START, START + timedelta(days=7 * (total_weeks - 1) + 6), target,
    )
    current = 552_000 - shock + extra if current_override is None else current_override
    snapshot = FinancialSnapshotSpec(2, 1, next_open, current, "user_input")
    income = (IncomeAssumptionSpec(1, "weekly", weekly_income, next_open,
                                   weeks[-1] + timedelta(days=6)),)
    specs = {
        "food": (6_000, None, False, 2_500),
        "shopping": (2_000, None, False, 10_000),
        "housing": (5_000, 5_000, True, 0),
        "other": (1_000, None, False, 5_000),
    }
    specs.update(constraints_override or {})
    constraints = tuple(
        BudgetConstraintSpec(1, category, minimum, maximum, fixed, adjustability,
                             next_open, weeks[-1], 1)
        for category, (minimum, maximum, fixed, adjustability) in specs.items()
    )
    predictions = []
    for week in weeks:
        for category, (natural, _budget) in CATEGORIES.items():
            predictions.append(WeeklyPredictionItem(week, category, natural))
        predictions.append(WeeklyPredictionItem(week, "total", 26_000, 10_000, 40_000))
    actuals = []
    for index in range(2):
        week = START + timedelta(days=7 * index)
        for category, (_natural, budget) in CATEGORIES.items():
            amount = budget + (shock if index == 1 and category == "other" else 0)
            actuals.append(WeeklyActual(week, category, amount, True, shock > 0))
        actuals.append(WeeklyActual(
            week, "total", 24_000 + (shock if index == 1 else 0), True, shock > 0,
        ))
    from backend.app.optimization.interfaces import ReplanRequest
    return ReplanRequest(
        plan, tuple(actuals), tuple(events), snapshot, goal, constraints,
        tuple(predictions), next_open, BudgetStrategy.PERSONALIZED, mode,
        income_schedule=income, recovery_horizon_weeks=recovery,
        trigger_source=ReplanTriggerSource.EVENT_REQUIRED if events else ReplanTriggerSource.USER_REQUESTED,
        model_selected_through_week=START - timedelta(days=7),
    )


class RollingReplanAlgorithmTests(unittest.TestCase):
    def test_01_normal_week_has_zero_variance(self):
        request = make_request()
        report = calculate_weekly_variance(request.previous_plan.items, request.locked_actuals, START)
        self.assertEqual(report.absolute_variance_minor, 0)
        self.assertFalse(report.replan_suggested)

    def test_02_small_overrun_does_not_suggest_replan(self):
        request = make_request(shock=5_000)
        report = calculate_weekly_variance(request.previous_plan.items, request.locked_actuals, START + timedelta(days=7))
        self.assertFalse(report.replan_suggested)

    def test_03_threshold_overrun_suggests_replan(self):
        request = make_request(shock=10_000)
        report = calculate_weekly_variance(request.previous_plan.items, request.locked_actuals, START + timedelta(days=7))
        self.assertTrue(report.replan_suggested)
        self.assertEqual(choose_replan_trigger(report, ()), ReplanTriggerSource.SYSTEM_SUGGESTED)

    def test_04_manual_replan_is_always_available(self):
        self.assertEqual(choose_replan_trigger(None, (), manual=True), ReplanTriggerSource.USER_REQUESTED)

    def test_05_event_required_trigger(self):
        self.assertEqual(choose_replan_trigger(None, (event(1, FinancialEventType.UNPLANNED_EXPENSE, 50_000),)),
                         ReplanTriggerSource.EVENT_REQUIRED)

    def test_06_expense_500_uses_existing_buffer(self):
        result = RollingBudgetReplanner().replan(make_request(
            shock=50_000, events=(event(1, FinancialEventType.UNPLANNED_EXPENSE, 50_000),)
        ))
        self.assertTrue(result.optimization_result.feasible)
        self.assertEqual(result.recovery_metrics.recovery_gap_minor, 0)

    def test_07_expense_1000_is_spread_and_feasible(self):
        result = RollingBudgetReplanner().replan(make_request(
            shock=100_000, events=(event(2, FinancialEventType.UNPLANNED_EXPENSE, 100_000),)
        ))
        self.assertTrue(result.optimization_result.feasible)
        self.assertEqual(result.recovery_metrics.recovery_gap_minor, 42_000)
        self.assertGreater(result.recovery_metrics.weeks_to_recover, 1)

    def test_08_expense_3000_makes_goal_infeasible(self):
        result = RollingBudgetReplanner().replan(make_request(
            shock=300_000, events=(event(3, FinancialEventType.UNPLANNED_EXPENSE, 300_000),)
        ))
        self.assertEqual(result.status_code, "REPLAN_INFEASIBLE")
        self.assertEqual(result.optimization_result.infeasibility.reason_code, "INFEASIBLE_GOAL")

    def test_09_expense_5000_makes_goal_infeasible(self):
        result = RollingBudgetReplanner().replan(make_request(
            shock=500_000, events=(event(4, FinancialEventType.UNPLANNED_EXPENSE, 500_000),)
        ))
        self.assertFalse(result.optimization_result.feasible)

    def test_10_extra_income_500_is_not_all_added_to_budget(self):
        result = RollingBudgetReplanner().replan(make_request(
            extra=50_000, events=(event(5, FinancialEventType.EXTRA_INCOME, 50_000),)
        ))
        self.assertTrue(result.optimization_result.feasible)
        self.assertLess(result.difference.new_future_budget_minor - result.difference.old_future_budget_minor, 50_000)

    def test_11_extra_income_2000(self):
        result = RollingBudgetReplanner().replan(make_request(
            extra=200_000, events=(event(6, FinancialEventType.EXTRA_INCOME, 200_000),)
        ))
        self.assertGreater(result.optimization_result.expected_final_savings_minor, 650_000)

    def test_12_extra_income_5000(self):
        result = RollingBudgetReplanner().replan(make_request(
            extra=500_000, events=(event(7, FinancialEventType.EXTRA_INCOME, 500_000),)
        ))
        self.assertEqual(result.status_code, "GOAL_ACHIEVED")
        self.assertEqual(result.optimization_result.recommended_spending_minor, 0)

    def test_13_income_decrease_10_percent(self):
        result = RollingBudgetReplanner().replan(make_request(
            weekly_income=45_000, events=(event(8, FinancialEventType.INCOME_DECREASE, 30_000),)
        ))
        self.assertTrue(result.optimization_result.feasible)

    def test_14_income_decrease_30_percent(self):
        result = RollingBudgetReplanner().replan(make_request(
            weekly_income=35_000, events=(event(9, FinancialEventType.INCOME_DECREASE, 90_000),)
        ))
        self.assertTrue(result.optimization_result.feasible)
        self.assertLess(result.difference.new_future_budget_minor, result.difference.old_future_budget_minor)

    def test_15_goal_amount_increase(self):
        result = RollingBudgetReplanner().replan(make_request(
            target=680_000, events=(event(10, FinancialEventType.GOAL_AMOUNT_CHANGE),)
        ))
        self.assertTrue(result.optimization_result.feasible)

    def test_16_goal_amount_decrease(self):
        result = RollingBudgetReplanner().replan(make_request(
            target=600_000, events=(event(11, FinancialEventType.GOAL_AMOUNT_CHANGE),)
        ))
        self.assertGreaterEqual(result.optimization_result.expected_final_savings_minor, 600_000)

    def test_17_shorter_deadline_uses_fewer_open_weeks(self):
        result = RollingBudgetReplanner().replan(make_request(total_weeks=6, target=600_000))
        self.assertEqual(len({item.week_start for item in result.optimization_result.items}), 4)

    def test_18_extended_deadline_adds_open_weeks(self):
        result = RollingBudgetReplanner().replan(make_request(total_weeks=10, target=700_000, recovery=8))
        self.assertEqual(len({item.week_start for item in result.optimization_result.items}), 8)

    def test_19_higher_food_minimum_is_respected(self):
        result = RollingBudgetReplanner().replan(make_request(
            shock=100_000, constraints_override={"food": (8_000, None, False, 2_500)}
        ))
        self.assertTrue(all(item.recommended_budget_minor >= 8_000 for item in result.optimization_result.items
                            if item.category_code == "food"))

    def test_20_low_shopping_adjustability_changes_limits(self):
        result = RollingBudgetReplanner().replan(make_request(
            shock=100_000, constraints_override={"shopping": (2_000, None, False, 2_500)}
        ))
        self.assertTrue(all(item.recommended_budget_minor >= 6_000 for item in result.optimization_result.items
                            if item.category_code == "shopping"))

    def test_21_new_fixed_category_is_unchanged(self):
        result = RollingBudgetReplanner().replan(make_request(
            constraints_override={"shopping": (7_000, 7_000, True, 0)}
        ))
        self.assertEqual({item.recommended_budget_minor for item in result.optimization_result.items
                          if item.category_code == "shopping"}, {7_000})

    def test_22_only_future_weeks_are_optimized(self):
        result = RollingBudgetReplanner().replan(make_request())
        self.assertTrue(all(item.week_start >= START + timedelta(days=14)
                            for item in result.optimization_result.items))

    def test_23_previous_plan_is_not_mutated(self):
        request = make_request(shock=100_000)
        before = request.previous_plan.items
        RollingBudgetReplanner().replan(request)
        self.assertEqual(request.previous_plan.items, before)

    def test_24_parent_and_version_are_correct(self):
        result = RollingBudgetReplanner().replan(make_request())
        self.assertEqual((result.parent_plan_id, result.new_version), (10, 2))

    def test_25_multiple_events_are_retained(self):
        events = (event(12, FinancialEventType.UNPLANNED_EXPENSE, 20_000),
                  event(13, FinancialEventType.INCOME_DECREASE, 10_000))
        result = RollingBudgetReplanner().replan(make_request(events=events))
        self.assertEqual(result.trigger_event_ids, (12, 13))

    def test_26_consecutive_replans_form_chain(self):
        second = RollingBudgetReplanner().replan(make_request())
        plan2 = replace(previous_plan(plan_id=11, version=2, parent=10),
                        expected_final_savings_minor=second.optimization_result.expected_final_savings_minor)
        third = RollingBudgetReplanner().replan(make_request(plan=plan2))
        plan3 = replace(previous_plan(plan_id=12, version=3, parent=11),
                        expected_final_savings_minor=third.optimization_result.expected_final_savings_minor)
        fourth = RollingBudgetReplanner().replan(make_request(plan=plan3))
        self.assertEqual(((third.parent_plan_id, third.new_version),
                          (fourth.parent_plan_id, fourth.new_version)), ((11, 3), (12, 4)))

    def test_27_plan_difference_is_money_conserving(self):
        result = RollingBudgetReplanner().replan(make_request(shock=100_000))
        difference = result.difference
        self.assertEqual(difference.total_adjustment_minor,
                         difference.new_future_budget_minor - difference.old_future_budget_minor)

    def test_28_risk_is_recomputed(self):
        result = RollingBudgetReplanner().replan(make_request(shock=100_000))
        self.assertEqual(result.difference.old_risk_level, "MEDIUM_RISK")
        self.assertIsNotNone(result.difference.new_risk_level)

    def test_29_goal_achieved_stops_budget_compression(self):
        result = RollingBudgetReplanner().replan(make_request(current_override=700_000))
        self.assertEqual(result.status_code, "GOAL_ACHIEVED")
        self.assertEqual(result.optimization_result.items, ())

    def test_30_replanned_budget_never_breaks_minimum_or_fixed(self):
        result = RollingBudgetReplanner().replan(make_request(shock=100_000))
        self.assertEqual(result.optimization_result.minimum_violation_count, 0)
        self.assertEqual({item.recommended_budget_minor for item in result.optimization_result.items
                          if item.category_code == "housing"}, {5_000})

    def test_31_future_actuals_are_rejected(self):
        request = make_request()
        leaked = (*request.locked_actuals, WeeklyActual(request.next_open_week, "total", 1, True, False))
        with self.assertRaisesRegex(ValueError, "future_actual_leakage"):
            RollingBudgetReplanner().replan(replace(request, locked_actuals=leaked))

    def test_32_forecast_refits_with_latest_history(self):
        history = []
        for index, total in enumerate((20_000, 20_000, 20_000, 100_000)):
            week = START + timedelta(days=7 * index)
            for category, share in (("food", .4), ("shopping", .3), ("other", .3)):
                history.append(WeeklyActual(week, category, round(total * share), True, False))
            history.append(WeeklyActual(week, "total", total, True, False))
        next_week = START + timedelta(days=28)
        predictions = RollingForecastUpdater().forecast(history, (next_week,), ("food", "shopping", "other"))
        total = next(item.predicted_minor for item in predictions if item.target_key == "total")
        self.assertEqual(total, 40_000)

    def test_33_model_reevaluation_only_after_eight_new_weeks(self):
        history = tuple(WeeklyActual(START + timedelta(days=7 * index), "total", 1, True, False)
                        for index in range(9))
        self.assertTrue(model_reevaluation_due(history, START))
        self.assertFalse(model_reevaluation_due(history[:8], START))

    def test_34_no_replan_baseline_uses_old_future_budget(self):
        request = make_request(shock=100_000)
        self.assertEqual(no_replan_expected_savings(request, 300_000), 608_000)

    def test_35_recovery_metric_matches_cumulative_definition(self):
        result = RollingBudgetReplanner().replan(make_request(shock=100_000))
        self.assertEqual(result.recovery_metrics.recovery_gap_minor, 42_000)
        self.assertLessEqual(result.recovery_metrics.weeks_to_recover, 6)

    def test_36_modes_produce_different_replans(self):
        results = [RollingBudgetReplanner().replan(make_request(
            shock=50_000, mode=mode, recovery=6
        )) for mode in (AdjustmentMode.GOAL_PRIORITY, AdjustmentMode.BALANCED,
                        AdjustmentMode.LIFE_STABILITY)]
        signatures = {tuple(item.recommended_budget_minor for item in result.optimization_result.items)
                      for result in results}
        self.assertEqual(len(signatures), 3)

    def test_37_snapshot_is_anchored_to_first_open_week(self):
        request = make_request()
        stale = replace(request.current_snapshot,
                        as_of_date=request.next_open_week - timedelta(days=1))
        with self.assertRaisesRegex(ValueError, "snapshot_must_match_next_open_week"):
            RollingBudgetReplanner().replan(replace(request, current_snapshot=stale))

    def test_38_prediction_weeks_must_be_contiguous(self):
        request = make_request()
        missing_week = request.next_open_week + timedelta(days=7)
        predictions = tuple(item for item in request.latest_predictions
                            if item.week_start != missing_week)
        with self.assertRaisesRegex(ValueError, "prediction_weeks_must_be_contiguous"):
            RollingBudgetReplanner().replan(replace(request, latest_predictions=predictions))

    def test_39_prediction_categories_must_match_each_week(self):
        request = make_request()
        predictions = tuple(item for item in request.latest_predictions
                            if not (item.week_start == request.next_open_week
                                    and item.target_key == "shopping"))
        with self.assertRaisesRegex(ValueError, "prediction_categories_must_be_consistent"):
            RollingBudgetReplanner().replan(replace(request, latest_predictions=predictions))


class RollingReplanPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "replan.sqlite3"
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", f"sqlite:///{self.db.as_posix()}")
        command.upgrade(config, "head")
        self.engine = create_sqlite_engine(self.db)
        self.Session = create_session_factory(self.engine)
        with self.Session.begin() as session:
            session.add(User(id=1, username="simulated-user", timezone="Asia/Shanghai"))
            session.add_all(Category(id=index, code=code, display_name=code, sort_order=index)
                            for index, code in enumerate(CATEGORIES, 1))
            session.flush()
            session.add(FinancialSnapshot(id=1, user_id=1, as_of_date=START,
                                          available_savings_minor=500_000,
                                          currency="CNY", source="user_input"))
            session.flush()
            session.add(SavingGoal(id=1, user_id=1, baseline_snapshot_id=1,
                                   start_date=START, target_date=START + timedelta(days=55),
                                   target_savings_minor=650_000, currency="CNY",
                                   status="active", version=1))
            session.flush()
            session.add(BudgetPlan(
                id=10, user_id=1, goal_id=1, snapshot_id=1, version=1,
                strategy="personalized", adjustment_mode="balanced", status="active",
                plan_start_week=START, plan_end_week=START + timedelta(days=49),
                target_savings_snapshot_minor=650_000,
                target_date_snapshot=START + timedelta(days=55), is_feasible=True,
                expected_final_savings_minor=708_000, risk_level="MEDIUM_RISK",
                diagnostics_json="[]", algorithm_version="personalized-slsqp-v1", is_current=True,
            ))
            session.flush()
            session.add(BudgetPlanItemModel(
                budget_plan_id=10, week_start=START, category_id=1,
                natural_spend_minor=10_000, recommended_budget_minor=9_000,
                adjustment_minor=-1_000, minimum_constraint_minor=6_000,
                adjustability_bps=2_500, explanation_code="initial",
            ))

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def _command(self):
        request = make_request(shock=100_000, events=(event(100, FinancialEventType.UNPLANNED_EXPENSE, 100_000),))
        result = RollingBudgetReplanner().replan(request)
        return PersistReplanCommand(10, request.current_snapshot, request.remaining_goal,
                                    tuple(request.events), result)

    def test_37_migration_creates_replan_tables(self):
        tables = set(inspect(self.engine).get_table_names())
        self.assertTrue({"financial_snapshots", "saving_goals", "financial_events",
                         "budget_plans", "budget_plan_events", "budget_plan_items"} <= tables)

    def test_38_replan_persists_version_event_and_items_atomically(self):
        with self.Session() as session:
            new_id = ReplanPersistenceService().save(session, self._command())
        with self.Session() as session:
            old = session.get(BudgetPlan, 10)
            new = session.get(BudgetPlan, new_id)
            self.assertEqual((old.status, old.is_current), ("superseded", False))
            self.assertEqual((new.parent_plan_id, new.version, new.is_current), (10, 2, True))
            self.assertEqual(session.scalar(select(func.count()).select_from(FinancialEvent)), 1)
            self.assertGreater(session.scalar(select(func.count()).select_from(BudgetPlanItemModel)), 1)
            self.assertEqual(session.scalar(select(func.count()).select_from(BudgetPlanEvent)), 1)

    def test_39_transaction_failure_rolls_back_active_switch(self):
        with self.Session() as session:
            with self.assertRaisesRegex(RuntimeError, "simulated_transaction_failure"):
                ReplanPersistenceService().save(session, self._command(), fail_after_retire_for_test=True)
        with self.Session() as session:
            old = session.get(BudgetPlan, 10)
            self.assertEqual((old.status, old.is_current), ("active", True))
            self.assertEqual(session.scalar(select(func.count()).select_from(FinancialEvent)), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(FinancialSnapshot)), 1)

    def test_40_event_cannot_be_persisted_twice(self):
        with self.Session() as session:
            ReplanPersistenceService().save(session, self._command())
        with self.Session() as session, self.assertRaises(ValueError):
            ReplanPersistenceService().save(session, self._command())

    def test_41_goal_change_creates_version_instead_of_overwrite(self):
        command_value = self._command()
        revised_goal = replace(command_value.goal, id=2, baseline_snapshot_id=2,
                               target_savings_minor=660_000, version=2)
        command_value = replace(command_value, goal=revised_goal)
        with self.Session() as session:
            new_id = ReplanPersistenceService().save(session, command_value)
        with self.Session() as session:
            old_goal = session.get(SavingGoal, 1)
            new_goal = session.get(SavingGoal, 2)
            new_plan = session.get(BudgetPlan, new_id)
            self.assertEqual(old_goal.target_savings_minor, 650_000)
            self.assertEqual((new_goal.previous_goal_id, new_goal.version), (1, 2))
            self.assertEqual(new_plan.goal_id, 2)


if __name__ == "__main__":
    unittest.main()
