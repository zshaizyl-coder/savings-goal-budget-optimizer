from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
import math
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .types import AnalysisTransaction


UNALLOCATED_REFUND = "unallocated_refund"


class RefundAllocationPolicy(StrEnum):
    TOTAL_ONLY = "total_only"
    RELIABLE_LINKS = "reliable_links"


@dataclass(frozen=True)
class LargeTransactionThresholds:
    q95_minor: int
    iqr_upper_minor: int
    recommended_minor: int


@dataclass(frozen=True)
class ConsumptionDatasets:
    facts: pd.DataFrame
    daily: pd.DataFrame
    weekly: pd.DataFrame
    monthly: pd.DataFrame
    category_daily: pd.DataFrame
    category_weekly: pd.DataFrame
    category_monthly: pd.DataFrame
    thresholds: LargeTransactionThresholds


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def build_transaction_facts(
    transactions: list[AnalysisTransaction],
    *,
    timezone_name: str = "Asia/Shanghai",
    refund_policy: RefundAllocationPolicy = RefundAllocationPolicy.RELIABLE_LINKS,
) -> pd.DataFrame:
    """Create the single auditable fact table used by every aggregation."""
    zone = ZoneInfo(timezone_name)
    rows: list[dict[str, object]] = []
    for transaction in transactions:
        if transaction.occurred_at_utc.tzinfo is None or transaction.occurred_at_utc.utcoffset() is None:
            raise ValueError("transaction time must be timezone-aware")
        local_time = transaction.occurred_at_utc.astimezone(zone)
        direction = _enum_value(transaction.direction)
        flow_type = _enum_value(transaction.flow_type)
        is_consumption = flow_type == "consumption" and direction == "outflow"
        is_refund = flow_type == "expense_refund" and direction == "inflow"
        category_code: str | None = None
        if is_consumption:
            category_code = transaction.effective_category_code
        elif is_refund:
            if refund_policy == RefundAllocationPolicy.RELIABLE_LINKS and transaction.related_transaction_id is not None:
                category_code = transaction.effective_category_code
            else:
                category_code = UNALLOCATED_REFUND
        rows.append({
            "transaction_id": transaction.id,
            "occurred_at_utc": transaction.occurred_at_utc,
            "local_date": local_time.date(),
            "week_start": local_time.date() - timedelta(days=local_time.weekday()),
            "year_month": f"{local_time.year:04d}-{local_time.month:02d}",
            "direction": direction,
            "flow_type": flow_type,
            "source_type_code": transaction.source_type_code,
            "amount_minor": transaction.amount_minor,
            "category_code": category_code,
            "planning_role": transaction.planning_role,
            "gross_consumption_minor": transaction.amount_minor if is_consumption else 0,
            "refund_minor": transaction.amount_minor if is_refund else 0,
            "net_consumption_minor": transaction.amount_minor if is_consumption else (-transaction.amount_minor if is_refund else 0),
            "routine_net_consumption_minor": (
                transaction.amount_minor if is_consumption and transaction.planning_role == "routine"
                else (-transaction.amount_minor if is_refund and transaction.planning_role == "routine" else 0)
            ),
            "irregular_essential_minor": (
                transaction.amount_minor if is_consumption and transaction.planning_role == "irregular_essential" else 0
            ),
            "consumption_count": int(is_consumption),
            "refund_count": int(is_refund),
            "is_weekend": local_time.weekday() >= 5,
        })
    columns = [
        "transaction_id", "occurred_at_utc", "local_date", "week_start", "year_month",
        "direction", "flow_type", "source_type_code", "amount_minor", "category_code", "planning_role",
        "gross_consumption_minor", "refund_minor", "net_consumption_minor",
        "routine_net_consumption_minor", "irregular_essential_minor",
        "consumption_count", "refund_count", "is_weekend",
    ]
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["occurred_at_utc", "transaction_id"], ignore_index=True
    ) if rows else pd.DataFrame(columns=columns)


def calculate_net_consumption(transactions: list[AnalysisTransaction]) -> int:
    facts = build_transaction_facts(transactions)
    return int(facts["net_consumption_minor"].sum()) if not facts.empty else 0


