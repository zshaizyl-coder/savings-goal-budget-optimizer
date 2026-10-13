"""Privacy-safe Stage 9 performance smoke test."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from time import perf_counter

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from backend.app.analytics.consumption import build_consumption_datasets
from backend.app.analytics.types import AnalysisTransaction
from backend.app.domain.enums import AdjustmentMode
from backend.app.optimization.optimizer import PersonalizedBudgetOptimizer
from backend.app.replanning import RollingBudgetReplanner
from scripts.run_budget_experiment import load_profile, make_request as make_budget_request
from scripts.run_replan_experiment import load_profiles, make_request as make_replan_request


ROOT = Path(__file__).resolve().parents[1]


def timed(function):
    started = perf_counter(); result = function()
    return result, round((perf_counter() - started) * 1000, 2)


def main() -> None:
    start = datetime(2025, 10, 1, tzinfo=timezone.utc)
    categories = ("food", "shopping", "transport", "entertainment", "other")
    transactions = [AnalysisTransaction(
        id=index + 1,
        occurred_at_utc=start + timedelta(hours=index * 3),
        amount_minor=500 + (index % 70) * 13,
        direction="outflow", flow_type="consumption",
        source_type_code="synthetic_benchmark",
        suggested_category_code=categories[index % len(categories)],
    ) for index in range(2445)]
    datasets, analytics_ms = timed(lambda: build_consumption_datasets(transactions))

    profile = load_profile(ROOT / "experiments" / "aggregates")
    future = pd.read_csv(ROOT / "experiments" / "forecasting" / "future_forecast.csv")
    request = make_budget_request(profile, 1_100_000, AdjustmentMode.BALANCED, future)
    budget_result, budget_ms = timed(lambda: PersonalizedBudgetOptimizer().optimize(request))

    natural, budget = load_profiles(ROOT / "experiments" / "optimization")
    replan_request = make_replan_request(natural, budget, shock_minor=50_000)
    replan_result, replan_ms = timed(lambda: RollingBudgetReplanner().replan(replan_request))
    payload = {
        "synthetic_transaction_count": len(transactions),
        "analytics_ms": analytics_ms,
        "weekly_rows": len(datasets.weekly),
        "budget_optimization_ms": budget_ms,
        "budget_feasible": budget_result.feasible,
        "replan_ms": replan_ms,
        "replan_feasible": replan_result.optimization_result.feasible,
        "note": "Synthetic in-memory smoke test; not a concurrency benchmark.",
    }
    output = ROOT / "experiments" / "web" / "performance.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
