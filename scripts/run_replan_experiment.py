"""Run privacy-safe stage-8 shocks on the stage-7 real-derived aggregate profile."""

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
    BudgetConstraintSpec, BudgetPlanItem, BudgetPlanVersion, FinancialEventSpec,
    FinancialSnapshotSpec, IncomeAssumptionSpec, SavingsGoalSpec, WeeklyPredictionItem,
)
from backend.app.domain.enums import (
    AdjustmentMode, BudgetStrategy, FinancialEventType, ImpactScope, ReplanTriggerSource,
)
from backend.app.forecasting.interfaces import WeeklyActual
from backend.app.optimization.interfaces import ReplanRequest
from backend.app.replanning import RollingBudgetReplanner, no_replan_expected_savings


START = date(2026, 9, 28)
NEXT_OPEN = START + timedelta(days=21)
TARGET_DATE = START + timedelta(days=83)
TARGET_MINOR = 1_250_000


def load_profiles(input_dir: Path):
    payload = json.loads((input_dir / "scenario_results.json").read_text(encoding="utf-8"))
    natural = {key: int(value) for key, value in payload["category_profile_minor"].items()}
    category_budget = pd.read_csv(input_dir / "category_budget_example.csv")
    budget = {
        row.category: int(row.recommended_minor) // 12
        for row in category_budget.itertuples(index=False)
    }
    if sum(natural.values()) != 75_540 or sum(budget.values()) != 62_500:
        raise ValueError("stage7_profile_conservation_failed")
    return natural, budget


def make_request(natural, budget, *, shock_minor=0, extra_minor=0,
                 monthly_income_minor=500_000, event_type=FinancialEventType.MANUAL_REPLAN,
                 event_id=1):
    all_weeks = tuple(START + timedelta(days=7 * index) for index in range(12))
    open_weeks = all_weeks[3:]
    items = tuple(
        BudgetPlanItem(week, category, natural[category], budget[category],
                       budget[category] - natural[category], round(natural[category] * .45),
                       None, {"food": 2_500, "shopping": 8_500, "transport": 2_500,
                              "communications": 1_000, "housing": 1_000, "health": 2_500,
                              "education": 5_000, "entertainment": 8_000,
                              "life_services": 5_000, "human_relations": 5_000,
                              "other": 5_000}.get(category, 5_000))
        for week in all_weeks for category in sorted(natural)
    )
    previous = BudgetPlanVersion(
        1, 1, 1, 1, BudgetStrategy.PERSONALIZED, AdjustmentMode.BALANCED,
        all_weeks[0], all_weeks[-1], True, items,
        expected_final_savings_minor=TARGET_MINOR, snapshot_id=1,
        risk_level="HIGH_RISK", target_date_snapshot=TARGET_DATE,
    )
    current = 812_500 - shock_minor + extra_minor
    snapshot = FinancialSnapshotSpec(2, 1, NEXT_OPEN, current)
    goal = SavingsGoalSpec(1, 1, 1, START, TARGET_DATE, TARGET_MINOR)
    income = (IncomeAssumptionSpec(1, "monthly", monthly_income_minor,
                                   date(2026, 11, 1), date(2026, 12, 31), 1),)
    willingness = {item.category_code: item.adjustability_bps for item in items[:len(natural)]}
    constraints = tuple(
        BudgetConstraintSpec(1, category, round(amount * .45), None, False,
                             willingness[category], NEXT_OPEN, all_weeks[-1], 1)
        for category, amount in sorted(natural.items())
    )
    predictions = tuple(
        prediction for week in open_weeks
        for prediction in (
            *(WeeklyPredictionItem(week, category, amount)
              for category, amount in sorted(natural.items())),
            WeeklyPredictionItem(week, "total", 75_540, 21_736, 129_344),
        )
    )
    actuals = []
    for index, week in enumerate(all_weeks[:3]):
        for category in sorted(budget):
            amount = budget[category] + (shock_minor if index == 2 and category == "other" else 0)
            actuals.append(WeeklyActual(week, category, amount, True, shock_minor > 0))
        actuals.append(WeeklyActual(week, "total", 62_500 + (shock_minor if index == 2 else 0),
                                    True, shock_minor > 0))
    event = FinancialEventSpec(
        event_id, 1, 1, event_type,
        shock_minor or extra_minor or abs(500_000 - monthly_income_minor) * 2 or None,
        NEXT_OPEN - timedelta(days=1),
        ImpactScope.ACTUAL_SPEND if shock_minor else ImpactScope.FUTURE_INCOME,
        "SIMULATED_STAGE8_EVENT",
    )
    return ReplanRequest(
        previous, tuple(actuals), (event,), snapshot, goal, constraints, predictions,
        NEXT_OPEN, BudgetStrategy.PERSONALIZED, AdjustmentMode.GOAL_PRIORITY,
        income_schedule=income, recovery_horizon_weeks=len(open_weeks),
        trigger_source=ReplanTriggerSource.EVENT_REQUIRED,
    )


