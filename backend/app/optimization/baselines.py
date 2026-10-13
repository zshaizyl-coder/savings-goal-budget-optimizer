from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Mapping, Sequence

import numpy as np


def build_category_natural_baselines(
    category_weekly: Mapping[str, Sequence[int]],
    total_weekly_minor: int,
    *,
    model_overrides: Mapping[str, int] | None = None,
) -> dict[str, int]:
    """Create a complete category profile that exactly reconciles to total."""
    if total_weekly_minor < 0 or not category_weekly:
        raise ValueError("invalid_category_baseline_input")
    overrides = dict(model_overrides or {})
    base = {}
    for category, values in category_weekly.items():
        series = np.asarray(values, dtype=float)
        if len(series) == 0 or (series < 0).any():
            raise ValueError(f"invalid_category_history:{category}")
        # Sparse categories often have Q1=Q3=0; winsorising them would erase
        # genuine spending and distort the shares after reconciliation.  The
        # total model already defines the normal aggregate level, so categories
        # without their own model use their transparent historical mean as the
        # allocation weight.  Model-backed categories are replaced below.
        base[category] = max(0, round(float(series.mean())))
    for category, value in overrides.items():
        if category not in base or value < 0:
            raise ValueError(f"invalid_override:{category}")
        base[category] = value
    fixed_total = sum(base[key] for key in overrides)
    if fixed_total > total_weekly_minor:
        raise ValueError("model_overrides_exceed_total")
    remainder_keys = [key for key in base if key not in overrides]
    remainder = max(0, total_weekly_minor - fixed_total)
    weight_total = sum(base[key] for key in remainder_keys)
    allocated = {key: base[key] for key in overrides}
    running = 0
    for index, key in enumerate(sorted(remainder_keys)):
        value = remainder - running if index == len(remainder_keys) - 1 else (
            round(remainder * base[key] / weight_total) if weight_total else 0
        )
        allocated[key] = max(0, value)
        running += allocated[key]
    difference = total_weekly_minor - sum(allocated.values())
    if difference and remainder_keys:
        allocated[sorted(remainder_keys)[-1]] += difference
    if sum(allocated.values()) != total_weekly_minor:
        raise ValueError("category_baseline_reconciliation_failed")
    return dict(sorted(allocated.items()))


def expand_long_horizon_baseline(
    planning_weeks: Sequence[date], short_term: Mapping[date, Mapping[str, int]],
    long_term: Mapping[str, int],
) -> dict[tuple[date, str], int]:
    result = {}
    for week in planning_weeks:
        profile = short_term.get(week, long_term)
        for category, amount in profile.items():
            result[(week, category)] = int(amount)
    return result
