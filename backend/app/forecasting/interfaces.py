"""Replaceable forecasting interface; no model implementation in stage 3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping, Protocol, Sequence

from backend.app.domain.contracts import WeeklyPredictionItem


@dataclass(frozen=True)
class WeeklyActual:
    week_start: date
    target_key: str
    net_spend_minor: int
    is_complete_week: bool
    is_abnormal: bool


@dataclass(frozen=True)
class ForecastRequest:
    history: Sequence[WeeklyActual]
    horizon_weeks: int
    target_keys: tuple[str, ...]
    data_cutoff_utc: datetime
    model_name: str
    model_version: str
    parameters: Mapping[str, object]


@dataclass(frozen=True)
class ForecastResult:
    model_name: str
    model_version: str
    train_start: date
    train_end: date
    data_cutoff_utc: datetime
    items: tuple[WeeklyPredictionItem, ...]
    warnings: tuple[str, ...] = ()


class WeeklyForecaster(Protocol):
    def forecast(self, request: ForecastRequest) -> ForecastResult:
        """Return predictions without reading files or writing persistence."""
