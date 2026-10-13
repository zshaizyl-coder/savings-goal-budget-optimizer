from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Date,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.db.types import UTCDateTime


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(180))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        Index("ix_import_batches_user_source_file", "user_id", "source", "file_fingerprint"),
        CheckConstraint("total_rows >= 0 AND parsed_rows >= 0 AND inserted_rows >= 0", name="ck_batch_counts_nonnegative"),
        CheckConstraint("duplicate_rows >= 0 AND failed_rows >= 0 AND warning_rows >= 0", name="ck_batch_issue_counts_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    source_file_type: Mapped[str] = mapped_column(String(20), nullable=False, default="xlsx")
    file_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parsed_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    earliest_transaction_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    latest_transaction_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    parser_version: Mapped[str] = mapped_column(String(40), nullable=False)
    error_summary_json: Mapped[str | None] = mapped_column(Text)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(60), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CategoryMapping(Base):
    __tablename__ = "category_mappings"
    __table_args__ = (
        UniqueConstraint("source", "source_type_code", "rule_version", name="uq_category_mapping_rule"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    source_type_code: Mapped[str] = mapped_column(String(80), nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    rule_version: Mapped[str] = mapped_column(String(40), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "source_record_fingerprint", name="uq_transaction_source_fingerprint"),
        CheckConstraint("amount_minor >= 0", name="ck_transaction_amount_nonnegative"),
        CheckConstraint("source IN ('wechat','alipay','bank')", name="ck_transaction_source"),
        CheckConstraint("direction IN ('inflow','outflow','neutral')", name="ck_transaction_direction"),
        Index("ix_transactions_user_time", "user_id", "occurred_at_utc"),
        Index("ix_transactions_user_flow_time", "user_id", "flow_type", "occurred_at_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    source_record_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    flow_type: Mapped[str] = mapped_column(String(28), nullable=False)
    normalized_status: Mapped[str] = mapped_column(String(24), nullable=False)
    source_type_code: Mapped[str] = mapped_column(String(80), nullable=False)
    original_category: Mapped[str | None] = mapped_column(String(80))
    suggested_category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    user_category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    classification_version: Mapped[str] = mapped_column(String(40), nullable=False)
    related_transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))
    canonical_transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))
    counterparty_fingerprint: Mapped[str | None] = mapped_column(String(64))
    source_account_fingerprint: Mapped[str | None] = mapped_column(String(64))
    is_abnormal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class TransactionLink(Base):
    __tablename__ = "transaction_links"
    __table_args__ = (
        UniqueConstraint("transaction_a_id", "transaction_b_id", "link_type", name="uq_transaction_link_pair"),
        CheckConstraint("confidence_bps >= 0 AND confidence_bps <= 10000", name="ck_link_confidence_bps"),
        CheckConstraint("transaction_a_id <> transaction_b_id", name="ck_link_distinct_transactions"),
        Index("ix_transaction_links_user_status", "user_id", "resolution_status", "link_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    transaction_a_id: Mapped[int] = mapped_column(ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False)
    transaction_b_id: Mapped[int] = mapped_column(ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False)
    link_type: Mapped[str] = mapped_column(String(28), nullable=False)
    confidence_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    evidence_codes: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class UserCategoryRule(Base):
    """A user-confirmed category for a pseudonymous counterparty.

    Raw merchant text is intentionally not stored. The HMAC fingerprint is
    stable only inside this installation and cannot be reversed.
    """

    __tablename__ = "user_category_rules"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "source", "counterparty_fingerprint",
            name="uq_user_category_rule_counterparty",
        ),
        Index("ix_user_category_rules_user_active", "user_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    counterparty_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    origin: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, onupdate=utc_now, nullable=False,
    )


class ClassificationSuggestion(Base):
    """Auditable model suggestion without prompt, response or merchant text."""

    __tablename__ = "classification_suggestions"
    __table_args__ = (
        CheckConstraint(
            "confidence_bps >= 0 AND confidence_bps <= 10000",
            name="ck_classification_suggestion_confidence",
        ),
        CheckConstraint(
            "status IN ('pending','accepted','rejected')",
            name="ck_classification_suggestion_status",
        ),
        UniqueConstraint(
            "user_id", "transaction_id", "provider", "model_name", "prompt_version",
            name="uq_classification_suggestion_run",
        ),
        Index("ix_classification_suggestions_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model_name: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    suggested_category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id"), nullable=False,
    )
    confidence_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    evidence_codes: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class FinancialSnapshot(Base):
    __tablename__ = "financial_snapshots"
    __table_args__ = (CheckConstraint("available_savings_minor >= 0", name="ck_snapshot_savings_nonnegative"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    available_savings_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="user_input")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class SavingGoal(Base):
    __tablename__ = "saving_goals"
    __table_args__ = (
        CheckConstraint("target_savings_minor > 0", name="ck_goal_target_positive"),
        CheckConstraint("version >= 1", name="ck_goal_version_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    baseline_snapshot_id: Mapped[int] = mapped_column(ForeignKey("financial_snapshots.id"), nullable=False)
    previous_goal_id: Mapped[int | None] = mapped_column(ForeignKey("saving_goals.id"))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_savings_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now, nullable=False)


class IncomeAssumption(Base):
    __tablename__ = "income_assumptions"
    __table_args__ = (
        CheckConstraint("amount_minor >= 0", name="ck_income_amount_nonnegative"),
        CheckConstraint("version >= 1", name="ck_income_version_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("saving_goals.id", ondelete="CASCADE"), nullable=False, index=True)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_day: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class BudgetConstraint(Base):
    __tablename__ = "budget_constraints"
    __table_args__ = (
        CheckConstraint("minimum_budget_minor >= 0", name="ck_constraint_minimum_nonnegative"),
        CheckConstraint("maximum_budget_minor IS NULL OR maximum_budget_minor >= minimum_budget_minor", name="ck_constraint_maximum"),
        CheckConstraint("adjustability_bps >= 0 AND adjustability_bps <= 10000", name="ck_constraint_adjustability"),
        CheckConstraint("version >= 1", name="ck_constraint_version_positive"),
        Index("ix_budget_constraints_goal_dates", "goal_id", "effective_from", "effective_to"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("saving_goals.id", ondelete="CASCADE"), nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    minimum_budget_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    maximum_budget_minor: Mapped[int | None] = mapped_column(BigInteger)
    is_fixed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    adjustability_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date] = mapped_column(Date, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class FinancialEvent(Base):
    __tablename__ = "financial_events"
    __table_args__ = (
        UniqueConstraint("user_id", "deduplication_key", name="uq_financial_event_dedup"),
        Index("ix_financial_events_goal_date", "goal_id", "event_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    goal_id: Mapped[int] = mapped_column(ForeignKey("saving_goals.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    impact_scope: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(60), nullable=False)
    safe_note: Mapped[str | None] = mapped_column(String(200))
    deduplication_key: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class BudgetPlan(Base):
    __tablename__ = "budget_plans"
    __table_args__ = (
        UniqueConstraint("goal_id", "version", name="uq_budget_plan_goal_version"),
        Index("ix_budget_plans_goal_current", "goal_id", "is_current"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    goal_id: Mapped[int] = mapped_column(ForeignKey("saving_goals.id", ondelete="CASCADE"), nullable=False)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("financial_snapshots.id"), nullable=False)
    parent_plan_id: Mapped[int | None] = mapped_column(ForeignKey("budget_plans.id"))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    adjustment_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    plan_start_week: Mapped[date] = mapped_column(Date, nullable=False)
    plan_end_week: Mapped[date] = mapped_column(Date, nullable=False)
    target_savings_snapshot_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_date_snapshot: Mapped[date] = mapped_column(Date, nullable=False)
    is_feasible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    infeasibility_code: Mapped[str | None] = mapped_column(String(48))
    expected_final_savings_minor: Mapped[int | None] = mapped_column(BigInteger)
    maximum_achievable_target_minor: Mapped[int | None] = mapped_column(BigInteger)
    required_additional_income_minor: Mapped[int | None] = mapped_column(BigInteger)
    recommended_deadline: Mapped[date | None] = mapped_column(Date)
    risk_level: Mapped[str | None] = mapped_column(String(20))
    diagnostics_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    algorithm_version: Mapped[str] = mapped_column(String(48), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class BudgetPlanEvent(Base):
    __tablename__ = "budget_plan_events"

    budget_plan_id: Mapped[int] = mapped_column(ForeignKey("budget_plans.id", ondelete="CASCADE"), primary_key=True)
    financial_event_id: Mapped[int] = mapped_column(ForeignKey("financial_events.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="supporting")


class BudgetPlanItemModel(Base):
    __tablename__ = "budget_plan_items"
    __table_args__ = (
        UniqueConstraint("budget_plan_id", "week_start", "category_id", name="uq_budget_plan_item"),
        CheckConstraint("recommended_budget_minor >= 0", name="ck_plan_item_budget_nonnegative"),
        Index("ix_budget_plan_items_plan_week", "budget_plan_id", "week_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    budget_plan_id: Mapped[int] = mapped_column(ForeignKey("budget_plans.id", ondelete="CASCADE"), nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    natural_spend_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    recommended_budget_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    adjustment_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    minimum_constraint_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    maximum_constraint_minor: Mapped[int | None] = mapped_column(BigInteger)
    adjustability_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    explanation_code: Mapped[str] = mapped_column(String(48), nullable=False, default="rolling_replan")
