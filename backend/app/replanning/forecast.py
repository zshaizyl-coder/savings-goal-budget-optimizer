from __future__ import annotations

from datetime import date
from typing import Mapping, Sequence

import numpy as np

from backend.app.domain.contracts import WeeklyPredictionItem
from backend.app.forecasting.evaluation import prediction_interval_from_errors
from backend.app.forecasting.interfaces import WeeklyActual
from backend.app.optimization.baselines import build_category_natural_baselines


TOTAL_MODEL_VERSION = "total-weekly-forecast-v1"
FOOD_MODEL_VERSION = "food-weekly-forecast-v1"
SHOPPING_MODEL_VERSION = "shopping-weekly-forecast-v1"


class RollingForecastUpdater:
    """Refit frozen stage-6 model types using history available at the cutoff."""

    def forecast(
        self,
        history: Sequence[WeeklyActual],
        planning_weeks: Sequence[date],
        category_codes: Sequence[str],
        *,
        absolute_errors_minor: Sequence[int] = (),
    ) -> tuple[WeeklyPredictionItem, ...]:
        weeks = tuple(sorted(planning_weeks))
        if not weeks or any(week.weekday() != 0 for week in weeks):
            raise ValueError("invalid_planning_weeks")
        cutoff = weeks[0]
        if any(item.week_start >= cutoff for item in history):
            raise ValueError("future_actual_leakage")
        grouped: dict[str, list[int]] = {}
        for target in ("total", *category_codes):
            values = [item.net_spend_minor for item in sorted(history, key=lambda value: value.week_start)
                      if item.target_key == target and item.is_complete_week]
            if not values:
                raise ValueError(f"insufficient_history:{target}")
            grouped[target] = values
        total = round(float(np.mean(grouped["total"])))
        overrides = {}
        if "food" in grouped:
            overrides["food"] = round(float(np.median(grouped["food"])))
        if "shopping" in grouped:
            overrides["shopping"] = round(float(np.median(grouped["shopping"])))
        category_history = {key: values for key, values in grouped.items() if key != "total"}
        profile = build_category_natural_baselines(category_history, total, model_overrides=overrides)
        lower = upper = None
        if absolute_errors_minor:
            lower, upper = prediction_interval_from_errors(total, absolute_errors_minor, .80)
        result = []
        for week in weeks:
            result.extend(WeeklyPredictionItem(week, category, amount)
                          for category, amount in sorted(profile.items()))
            result.append(WeeklyPredictionItem(week, "total", total, lower, upper))
        return tuple(result)


def model_reevaluation_due(history: Sequence[WeeklyActual], selected_through_week: date | None,
                           interval_weeks: int = 8) -> bool:
    if selected_through_week is None:
        return False
    new_weeks = {item.week_start for item in history
                 if item.target_key == "total" and item.is_complete_week
                 and item.week_start > selected_through_week}
    return len(new_weeks) >= interval_weeks
