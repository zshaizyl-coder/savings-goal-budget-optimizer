from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from backend.app.domain.enums import AdjustmentMode
from backend.app.optimization.feasibility import SavingsGoalFeasibilityEvaluator, income_total_minor
from backend.app.optimization.interfaces import (
    BudgetOptimizationRequest,
    BudgetOptimizationResult,
    FeasibilityRequest,
    PlanDifference,
    RecoveryMetrics,
    ReplanRequest,
    ReplanResult,
)
from backend.app.optimization.optimizer import PersonalizedBudgetOptimizer
from .forecast import model_reevaluation_due


def no_replan_expected_savings(request: ReplanRequest, future_income_minor: int) -> int:
    old_future = sum(item.recommended_budget_minor for item in request.previous_plan.items
                     if item.week_start >= request.next_open_week)
    return request.current_snapshot.available_savings_minor + future_income_minor - old_future


class RollingBudgetReplanner:
    def __init__(self, optimizer=None):
        self.optimizer = optimizer or PersonalizedBudgetOptimizer()

    def replan(self, request: ReplanRequest) -> ReplanResult:
        self._validate(request)
        open_weeks = tuple(sorted({
            item.week_start for item in request.latest_predictions
            if item.target_key != "total" and item.week_start >= request.next_open_week
            and item.week_start <= request.remaining_goal.target_date
        }))
        if not open_weeks:
            raise ValueError("no_open_planning_weeks")
        locked_through = request.next_open_week - timedelta(days=7)
        old_future_items = tuple(item for item in request.previous_plan.items
                                 if item.week_start >= request.next_open_week)
        old_future_total = sum(item.recommended_budget_minor for item in old_future_items)
        income_schedule = request.income_schedule
        feasibility_request = FeasibilityRequest(
            request.remaining_goal, request.current_snapshot, income_schedule,
            request.latest_predictions, request.current_constraints, open_weeks,
        )
        future_income = income_total_minor(feasibility_request)
        no_replan_final = request.current_snapshot.available_savings_minor + future_income - old_future_total
        recovery_gap = max(0, request.remaining_goal.target_savings_minor - no_replan_final)

        if request.current_snapshot.available_savings_minor >= request.remaining_goal.target_savings_minor:
            result = BudgetOptimizationResult(
                True, request.strategy, "goal-achieved-v1",
                request.current_snapshot.available_savings_minor, (),
                ("goal_already_achieved",), optimization_mode=request.adjustment_mode,
                plan_start=open_weeks[0], plan_end=open_weeks[-1] + timedelta(days=6),
                required_savings_minor=0, natural_spending_minor=0,
                recommended_spending_minor=0, habit_deviation_bps=0,
                risk_level="LOW_RISK", minimum_violation_count=0,
            )
            return self._result(request, locked_through, result, old_future_items,
                                recovery_gap=0, status="GOAL_ACHIEVED")

        feasibility = SavingsGoalFeasibilityEvaluator().evaluate(feasibility_request)
        cumulative_caps = ()
        if recovery_gap:
            horizon = request.recovery_horizon_weeks or self._default_horizon(
                request.adjustment_mode, len(open_weeks)
            )
            horizon = min(max(1, horizon), len(open_weeks))
            deadline = open_weeks[horizon - 1]
            old_prefix = sum(item.recommended_budget_minor for item in old_future_items
                             if item.week_start <= deadline)
            cumulative_caps = ((deadline, max(0, old_prefix - recovery_gap)),)
        optimization_request = BudgetOptimizationRequest(
            request.remaining_goal, request.current_snapshot, income_schedule,
            request.latest_predictions, request.current_constraints, feasibility,
            request.adjustment_mode, open_weeks, cumulative_caps,
        )
        result = self.optimizer.optimize(optimization_request)
        status = "REPLANNED" if result.feasible else "REPLAN_INFEASIBLE"
        return self._result(request, locked_through, result, old_future_items,
                            recovery_gap=recovery_gap, status=status)

    @staticmethod
    def _default_horizon(mode: AdjustmentMode, remaining: int) -> int:
        return {
            AdjustmentMode.GOAL_PRIORITY: min(2, remaining),
            AdjustmentMode.BALANCED: min(4, remaining),
            AdjustmentMode.LIFE_STABILITY: remaining,
            AdjustmentMode.CUSTOM: remaining,
        }[mode]

    @staticmethod
    def _validate(request: ReplanRequest) -> None:
        if request.next_open_week.weekday() != 0:
            raise ValueError("next_open_week_must_be_monday")
        if request.current_snapshot.as_of_date != request.next_open_week:
            raise ValueError("snapshot_must_match_next_open_week")
        if request.remaining_goal.target_date < request.next_open_week:
            raise ValueError("goal_target_precedes_open_period")
        if request.previous_plan.user_id != request.current_snapshot.user_id \
                or request.previous_plan.user_id != request.remaining_goal.user_id:
            raise ValueError("user_mismatch")
        if len({event.id for event in request.events}) != len(request.events):
            raise ValueError("duplicate_event")
        if any(event.user_id != request.previous_plan.user_id for event in request.events):
            raise ValueError("event_user_mismatch")
        if any(item.week_start >= request.next_open_week for item in request.locked_actuals):
            raise ValueError("future_actual_leakage")
        actual_keys = [(item.week_start, item.target_key) for item in request.locked_actuals]
        if len(actual_keys) != len(set(actual_keys)):
            raise ValueError("duplicate_actual")

        predictions_by_week: defaultdict[object, list[str]] = defaultdict(list)
        for item in request.latest_predictions:
            if item.week_start < request.next_open_week:
                raise ValueError("prediction_overlaps_locked_history")
            if item.week_start > request.remaining_goal.target_date:
                raise ValueError("prediction_after_goal_date")
            predictions_by_week[item.week_start].append(item.target_key)
        if not predictions_by_week:
            raise ValueError("future_predictions_required")
        prediction_weeks = sorted(predictions_by_week)
        if any(right - left != timedelta(days=7)
               for left, right in zip(prediction_weeks, prediction_weeks[1:])):
            raise ValueError("prediction_weeks_must_be_contiguous")
        expected_categories: set[str] | None = None
        for keys in predictions_by_week.values():
            if len(keys) != len(set(keys)) or keys.count("total") != 1:
                raise ValueError("prediction_targets_invalid")
            categories = set(keys) - {"total"}
            if not categories:
                raise ValueError("category_predictions_required")
            if expected_categories is None:
                expected_categories = categories
            elif categories != expected_categories:
                raise ValueError("prediction_categories_must_be_consistent")

    @staticmethod
    def _result(request, locked_through, optimization, old_future_items,
                *, recovery_gap: int, status: str) -> ReplanResult:
        old_total = sum(item.recommended_budget_minor for item in old_future_items)
        new_total = (None if status == "GOAL_ACHIEVED" else
                     optimization.recommended_spending_minor if optimization.feasible else None)
        delta = (new_total - old_total) if new_total is not None else 0
        old_by_category: defaultdict[str, int] = defaultdict(int)
        new_by_category: defaultdict[str, int] = defaultdict(int)
        old_by_week: defaultdict[object, int] = defaultdict(int)
        new_by_week: defaultdict[object, int] = defaultdict(int)
        for item in old_future_items:
            old_by_category[item.category_code] += item.recommended_budget_minor
            old_by_week[item.week_start] += item.recommended_budget_minor
        for item in optimization.items:
            new_by_category[item.category_code] += item.recommended_budget_minor
            new_by_week[item.week_start] += item.recommended_budget_minor
        category_deltas = tuple((category, new_by_category[category] - old_by_category[category])
                                for category in sorted(set(old_by_category) | set(new_by_category)))
        weeks_to_recover = None
        if recovery_gap == 0:
            weeks_to_recover = 0
        elif optimization.feasible:
            recovered = 0
            for index, week in enumerate(sorted(new_by_week), 1):
                recovered += max(0, old_by_week[week] - new_by_week[week])
                if recovered >= recovery_gap:
                    weeks_to_recover = index
                    break
        average = round(delta / len(new_by_week)) if new_by_week and new_total is not None else None
        maximum = max((abs(value) for _category, value in category_deltas), default=None)
        difference = PlanDifference(
            request.previous_plan.expected_final_savings_minor,
            optimization.expected_final_savings_minor,
            old_total, new_total, delta if new_total is not None else None, average,
            category_deltas, request.previous_plan.risk_level, optimization.risk_level,
            request.previous_plan.target_date_snapshot
            or request.previous_plan.plan_end_week + timedelta(days=6),
            request.remaining_goal.target_date,
        )
        metrics = RecoveryMetrics(
            recovery_gap,
            max(0, request.remaining_goal.target_savings_minor - request.current_snapshot.available_savings_minor),
            delta if new_total is not None else None, average, maximum,
            optimization.habit_deviation_bps, weeks_to_recover,
        )
        return ReplanResult(
            request.previous_plan.id, request.previous_plan.version + 1,
            tuple(event.id for event in request.events), locked_through,
            optimization, delta,
            explanations=(
                f"locked_through_week={locked_through.isoformat()}",
                f"recovery_gap_minor={recovery_gap}",
                f"trigger_source={request.trigger_source.value}",
                "events_are_audit_inputs_not_double_counted",
            ),
            status_code=status, difference=difference, recovery_metrics=metrics,
            model_reevaluation_due=model_reevaluation_due(
                request.locked_actuals, request.model_selected_through_week,
            ),
        )
