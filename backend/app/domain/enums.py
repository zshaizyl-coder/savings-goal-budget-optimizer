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
    UNEXPECTED_EXPENSE = "unexpected_expense"
    TEMPORARY_INCOME = "temporary_income"
    INCOME_DECREASE = "income_decrease"
    INCOME_INCREASE = "income_increase"
    GOAL_CHANGE = "goal_change"
    CONSTRAINT_CHANGE = "constraint_change"


class ImpactScope(StrEnum):
    BALANCE = "balance"
    FUTURE_INCOME = "future_income"
    GOAL = "goal"
    CONSTRAINT = "constraint"
    ACTUAL_SPEND = "actual_spend"
