"""Add privacy-preserving hybrid transaction classification.

Revision ID: 0002_hybrid_classification
Revises: 0001_stage4
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_hybrid_classification"
down_revision = "0001_stage4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_category_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("counterparty_fingerprint", sa.String(64), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("origin", sa.String(24), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "user_id", "source", "counterparty_fingerprint",
            name="uq_user_category_rule_counterparty",
        ),
    )
    op.create_index(
        "ix_user_category_rules_user_active", "user_category_rules",
        ["user_id", "is_active"],
    )

    op.create_table(
        "classification_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "transaction_id", sa.Integer(),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model_name", sa.String(80), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column(
            "suggested_category_id", sa.Integer(),
            sa.ForeignKey("categories.id"), nullable=False,
        ),
        sa.Column("confidence_bps", sa.Integer(), nullable=False),
        sa.Column("requires_review", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("evidence_codes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime()),
        sa.CheckConstraint(
            "confidence_bps >= 0 AND confidence_bps <= 10000",
            name="ck_classification_suggestion_confidence",
        ),
        sa.CheckConstraint(
            "status IN ('pending','accepted','rejected')",
            name="ck_classification_suggestion_status",
        ),
        sa.UniqueConstraint(
            "user_id", "transaction_id", "provider", "model_name", "prompt_version",
            name="uq_classification_suggestion_run",
        ),
    )
    op.create_index(
        "ix_classification_suggestions_user_status", "classification_suggestions",
        ["user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_classification_suggestions_user_status",
        table_name="classification_suggestions",
    )
    op.drop_table("classification_suggestions")
    op.drop_index(
        "ix_user_category_rules_user_active",
        table_name="user_category_rules",
    )
    op.drop_table("user_category_rules")
