from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from math import sqrt
from typing import Callable

import numpy as np
from scipy.optimize import minimize

from backend.app.domain.contracts import BudgetPlanItem
from backend.app.domain.enums import AdjustmentMode, BudgetStrategy

from .feasibility import SavingsGoalFeasibilityEvaluator, constraint_for_week, income_total_minor
from .interfaces import (
    BudgetOptimizationRequest, BudgetOptimizationResult, FeasibilityRequest, FeasibilityResult,
)


MODE_REDUCTION_FACTOR = {
    AdjustmentMode.GOAL_PRIORITY: 1.0,
    AdjustmentMode.BALANCED: 0.75,
    AdjustmentMode.LIFE_STABILITY: 0.40,
    AdjustmentMode.CUSTOM: 0.75,
}
MODE_WEIGHT_POWER = {
    AdjustmentMode.GOAL_PRIORITY: 0.5,
    AdjustmentMode.BALANCED: 1.0,
    AdjustmentMode.LIFE_STABILITY: 2.0,
    AdjustmentMode.CUSTOM: 1.0,
}
MODE_SMOOTHING = {
    AdjustmentMode.GOAL_PRIORITY: 0.05,
    AdjustmentMode.BALANCED: 0.20,
    AdjustmentMode.LIFE_STABILITY: 0.80,
    AdjustmentMode.CUSTOM: 0.20,
}


def _natural_map(request: BudgetOptimizationRequest) -> dict[tuple[date, str], int]:
    result = {}
    for item in request.predictions:
        if item.target_key == "total":
            continue
        key = (item.week_start, item.target_key)
        if key in result:
            raise ValueError(f"duplicate_prediction:{key}")
        result[key] = item.predicted_minor
    if not result:
        raise ValueError("category_predictions_required")
    return result


def _risk_level(request: BudgetOptimizationRequest, income: int, natural_total: int) -> str:
    funds = request.financial_snapshot.available_savings_minor + income
    point_ok = funds - natural_total >= request.goal.target_savings_minor
    upper_by_week = {
        item.week_start: item.upper_minor for item in request.predictions
        if item.target_key == "total" and item.upper_minor is not None
    }
    point_by_week = {
        item.week_start: item.predicted_minor for item in request.predictions if item.target_key == "total"
    }
    upper_total = sum(upper_by_week.get(week, point_by_week.get(week, 0)) for week in request.planning_weeks)
    upper_ok = funds - upper_total >= request.goal.target_savings_minor
    return "LOW_RISK" if upper_ok else "MEDIUM_RISK" if point_ok else "HIGH_RISK"


def _monthly(items: tuple[BudgetPlanItem, ...]) -> tuple[tuple[str, int], ...]:
    totals: defaultdict[str, int] = defaultdict(int)
    for item in items:
        base, remainder = divmod(item.recommended_budget_minor, 7)
        for offset in range(7):
            day = item.week_start + timedelta(days=offset)
            totals[day.strftime("%Y-%m")] += base + (1 if offset < remainder else 0)
    return tuple(sorted(totals.items()))


def _metrics(items: tuple[BudgetPlanItem, ...]) -> tuple[int, int]:
    deviations = []
    by_category: defaultdict[str, list[int]] = defaultdict(list)
    for item in items:
        scale = max(item.natural_spend_minor, item.minimum_constraint_minor, 100)
        deviations.append(((item.recommended_budget_minor - item.natural_spend_minor) / scale) ** 2)
        by_category[item.category_code].append(item.recommended_budget_minor)
    habit_bps = round(sqrt(sum(deviations) / len(deviations)) * 10_000) if deviations else 0
    changes = [abs(current - previous) for values in by_category.values() for previous, current in zip(values, values[1:])]
    volatility = round(sum(changes) / len(changes)) if changes else 0
    return habit_bps, volatility


