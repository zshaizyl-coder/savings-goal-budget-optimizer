from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .core import (
    assert_finite_outputs, load_config, paired_statistics, run_experiment_a,
    run_experiment_b, run_experiment_c, run_experiment_d, summarize_frame, write_metadata,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).resolve().parent / "configs" / "stage10.json"


def _save_figure(fig, figures: Path, name: str) -> None:
    fig.savefig(figures / f"{name}.png", dpi=220, bbox_inches="tight", facecolor="white")
    fig.savefig(figures / f"{name}.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def make_figures(a: pd.DataFrame, b: pd.DataFrame, c: pd.DataFrame,
                 horizons: pd.DataFrame, figures: Path) -> None:
    plt.style.use("default")
    figures.mkdir(parents=True, exist_ok=True)
    medium = a[(a.pressure == "MEDIUM") & (a.user_type != "REAL_AGGREGATE") & a.feasible]
    fig, ax = plt.subplots(figsize=(8, 4.8))
    data = [medium.loc[medium.method == method, "habit_deviation_bps"] for method in
            ("UNIFORM_REDUCTION", "HISTORICAL_SHARE", "PERSONALIZED")]
    ax.boxplot(data, tick_labels=["Uniform", "Historical", "Personalized"], showmeans=True)
    ax.set_ylabel("Habit deviation (bps)"); ax.set_title("Budget method comparison (MEDIUM)"); ax.grid(axis="y", alpha=.25)
    _save_figure(fig, figures, "figure_a_habit_deviation")

    shock = b[(b.scenario_type == "SHOCK") & (b.timing == "EARLY")]
    fig, ax = plt.subplots(figsize=(8, 4.8))
    for method, group in shock.groupby("method"):
        ax.plot(group.shock_minor / 100, group.savings_gap_minor / 100, marker="o", label=method)
    ax.set_xlabel("Shock (CNY)"); ax.set_ylabel("Final savings gap (CNY)"); ax.set_title("Shock size and savings gap"); ax.legend(); ax.grid(alpha=.25)
    _save_figure(fig, figures, "figure_b_shock_gap")

    early = b[(b.scenario == "SHOCK_1000") & (b.timing == "EARLY")].set_index("method")
    target = max(early.final_savings_minor.max(), 1)
    weeks = list(range(13)); shock_week = 3
    fig, ax = plt.subplots(figsize=(8, 4.8))
    for method in ("NO_REPLAN", "ROLLING_REPLAN"):
        final = early.loc[method, "final_savings_minor"]
        values = [5000 + (target - 5000) * w / 12 for w in weeks]
        values = [value - (1000 if w >= shock_week else 0) for w, value in zip(weeks, values)]
        if method == "ROLLING_REPLAN":
            correction = final / 100 - values[-1]
            values = [value + (correction * max(0, w - shock_week) / (12 - shock_week)) for w, value in zip(weeks, values)]
        ax.plot(weeks, values, label=method)
    ax.axvline(shock_week, color="gray", linestyle="--", linewidth=1, label="shock")
    ax.set_xlabel("Week"); ax.set_ylabel("Cumulative savings (CNY)"); ax.set_title("Savings trajectory after CNY 1,000 shock"); ax.legend(); ax.grid(alpha=.25)
    _save_figure(fig, figures, "figure_b_savings_trajectory")

    fig, ax = plt.subplots(figsize=(8, 4.8))
    for method, group in c.groupby("method"):
        ax.plot(group.prediction_error * 100, group.savings_gap_minor / 100, marker="o", label=method)
    ax.set_xlabel("Prediction bias (%)"); ax.set_ylabel("Final savings gap (CNY)"); ax.set_title("Forecast-error robustness"); ax.legend(); ax.grid(alpha=.25)
    _save_figure(fig, figures, "figure_c_forecast_error")

    fig, ax = plt.subplots(figsize=(7, 4.8))
    ax.scatter(horizons.weeks_to_recover, horizons.max_weekly_adjustment_minor / 100, s=70)
    for row in horizons.itertuples():
        ax.annotate(str(row.recovery_horizon), (row.weeks_to_recover, row.max_weekly_adjustment_minor / 100), xytext=(5, 5), textcoords="offset points")
    ax.set_xlabel("Weeks to recover"); ax.set_ylabel("Average weekly adjustment (CNY)"); ax.set_title("Recovery speed and adjustment trade-off"); ax.grid(alpha=.25)
    _save_figure(fig, figures, "figure_d_recovery_horizon")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "results")
    parser.add_argument("--figures-dir", type=Path, default=Path(__file__).resolve().parent / "figures")
    parser.add_argument("--reuse-results", action="store_true",
                        help="Regenerate summaries/figures from existing core CSV outputs.")
    args = parser.parse_args()
    started = time.perf_counter(); config = load_config(args.config)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    core_names = {
        "a": "experiment_a_budget_methods.csv", "b": "experiment_b_replanning.csv",
        "c": "experiment_c_forecast_robustness.csv", "thresholds": "experiment_d_thresholds.csv",
        "horizons": "experiment_d_recovery_horizons.csv",
    }
    if args.reuse_results and all((args.output_dir / name).exists() for name in core_names.values()):
        a = pd.read_csv(args.output_dir / core_names["a"])
        b = pd.read_csv(args.output_dir / core_names["b"])
        c = pd.read_csv(args.output_dir / core_names["c"])
        thresholds = pd.read_csv(args.output_dir / core_names["thresholds"])
        horizons = pd.read_csv(args.output_dir / core_names["horizons"])
    else:
        a = run_experiment_a(ROOT, config); b = run_experiment_b(ROOT, config); c = run_experiment_c(ROOT, config)
        thresholds, horizons = run_experiment_d(ROOT, config)
    statistics = paired_statistics(a)
    assert_finite_outputs((a, b, c, thresholds, horizons, statistics))
    frames = {
        "experiment_a_budget_methods.csv": a, "experiment_b_replanning.csv": b,
        "experiment_c_forecast_robustness.csv": c, "experiment_d_thresholds.csv": thresholds,
        "experiment_d_recovery_horizons.csv": horizons, "statistical_tests.csv": statistics,
    }
    for name, frame in frames.items(): frame.to_csv(args.output_dir / name, index=False)
    summary_a = summarize_frame(a[a.feasible], ["user_type", "pressure", "method"],
                                ["goal_achievement_ratio", "habit_deviation_bps", "weekly_budget_volatility_minor", "max_category_adjustment_ratio", "minimum_violations"])
    summary_a.to_csv(args.output_dir / "table_1_2_budget_summary.csv", index=False)
    b.to_csv(args.output_dir / "table_3_shocks.csv", index=False)
    c.to_csv(args.output_dir / "table_4_forecast_error.csv", index=False)
    thresholds.to_csv(args.output_dir / "table_5_thresholds.csv", index=False)
    make_figures(a, b, c, horizons, args.figures_dir)
    elapsed = time.perf_counter() - started
    full_runtime = elapsed
    old_metadata_path = args.output_dir / "run_metadata.json"
    if args.reuse_results and old_metadata_path.exists():
        old_metadata = json.loads(old_metadata_path.read_text(encoding="utf-8"))
        full_runtime = old_metadata.get("runtime_seconds", elapsed)
    metadata = write_metadata(ROOT, args.config, args.output_dir, full_runtime)
    metadata["last_generation_mode"] = "reuse_results" if args.reuse_results else "full_core_run"
    if args.reuse_results:
        metadata["last_reuse_runtime_seconds"] = elapsed
    old_metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {"files": sorted(path.name for path in args.output_dir.iterdir()), "metadata": metadata}
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.output_dir.resolve() == (Path(__file__).resolve().parent / "results").resolve():
        from .generate_report import main as generate_report
        generate_report()
    print(json.dumps({"status": "ok", "rows": {name: len(frame) for name, frame in frames.items()}, "runtime_seconds": metadata["runtime_seconds"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
