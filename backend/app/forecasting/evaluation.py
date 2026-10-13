from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from math import sqrt
from typing import Sequence

import numpy as np

from .models import NumericForecaster, model_id


@dataclass(frozen=True)
class BacktestPoint:
    origin_index: int
    target_index: int
    actual_minor: int
    raw_prediction_minor: float
    prediction_minor: int
    was_negative: bool
    fit_parameters: dict[str, object]
    absolute_error_minor: int
    interval80_lower_minor: int | None
    interval80_upper_minor: int | None
    interval95_lower_minor: int | None
    interval95_upper_minor: int | None


@dataclass(frozen=True)
class BacktestMetrics:
    model_id: str
    model_name: str
    model_version: str
    training_treatment: str
    horizon_weeks: int
    sample_count: int
    failure_count: int
    negative_prediction_count: int
    mae_minor: int
    rmse_minor: int
    wape_bps: int | None
    mape_bps: int | None
    smape_bps: int
    anomaly_mae_minor: int | None
    early_mae_minor: int
    middle_mae_minor: int
    late_mae_minor: int
    interval80_coverage_bps: int | None
    interval80_average_width_minor: int | None
    interval95_coverage_bps: int | None
    interval95_average_width_minor: int | None


@dataclass(frozen=True)
class BacktestResult:
    metrics: BacktestMetrics
    points: tuple[BacktestPoint, ...]


def winsorize_training(values: Sequence[float]) -> np.ndarray:
    source = np.asarray(values, dtype=float)
    q1, q3 = np.quantile(source, [0.25, 0.75])
    return np.minimum(source, q3 + 1.5 * (q3 - q1))


def _third_mae(errors: np.ndarray) -> tuple[int, int, int]:
    if len(errors) < 3:
        value = round(float(np.mean(np.abs(errors))))
        return value, value, value
    parts = np.array_split(np.abs(errors), 3)
    return tuple(round(float(part.mean())) for part in parts)  # type: ignore[return-value]


def rolling_backtest(
    values: Sequence[float], forecaster: NumericForecaster, *, horizon: int,
    initial_train_weeks: int = 24, training_treatment: str = "raw",
) -> BacktestResult:
    actual = np.asarray(values, dtype=float)
    if len(actual) < initial_train_weeks + horizon:
        raise ValueError("insufficient_backtest_history")
    if training_treatment not in {"raw", "winsorized"}:
        raise ValueError("unsupported_training_treatment")
    points: list[BacktestPoint] = []
    prior_abs_errors: list[int] = []
    failures = 0
    for train_end in range(initial_train_weeks, len(actual) - horizon + 1):
        train = actual[:train_end]
        model_train = winsorize_training(train) if training_treatment == "winsorized" else train
        observed = round(float(actual[train_end + horizon - 1]))
        try:
            output = forecaster.forecast(model_train, horizon)
            raw_prediction = float(output.raw_predictions[horizon - 1])
        except Exception:
            failures += 1
            continue
        prediction = max(0, round(raw_prediction))
        error = abs(prediction - observed)
        bounds = {}
        for level, quantile in ((80, 0.80), (95, 0.95)):
            if len(prior_abs_errors) >= 5:
                radius = round(float(np.quantile(prior_abs_errors, quantile, method="higher")))
                bounds[level] = (max(0, prediction - radius), prediction + radius)
            else:
                bounds[level] = (None, None)
        points.append(BacktestPoint(
            origin_index=train_end - 1, target_index=train_end + horizon - 1,
            actual_minor=observed, raw_prediction_minor=raw_prediction,
            prediction_minor=prediction, was_negative=raw_prediction < 0,
            fit_parameters=output.fit_parameters, absolute_error_minor=error,
            interval80_lower_minor=bounds[80][0], interval80_upper_minor=bounds[80][1],
            interval95_lower_minor=bounds[95][0], interval95_upper_minor=bounds[95][1],
        ))
        prior_abs_errors.append(error)
    if not points:
        raise ValueError("all_backtest_origins_failed")
    errors = np.asarray([point.prediction_minor - point.actual_minor for point in points], dtype=float)
    actuals = np.asarray([point.actual_minor for point in points], dtype=float)
    predictions = np.asarray([point.prediction_minor for point in points], dtype=float)
    q1, q3 = np.quantile(actual, [0.25, 0.75])
    anomaly = actuals > q3 + 1.5 * (q3 - q1)
    denominator = np.abs(actuals) + np.abs(predictions)
    smape = np.divide(2 * np.abs(errors), denominator, out=np.zeros_like(denominator), where=denominator != 0)
    mape = None if np.any(actuals == 0) else round(float(np.mean(np.abs(errors / actuals))) * 10_000)
    wape = None if actuals.sum() == 0 else round(float(np.abs(errors).sum() / actuals.sum()) * 10_000)

    def interval_stats(level: int) -> tuple[int | None, int | None]:
        eligible = [point for point in points if getattr(point, f"interval{level}_lower_minor") is not None]
        if not eligible:
            return None, None
        covered = sum(
            getattr(point, f"interval{level}_lower_minor") <= point.actual_minor <= getattr(point, f"interval{level}_upper_minor")
            for point in eligible
        )
        widths = [
            getattr(point, f"interval{level}_upper_minor") - getattr(point, f"interval{level}_lower_minor")
            for point in eligible
        ]
        return round(covered / len(eligible) * 10_000), round(sum(widths) / len(widths))

    coverage80, width80 = interval_stats(80)
    coverage95, width95 = interval_stats(95)
    early, middle, late = _third_mae(errors)
    metrics = BacktestMetrics(
        model_id=model_id(forecaster), model_name=forecaster.name, model_version=forecaster.version,
        training_treatment=training_treatment, horizon_weeks=horizon,
        sample_count=len(points), failure_count=failures,
        negative_prediction_count=sum(point.was_negative for point in points),
        mae_minor=round(float(np.mean(np.abs(errors)))), rmse_minor=round(sqrt(float(np.mean(errors ** 2)))),
        wape_bps=wape, mape_bps=mape, smape_bps=round(float(np.mean(smape)) * 10_000),
        anomaly_mae_minor=round(float(np.mean(np.abs(errors[anomaly])))) if anomaly.any() else None,
        early_mae_minor=early, middle_mae_minor=middle, late_mae_minor=late,
        interval80_coverage_bps=coverage80, interval80_average_width_minor=width80,
        interval95_coverage_bps=coverage95, interval95_average_width_minor=width95,
    )
    return BacktestResult(metrics, tuple(points))


def prediction_interval_from_errors(point: int, errors: Sequence[int], level: float) -> tuple[int, int]:
    if not errors:
        raise ValueError("residual_history_required")
    radius = round(float(np.quantile(np.abs(errors), level, method="higher")))
    return max(0, point - radius), point + radius


def allocate_weekly_to_months(week_starts: Sequence[date], predictions_minor: Sequence[int]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for week_start, amount in zip(week_starts, predictions_minor, strict=True):
        base, remainder = divmod(amount, 7)
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            key = day.strftime("%Y-%m")
            totals[key] = totals.get(key, 0) + base + (1 if offset < remainder else 0)
    return dict(sorted(totals.items()))


def metrics_as_dict(metrics: BacktestMetrics) -> dict[str, object]:
    return asdict(metrics)
