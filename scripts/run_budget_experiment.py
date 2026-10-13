"""Run privacy-safe stage 7 scenarios from stage 6 aggregate data only."""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from backend.app.domain.contracts import (
    BudgetConstraintSpec,
    FinancialSnapshotSpec,
    IncomeAssumptionSpec,
    SavingsGoalSpec,
    WeeklyPredictionItem,
)
from backend.app.domain.enums import AdjustmentMode
from backend.app.optimization.baselines import build_category_natural_baselines
from backend.app.optimization.feasibility import SavingsGoalFeasibilityEvaluator
from backend.app.optimization.interfaces import BudgetOptimizationRequest, FeasibilityRequest
from backend.app.optimization.optimizer import (
    HistoricalShareOptimizer,
    PersonalizedBudgetOptimizer,
    ProportionalReductionOptimizer,
)


START = date(2026, 9, 28)
WEEKS = tuple(START + timedelta(days=7 * index) for index in range(12))
CURRENT_SAVINGS_MINOR = 500_000
MONTHLY_INCOME_MINOR = 500_000
TOTAL_WEEKLY_BASELINE_MINOR = 75_540


def load_profile(analysis_dir: Path) -> dict[str, int]:
    weekly = pd.read_csv(analysis_dir / "category_weekly.csv")
    complete = pd.read_csv(analysis_dir / "weekly_consumption.csv")
    complete_weeks = set(complete.loc[complete["is_complete_week"].astype(bool), "week_start"])
    weekly = weekly[
        weekly["week_start"].isin(complete_weeks)
        & (weekly["category_code"] != "unallocated_refund")
    ]
    histories = {
        category: group.sort_values("week_start")["net_consumption_minor"].round().astype(int).tolist()
        for category, group in weekly.groupby("category_code")
    }
    return build_category_natural_baselines(
        histories,
        TOTAL_WEEKLY_BASELINE_MINOR,
        model_overrides={"food": 12_930, "shopping": 7_925},
    )


def make_request(profile: dict[str, int], target_minor: int, mode: AdjustmentMode,
                 future_forecast: pd.DataFrame) -> BudgetOptimizationRequest:
    goal = SavingsGoalSpec(1, 1, 1, START, WEEKS[-1] + timedelta(days=6), target_minor)
    snapshot = FinancialSnapshotSpec(1, 1, START, CURRENT_SAVINGS_MINOR)
    income = (IncomeAssumptionSpec(
        1, "monthly", MONTHLY_INCOME_MINOR, date(2026, 10, 1), date(2026, 12, 31), 1,
    ),)
    # These are scenario assumptions, not inferred necessities. A product user must confirm them.
    willingness = {
        "food": 2_500, "shopping": 8_500, "transport": 2_500,
        "communications": 1_000, "housing": 1_000, "health": 2_500,
        "education": 5_000, "entertainment": 8_000, "life_services": 5_000,
        "human_relations": 5_000, "other": 5_000,
    }
    constraints = tuple(
        BudgetConstraintSpec(
            1, category, round(amount * 0.45), None, False,
            willingness.get(category, 5_000), START, WEEKS[-1], 1,
        )
        for category, amount in sorted(profile.items())
    )
    total_rows = future_forecast[future_forecast["category"] == "total"].copy()
    total_rows["week_start"] = pd.to_datetime(total_rows["week_start"]).dt.date
    upper_map = dict(zip(total_rows["week_start"], total_rows["upper_bound_minor"].astype(int)))
    fallback_upper = round(float(total_rows["upper_bound_minor"].mean()))
    predictions: list[WeeklyPredictionItem] = []
    for week in WEEKS:
        predictions.extend(WeeklyPredictionItem(week, category, amount)
                           for category, amount in sorted(profile.items()))
        predictions.append(WeeklyPredictionItem(
            week, "total", TOTAL_WEEKLY_BASELINE_MINOR,
            lower_minor=0, upper_minor=upper_map.get(week, fallback_upper),
        ))
    feasibility_request = FeasibilityRequest(
        goal, snapshot, income, tuple(predictions), constraints, WEEKS,
    )
    feasibility = SavingsGoalFeasibilityEvaluator().evaluate(feasibility_request)
    return BudgetOptimizationRequest(
        goal, snapshot, income, tuple(predictions), constraints, feasibility, mode, WEEKS,
    )