def _build_result(request: BudgetOptimizationRequest, strategy: BudgetStrategy,
                  algorithm_version: str, budgets: dict[tuple[date, str], int],
                  diagnostics: tuple[str, ...]) -> BudgetOptimizationResult:
    natural = _natural_map(request)
    items = []
    for week, category in sorted(natural):
        constraint = constraint_for_week(
            FeasibilityRequest(request.goal, request.financial_snapshot, request.income_schedule,
                               request.predictions, request.constraints, request.planning_weeks),
            category, week,
        )
        recommended = budgets[(week, category)]
        items.append(BudgetPlanItem(
            week_start=week, category_code=category, natural_spend_minor=natural[(week, category)],
            recommended_budget_minor=recommended,
            adjustment_minor=recommended - natural[(week, category)],
            minimum_constraint_minor=constraint.minimum_budget_minor,
            maximum_constraint_minor=constraint.maximum_budget_minor,
            adjustability_bps=constraint.adjustability_bps,
        ))
    item_tuple = tuple(items)
    income = income_total_minor(FeasibilityRequest(
        request.goal, request.financial_snapshot, request.income_schedule,
        request.predictions, request.constraints, request.planning_weeks,
    ))
    recommended_total = sum(item.recommended_budget_minor for item in item_tuple)
    natural_total = sum(item.natural_spend_minor for item in item_tuple)
    final_savings = request.financial_snapshot.available_savings_minor + income - recommended_total
    habit, volatility = _metrics(item_tuple)
    violations = sum(item.recommended_budget_minor < item.minimum_constraint_minor for item in item_tuple)
    generated_explanations = (
        f"natural_spending_minor={natural_total}",
        f"recommended_spending_minor={recommended_total}",
        f"target_savings_minor={request.goal.target_savings_minor}",
        "other_preserved_as_independent_category" if any(
            item.category_code == "other" for item in item_tuple
        ) else "other_not_present_in_input",
    )
    return BudgetOptimizationResult(
        feasible=True, strategy=strategy, algorithm_version=algorithm_version,
        expected_final_savings_minor=final_savings, items=item_tuple,
        diagnostics=diagnostics + generated_explanations,
        optimization_mode=request.adjustment_mode,
        plan_start=min(request.planning_weeks), plan_end=max(request.planning_weeks) + timedelta(days=6),
        required_savings_minor=max(0, request.goal.target_savings_minor - request.financial_snapshot.available_savings_minor),
        natural_spending_minor=natural_total, recommended_spending_minor=recommended_total,
        habit_deviation_bps=habit, risk_level=_risk_level(request, income, natural_total),
        monthly_budget_minor=_monthly(item_tuple), minimum_violation_count=violations,
        weekly_volatility_minor=volatility,
    )


def _infeasible(request: BudgetOptimizationRequest, strategy: BudgetStrategy,
                result: FeasibilityResult, code: str | None = None) -> BudgetOptimizationResult:
    if code:
        result = FeasibilityResult(False, code, explanations=(code.lower(),))
    return BudgetOptimizationResult(
        feasible=False, strategy=strategy, algorithm_version="budget-v1",
        expected_final_savings_minor=None, items=(), diagnostics=(result.reason_code or "INFEASIBLE",),
        infeasibility=result, optimization_mode=request.adjustment_mode,
        plan_start=min(request.planning_weeks) if request.planning_weeks else None,
        plan_end=max(request.planning_weeks) + timedelta(days=6) if request.planning_weeks else None,
    )


def _mode_infeasible(request: BudgetOptimizationRequest, income: int,
                     minimum_mode_total: int) -> BudgetOptimizationResult:
    funds = request.financial_snapshot.available_savings_minor + income
    maximum_target = max(0, funds - minimum_mode_total)
    shortfall = max(0, request.goal.target_savings_minor - maximum_target)
    result = FeasibilityResult(
        feasible=False,
        reason_code="MODE_CONSTRAINT_INFEASIBLE",
        maximum_achievable_target_minor=maximum_target,
        required_additional_income_minor=shortfall,
        binding_constraint_codes=tuple(sorted({item.category_code for item in request.constraints})),
        explanations=("selected_mode_adjustment_bounds_do_not_leave_enough_spending_reduction",),
        projected_income_minor=income,
        minimum_required_spending_minor=minimum_mode_total,
        available_for_spending_minor=max(0, funds - request.goal.target_savings_minor),
    )
    return _infeasible(request, BudgetStrategy.PERSONALIZED, result)