def summarize(name, request, result):
    future_income = sum(item.amount_minor for item in request.income_schedule) * 2
    no_replan = no_replan_expected_savings(request, future_income)
    return {
        "scenario": name,
        "feasible": result.optimization_result.feasible,
        "status": result.status_code,
        "old_risk": result.difference.old_risk_level,
        "new_risk": result.difference.new_risk_level,
        "old_future_budget_minor": result.difference.old_future_budget_minor,
        "new_future_budget_minor": result.difference.new_future_budget_minor,
        "budget_adjustment_minor": result.difference.total_adjustment_minor,
        "no_replan_final_savings_minor": no_replan,
        "replan_final_savings_minor": result.optimization_result.expected_final_savings_minor,
        "recovery_gap_minor": result.recovery_metrics.recovery_gap_minor,
        "weeks_to_recover": result.recovery_metrics.weeks_to_recover,
        "minimum_violation_count": result.optimization_result.minimum_violation_count,
        "reason_code": (result.optimization_result.infeasibility.reason_code
                        if result.optimization_result.infeasibility else None),
        "maximum_achievable_target_minor": (
            result.optimization_result.infeasibility.maximum_achievable_target_minor
            if result.optimization_result.infeasibility else None
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("experiments/optimization"))
    parser.add_argument("--output-dir", type=Path, default=Path("experiments/replanning"))
    args = parser.parse_args()
    natural, budget = load_profiles(args.input_dir)
    rows = []
    event_id = 1
    for yuan in (500, 1000, 3000, 5000):
        request = make_request(natural, budget, shock_minor=yuan * 100,
                               event_type=FinancialEventType.UNPLANNED_EXPENSE, event_id=event_id)
        rows.append(summarize(f"expense_{yuan}", request, RollingBudgetReplanner().replan(request)))
        event_id += 1
    for yuan in (500, 2000, 5000):
        request = make_request(natural, budget, extra_minor=yuan * 100,
                               event_type=FinancialEventType.EXTRA_INCOME, event_id=event_id)
        rows.append(summarize(f"extra_income_{yuan}", request, RollingBudgetReplanner().replan(request)))
        event_id += 1
    for percent, income in ((10, 450_000), (30, 350_000)):
        request = make_request(natural, budget, monthly_income_minor=income,
                               event_type=FinancialEventType.INCOME_DECREASE, event_id=event_id)
        rows.append(summarize(f"income_decrease_{percent}pct", request,
                              RollingBudgetReplanner().replan(request)))
        event_id += 1
    args.output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(args.output_dir / "replan_scenarios.csv", index=False)
    payload = {
        "assumptions": {
            "uses_transaction_detail": False,
            "source": "stage7_real_derived_aggregate_profile",
            "executed_weeks_locked": 3,
            "open_weeks": 9,
            "initial_target_minor": TARGET_MINOR,
            "initial_plan_expected_final_savings_minor": TARGET_MINOR,
            "minimums_are_hypothetical": True,
        },
        "results": rows,
    }
    (args.output_dir / "replan_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