def summarize(name: str, result) -> dict:
    row = {
        "name": name,
        "feasible": result.feasible,
        "strategy": result.strategy.value,
        "mode": result.optimization_mode.value if result.optimization_mode else None,
        "expected_final_savings_minor": result.expected_final_savings_minor,
        "natural_spending_minor": result.natural_spending_minor,
        "recommended_spending_minor": result.recommended_spending_minor,
        "habit_deviation_bps": result.habit_deviation_bps,
        "weekly_volatility_minor": result.weekly_volatility_minor,
        "minimum_violation_count": result.minimum_violation_count,
        "risk_level": result.risk_level,
        "reason_code": result.infeasibility.reason_code if result.infeasibility else None,
        "maximum_achievable_target_minor": (
            result.infeasibility.maximum_achievable_target_minor if result.infeasibility else None
        ),
        "required_additional_income_minor": (
            result.infeasibility.required_additional_income_minor if result.infeasibility else None
        ),
    }
    if result.natural_spending_minor and result.recommended_spending_minor is not None:
        row["reduction_ratio"] = 1 - result.recommended_spending_minor / result.natural_spending_minor
    else:
        row["reduction_ratio"] = None
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-dir", type=Path, default=Path("data/private/stage6-analysis"))
    parser.add_argument("--forecast", type=Path, default=Path("experiments/forecasting/future_forecast.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("experiments/optimization"))
    args = parser.parse_args()
    profile = load_profile(args.analysis_dir)
    future = pd.read_csv(args.forecast)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    scenarios = []
    scenario_inputs = (
        ("loose", 1_000_000, AdjustmentMode.BALANCED),
        ("medium", 1_250_000, AdjustmentMode.BALANCED),
        ("aggressive", 1_380_000, AdjustmentMode.GOAL_PRIORITY),
        ("infeasible", 1_700_000, AdjustmentMode.GOAL_PRIORITY),
    )
    for name, target, mode in scenario_inputs:
        request = make_request(profile, target, mode, future)
        scenarios.append(summarize(name, PersonalizedBudgetOptimizer().optimize(request)))

    mode_rows = []
    for mode in (AdjustmentMode.GOAL_PRIORITY, AdjustmentMode.BALANCED, AdjustmentMode.LIFE_STABILITY):
        request = make_request(profile, 1_180_000, mode, future)
        mode_rows.append(summarize(mode.value, PersonalizedBudgetOptimizer().optimize(request)))

    strategy_rows = []
    request = make_request(profile, 1_250_000, AdjustmentMode.BALANCED, future)
    for name, optimizer in (
        ("uniform_proportional", ProportionalReductionOptimizer()),
        ("historical_share", HistoricalShareOptimizer()),
        ("personalized", PersonalizedBudgetOptimizer()),
    ):
        strategy_rows.append(summarize(name, optimizer.optimize(request)))

    medium = PersonalizedBudgetOptimizer().optimize(request)
    category_rows = []
    for category in sorted(profile):
        natural = sum(item.natural_spend_minor for item in medium.items if item.category_code == category)
        budget = sum(item.recommended_budget_minor for item in medium.items if item.category_code == category)
        category_rows.append({
            "category": category,
            "natural_minor": natural,
            "recommended_minor": budget,
            "adjustment_minor": budget - natural,
            "adjustment_ratio": (budget / natural - 1) if natural else 0.0,
        })

    payload = {
        "assumptions": {
            "current_savings_minor": CURRENT_SAVINGS_MINOR,
            "monthly_income_minor": MONTHLY_INCOME_MINOR,
            "income_months": 3,
            "planning_weeks": len(WEEKS),
            "total_weekly_baseline_minor": TOTAL_WEEKLY_BASELINE_MINOR,
            "minimums_are_hypothetical": True,
            "transaction_level_data_used": False,
        },
        "category_profile_minor": profile,
        "scenarios": scenarios,
        "modes": mode_rows,
        "strategies": strategy_rows,
    }
    (args.output_dir / "scenario_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    pd.DataFrame(mode_rows).to_csv(args.output_dir / "mode_comparison.csv", index=False)
    pd.DataFrame(strategy_rows).to_csv(args.output_dir / "strategy_comparison.csv", index=False)
    pd.DataFrame(category_rows).to_csv(args.output_dir / "category_budget_example.csv", index=False)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
