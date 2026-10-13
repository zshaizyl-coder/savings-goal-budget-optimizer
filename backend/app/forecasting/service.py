from __future__ import annotations

from datetime import timedelta
from typing import Sequence

from backend.app.domain.contracts import WeeklyPredictionItem

from .evaluation import prediction_interval_from_errors
from .interfaces import ForecastRequest, ForecastResult
from .models import (
    ArimaForecaster, HistoricalMeanForecaster, HistoricalMedianForecaster,
    HoltLinearForecaster, LagRegressionForecaster, NumericForecaster,
    RecentMeanForecaster, RecentMedianForecaster, SimpleExponentialSmoothingForecaster,
)


def create_forecaster(model_id: str) -> NumericForecaster:
    factories = {
        "historical_mean": HistoricalMeanForecaster,
        "historical_median": HistoricalMedianForecaster,
        "recent_mean_2": lambda: RecentMeanForecaster(2),
        "recent_mean_4": lambda: RecentMeanForecaster(4),
        "recent_median_4": RecentMedianForecaster,
        "simple_exponential_smoothing": SimpleExponentialSmoothingForecaster,
        "holt_linear": HoltLinearForecaster,
        "arima_1_0_0": lambda: ArimaForecaster((1, 0, 0)),
        "arima_0_1_1": lambda: ArimaForecaster((0, 1, 1)),
        "arima_1_1_0": lambda: ArimaForecaster((1, 1, 0)),
        "arima_1_0_1": lambda: ArimaForecaster((1, 0, 1)),
        "linear_lag": lambda: LagRegressionForecaster(0.0),
        "ridge_lag": lambda: LagRegressionForecaster(1.0),
    }
    try:
        return factories[model_id]()
    except KeyError as exc:
        raise ValueError(f"unknown_forecast_model:{model_id}") from exc


class ProductionWeeklyForecaster:
    """Adapter from the frozen stage-3 contract to selected numeric models."""

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        if len(request.target_keys) != 1:
            raise ValueError("one target_key per forecast run is required")
        target = request.target_keys[0]
        history = sorted(
            (item for item in request.history if item.target_key == target and item.is_complete_week),
            key=lambda item: item.week_start,
        )
        if not history:
            raise ValueError("insufficient_history")
        model = create_forecaster(request.model_name)
        output = model.forecast([item.net_spend_minor for item in history], request.horizon_weeks)
        residuals = tuple(int(value) for value in request.parameters.get("absolute_errors_minor", ()))
        items = []
        for lead, raw in enumerate(output.raw_predictions, 1):
            point = max(0, round(raw))
            lower = upper = None
            if residuals:
                lower, upper = prediction_interval_from_errors(point, residuals, .80)
            items.append(WeeklyPredictionItem(
                week_start=history[-1].week_start + timedelta(days=7 * lead),
                target_key=target,
                predicted_minor=point,
                lower_minor=lower,
                upper_minor=upper,
            ))
        return ForecastResult(
            model_name=request.model_name,
            model_version=request.model_version,
            train_start=history[0].week_start,
            train_end=history[-1].week_start,
            data_cutoff_utc=request.data_cutoff_utc,
            items=tuple(items),
            warnings=("prediction_interval_unavailable",) if not residuals else (),
        )
