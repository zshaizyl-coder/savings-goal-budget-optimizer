"""Minimal immutable contracts used to validate the stage 3 architecture.

These are not SQLAlchemy or FastAPI models. They define framework-independent
boundaries that later adapters can map to and from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .enums import (
    AdjustmentMode,
    BudgetStrategy,
    Direction,
    FinancialEventType,
    FlowType,
    ImpactScope,
    NormalizedStatus,
    PaymentSource,
)


def _require_int_minor(name: str, value: int, *, allow_negative: bool = False) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer minor-unit amount")
    if not allow_negative and value < 0:
        raise ValueError(f"{name} must be non-negative")


def _require_bps(name: str, value: int) -> None:
    if type(value) is not int or not 0 <= value <= 10_000:
        raise ValueError(f"{name} must be an integer from 0 to 10000")


def _require_monday(name: str, value: date) -> None:
    if value.weekday() != 0:
        raise ValueError(f"{name} must be a Monday")


@dataclass(frozen=True)
class NormalizedTransaction:
    id: int
    user_id: int
    import_batch_id: int
    source: PaymentSource
    source_record_fingerprint: str
    occurred_at_utc: datetime
    amount_minor: int
    direction: Direction
    flow_type: FlowType
    normalized_status: NormalizedStatus
    source_type_code: str
    currency: str = "CNY"
    original_category: str | None = None
    suggested_category_code: str | None = None
    user_category_code: str | None = None
    related_transaction_id: int | None = None
    canonical_transaction_id: int | None = None
    is_abnormal: bool = False

    def __post_init__(self) -> None:
        _require_int_minor("amount_minor", self.amount_minor)
        if self.occurred_at_utc.tzinfo is None:
            raise ValueError("occurred_at_utc must be timezone-aware")
        if self.occurred_at_utc.utcoffset() is None or self.occurred_at_utc.utcoffset().total_seconds() != 0:
            raise ValueError("occurred_at_utc must use UTC")
        if len(self.currency) != 3:
            raise ValueError("currency must be a three-letter ISO code")
        if not self.source_record_fingerprint:
            raise ValueError("source_record_fingerprint is required")
        if self.related_transaction_id == self.id:
            raise ValueError("a transaction cannot relate to itself")
        if self.canonical_transaction_id == self.id:
            raise ValueError("a transaction cannot be its own canonical record")

    @property
    def effective_category_code(self) -> str | None:
        return self.user_category_code or self.suggested_category_code


@dataclass(frozen=True)
class FinancialSnapshotSpec:
    id: int
    user_id: int
    as_of_date: date
    available_savings_minor: int
    source: str = "user_input"
    currency: str = "CNY"

    def __post_init__(self) -> None:
        _require_int_minor("available_savings_minor", self.available_savings_minor)


@dataclass(frozen=True)
class SavingsGoalSpec:
    id: int
    user_id: int
    baseline_snapshot_id: int
    start_date: date
    target_date: date
    target_savings_minor: int
    version: int = 1
    currency: str = "CNY"

    def __post_init__(self) -> None:
        _require_int_minor("target_savings_minor", self.target_savings_minor)
        if self.target_savings_minor == 0:
            raise ValueError("target_savings_minor must be positive")
        if self.target_date <= self.start_date:
            raise ValueError("target_date must be after start_date")
        if self.version < 1:
            raise ValueError("version must be at least 1")


@dataclass(frozen=True)
class IncomeAssumptionSpec:
    goal_id: int
    frequency: str
    amount_minor: int
    start_date: date
    end_date: date
    expected_day: int | None = None

    def __post_init__(self) -> None:
        _require_int_minor("amount_minor", self.amount_minor)
        if self.frequency not in {"one_time", "weekly", "monthly"}:
            raise ValueError("unsupported income frequency")
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot precede start_date")


@dataclass(frozen=True)
class BudgetConstraintSpec:
    goal_id: int
    category_code: str
    minimum_budget_minor: int
    maximum_budget_minor: int | None
    is_fixed: bool
    adjustability_bps: int
    effective_from: date
    effective_to: date
    version: int = 1

    def __post_init__(self) -> None:
        _require_int_minor("minimum_budget_minor", self.minimum_budget_minor)
        if self.maximum_budget_minor is not None:
            _require_int_minor("maximum_budget_minor", self.maximum_budget_minor)
            if self.maximum_budget_minor < self.minimum_budget_minor:
                raise ValueError("maximum budget cannot be below minimum budget")
        _require_bps("adjustability_bps", self.adjustability_bps)
        if self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot precede effective_from")
        if self.is_fixed and (
            self.maximum_budget_minor != self.minimum_budget_minor
            or self.adjustability_bps != 0
        ):
            raise ValueError("a fixed constraint needs equal min/max and zero adjustability")


@dataclass(frozen=True)
class WeeklyPredictionItem:
    week_start: date
    target_key: str
    predicted_minor: int
    lower_minor: int | None = None
    upper_minor: int | None = None

    def __post_init__(self) -> None:
        _require_monday("week_start", self.week_start)
        _require_int_minor("predicted_minor", self.predicted_minor)
        if self.lower_minor is not None:
            _require_int_minor("lower_minor", self.lower_minor)
        if self.upper_minor is not None:
            _require_int_minor("upper_minor", self.upper_minor)
        if self.lower_minor is not None and self.lower_minor > self.predicted_minor:
            raise ValueError("lower bound cannot exceed prediction")
        if self.upper_minor is not None and self.upper_minor < self.predicted_minor:
            raise ValueError("upper bound cannot be below prediction")


@dataclass(frozen=True)
class FinancialEventSpec:
    id: int
    user_id: int
    goal_id: int
    event_type: FinancialEventType
    amount_minor: int | None
    event_date: date
    impact_scope: ImpactScope
    reason_code: str
    safe_note: str | None = None

    def __post_init__(self) -> None:
        if self.amount_minor is not None:
            _require_int_minor("amount_minor", self.amount_minor)
        if self.safe_note is not None and len(self.safe_note) > 200:
            raise ValueError("safe_note cannot exceed 200 characters")


@dataclass(frozen=True)
class BudgetPlanItem:
    week_start: date
    category_code: str
    natural_spend_minor: int
    recommended_budget_minor: int
    adjustment_minor: int
    minimum_constraint_minor: int
    maximum_constraint_minor: int | None
    adjustability_bps: int

    def __post_init__(self) -> None:
        _require_monday("week_start", self.week_start)
        _require_int_minor("natural_spend_minor", self.natural_spend_minor)
        _require_int_minor("recommended_budget_minor", self.recommended_budget_minor)
        _require_int_minor("adjustment_minor", self.adjustment_minor, allow_negative=True)
        _require_int_minor("minimum_constraint_minor", self.minimum_constraint_minor)
        if self.maximum_constraint_minor is not None:
            _require_int_minor("maximum_constraint_minor", self.maximum_constraint_minor)
        _require_bps("adjustability_bps", self.adjustability_bps)
        if self.adjustment_minor != (
            self.recommended_budget_minor - self.natural_spend_minor
        ):
            raise ValueError("adjustment must equal recommended minus natural spend")


@dataclass(frozen=True)
class BudgetPlanVersion:
    id: int
    user_id: int
    goal_id: int
    version: int
    strategy: BudgetStrategy
    adjustment_mode: AdjustmentMode
    plan_start_week: date
    plan_end_week: date
    is_feasible: bool
    items: tuple[BudgetPlanItem, ...]
    parent_plan_id: int | None = None
    trigger_event_ids: tuple[int, ...] = ()
    expected_final_savings_minor: int | None = None
    infeasibility_code: str | None = None
    maximum_achievable_target_minor: int | None = None
    required_additional_income_minor: int | None = None
    recommended_deadline: date | None = None
    snapshot_id: int | None = None
    risk_level: str | None = None
    status: str = "active"
    target_date_snapshot: date | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("version must be at least 1")
        if len(set(self.trigger_event_ids)) != len(self.trigger_event_ids):
            raise ValueError("trigger_event_ids cannot contain duplicates")
        _require_monday("plan_start_week", self.plan_start_week)
        _require_monday("plan_end_week", self.plan_end_week)
        if self.plan_end_week < self.plan_start_week:
            raise ValueError("plan_end_week cannot precede plan_start_week")
        if any(
            item.week_start < self.plan_start_week or item.week_start > self.plan_end_week
            for item in self.items
        ):
            raise ValueError("budget items must fall inside the plan period")
        for name in (
            "expected_final_savings_minor",
            "maximum_achievable_target_minor",
            "required_additional_income_minor",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_int_minor(name, value)
        if self.is_feasible:
            if self.expected_final_savings_minor is None:
                raise ValueError("a feasible plan needs expected final savings")
            if self.infeasibility_code is not None:
                raise ValueError("a feasible plan cannot have an infeasibility code")
        else:
            if not self.infeasibility_code:
                raise ValueError("an infeasible plan needs a reason code")
            if self.items:
                raise ValueError("an infeasible plan cannot contain pretend budget items")
