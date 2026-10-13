from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence
import warnings

import numpy as np
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.holtwinters import ExponentialSmoothing, SimpleExpSmoothing


@dataclass(frozen=True)
class NumericForecast:
    raw_predictions: tuple[float, ...]
    fit_parameters: dict[str, object]


class NumericForecaster(Protocol):
    name: str
    version: str

    def forecast(self, history: Sequence[float], horizon: int) -> NumericForecast: ...


def _validate(history: Sequence[float], horizon: int, minimum: int = 1) -> np.ndarray:
    values = np.asarray(history, dtype=float)
    if horizon < 1:
        raise ValueError("horizon must be positive")
    if len(values) < minimum:
        raise ValueError(f"insufficient_history:{minimum}")
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("history must contain finite non-negative values")
    return values


@dataclass(frozen=True)
class HistoricalMeanForecaster:
    name: str = "historical_mean"
    version: str = "1.0"

    def forecast(self, history: Sequence[float], horizon: int) -> NumericForecast:
        values = _validate(history, horizon)
        return NumericForecast((float(values.mean()),) * horizon, {})


@dataclass(frozen=True)
class HistoricalMedianForecaster:
    name: str = "historical_median"
    version: str = "1.0"

    def forecast(self, history: Sequence[float], horizon: int) -> NumericForecast:
        values = _validate(history, horizon)
        return NumericForecast((float(np.median(values)),) * horizon, {})


@dataclass(frozen=True)
class RecentMeanForecaster:
    window: int
    name: str = "recent_mean"
    version: str = "1.0"

    def forecast(self, history: Sequence[float], horizon: int) -> NumericForecast:
        values = _validate(history, horizon, self.window)
        return NumericForecast((float(values[-self.window:].mean()),) * horizon, {"window": self.window})


@dataclass(frozen=True)
class RecentMedianForecaster:
    window: int = 4
    name: str = "recent_median"
    version: str = "1.0"

    def forecast(self, history: Sequence[float], horizon: int) -> NumericForecast:
        values = _validate(history, horizon, self.window)
        return NumericForecast((float(np.median(values[-self.window:])),) * horizon, {"window": self.window})


@dataclass(frozen=True)
class SimpleExponentialSmoothingForecaster:
    name: str = "simple_exponential_smoothing"
    version: str = "1.0"

    def forecast(self, history: Sequence[float], horizon: int) -> NumericForecast:
        values = _validate(history, horizon, 8)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = SimpleExpSmoothing(values, initialization_method="estimated").fit(optimized=True)
        alpha = float(fit.params["smoothing_level"])
        return NumericForecast(tuple(float(value) for value in fit.forecast(horizon)), {"alpha": alpha})


@dataclass(frozen=True)
class HoltLinearForecaster:
    name: str = "holt_linear"
    version: str = "1.0"

    def forecast(self, history: Sequence[float], horizon: int) -> NumericForecast:
        values = _validate(history, horizon, 12)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = ExponentialSmoothing(values, trend="add", seasonal=None, initialization_method="estimated").fit(optimized=True)
        return NumericForecast(tuple(float(value) for value in fit.forecast(horizon)), {
            "alpha": float(fit.params["smoothing_level"]),
            "beta": float(fit.params["smoothing_trend"]),
        })


@dataclass(frozen=True)
class ArimaForecaster:
    order: tuple[int, int, int]
    name: str = "arima"
    version: str = "1.0"

    def forecast(self, history: Sequence[float], horizon: int) -> NumericForecast:
        values = _validate(history, horizon, 16)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = ARIMA(values, order=self.order, trend=None).fit()
        return NumericForecast(tuple(float(value) for value in fit.forecast(horizon)), {
            "order": list(self.order), "aic": float(fit.aic), "bic": float(fit.bic),
        })


@dataclass(frozen=True)
class LagRegressionForecaster:
    penalty: float = 0.0
    name: str = "linear_lag"
    version: str = "1.0"

    def _feature(self, values: Sequence[float]) -> list[float]:
        tail = np.asarray(values[-4:], dtype=float)
        return [tail[-1], tail[-2], tail[-4], float(tail.mean()), float(tail.std(ddof=1))]

    def forecast(self, history: Sequence[float], horizon: int) -> NumericForecast:
        values = _validate(history, horizon, 16)
        x = np.asarray([self._feature(values[:index]) for index in range(4, len(values))])
        y = values[4:]
        x_mean, x_std = x.mean(axis=0), x.std(axis=0)
        x_std[x_std == 0] = 1.0
        y_mean = float(y.mean())
        design = (x - x_mean) / x_std
        gram = design.T @ design + self.penalty * np.eye(design.shape[1])
        beta = np.linalg.pinv(gram) @ design.T @ (y - y_mean)
        extended = values.tolist()
        predictions = []
        for _ in range(horizon):
            feature = (np.asarray(self._feature(extended)) - x_mean) / x_std
            prediction = y_mean + float(feature @ beta)
            predictions.append(prediction)
            extended.append(max(0.0, prediction))
        return NumericForecast(tuple(predictions), {
            "penalty": self.penalty,
            "features": ["lag_1", "lag_2", "lag_4", "rolling_mean_4", "rolling_std_4"],
        })


def model_id(model: NumericForecaster) -> str:
    if isinstance(model, RecentMeanForecaster):
        return f"recent_mean_{model.window}"
    if isinstance(model, RecentMedianForecaster):
        return f"recent_median_{model.window}"
    if isinstance(model, ArimaForecaster):
        return "arima_" + "_".join(str(value) for value in model.order)
    if isinstance(model, LagRegressionForecaster):
        return "ridge_lag" if model.penalty else "linear_lag"
    return model.name


def default_forecasters() -> tuple[NumericForecaster, ...]:
    return (
        HistoricalMeanForecaster(), HistoricalMedianForecaster(),
        RecentMeanForecaster(2), RecentMeanForecaster(4), RecentMedianForecaster(4),
        SimpleExponentialSmoothingForecaster(), HoltLinearForecaster(),
        ArimaForecaster((1, 0, 0)), ArimaForecaster((0, 1, 1)),
        ArimaForecaster((1, 1, 0)), ArimaForecaster((1, 0, 1)),
        LagRegressionForecaster(0.0), LagRegressionForecaster(1.0),
    )