def compute_large_transaction_thresholds(facts: pd.DataFrame) -> LargeTransactionThresholds:
    amounts = facts.loc[facts["consumption_count"] == 1, "amount_minor"].astype(float)
    if amounts.empty:
        return LargeTransactionThresholds(0, 0, 0)
    q1, q3, q95 = amounts.quantile([0.25, 0.75, 0.95]).tolist()
    iqr_upper = q3 + 1.5 * (q3 - q1)
    # The larger threshold avoids labeling a broad middle tail as "large";
    # callers may override it with an explicit user threshold.
    return LargeTransactionThresholds(
        q95_minor=math.ceil(q95),
        iqr_upper_minor=math.ceil(iqr_upper),
        recommended_minor=math.ceil(max(q95, iqr_upper)),
    )


def _daily(facts: pd.DataFrame, large_threshold_minor: int) -> pd.DataFrame:
    if facts.empty:
        return pd.DataFrame()
    work = facts.copy()
    work["large_transaction_count"] = (
        (work["consumption_count"] == 1) & (work["amount_minor"] > large_threshold_minor)
    ).astype(int)
    grouped = work.groupby("local_date", sort=True).agg(
        gross_consumption_minor=("gross_consumption_minor", "sum"),
        refund_minor=("refund_minor", "sum"),
        net_consumption_minor=("net_consumption_minor", "sum"),
        routine_net_consumption_minor=("routine_net_consumption_minor", "sum"),
        irregular_essential_minor=("irregular_essential_minor", "sum"),
        transaction_count=("transaction_id", "size"),
        consumption_count=("consumption_count", "sum"),
        refund_count=("refund_count", "sum"),
        large_transaction_count=("large_transaction_count", "sum"),
    )
    index = pd.date_range(min(work["local_date"]), max(work["local_date"]), freq="D")
    grouped.index = pd.to_datetime(grouped.index)
    result = grouped.reindex(index, fill_value=0).rename_axis("date").reset_index()
    result["date"] = result["date"].dt.date
    result["is_weekend"] = pd.to_datetime(result["date"]).dt.weekday >= 5
    return result


def _category_period(facts: pd.DataFrame, period_column: str, period_values) -> pd.DataFrame:
    relevant = facts.loc[(facts["consumption_count"] == 1) | (facts["refund_count"] == 1)].copy()
    if relevant.empty:
        return pd.DataFrame(columns=[period_column, "category_code", "gross_consumption_minor", "refund_minor", "net_consumption_minor", "routine_net_consumption_minor", "irregular_essential_minor"])
    grouped = relevant.groupby([period_column, "category_code"], sort=True).agg(
        gross_consumption_minor=("gross_consumption_minor", "sum"),
        refund_minor=("refund_minor", "sum"),
        net_consumption_minor=("net_consumption_minor", "sum"),
        routine_net_consumption_minor=("routine_net_consumption_minor", "sum"),
        irregular_essential_minor=("irregular_essential_minor", "sum"),
        consumption_count=("consumption_count", "sum"),
        refund_count=("refund_count", "sum"),
    ).reset_index()
    categories = sorted(relevant["category_code"].dropna().unique())
    complete = pd.MultiIndex.from_product([period_values, categories], names=[period_column, "category_code"]).to_frame(index=False)
    result = complete.merge(grouped, on=[period_column, "category_code"], how="left").fillna({
        "gross_consumption_minor": 0,
        "refund_minor": 0,
        "net_consumption_minor": 0,
        "routine_net_consumption_minor": 0,
        "irregular_essential_minor": 0,
        "consumption_count": 0,
        "refund_count": 0,
    })
    integer_columns = ["gross_consumption_minor", "refund_minor", "net_consumption_minor", "routine_net_consumption_minor", "irregular_essential_minor", "consumption_count", "refund_count"]
    result[integer_columns] = result[integer_columns].astype("int64")
    return result


def _category_weekly(facts: pd.DataFrame) -> pd.DataFrame:
    weeks = pd.date_range(min(facts["week_start"]), max(facts["week_start"]), freq="W-MON").date
    return _category_period(facts, "week_start", weeks)


