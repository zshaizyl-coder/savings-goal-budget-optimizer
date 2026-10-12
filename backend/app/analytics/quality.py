from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .consumption import UNALLOCATED_REFUND


@dataclass(frozen=True)
class QualificationRule:
    minimum_transactions: int = 50
    minimum_active_weeks: int = 30
    minimum_gross_share: float = 0.05
    maximum_zero_week_ratio: float = 0.45


def weekly_descriptive_statistics(weekly: pd.DataFrame) -> dict[str, float | int]:
    values = weekly.loc[weekly["is_complete_week"], "net_consumption_minor"].astype(float)
    if values.empty:
        return {}
    q1, median, q3 = values.quantile([0.25, 0.5, 0.75]).tolist()
    mean = float(values.mean())
    std = float(values.std(ddof=1))
    return {
        "complete_weeks": int(len(values)),
        "mean_minor": mean,
        "median_minor": float(median),
        "std_minor": std,
        "cv": std / abs(mean) if mean else float("nan"),
        "minimum_minor": float(values.min()),
        "maximum_minor": float(values.max()),
        "q1_minor": float(q1),
        "q3_minor": float(q3),
        "iqr_minor": float(q3 - q1),
        "skewness": float(values.skew()),
    }


def identify_anomaly_weeks(weekly: pd.DataFrame) -> tuple[pd.DataFrame, float, float]:
    complete = weekly.loc[weekly["is_complete_week"]].copy()
    values = complete["net_consumption_minor"].astype(float)
    if values.empty:
        return complete.iloc[0:0], float("nan"), float("nan")
    q1, q3 = values.quantile([0.25, 0.75]).tolist()
    iqr = q3 - q1
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    anomalies = complete.loc[(values < lower) | (values > upper)].copy()
    anomalies["anomaly_direction"] = np.where(anomalies["net_consumption_minor"] > upper, "high", "low")
    return anomalies, lower, upper


def category_quality_table(
    facts: pd.DataFrame,
    category_weekly: pd.DataFrame,
    weekly: pd.DataFrame,
    rule: QualificationRule = QualificationRule(),
) -> pd.DataFrame:
    full_weeks = set(weekly.loc[weekly["is_complete_week"], "week_start"])
    total_gross = int(facts["gross_consumption_minor"].sum())
    consumption = facts.loc[facts["consumption_count"] == 1]
    categories = sorted(set(consumption["category_code"].dropna()) | set(category_weekly["category_code"].dropna()))
    rows: list[dict[str, object]] = []
    for category in categories:
        source = consumption.loc[consumption["category_code"] == category]
        series_frame = category_weekly.loc[
            (category_weekly["category_code"] == category) & category_weekly["week_start"].isin(full_weeks)
        ].sort_values("week_start")
        net = series_frame["net_consumption_minor"].astype(float)
        gross = series_frame["gross_consumption_minor"].astype(float)
        active_weeks = int((gross > 0).sum())
        complete_count = len(full_weeks)
        mean = float(net.mean()) if complete_count else 0.0
        std = float(net.std(ddof=1)) if complete_count > 1 else 0.0
        tx_count = int(len(source))
        gross_total = int(source["gross_consumption_minor"].sum())
        share = gross_total / total_gross if total_gross else 0.0
        zero_ratio = 1 - active_weeks / complete_count if complete_count else 1.0
        if category == "other":
            grade, reason = "C", "semantic_heterogeneity"
        elif category == UNALLOCATED_REFUND:
            grade, reason = "C", "refund_allocation_bucket"
        elif (
            tx_count >= rule.minimum_transactions
            and active_weeks >= rule.minimum_active_weeks
            and share >= rule.minimum_gross_share
            and zero_ratio <= rule.maximum_zero_week_ratio
        ):
            grade, reason = "A", "meets_independent_forecast_rule"
        elif tx_count >= 20 or active_weeks >= 12 or share >= 0.01:
            grade, reason = "B", "budget_analysis_only"
        else:
            grade, reason = "C", "insufficient_coverage"
        rows.append({
            "category_code": category,
            "transaction_count": tx_count,
            "gross_amount_minor": gross_total,
            "gross_amount_share": share,
            "active_weeks": active_weeks,
            "nonzero_weeks": int((net != 0).sum()),
            "zero_week_ratio": zero_ratio,
            "weekly_mean_minor": mean,
            "weekly_median_minor": float(net.median()) if complete_count else 0.0,
            "weekly_std_minor": std,
            "weekly_cv": std / abs(mean) if mean else float("nan"),
            "qualification": grade,
            "qualification_reason": reason,
        })
    return pd.DataFrame(rows).sort_values(["qualification", "gross_amount_minor"], ascending=[True, False], ignore_index=True)


def refund_sensitivity_table(
    category_weekly_total_only: pd.DataFrame,
    category_weekly_reliable: pd.DataFrame,
    complete_week_starts: set,
) -> pd.DataFrame:
    def summarize(frame: pd.DataFrame, suffix: str) -> pd.DataFrame:
        selected = frame.loc[frame["week_start"].isin(complete_week_starts)]
        return selected.groupby("category_code").agg(**{
            f"net_minor_{suffix}": ("net_consumption_minor", "sum"),
            f"weekly_mean_minor_{suffix}": ("net_consumption_minor", "mean"),
            f"weekly_std_minor_{suffix}": ("net_consumption_minor", "std"),
        })

    left = summarize(category_weekly_total_only, "total_only")
    right = summarize(category_weekly_reliable, "reliable")
    result = left.join(right, how="outer").fillna(0).reset_index()
    result["allocated_refund_effect_minor"] = result["net_minor_reliable"] - result["net_minor_total_only"]
    return result.sort_values("category_code", ignore_index=True)