class PersonalizedBudgetOptimizer:
    def __init__(self, solver: Callable = minimize, smoothing_override: float | None = None):
        self.solver = solver
        self.smoothing_override = smoothing_override

    def optimize(self, request: BudgetOptimizationRequest) -> BudgetOptimizationResult:
        if not request.feasibility.feasible:
            return _infeasible(request, BudgetStrategy.PERSONALIZED, request.feasibility)
        natural = _natural_map(request); keys = sorted(natural)
        feasibility_request = FeasibilityRequest(
            request.goal, request.financial_snapshot, request.income_schedule,
            request.predictions, request.constraints, request.planning_weeks,
        )
        income = income_total_minor(feasibility_request)
        spending_cap = request.financial_snapshot.available_savings_minor + income - request.goal.target_savings_minor
        natural_total = sum(natural.values())
        if natural_total > spending_cap and request.adjustment_mode == AdjustmentMode.GOAL_PRIORITY:
            spending_cap -= min(spending_cap - (request.feasibility.minimum_required_spending_minor or 0),
                                round((natural_total - spending_cap) * 0.05))
        bounds, weights, scales = [], [], []
        factor = MODE_REDUCTION_FACTOR[request.adjustment_mode]
        power = MODE_WEIGHT_POWER[request.adjustment_mode]
        for week, category in keys:
            constraint = constraint_for_week(feasibility_request, category, week)
            mu = natural[(week, category)]
            if constraint.is_fixed:
                lower = upper = constraint.minimum_budget_minor
            else:
                willingness = constraint.adjustability_bps / 10_000
                lower = max(constraint.minimum_budget_minor, round(mu * (1 - willingness * factor)))
                upper = min(mu, constraint.maximum_budget_minor) if constraint.maximum_budget_minor is not None else mu
                if lower > upper:
                    return _infeasible(request, BudgetStrategy.PERSONALIZED, request.feasibility, "MODE_CONSTRAINT_INFEASIBLE")
            bounds.append((float(lower), float(upper)))
            weights.append(1 / max(0.05, constraint.adjustability_bps / 10_000 + 0.05) ** power)
            scales.append(max(mu, constraint.minimum_budget_minor, 100))
        cumulative_caps = tuple(sorted(request.cumulative_spending_caps))
        if any(deadline not in request.planning_weeks or deadline.weekday() != 0 or cap < 0
               for deadline, cap in cumulative_caps):
            raise ValueError("invalid_cumulative_spending_cap")
        minimum_mode_total = round(sum(lower for lower, _ in bounds))
        if minimum_mode_total > spending_cap:
            return _mode_infeasible(request, income, minimum_mode_total)
        for deadline, cap in cumulative_caps:
            prefix_minimum = round(sum(
                bounds[index][0] for index, key in enumerate(keys) if key[0] <= deadline
            ))
            if prefix_minimum > cap:
                return _infeasible(
                    request, BudgetStrategy.PERSONALIZED, request.feasibility,
                    "RECOVERY_HORIZON_INFEASIBLE",
                )
        x0 = np.asarray([min(upper, natural[key]) for key, (_lower, upper) in zip(keys, bounds)], dtype=float)
        if x0.sum() > spending_cap:
            lowers = np.asarray([lower for lower, _upper in bounds])
            room = x0.sum() - lowers.sum()
            ratio = (spending_cap - lowers.sum()) / room if room else 0.0
            x0 = lowers + np.clip(ratio, 0.0, 1.0) * (x0 - lowers)
        for deadline, cap in cumulative_caps:
            indices = [index for index, key in enumerate(keys) if key[0] <= deadline]
            excess = float(x0[indices].sum() - cap)
            if excess > 0:
                slack = np.asarray([x0[index] - bounds[index][0] for index in indices])
                if slack.sum() > 0:
                    x0[indices] -= np.minimum(slack, excess * slack / slack.sum())
        smoothing = (MODE_SMOOTHING[request.adjustment_mode]
                     if self.smoothing_override is None else self.smoothing_override)
        if smoothing < 0:
            raise ValueError("smoothing_weight_must_be_non_negative")
        index_by_key = {key: index for index, key in enumerate(keys)}
        previous_pairs = []
        for category in sorted({category for _week, category in keys}):
            category_keys = sorted(key for key in keys if key[1] == category)
            previous_pairs.extend(
                (index_by_key[previous], index_by_key[current])
                for previous, current in zip(category_keys, category_keys[1:])
            )

        def objective(x):
            deviation = sum(weight * ((value - natural[key]) / scale) ** 2
                            for value, key, weight, scale in zip(x, keys, weights, scales))
            smooth = sum(((x[current] - x[previous]) / max(scales[current], 100)) ** 2
                         for previous, current in previous_pairs)
            return deviation + smoothing * smooth

        def gradient(x):
            result = np.asarray([
                2 * weight * (value - natural[key]) / (scale ** 2)
                for value, key, weight, scale in zip(x, keys, weights, scales)
            ])
            for previous, current in previous_pairs:
                denominator = max(scales[current], 100) ** 2
                delta = 2 * smoothing * (x[current] - x[previous]) / denominator
                result[current] += delta
                result[previous] -= delta
            return result

        constraint_scale = max(float(spending_cap), 1.0)

        constraints = [{
            "type": "ineq",
            "fun": lambda x: (spending_cap - float(np.sum(x))) / constraint_scale,
            "jac": lambda x: -np.ones_like(x) / constraint_scale,
        }]
        for deadline, cap in cumulative_caps:
            mask = np.asarray([key[0] <= deadline for key in keys], dtype=float)
            cap_scale = max(float(cap), 1.0)
            constraints.append({
                "type": "ineq",
                "fun": lambda x, mask=mask, cap=cap, scale=cap_scale: (
                    cap - float(mask @ x)
                ) / scale,
                "jac": lambda x, mask=mask, scale=cap_scale: -mask / scale,
            })
        solved = self.solver(objective, x0, jac=gradient, method="SLSQP", bounds=bounds,
                             constraints=tuple(constraints),
                             options={"maxiter": 1500, "ftol": 1e-9})
        if not getattr(solved, "success", False):
            return _infeasible(request, BudgetStrategy.PERSONALIZED, request.feasibility, "SOLVER_FAILURE")
        integer = {key: max(round(bounds[index][0]), min(round(bounds[index][1]), round(value)))
                   for index, (key, value) in enumerate(zip(keys, solved.x))}
        excess = sum(integer.values()) - spending_cap
        if excess > 0:
            for key in sorted(keys, key=lambda item: integer[item] - bounds[keys.index(item)][0], reverse=True):
                index = keys.index(key); slack = integer[key] - round(bounds[index][0]); cut = min(slack, excess)
                integer[key] -= cut; excess -= cut
                if excess <= 0: break
        if excess > 0:
            return _infeasible(request, BudgetStrategy.PERSONALIZED, request.feasibility, "ROUNDING_RECONCILIATION_FAILURE")
        for deadline, cap in cumulative_caps:
            prefix_keys = [key for key in keys if key[0] <= deadline]
            excess = sum(integer[key] for key in prefix_keys) - cap
            if excess > 0:
                for key in sorted(prefix_keys, key=lambda item: integer[item] - bounds[keys.index(item)][0], reverse=True):
                    index = keys.index(key)
                    slack = integer[key] - round(bounds[index][0])
                    cut = min(slack, excess)
                    integer[key] -= cut
                    excess -= cut
                    if excess <= 0:
                        break
            if excess > 0:
                return _infeasible(
                    request, BudgetStrategy.PERSONALIZED, request.feasibility,
                    "ROUNDING_RECONCILIATION_FAILURE",
                )
        return _build_result(request, BudgetStrategy.PERSONALIZED, "personalized-slsqp-v1", integer,
                             ("weighted_normalized_habit_deviation", "weekly_smoothing", "hard_minimums"))