def _weekly(facts: pd.DataFrame, category_weekly: pd.DataFrame) -> pd.DataFrame:
    if facts.empty:
        return pd.DataFrame()
    grouped = facts.groupby("week_start", sort=True).agg(
        gross_consumption_minor=("gross_consumption_minor", "sum"),
        refund_minor=("refund_minor", "sum"),
        net_consumption_minor=("net_consumption_minor", "sum"),
        routine_net_consumption_minor=("routine_net_consumption_minor", "sum"),
        irregular_essential_minor=("irregular_essential_minor", "sum"),
        transaction_count=("transaction_id", "size"),
        consumption_count=("consumption_count", "sum"),
        refund_count=("refund_count", "sum"),
    )
    week_index = pd.date_range(min(facts["week_start"]), max(facts["week_start"]), freq="W-MON").date
    result = grouped.reindex(week_index, fill_value=0).rename_axis("week_start").reset_index()
    min_date, max_date = min(facts["local_date"]), max(facts["local_date"])
    result["week_end"] = result["week_start"].map(lambda value: value + timedelta(days=6))
    result["is_complete_week"] = result.apply(
        lambda row: row["week_start"] >= min_date and row["week_end"] <= max_date, axis=1
    )
    if not category_weekly.empty:
        pivot = category_weekly.pivot(index="week_start", columns="category_code", values="net_consumption_minor").fillna(0)
        pivot = pivot.astype("int64")
        pivot.columns = [f"category_net__{column}" for column in pivot.columns]
        result = result.merge(pivot.reset_index(), on="week_start", how="left")
        routine = category_weekly.pivot(
            index="week_start", columns="category_code", values="routine_net_consumption_minor"
        ).fillna(0).astype("int64")
        routine.columns = [f"category_routine__{column}" for column in routine.columns]
        result = result.merge(routine.reset_index(), on="week_start", how="left")
    return result.sort_values("week_start", ignore_index=True)


def _monthly(facts: pd.DataFrame) -> pd.DataFrame:
    if facts.empty:
        return pd.DataFrame()
    grouped = facts.groupby("year_month", sort=True).agg(
        gross_consumption_minor=("gross_consumption_minor", "sum"),
        refund_minor=("refund_minor", "sum"),
        net_consumption_minor=("net_consumption_minor", "sum"),
        routine_net_consumption_minor=("routine_net_consumption_minor", "sum"),
        irregular_essential_minor=("irregular_essential_minor", "sum"),
        transaction_count=("transaction_id", "size"),
        consumption_count=("consumption_count", "sum"),
        refund_count=("refund_count", "sum"),
    ).reset_index()
    periods = pd.period_range(min(facts["year_month"]), max(facts["year_month"]), freq="M").astype(str)
    result = pd.DataFrame({"year_month": periods}).merge(grouped, on="year_month", how="left").fillna(0)
    numeric = ["gross_consumption_minor", "refund_minor", "net_consumption_minor", "routine_net_consumption_minor", "irregular_essential_minor", "transaction_count", "consumption_count", "refund_count"]
    result[numeric] = result[numeric].astype("int64")
    return result


def build_consumption_datasets(
    transactions: list[AnalysisTransaction],
    *,
    timezone_name: str = "Asia/Shanghai",
    refund_policy: RefundAllocationPolicy = RefundAllocationPolicy.RELIABLE_LINKS,
    user_large_threshold_minor: int | None = None,
) -> ConsumptionDatasets:
    facts = build_transaction_facts(transactions, timezone_name=timezone_name, refund_policy=refund_policy)
    thresholds = compute_large_transaction_thresholds(facts)
    threshold = thresholds.recommended_minor if user_large_threshold_minor is None else user_large_threshold_minor
    if threshold < 0:
        raise ValueError("large transaction threshold must be non-negative")
    if facts.empty:
        empty = pd.DataFrame()
        return ConsumptionDatasets(facts, empty, empty, empty, empty, empty, empty, thresholds)
    category_weekly = _category_weekly(facts)
    day_values = pd.date_range(min(facts["local_date"]), max(facts["local_date"]), freq="D").date
    month_values = pd.period_range(min(facts["year_month"]), max(facts["year_month"]), freq="M").astype(str)
    return ConsumptionDatasets(
        facts=facts,
        daily=_daily(facts, threshold),
        weekly=_weekly(facts, category_weekly),
        monthly=_monthly(facts),
        category_daily=_category_period(facts, "local_date", day_values).rename(columns={"local_date": "date"}),
        category_weekly=category_weekly,
        category_monthly=_category_period(facts, "year_month", month_values),
        thresholds=thresholds,
    )
