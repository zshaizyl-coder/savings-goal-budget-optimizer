"""Privacy-safe consumption aggregation and forecast dataset construction."""

from .consumption import RefundAllocationPolicy, build_consumption_datasets, calculate_net_consumption
from .forecast_dataset import build_weekly_forecast_dataset

__all__ = [
    "RefundAllocationPolicy",
    "build_consumption_datasets",
    "build_weekly_forecast_dataset",
    "calculate_net_consumption",
]
