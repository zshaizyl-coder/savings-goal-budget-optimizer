"""Shared enums that are independent of source files, web frameworks, and ORM."""

from enum import StrEnum


class PaymentSource(StrEnum):
    WECHAT = "wechat"
    ALIPAY = "alipay"
    BANK = "bank"


class Direction(StrEnum):
    INFLOW = "inflow"
    OUTFLOW = "outflow"
    NEUTRAL = "neutral"


class FlowType(StrEnum):
    CONSUMPTION = "consumption"
    INCOME = "income"
    INCOME_CANDIDATE = "income_candidate"
    EXPENSE_REFUND = "expense_refund"
    TRANSFER = "transfer"
    INTERNAL_TRANSFER = "internal_transfer"
    BALANCE_TRANSFER = "balance_transfer"
    RED_PACKET = "red_packet"
    OTHER = "other"


class NormalizedStatus(StrEnum):
    COMPLETED = "completed"
    REFUNDED = "refunded"
    PARTIAL_REFUND = "partial_refund"
    REVERSED = "reversed"
    PENDING = "pending"
    FAILED = "failed"
    UNKNOWN = "unknown"


class AdjustmentMode(StrEnum):
    GOAL_PRIORITY = "goal_priority"
    BALANCED = "balanced"
    LIFE_STABILITY = "life_stability"
    CUSTOM = "custom"


class BudgetStrategy(StrEnum):
    PROPORTIONAL = "proportional"
    HISTORICAL_SHARE = "historical_share"
    PERSONALIZED = "personalized"


class FinancialEventType(StrEnum):
    PLANNED_OVERRUN = "planned_overrun"
    UNPLANNED_EXPENSE = "unplanned_expense"
    EXTRA_INCOME = "extra_income"
    UNEXPECTED_EXPENSE = "unexpected_expense"
    TEMPORARY_INCOME = "temporary_income"
    INCOME_DECREASE = "income_decrease"
    INCOME_INCREASE = "income_increase"
    GOAL_AMOUNT_CHANGE = "goal_amount_change"
    GOAL_DATE_CHANGE = "goal_date_change"
    BUDGET_CONSTRAINT_CHANGE = "budget_constraint_change"
    MANUAL_REPLAN = "manual_replan"
    GOAL_CHANGE = "goal_change"
    CONSTRAINT_CHANGE = "constraint_change"


class ImpactScope(StrEnum):
    BALANCE = "balance"
    FUTURE_INCOME = "future_income"
    GOAL = "goal"
    CONSTRAINT = "constraint"
    ACTUAL_SPEND = "actual_spend"


class ReplanTriggerSource(StrEnum):
    SYSTEM_SUGGESTED = "system_suggested"
    USER_REQUESTED = "user_requested"
    EVENT_REQUIRED = "event_required"


class BudgetPlanStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    COMPLETED = "completed"
    INFEASIBLE = "infeasible"
