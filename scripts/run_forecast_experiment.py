"""Reproducible stage-6 rolling forecast experiment on weekly aggregates only."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

from backend.app.analytics.forecast_dataset import DATASET_VERSION
from backend.app.forecasting.evaluation import (
    allocate_weekly_to_months,
    prediction_interval_from_errors,
    rolling_backtest,
    winsorize_training,
)
from backend.app.forecasting.models import default_forecasters, model_id


BASELINE_IDS = {"historical_mean", "historical_median", "recent_mean_2", "recent_mean_4", "recent_median_4"}
COMPLEXITY = {
    "historical_mean": 1, "historical_median": 1, "recent_mean_2": 1,
    "recent_mean_4": 1, "recent_median_4": 1, "simple_exponential_smoothing": 2,
    "holt_linear": 3, "linear_lag": 3, "ridge_lag": 3,
    "arima_1_0_0": 4, "arima_0_1_1": 4, "arima_1_1_0": 4, "arima_1_0_1": 4,
}


def choose_model(metrics: list[dict[str, object]]) -> tuple[str, str, dict[str, float]]:
    one_week = [row for row in metrics if row["horizon_weeks"] == 1 and row["failure_count"] == 0]
    baseline_by_horizon = {
        horizon: min(
            (row for row in metrics if row["horizon_weeks"] == horizon
             and row["model_id"] in BASELINE_IDS and row["failure_count"] == 0),
            key=lambda row: row["mae_minor"],
        )
        for horizon in (1, 2, 4)
    }
    baseline = baseline_by_horizon[1]
    stable = []
    for row in one_week:
        matching = {
            int(item["horizon_weeks"]): item for item in metrics
            if item["model_id"] == row["model_id"]
            and item["training_treatment"] == row["training_treatment"]
            and item["horizon_weeks"] in {2, 4} and item["failure_count"] == 0
        }
        if all(horizon in matching and int(matching[horizon]["mae_minor"]) <= int(baseline_by_horizon[horizon]["mae_minor"]) * 1.10
               for horizon in (2, 4)):
            stable.append(row)
    best_mae = min(int(row["mae_minor"]) for row in stable)
    near_best = [row for row in stable if int(row["mae_minor"]) <= best_mae * 1.03]
    best = min(near_best, key=lambda row: (COMPLEXITY.get(str(row["model_id"]), 9), int(row["rmse_minor"])))
    improvement = (int(baseline["mae_minor"]) - int(best["mae_minor"])) / int(baseline["mae_minor"])
    if str(best["model_id"]) not in BASELINE_IDS and improvement < 0.05:
        best = baseline
        improvement = 0.0
    treatment = str(best["training_treatment"])
    return str(best["model_id"]), treatment, {
        "best_baseline_mae_minor": int(baseline["mae_minor"]),
        "selected_mae_minor": int(best["mae_minor"]),
        "mae_improvement_rate": improvement,
        "rmse_improvement_rate": (
            (int(baseline["rmse_minor"]) - int(best["rmse_minor"])) / int(baseline["rmse_minor"])
        ),
    }


def _plot_outputs(output_dir: Path, frames: dict[str, pd.DataFrame], comparisons: pd.DataFrame,
                  selected_results: dict[str, object], future: pd.DataFrame) -> None:
    figures = output_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    total = selected_results["total"]
    total_points = pd.DataFrame(total["one_week_points"])
    source = frames["total"]
    total_points["week_start"] = total_points["target_index"].map(dict(enumerate(source["week_start"])))

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(pd.to_datetime(source["week_start"]), source["target_minor"] / 100, label="Actual", color="#333333")
    ax.plot(pd.to_datetime(total_points["week_start"]), total_points["prediction_minor"] / 100,
            label="Rolling prediction", color="#4C78A8")
    ax.set(title="Weekly total consumption: actual vs rolling prediction", ylabel="CNY")
    ax.legend(); ax.grid(alpha=.25); fig.tight_layout(); fig.savefig(figures / "total_actual_vs_backtest.png", dpi=180); plt.close(fig)

    raw1 = comparisons[(comparisons.target == "total") & (comparisons.horizon_weeks == 1) & (comparisons.training_treatment == "raw")]
    fig, ax = plt.subplots(figsize=(10, 6)); ordered = raw1.sort_values("mae_minor")
    ax.barh(ordered.model_id, ordered.mae_minor / 100, color="#4C78A8")
    ax.set(title="Total consumption model comparison (1-week MAE)", xlabel="MAE (CNY)")
    ax.grid(axis="x", alpha=.25); fig.tight_layout(); fig.savefig(figures / "model_mae_comparison.png", dpi=180); plt.close(fig)

    selected_id = total["selected_model_id"]
    horizon = comparisons[(comparisons.target == "total") & (comparisons.model_id == selected_id) &
                          (comparisons.training_treatment == total["training_treatment"])].sort_values("horizon_weeks")
    fig, ax = plt.subplots(figsize=(7, 4)); ax.bar(horizon.horizon_weeks.astype(str), horizon.mae_minor / 100, color="#59A14F")
    ax.set(title="Selected total model MAE by horizon", xlabel="Forecast horizon (weeks)", ylabel="MAE (CNY)")
    ax.grid(axis="y", alpha=.25); fig.tight_layout(); fig.savefig(figures / "horizon_mae_comparison.png", dpi=180); plt.close(fig)

    values = source.target_minor.to_numpy(); q1, q3 = np.quantile(values, [.25, .75]); threshold = q3 + 1.5 * (q3 - q1)
    anomaly_indices = np.where(values > threshold)[0]
    mask = total_points.target_index.isin(anomaly_indices)
    fig, ax = plt.subplots(figsize=(9, 4)); subset = total_points[mask]
    ax.bar(np.arange(len(subset)) - .18, subset.actual_minor / 100, width=.36, label="Actual", color="#E15759")
    ax.bar(np.arange(len(subset)) + .18, subset.prediction_minor / 100, width=.36, label="Predicted", color="#4C78A8")
    ax.set(title="Selected model around statistical anomaly weeks", ylabel="CNY", xticks=np.arange(len(subset)))
    ax.legend(); ax.grid(axis="y", alpha=.25); fig.tight_layout(); fig.savefig(figures / "anomaly_week_performance.png", dpi=180); plt.close(fig)

    total_future = future[future.category == "total"]
    fig, ax = plt.subplots(figsize=(9, 4)); x = pd.to_datetime(total_future.week_start)
    ax.plot(x, total_future.point_forecast_minor / 100, marker="o", color="#4C78A8")
    ax.fill_between(x, total_future.lower_bound_minor / 100, total_future.upper_bound_minor / 100,
                    alpha=.2, color="#4C78A8", label="80% empirical interval")
    ax.set(title="Future weekly total consumption forecast", ylabel="CNY")
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO, interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.legend(); ax.grid(alpha=.25); fig.tight_layout(); fig.savefig(figures / "future_total_forecast.png", dpi=180); plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", default="experiments/forecasting")
    parser.add_argument("--initial-train-weeks", type=int, default=24)
    args = parser.parse_args()
    input_dir, output_dir = Path(args.input_dir), Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    models = default_forecasters()
    frames, comparison_rows, prediction_rows = {}, [], []
    result_cache = {}
    failures = []

    for target in ("total", "food", "shopping"):
        frame = pd.read_csv(input_dir / f"weekly_forecast_{target}.csv")
        if set(frame.dataset_version) != {DATASET_VERSION}:
            raise ValueError("unexpected_dataset_version")
        frames[target] = frame
        values = frame.target_minor.astype(float).tolist()
        treatments = ("raw", "winsorized") if target == "total" else ("raw",)
        for treatment in treatments:
            for model in models:
                for horizon in (1, 2, 4):
                    try:
                        result = rolling_backtest(values, model, horizon=horizon,
                                                  initial_train_weeks=args.initial_train_weeks,
                                                  training_treatment=treatment)
                    except Exception as exc:
                        failures.append({"target": target, "model_id": model_id(model), "horizon": horizon,
                                         "treatment": treatment, "error_type": type(exc).__name__})
                        continue
                    key = (target, model_id(model), treatment, horizon)
                    result_cache[key] = result
                    metric = asdict(result.metrics); metric["target"] = target
                    comparison_rows.append(metric)
                    for point in result.points:
                        row = asdict(point)
                        row.update({"target": target, "model_id": model_id(model), "model_version": model.version,
                                    "training_treatment": treatment, "horizon_weeks": horizon,
                                    "origin_week_start": frame.week_start.iloc[point.origin_index],
                                    "target_week_start": frame.week_start.iloc[point.target_index],
                                    "dataset_version": DATASET_VERSION})
                        prediction_rows.append(row)

    comparisons = pd.DataFrame(comparison_rows)
    predictions = pd.DataFrame(prediction_rows)
    comparisons.to_csv(output_dir / "model_comparison.csv", index=False)
    predictions.to_csv(output_dir / "backtest_predictions.csv", index=False)

    selected, future_rows, interval_rows = {}, [], []
    for target in ("total", "food", "shopping"):
        target_metrics = comparisons[comparisons.target == target].to_dict("records")
        chosen_id, treatment, improvement = choose_model(target_metrics)
        model = next(model for model in models if model_id(model) == chosen_id)
        values = frames[target].target_minor.astype(float).tolist()
        model_values = winsorize_training(values) if treatment == "winsorized" else values
        forecast = model.forecast(model_values, 4)
        points = [max(0, round(value)) for value in forecast.raw_predictions]
        last_week = pd.to_datetime(frames[target].week_start.iloc[-1]).date()
        weeks = [last_week + timedelta(days=7 * lead) for lead in range(1, 5)]
        one_week_result = result_cache[(target, chosen_id, treatment, 1)]
        future_intervals = []
        for lead, point in enumerate(points, 1):
            interval_horizon = 1 if lead == 1 else 2 if lead == 2 else 4
            lead_result = result_cache[(target, chosen_id, treatment, interval_horizon)]
            errors = [item.absolute_error_minor for item in lead_result.points]
            lower80, upper80 = prediction_interval_from_errors(point, errors, .80)
            lower95, upper95 = prediction_interval_from_errors(point, errors, .95)
            future_intervals.append((lower80, upper80, lower95, upper95))
            future_rows.append({
                "week_start": weeks[lead - 1].isoformat(),
                "week_end": (weeks[lead - 1] + timedelta(days=6)).isoformat(),
                "point_forecast_minor": point, "raw_point_forecast": forecast.raw_predictions[lead - 1],
                "lower_bound_minor": lower80, "upper_bound_minor": upper80,
                "lower95_minor": lower95, "upper95_minor": upper95,
                "model_name": model.name, "model_version": f"{target}-weekly-forecast-v1",
                "forecast_horizon": lead, "category": target, "generated_at": generated_at,
                "dataset_version": DATASET_VERSION,
            })
        selected[target] = {
            "selected_model_id": chosen_id, "training_treatment": treatment,
            "production_version": f"{target}-weekly-forecast-v1",
            "selection": improvement, "fit_parameters": forecast.fit_parameters,
            "one_week_metrics": asdict(one_week_result.metrics),
            "one_week_points": [asdict(point) for point in one_week_result.points],
            "monthly_allocation_minor": allocate_weekly_to_months(weeks, points),
            "update_policy": "refit weekly; rerun model comparison after every 8 new complete weeks",
        }
        for level in (80, 95):
            metric = one_week_result.metrics
            interval_rows.append({
                "target": target, "model_id": chosen_id, "level": level,
                "coverage_bps": getattr(metric, f"interval{level}_coverage_bps"),
                "average_width_minor": getattr(metric, f"interval{level}_average_width_minor"),
            })

    future = pd.DataFrame(future_rows); future.to_csv(output_dir / "future_forecast.csv", index=False)
    interval_frame = pd.DataFrame(interval_rows); interval_frame.to_json(
        output_dir / "interval_evaluation.json", orient="records", force_ascii=False, indent=2)
    (output_dir / "selected_model.json").write_text(json.dumps({
        "experiment_version": "weekly-rolling-backtest-v2", "dataset_version": DATASET_VERSION,
        "initial_train_weeks": args.initial_train_weeks, "generated_at": generated_at,
        "models": selected,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    trend = {}
    for target, frame in frames.items():
        values = frame.target_minor.to_numpy(dtype=float)
        slope = float(np.polyfit(np.arange(len(values)), values, 1)[0])
        robust = winsorize_training(values)
        robust_slope = float(np.polyfit(np.arange(len(robust)), robust, 1)[0])
        trend[target] = {"raw_slope_minor_per_week": slope, "winsorized_slope_minor_per_week": robust_slope}
    (output_dir / "forecast_metrics.json").write_text(json.dumps({
        "experiment_version": "weekly-rolling-backtest-v2", "dataset_version": DATASET_VERSION,
        "initial_train_weeks": args.initial_train_weeks, "targets": ["total", "food", "shopping"],
        "trend_diagnostics": trend, "experiment_failures": failures,
        "negative_predictions_total": int(comparisons.negative_prediction_count.sum()),
        "privacy": {"weekly_aggregates_only": True, "contains_transaction_text": False},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    _plot_outputs(output_dir, frames, comparisons, selected, future)
    print(json.dumps({target: {"model": selected[target]["selected_model_id"],
                                     "treatment": selected[target]["training_treatment"],
                                     "mae_minor": selected[target]["one_week_metrics"]["mae_minor"]}
                      for target in selected}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