class ProportionalReductionOptimizer:
    def optimize(self, request: BudgetOptimizationRequest) -> BudgetOptimizationResult:
        return _baseline_optimize(request, BudgetStrategy.PROPORTIONAL, proportional=True)


class HistoricalShareOptimizer:
    def optimize(self, request: BudgetOptimizationRequest) -> BudgetOptimizationResult:
        return _baseline_optimize(request, BudgetStrategy.HISTORICAL_SHARE, proportional=False)


def _baseline_optimize(request: BudgetOptimizationRequest, strategy: BudgetStrategy,
                       *, proportional: bool) -> BudgetOptimizationResult:
    if not request.feasibility.feasible:
        return _infeasible(request, strategy, request.feasibility)
    natural = _natural_map(request); keys = sorted(natural)
    feasibility_request = FeasibilityRequest(request.goal, request.financial_snapshot, request.income_schedule,
                                             request.predictions, request.constraints, request.planning_weeks)
    income = income_total_minor(feasibility_request)
    cap = request.financial_snapshot.available_savings_minor + income - request.goal.target_savings_minor
    minima, maxima, fixed = {}, {}, set()
    for key in keys:
        constraint = constraint_for_week(feasibility_request, key[1], key[0])
        minima[key] = constraint.minimum_budget_minor
        maxima[key] = constraint.maximum_budget_minor
        if constraint.is_fixed or constraint.adjustability_bps == 0: fixed.add(key)
    budgets = {
        key: min(natural[key], maxima[key]) if maxima[key] is not None else natural[key]
        for key in keys
    }
    if sum(budgets.values()) > cap:
        if proportional:
            low, high = 0.0, 1.0
            for _ in range(60):
                ratio = (low + high) / 2
                trial = {
                    key: minima[key] if key in fixed else min(
                        maxima[key] if maxima[key] is not None else natural[key],
                        max(minima[key], round(natural[key] * ratio)),
                    )
                    for key in keys
                }
                if sum(trial.values()) > cap: high = ratio
                else: low = ratio
            budgets = {
                key: minima[key] if key in fixed else min(
                    maxima[key] if maxima[key] is not None else natural[key],
                    max(minima[key], round(natural[key] * low)),
                )
                for key in keys
            }
        else:
            budgets = dict(minima)
            remaining = cap - sum(budgets.values())
            flexible = [key for key in keys if key not in fixed]
            weight = sum(natural[key] for key in flexible)
            for key in flexible:
                ceiling = min(natural[key], maxima[key]) if maxima[key] is not None else natural[key]
                add = min(ceiling - budgets[key], round(remaining * natural[key] / weight)) if weight else 0
                budgets[key] += max(0, add)
            while sum(budgets.values()) < cap:
                candidates = [
                    key for key in flexible
                    if budgets[key] < (min(natural[key], maxima[key]) if maxima[key] is not None else natural[key])
                ]
                if not candidates: break
                key = max(candidates, key=lambda item: natural[item] - budgets[item]); budgets[key] += 1
    # Historical-share rounding can exceed the cap by a few minor units even
    # when the continuous allocation is feasible. Reconcile only that integer
    # excess; this does not change the baseline allocation rule.
    excess = sum(budgets.values()) - cap
    if excess > 0:
        for key in sorted(keys, key=lambda item: budgets[item] - minima[item], reverse=True):
            slack = budgets[key] - minima[key]
            cut = min(slack, excess)
            budgets[key] -= cut
            excess -= cut
            if excess <= 0:
                break
    if sum(budgets.values()) > cap or any(budgets[key] < minima[key] for key in keys):
        return _infeasible(request, strategy, request.feasibility, "BASELINE_ALLOCATION_FAILURE")
    return _build_result(request, strategy, f"{strategy.value}-v1", budgets, ("baseline_method",))
