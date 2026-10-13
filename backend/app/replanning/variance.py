from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from backend.app.domain.contracts import BudgetPlanItem
from backend.app.domain.contracts import FinancialEventSpec
from backend.app.domain.enums import FinancialEventType, ReplanTriggerSource
from backend.app.forecasting.interfaces import WeeklyActual


@dataclass(frozen=True)
class VarianceTriggerPolicy:
    """Configurable suggestion threshold, not an automatic financial action."""

    absolute_overrun_minor: int = 10_000
    relative_overrun_bps: int = 2_000
    require_both: bool = True

    def __post_init__(self) -> None:
        if self.absolute_overrun_minor < 0:
            raise ValueError("absolute threshold must be non-negative")
        if not 0 <= self.relative_overrun_bps <= 10_000:
            raise ValueError("relative threshold must be 0..10000")


@dataclass(frozen=True)
class CategoryVariance:
    category_code: str
    budget_minor: int
    actual_minor: int
    absolute_variance_minor: int
    percentage_variance_bps: int | None


@dataclass(frozen=True)
class WeeklyVarianceReport:
    week_start: date
    budget_minor: int
    actual_minor: int
    absolute_variance_minor: int
    percentage_variance_bps: int | None
    category_variances: tuple[CategoryVariance, ...]
    replan_suggested: bool
    reason_codes: tuple[str, ...]


EVENT_REQUIRED_TYPES = {
    FinancialEventType.UNPLANNED_EXPENSE,
    FinancialEventType.EXTRA_INCOME,
    FinancialEventType.INCOME_DECREASE,
    FinancialEventType.INCOME_INCREASE,
    FinancialEventType.GOAL_AMOUNT_CHANGE,
    FinancialEventType.GOAL_DATE_CHANGE,
    FinancialEventType.BUDGET_CONSTRAINT_CHANGE,
}


def choose_replan_trigger(report: WeeklyVarianceReport | None,
                          events: Sequence[FinancialEventSpec], *, manual: bool = False
                          ) -> ReplanTriggerSource | None:
    if manual:
        return ReplanTriggerSource.USER_REQUESTED
    if any(event.event_type in EVENT_REQUIRED_TYPES for event in events):
        return ReplanTriggerSource.EVENT_REQUIRED
    if report is not None and report.replan_suggested:
        return ReplanTriggerSource.SYSTEM_SUGGESTED
    return None


def calculate_weekly_variance(
    items: Sequence[BudgetPlanItem],
    actuals: Sequence[WeeklyActual],
    week_start: date,
    policy: VarianceTriggerPolicy = VarianceTriggerPolicy(),
) -> WeeklyVarianceReport:
    planned = {item.category_code: item.recommended_budget_minor
               for item in items if item.week_start == week_start}
    observed = {
        item.target_key: item.net_spend_minor for item in actuals
        if item.week_start == week_start and item.target_key != "total"
    }
    budget_total = sum(planned.values())
    actual_total_record = next((
        item.net_spend_minor for item in actuals
        if item.week_start == week_start and item.target_key == "total"
    ), None)
    actual_total = sum(observed.values()) if actual_total_record is None else actual_total_record
    variance = actual_total - budget_total
    percentage = round(variance / budget_total * 10_000) if budget_total else None
    rows = []
    for category in sorted(set(planned) | set(observed)):
        category_budget = planned.get(category, 0)
        category_actual = observed.get(category, 0)
        delta = category_actual - category_budget
        ratio = round(delta / category_budget * 10_000) if category_budget else None
        rows.append(CategoryVariance(category, category_budget, category_actual, delta, ratio))
    absolute_hit = variance >= policy.absolute_overrun_minor
    relative_hit = percentage is not None and percentage >= policy.relative_overrun_bps
    suggested = absolute_hit and relative_hit if policy.require_both else absolute_hit or relative_hit
    reasons = tuple(code for hit, code in (
        (absolute_hit, "ABSOLUTE_OVERRUN_THRESHOLD"),
        (relative_hit, "RELATIVE_OVERRUN_THRESHOLD"),
    ) if hit)
    return WeeklyVarianceReport(
        week_start, budget_total, actual_total, variance, percentage,
        tuple(rows), suggested, reasons,
    )
