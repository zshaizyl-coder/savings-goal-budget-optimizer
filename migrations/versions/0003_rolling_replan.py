"""Add rolling budget replanning persistence.

Revision ID: 0003_rolling_replan
Revises: 0002_hybrid_classification
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_rolling_replan"
down_revision = "0002_hybrid_classification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "financial_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("available_savings_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("available_savings_minor >= 0", name="ck_snapshot_savings_nonnegative"),
    )
    op.create_index("ix_financial_snapshots_user_id", "financial_snapshots", ["user_id"])

    op.create_table(
        "saving_goals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("baseline_snapshot_id", sa.Integer(), sa.ForeignKey("financial_snapshots.id"), nullable=False),
        sa.Column("previous_goal_id", sa.Integer(), sa.ForeignKey("saving_goals.id")),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("target_savings_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("target_savings_minor > 0", name="ck_goal_target_positive"),
        sa.CheckConstraint("version >= 1", name="ck_goal_version_positive"),
    )
    op.create_index("ix_saving_goals_user_id", "saving_goals", ["user_id"])

    op.create_table(
        "income_assumptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("goal_id", sa.Integer(), sa.ForeignKey("saving_goals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("expected_day", sa.Integer()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("amount_minor >= 0", name="ck_income_amount_nonnegative"),
        sa.CheckConstraint("version >= 1", name="ck_income_version_positive"),
    )
    op.create_index("ix_income_assumptions_goal_id", "income_assumptions", ["goal_id"])

    op.create_table(
        "budget_constraints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("goal_id", sa.Integer(), sa.ForeignKey("saving_goals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("minimum_budget_minor", sa.BigInteger(), nullable=False),
        sa.Column("maximum_budget_minor", sa.BigInteger()),
        sa.Column("is_fixed", sa.Boolean(), nullable=False),
        sa.Column("adjustability_bps", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("minimum_budget_minor >= 0", name="ck_constraint_minimum_nonnegative"),
        sa.CheckConstraint("maximum_budget_minor IS NULL OR maximum_budget_minor >= minimum_budget_minor", name="ck_constraint_maximum"),
        sa.CheckConstraint("adjustability_bps >= 0 AND adjustability_bps <= 10000", name="ck_constraint_adjustability"),
        sa.CheckConstraint("version >= 1", name="ck_constraint_version_positive"),
    )
    op.create_index("ix_budget_constraints_goal_dates", "budget_constraints", ["goal_id", "effective_from", "effective_to"])

    op.create_table(
        "financial_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("goal_id", sa.Integer(), sa.ForeignKey("saving_goals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("amount_minor", sa.BigInteger()),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("impact_scope", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(60), nullable=False),
        sa.Column("safe_note", sa.String(200)),
        sa.Column("deduplication_key", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "deduplication_key", name="uq_financial_event_dedup"),
    )
    op.create_index("ix_financial_events_goal_date", "financial_events", ["goal_id", "event_date"])

    op.create_table(
        "budget_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("goal_id", sa.Integer(), sa.ForeignKey("saving_goals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("financial_snapshots.id"), nullable=False),
        sa.Column("parent_plan_id", sa.Integer(), sa.ForeignKey("budget_plans.id")),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("adjustment_mode", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("plan_start_week", sa.Date(), nullable=False),
        sa.Column("plan_end_week", sa.Date(), nullable=False),
        sa.Column("target_savings_snapshot_minor", sa.BigInteger(), nullable=False),
        sa.Column("target_date_snapshot", sa.Date(), nullable=False),
        sa.Column("is_feasible", sa.Boolean(), nullable=False),
        sa.Column("infeasibility_code", sa.String(48)),
        sa.Column("expected_final_savings_minor", sa.BigInteger()),
        sa.Column("maximum_achievable_target_minor", sa.BigInteger()),
        sa.Column("required_additional_income_minor", sa.BigInteger()),
        sa.Column("recommended_deadline", sa.Date()),
        sa.Column("risk_level", sa.String(20)),
        sa.Column("diagnostics_json", sa.Text(), nullable=False),
        sa.Column("algorithm_version", sa.String(48), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("goal_id", "version", name="uq_budget_plan_goal_version"),
    )
    op.create_index("ix_budget_plans_goal_current", "budget_plans", ["goal_id", "is_current"])

    op.create_table(
        "budget_plan_events",
        sa.Column("budget_plan_id", sa.Integer(), sa.ForeignKey("budget_plans.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("financial_event_id", sa.Integer(), sa.ForeignKey("financial_events.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
    )
    op.create_table(
        "budget_plan_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("budget_plan_id", sa.Integer(), sa.ForeignKey("budget_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("natural_spend_minor", sa.BigInteger(), nullable=False),
        sa.Column("recommended_budget_minor", sa.BigInteger(), nullable=False),
        sa.Column("adjustment_minor", sa.BigInteger(), nullable=False),
        sa.Column("minimum_constraint_minor", sa.BigInteger(), nullable=False),
        sa.Column("maximum_constraint_minor", sa.BigInteger()),
        sa.Column("adjustability_bps", sa.Integer(), nullable=False),
        sa.Column("explanation_code", sa.String(48), nullable=False),
        sa.CheckConstraint("recommended_budget_minor >= 0", name="ck_plan_item_budget_nonnegative"),
        sa.UniqueConstraint("budget_plan_id", "week_start", "category_id", name="uq_budget_plan_item"),
    )
    op.create_index("ix_budget_plan_items_plan_week", "budget_plan_items", ["budget_plan_id", "week_start"])


def downgrade() -> None:
    op.drop_index("ix_budget_plan_items_plan_week", table_name="budget_plan_items")
    op.drop_table("budget_plan_items")
    op.drop_table("budget_plan_events")
    op.drop_index("ix_budget_plans_goal_current", table_name="budget_plans")
    op.drop_table("budget_plans")
    op.drop_index("ix_financial_events_goal_date", table_name="financial_events")
    op.drop_table("financial_events")
    op.drop_index("ix_budget_constraints_goal_dates", table_name="budget_constraints")
    op.drop_table("budget_constraints")
    op.drop_index("ix_income_assumptions_goal_id", table_name="income_assumptions")
    op.drop_table("income_assumptions")
    op.drop_index("ix_saving_goals_user_id", table_name="saving_goals")
    op.drop_table("saving_goals")
    op.drop_index("ix_financial_snapshots_user_id", table_name="financial_snapshots")
    op.drop_table("financial_snapshots")
