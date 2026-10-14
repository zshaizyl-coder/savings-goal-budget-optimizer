from __future__ import annotations

import hashlib
import json
import math
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from statistics import pstdev
from typing import Iterable

import numpy as np
import pandas as pd
from scipy import stats

from backend.app.domain.contracts import (
    BudgetConstraintSpec, BudgetPlanItem, BudgetPlanVersion, FinancialEventSpec,
    FinancialSnapshotSpec, IncomeAssumptionSpec, SavingsGoalSpec, WeeklyPredictionItem,
)
from backend.app.domain.enums import (
    AdjustmentMode, BudgetStrategy, FinancialEventType, ImpactScope, ReplanTriggerSource,
)
from backend.app.forecasting.interfaces import WeeklyActual
from backend.app.optimization.feasibility import SavingsGoalFeasibilityEvaluator
from backend.app.optimization.interfaces import BudgetOptimizationRequest, FeasibilityRequest, ReplanRequest
from backend.app.optimization.optimizer import (
    HistoricalShareOptimizer, PersonalizedBudgetOptimizer, ProportionalReductionOptimizer,
)
from backend.app.replanning.service import RollingBudgetReplanner
from backend.app.replanning.variance import VarianceTriggerPolicy, calculate_weekly_variance


ALGORITHMS = {
    "UNIFORM_REDUCTION": ProportionalReductionOptimizer,
    "HISTORICAL_SHARE": HistoricalShareOptimizer,
    "PERSONALIZED": PersonalizedBudgetOptimizer,
}
DEFAULT_ADJUSTABILITY = {
    "food": 2500, "shopping": 8500, "transport": 2500, "communications": 1000,
    "housing": 0, "health": 2500, "education": 5000, "entertainment": 8000,
    "life_services": 5000, "human_relations": 5000, "other": 5000,
}
FIXED_CATEGORIES = {"housing", "communications"}
ELASTIC_CATEGORIES = {"shopping", "entertainment", "other", "life_services"}


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def config_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_commit(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def weeks(config: dict) -> tuple[date, ...]:
    start = date.fromisoformat(config["start_date"])
    return tuple(start + timedelta(days=7 * index) for index in range(config["planning_weeks"]))


def load_real_profile(root: Path, config: dict) -> dict[str, int]:
    payload = json.loads((root / config["real_aggregate_profile_source"]).read_text(encoding="utf-8"))
    return {key: int(value) for key, value in payload["category_profile_minor"].items()}


def synthetic_profile(base: dict[str, int], spec: dict) -> dict[str, int]:
    result = {}
    for category, amount in base.items():
        multiplier = spec["scale"]
        if category in FIXED_CATEGORIES:
            multiplier *= spec["fixed_multiplier"]
        if category in ELASTIC_CATEGORIES:
            multiplier *= spec["elastic_multiplier"]
        result[category] = max(100, round(amount * multiplier))
    return result


def weekly_paths(profile: dict[str, int], volatility: float, rng: np.random.Generator,
                 count: int, shock_probability: float = 0.0) -> list[dict[str, int]]:
    paths = []
    for _ in range(count):
        common = max(0.35, float(rng.normal(1.0, volatility)))
        row = {
            category: (amount if category in FIXED_CATEGORIES else
                       max(0, round(amount * common * max(0.45, rng.normal(1.0, volatility / 2)))))
            for category, amount in profile.items()
        }
        if shock_probability and rng.random() < shock_probability:
            row["other"] += round(sum(profile.values()) * rng.uniform(0.5, 1.2))
        paths.append(row)
    return paths


def make_constraints(profile: dict[str, int], planning_weeks: tuple[date, ...],
                     *, equal_adjustability: bool = False) -> tuple[BudgetConstraintSpec, ...]:
    rows = []
    for category, amount in sorted(profile.items()):
        fixed = category in FIXED_CATEGORIES
        minimum = amount if fixed else round(amount * 0.45)
        adjustability = 0 if fixed else (5000 if equal_adjustability else DEFAULT_ADJUSTABILITY.get(category, 5000))
        rows.append(BudgetConstraintSpec(
            1, category, minimum, minimum if fixed else None, fixed, adjustability,
            planning_weeks[0], planning_weeks[-1], 1,
        ))
    return tuple(rows)


def make_predictions(paths: list[dict[str, int]], planning_weeks: tuple[date, ...],
                     error: float = 0.0) -> tuple[WeeklyPredictionItem, ...]:
    rows = []
    for week, path in zip(planning_weeks, paths):
        scaled = {category: max(0, round(amount * (1 + error))) for category, amount in path.items()}
        rows.extend(WeeklyPredictionItem(week, category, amount) for category, amount in sorted(scaled.items()))
        total = sum(scaled.values())
        rows.append(WeeklyPredictionItem(week, "total", total, 0, round(total * 1.75)))
    return tuple(rows)


def make_budget_request(config: dict, profile: dict[str, int], paths: list[dict[str, int]],
                        pressure: str, *, error: float = 0.0,
                        equal_adjustability: bool = False,
                        mode: AdjustmentMode | None = None) -> BudgetOptimizationRequest:
    plan_weeks = weeks(config)
    mode = mode or AdjustmentMode(config["pressure_mode"][pressure])
    predictions = make_predictions(paths, plan_weeks, error)
    constraints = make_constraints(profile, plan_weeks, equal_adjustability=equal_adjustability)
    natural_total = sum(sum(row.values()) for row in paths)
    minimum_total = sum(item.minimum_budget_minor for item in constraints) * len(plan_weeks)
    funds = config["current_savings_minor"] + config["weekly_income_minor"] * len(plan_weeks)
    if pressure == "INFEASIBLE":
        cap = max(0, minimum_total - 100)
    else:
        cap = round(natural_total * (1 - config["pressure_reduction"][pressure]))
        cap = max(minimum_total, cap)
    target = funds - cap
    goal = SavingsGoalSpec(1, 1, 1, plan_weeks[0], plan_weeks[-1] + timedelta(days=6), target)
    snapshot = FinancialSnapshotSpec(1, 1, plan_weeks[0], config["current_savings_minor"])
    income = (IncomeAssumptionSpec(
        1, "weekly", config["weekly_income_minor"], plan_weeks[0], plan_weeks[-1] + timedelta(days=6),
    ),)
    feasibility_request = FeasibilityRequest(goal, snapshot, income, predictions, constraints, plan_weeks)
    feasibility = SavingsGoalFeasibilityEvaluator().evaluate(feasibility_request)
    return BudgetOptimizationRequest(
        goal, snapshot, income, predictions, constraints, feasibility,
        mode, plan_weeks,
    )


def common_metrics(request: BudgetOptimizationRequest, result) -> dict:
    target = request.goal.target_savings_minor
    final = result.expected_final_savings_minor
    if not result.feasible or final is None:
        return {
            "feasible": False, "final_savings_minor": None, "savings_gap_minor": None,
            "goal_achievement_ratio": None, "goal_achieved": False,
            "minimum_violations": result.minimum_violation_count, "habit_deviation_bps": None,
            "total_adjustment_ratio": None, "max_category_adjustment_ratio": None,
            "category_adjustment_dispersion": None, "weekly_budget_volatility_minor": None,
            "reason_code": result.infeasibility.reason_code if result.infeasibility else "INFEASIBLE",
        }
    natural_by_category: dict[str, int] = {}
    budget_by_category: dict[str, int] = {}
    budget_by_week: dict[date, int] = {}
    for item in result.items:
        natural_by_category[item.category_code] = natural_by_category.get(item.category_code, 0) + item.natural_spend_minor
        budget_by_category[item.category_code] = budget_by_category.get(item.category_code, 0) + item.recommended_budget_minor
        budget_by_week[item.week_start] = budget_by_week.get(item.week_start, 0) + item.recommended_budget_minor
    ratios = [
        (natural_by_category[c] - budget_by_category[c]) / natural_by_category[c]
        for c in natural_by_category if natural_by_category[c] > 0
    ]
    ordered = [budget_by_week[key] for key in sorted(budget_by_week)]
    weekly_volatility = float(np.mean(np.abs(np.diff(ordered)))) if len(ordered) > 1 else 0.0
    natural_total = sum(natural_by_category.values())
    budget_total = sum(budget_by_category.values())
    return {
        "feasible": True, "final_savings_minor": final,
        "savings_gap_minor": max(target - final, 0),
        "goal_achievement_ratio": final / target, "goal_achieved": final >= target,
        "minimum_violations": result.minimum_violation_count,
        "habit_deviation_bps": result.habit_deviation_bps,
        "total_adjustment_ratio": (natural_total - budget_total) / natural_total if natural_total else 0.0,
        "max_category_adjustment_ratio": max(ratios, default=0.0),
        "category_adjustment_dispersion": pstdev(ratios) if len(ratios) > 1 else 0.0,
        "weekly_budget_volatility_minor": weekly_volatility, "reason_code": None,
    }


def run_experiment_a(root: Path, config: dict) -> pd.DataFrame:
    base = load_real_profile(root, config)
    users = {"REAL_AGGREGATE": {"profile": base, "volatility": 0.0, "shock_probability": 0.0}}
    for name, spec in config["synthetic_users"].items():
        users[name] = {
            "profile": synthetic_profile(base, spec), "volatility": spec["volatility"],
            "shock_probability": spec["shock_probability"],
        }
    rows = []
    for user_index, (user_type, user) in enumerate(users.items()):
        repetitions = 1 if user_type == "REAL_AGGREGATE" else config["repetitions"]
        for repetition in range(repetitions):
            seed = config["master_seed"] + user_index * 1000 + repetition
            rng = np.random.default_rng(seed)
            # Experiment A compares allocation, not clairvoyant week-level shock forecasts.
            # User volatility changes the sampled personal baseline across repetitions;
            # the planning forecast itself retains only modest week-to-week variation.
            sampled_profile = {
                category: (amount if category in FIXED_CATEGORIES else
                           max(100, round(amount * max(0.55, rng.normal(1.0, user["volatility"])))))
                for category, amount in user["profile"].items()
            }
            paths = weekly_paths(sampled_profile, min(user["volatility"], 0.04), rng,
                                 config["planning_weeks"], 0.0)
            for pressure in ("EASY", "MEDIUM", "HARD", "INFEASIBLE"):
                request = make_budget_request(config, sampled_profile, paths, pressure)
                for method, factory in ALGORITHMS.items():
                    result = factory().optimize(request)
                    rows.append({
                        "experiment": "A", "user_type": user_type, "repetition": repetition,
                        "seed": seed, "pressure": pressure, "method": method,
                        "target_savings_minor": request.goal.target_savings_minor,
                        "natural_total_minor": sum(sum(path.values()) for path in paths),
                        **common_metrics(request, result),
                    })
    return pd.DataFrame(rows)


def result_to_plan(result, request: BudgetOptimizationRequest, plan_id: int = 1,
                   version: int = 1) -> BudgetPlanVersion:
    return BudgetPlanVersion(
        plan_id, 1, 1, version, result.strategy, request.adjustment_mode,
        request.planning_weeks[0], request.planning_weeks[-1], result.feasible, result.items,
        expected_final_savings_minor=result.expected_final_savings_minor,
        infeasibility_code=None if result.feasible else (result.infeasibility.reason_code if result.infeasibility else "INFEASIBLE"),
        snapshot_id=version, risk_level=result.risk_level,
        target_date_snapshot=request.goal.target_date,
    )


def replan_once(config: dict, base_request: BudgetOptimizationRequest, initial_result, *,
                elapsed_weeks: int, balance_delta_minor: int = 0,
                income_change: float = 0.0, recovery_horizon: int | None = None,
                event_type: FinancialEventType = FinancialEventType.UNPLANNED_EXPENSE,
                actual_paths: list[dict[str, int]] | None = None):
    plan_weeks = tuple(base_request.planning_weeks)
    next_open = plan_weeks[elapsed_weeks]
    previous = result_to_plan(initial_result, base_request)
    items_by_key = {(item.week_start, item.category_code): item for item in initial_result.items}
    actuals = []
    spent = 0
    for week_index, week in enumerate(plan_weeks[:elapsed_weeks]):
        category_total = 0
        for category in sorted({item.category_code for item in initial_result.items}):
            if actual_paths is None:
                amount = items_by_key[(week, category)].recommended_budget_minor
            else:
                amount = actual_paths[week_index][category]
            spent += amount; category_total += amount
            actuals.append(WeeklyActual(week, category, amount, True, False))
        actuals.append(WeeklyActual(week, "total", category_total, True, False))
    received = config["weekly_income_minor"] * elapsed_weeks
    current = config["current_savings_minor"] + received - spent + balance_delta_minor
    snapshot = FinancialSnapshotSpec(2, 1, next_open, max(0, current))
    open_weeks = plan_weeks[elapsed_weeks:]
    weekly_income = max(0, round(config["weekly_income_minor"] * (1 + income_change)))
    income = (IncomeAssumptionSpec(1, "weekly", weekly_income, next_open,
                                   plan_weeks[-1] + timedelta(days=6)),)
    predictions = tuple(item for item in base_request.predictions if item.week_start >= next_open)
    constraints = tuple(replace(item, effective_from=next_open) for item in base_request.constraints)
    amount = abs(balance_delta_minor) if balance_delta_minor else round(abs(income_change) * config["weekly_income_minor"] * len(open_weeks))
    event = FinancialEventSpec(
        1, 1, 1, event_type, amount or None, next_open - timedelta(days=1),
        ImpactScope.ACTUAL_SPEND if balance_delta_minor < 0 else ImpactScope.FUTURE_INCOME,
        "STAGE10_SIMULATED_EVENT",
    )
    request = ReplanRequest(
        previous, tuple(actuals), (event,), snapshot, base_request.goal, constraints,
        predictions, next_open, BudgetStrategy.PERSONALIZED, AdjustmentMode.GOAL_PRIORITY,
        income_schedule=income, recovery_horizon_weeks=recovery_horizon,
        trigger_source=ReplanTriggerSource.EVENT_REQUIRED,
    )
    result = RollingBudgetReplanner().replan(request)
    old_future = sum(item.recommended_budget_minor for item in initial_result.items if item.week_start >= next_open)
    no_replan_final = current + weekly_income * len(open_weeks) - old_future
    return request, result, no_replan_final


def run_experiment_b(root: Path, config: dict) -> pd.DataFrame:
    profile = load_real_profile(root, config)
    paths = weekly_paths(profile, 0.0, np.random.default_rng(config["master_seed"]), config["planning_weeks"])
    request = make_budget_request(config, profile, paths, "MEDIUM")
    initial = PersonalizedBudgetOptimizer().optimize(request)
    rows = []
    for timing, elapsed in config["shock_weeks"].items():
        for shock in config["shock_minor"]:
            replan_request, rolling, static_final = replan_once(
                config, request, initial, elapsed_weeks=elapsed, balance_delta_minor=-shock,
                recovery_horizon=config["planning_weeks"] - elapsed,
                event_type=FinancialEventType.UNPLANNED_EXPENSE,
            )
            for method, final, feasible, habit, recovery, adjustment, violations in (
                ("NO_REPLAN", static_final, True, initial.habit_deviation_bps, None, 0, 0),
                ("ROLLING_REPLAN", rolling.optimization_result.expected_final_savings_minor,
                 rolling.optimization_result.feasible, rolling.optimization_result.habit_deviation_bps,
                 rolling.recovery_metrics.weeks_to_recover, rolling.difference.total_adjustment_minor,
                 rolling.optimization_result.minimum_violation_count),
            ):
                gap = None if final is None else max(request.goal.target_savings_minor - final, 0)
                rows.append({
                    "experiment": "B", "scenario_type": "SHOCK", "scenario": f"SHOCK_{shock // 100}",
                    "timing": timing, "elapsed_weeks": elapsed, "method": method,
                    "shock_minor": shock, "shock_income_ratio": shock / (config["weekly_income_minor"] * 4),
                    "income_change": 0.0, "feasible": feasible, "final_savings_minor": final,
                    "savings_gap_minor": gap, "goal_achieved": final is not None and final >= request.goal.target_savings_minor,
                    "weeks_to_recover": recovery, "future_budget_adjustment_minor": adjustment,
                    "max_weekly_adjustment_minor": (None if rolling.difference.average_weekly_adjustment_minor is None else abs(rolling.difference.average_weekly_adjustment_minor)),
                    "max_category_adjustment_minor": (rolling.recovery_metrics.max_category_adjustment_minor
                                                       if method == "ROLLING_REPLAN" else None),
                    "habit_deviation_bps": habit, "minimum_violations": violations,
                    "replan_count": 1 if method == "ROLLING_REPLAN" else 0,
                    "early_infeasibility_identified": method == "ROLLING_REPLAN" and not feasible,
                    "reason_code": (rolling.optimization_result.infeasibility.reason_code
                                    if method == "ROLLING_REPLAN" and rolling.optimization_result.infeasibility else None),
                    "required_additional_income_minor": (
                        rolling.optimization_result.infeasibility.required_additional_income_minor
                        if method == "ROLLING_REPLAN" and rolling.optimization_result.infeasibility else None
                    ),
                })
    for change in config["income_changes"]:
        event_type = FinancialEventType.INCOME_DECREASE if change < 0 else FinancialEventType.EXTRA_INCOME
        delta = 0 if change < 0 else round(config["weekly_income_minor"] * 6 * change)
        _, rolling, static_final = replan_once(
            config, request, initial, elapsed_weeks=6, balance_delta_minor=delta,
            income_change=change if change < 0 else 0.0, event_type=event_type,
            recovery_horizon=config["planning_weeks"] - 6,
        )
        for method, final, feasible, habit, recovery, adjustment, violations in (
            ("NO_REPLAN", static_final, True, initial.habit_deviation_bps, None, 0, 0),
            ("ROLLING_REPLAN", rolling.optimization_result.expected_final_savings_minor,
             rolling.optimization_result.feasible, rolling.optimization_result.habit_deviation_bps,
             rolling.recovery_metrics.weeks_to_recover, rolling.difference.total_adjustment_minor,
             rolling.optimization_result.minimum_violation_count),
        ):
            gap = None if final is None else max(request.goal.target_savings_minor - final, 0)
            rows.append({
                "experiment": "B", "scenario_type": "INCOME", "scenario": f"INCOME_{change:+.0%}",
                "timing": "MIDDLE", "elapsed_weeks": 6, "method": method, "shock_minor": 0,
                "shock_income_ratio": 0.0, "income_change": change, "feasible": feasible,
                "final_savings_minor": final, "savings_gap_minor": gap,
                "goal_achieved": final is not None and final >= request.goal.target_savings_minor,
                "weeks_to_recover": recovery, "future_budget_adjustment_minor": adjustment,
                "max_weekly_adjustment_minor": (None if rolling.difference.average_weekly_adjustment_minor is None else abs(rolling.difference.average_weekly_adjustment_minor)),
                "max_category_adjustment_minor": (rolling.recovery_metrics.max_category_adjustment_minor
                                                   if method == "ROLLING_REPLAN" else None),
                "habit_deviation_bps": habit, "minimum_violations": violations,
                "replan_count": 1 if method == "ROLLING_REPLAN" else 0,
                "early_infeasibility_identified": method == "ROLLING_REPLAN" and not feasible,
                "reason_code": (rolling.optimization_result.infeasibility.reason_code
                                if method == "ROLLING_REPLAN" and rolling.optimization_result.infeasibility else None),
                "required_additional_income_minor": (
                    rolling.optimization_result.infeasibility.required_additional_income_minor
                    if method == "ROLLING_REPLAN" and rolling.optimization_result.infeasibility else None
                ),
            })
    return pd.DataFrame(rows)


def run_experiment_c(root: Path, config: dict) -> pd.DataFrame:
    profile = load_real_profile(root, config)
    true_paths = weekly_paths(profile, 0.0, np.random.default_rng(config["master_seed"]), config["planning_weeks"])
    rows = []
    for error in config["prediction_errors"]:
        # EASY keeps every tested error level initially feasible. The target is still
        # derived from the unchanged true path; only the optimizer input is biased.
        request = make_budget_request(config, profile, true_paths, "EASY", error=error,
                                      mode=AdjustmentMode.GOAL_PRIORITY)
        initial = PersonalizedBudgetOptimizer().optimize(request)
        if not initial.feasible:
            raise AssertionError(f"forecast_robustness_initial_plan_infeasible:{error}")
        _, rolling, static_final = replan_once(
            config, request, initial, elapsed_weeks=6, actual_paths=true_paths,
            event_type=FinancialEventType.MANUAL_REPLAN,
        )
        for method, final, feasible, habit, count in (
            ("STATIC", static_final, True, initial.habit_deviation_bps, 0),
            ("ROLLING", rolling.optimization_result.expected_final_savings_minor,
             rolling.optimization_result.feasible, rolling.optimization_result.habit_deviation_bps, 1),
        ):
            rows.append({
                "experiment": "C", "prediction_error": error, "method": method,
                "initial_budget_minor": initial.recommended_spending_minor,
                "final_savings_minor": final,
                "savings_gap_minor": None if final is None else max(request.goal.target_savings_minor - final, 0),
                "goal_achieved": final is not None and final >= request.goal.target_savings_minor,
                "feasible": feasible, "habit_deviation_bps": habit, "replan_count": count,
            })
    return pd.DataFrame(rows)


def run_experiment_d(root: Path, config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    profile = load_real_profile(root, config)
    paths = weekly_paths(profile, 0.0, np.random.default_rng(config["master_seed"]), config["planning_weeks"])
    request = make_budget_request(config, profile, paths, "MEDIUM")
    initial = PersonalizedBudgetOptimizer().optimize(request)
    plan_weeks = weeks(config)
    deviations = (0.05, 0.12, 0.25, -0.04, 0.35, 0.08, 0.22, -0.03, 0.45, 0.11, 0.28, 0.0)
    actuals = []
    categories = sorted(profile)
    items = {(item.week_start, item.category_code): item for item in initial.items}
    for week, deviation in zip(plan_weeks, deviations):
        total = 0
        for category in categories:
            amount = items[(week, category)].recommended_budget_minor
            if category == "other":
                amount += round(sum(items[(week, c)].recommended_budget_minor for c in categories) * deviation)
            total += amount
            actuals.append(WeeklyActual(week, category, max(0, amount), True, deviation > 0))
        actuals.append(WeeklyActual(week, "total", total, True, deviation > 0))
    threshold_rows = []
    for policy in config["thresholds"]:
        value = VarianceTriggerPolicy(policy["absolute_minor"], policy["relative_bps"], policy["require_both"])
        reports = [calculate_weekly_variance(initial.items, actuals, week, value) for week in plan_weeks]
        triggered = [report for report in reports if report.replan_suggested]
        unnecessary = sum(report.absolute_variance_minor < 10_000 for report in triggered)
        total_overrun = sum(max(0, report.absolute_variance_minor) for report in reports)
        recovered = sum(max(0, report.absolute_variance_minor) for report in triggered)
        threshold_rows.append({
            "experiment": "D_THRESHOLD", "threshold": policy["name"],
            "absolute_minor": policy["absolute_minor"], "relative_bps": policy["relative_bps"],
            "require_both": policy["require_both"], "replan_count": len(triggered),
            "algorithm_runs": len(triggered), "unnecessary_replans": unnecessary,
            "savings_gap_minor": max(0, total_overrun - recovered),
            "residual_untriggered_overrun_minor": max(0, total_overrun - recovered),
            "habit_deviation_bps": initial.habit_deviation_bps,
            "max_weekly_adjustment_minor": max((report.absolute_variance_minor for report in triggered), default=0),
        })
    horizon_rows = []
    for horizon_value in config["recovery_horizons"]:
        horizon = config["planning_weeks"] - 3 if horizon_value == "remaining" else int(horizon_value)
        _, rolling, _ = replan_once(
            config, request, initial, elapsed_weeks=3, balance_delta_minor=-50_000,
            recovery_horizon=horizon, event_type=FinancialEventType.UNPLANNED_EXPENSE,
        )
        opt = rolling.optimization_result
        by_week: dict[date, int] = {}
        for item in opt.items:
            by_week[item.week_start] = by_week.get(item.week_start, 0) + item.recommended_budget_minor
        ordered = [by_week[key] for key in sorted(by_week)]
        horizon_rows.append({
            "experiment": "D_HORIZON", "recovery_horizon": str(horizon_value),
            "feasible": opt.feasible, "weeks_to_recover": rolling.recovery_metrics.weeks_to_recover,
            "max_weekly_adjustment_minor": abs(rolling.difference.average_weekly_adjustment_minor or 0),
            "habit_deviation_bps": opt.habit_deviation_bps,
            "goal_achieved": opt.expected_final_savings_minor is not None and opt.expected_final_savings_minor >= request.goal.target_savings_minor,
            "weekly_volatility_minor": float(np.mean(np.abs(np.diff(ordered)))) if len(ordered) > 1 else 0.0,
        })
    return pd.DataFrame(threshold_rows), pd.DataFrame(horizon_rows)


def paired_statistics(a: pd.DataFrame) -> pd.DataFrame:
    subset = a[(a.user_type != "REAL_AGGREGATE") & a.pressure.isin(["MEDIUM", "HARD"]) & a.feasible]
    pivot = subset.pivot_table(index=["user_type", "pressure", "repetition"], columns="method",
                               values="habit_deviation_bps", aggfunc="first").dropna()
    rows = []
    for baseline in ("UNIFORM_REDUCTION", "HISTORICAL_SHARE"):
        difference = pivot[baseline].to_numpy() - pivot["PERSONALIZED"].to_numpy()
        if len(difference) < 3:
            continue
        normal_p = stats.shapiro(difference).pvalue
        if normal_p >= 0.05:
            statistic, pvalue = stats.ttest_rel(pivot[baseline], pivot["PERSONALIZED"])
            test = "paired_t"
            effect = float(np.mean(difference) / np.std(difference, ddof=1)) if np.std(difference, ddof=1) else 0.0
            effect_name = "cohen_dz"
        else:
            statistic, pvalue = stats.wilcoxon(difference, zero_method="zsplit")
            test = "wilcoxon"
            nonzero = difference[difference != 0]
            ranks = stats.rankdata(abs(nonzero)) if len(nonzero) else np.array([])
            effect = float(np.sum(ranks[nonzero > 0]) - np.sum(ranks[nonzero < 0])) / float(np.sum(ranks)) if len(ranks) and np.sum(ranks) else 0.0
            effect_name = "rank_biserial"
        improvement = float(np.mean(difference) / np.mean(pivot[baseline])) if np.mean(pivot[baseline]) else 0.0
        rows.append({
            "metric": "habit_deviation_bps", "comparison": f"PERSONALIZED_vs_{baseline}",
            "n": len(difference), "normality_p": normal_p, "test": test,
            "statistic": statistic, "p_value": pvalue, "effect_name": effect_name,
            "effect_size": effect, "mean_improvement_ratio": improvement,
        })
    return pd.DataFrame(rows)


def summarize_frame(frame: pd.DataFrame, groups: list[str], metrics: list[str]) -> pd.DataFrame:
    usable = [metric for metric in metrics if metric in frame.columns]
    return frame.groupby(groups, dropna=False)[usable].agg(["mean", "median", "std", "min", "max"]).reset_index()


def write_metadata(root: Path, config_path: Path, output: Path, elapsed: float) -> dict:
    import matplotlib, scipy
    source_files = [
        root / "backend/app/optimization/optimizer.py",
        root / "backend/app/replanning/service.py",
        root / "experiments/stage10/core.py",
        root / "experiments/stage10/run_all.py",
    ]
    metadata = {
        "experiment_version": "stage10-v1", "dataset_version": "weekly-forecast-v1",
        "algorithm_versions": ["proportional-v1", "historical_share-v1", "personalized-slsqp-v1", "rolling-replan-v1"],
        "master_seed": load_config(config_path)["master_seed"], "config_sha256": config_hash(config_path),
        "git_commit_at_run": git_commit(root), "runtime_seconds": elapsed,
        "git_worktree_dirty_at_run": bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=root, text=True
        ).strip()),
        "source_sha256": {
            str(path.relative_to(root)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in source_files
        },
        "python": platform.python_version(), "platform": platform.platform(),
        "dependencies": {"numpy": np.__version__, "pandas": pd.__version__, "scipy": scipy.__version__, "matplotlib": matplotlib.__version__},
        "privacy": {"aggregate_only": True, "contains_transaction_text": False},
    }
    (output / "run_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def assert_finite_outputs(frames: Iterable[pd.DataFrame]) -> None:
    for frame in frames:
        numeric = frame.select_dtypes(include=[np.number])
        if np.isinf(numeric.to_numpy()).any():
            raise ValueError("experiment_output_contains_infinity")
