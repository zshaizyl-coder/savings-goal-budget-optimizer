from __future__ import annotations

import pandas as pd


DATASET_VERSION = "weekly-forecast-v2-routine"


def build_weekly_forecast_dataset(
    weekly: pd.DataFrame,
    *,
    target_category_code: str | None = None,
    complete_only: bool = True,
) -> pd.DataFrame:
    """Build the only stage-6 model input; every feature is past-only."""
    if weekly.empty:
        return pd.DataFrame()
    source = weekly.sort_values("week_start").reset_index(drop=True).copy()
    if source["week_start"].duplicated().any():
        raise ValueError("weekly data contains duplicate week_start values")
    if complete_only:
        source = source.loc[source["is_complete_week"]].reset_index(drop=True)
    target_column = ("routine_net_consumption_minor" if target_category_code is None
                     and "routine_net_consumption_minor" in source.columns
                     else "net_consumption_minor" if target_category_code is None
                     else f"category_routine__{target_category_code}" if f"category_routine__{target_category_code}" in source.columns
                     else f"category_net__{target_category_code}")
    if target_column not in source.columns:
        raise ValueError(f"target column not found: {target_column}")
    target = source[target_column].astype(float)
    shifted = target.shift(1)
    result = pd.DataFrame({
        "week_start": source["week_start"],
        "target_minor": target,
        "lag_1": target.shift(1),
        "lag_2": target.shift(2),
        "lag_3": target.shift(3),
        "lag_4": target.shift(4),
        "rolling_mean_2": shifted.rolling(2).mean(),
        "rolling_mean_4": shifted.rolling(4).mean(),
        "rolling_std_4": shifted.rolling(4).std(ddof=1),
    })
    dates = pd.to_datetime(result["week_start"])
    result["month"] = dates.dt.month
    result["week_of_year"] = dates.dt.isocalendar().week.astype(int)
    result["target_key"] = "total" if target_category_code is None else target_category_code
    result["dataset_version"] = DATASET_VERSION
    return result
