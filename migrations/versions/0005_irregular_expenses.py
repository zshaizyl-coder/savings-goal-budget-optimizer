"""Add user-confirmed treatment for irregular expenses.

Revision ID: 0005_irregular_expenses
Revises: 0004_web_auth
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_irregular_expenses"
down_revision = "0004_web_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transactions") as batch:
        batch.add_column(sa.Column("planning_role", sa.String(length=32), nullable=False,
                                   server_default="routine"))
        batch.add_column(sa.Column("recurrence_frequency", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("next_expected_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("expected_amount_minor", sa.BigInteger(), nullable=True))
        batch.create_check_constraint(
            "ck_transaction_planning_role",
            "planning_role IN ('routine','irregular_essential','irregular_discretionary')",
        )
        batch.create_check_constraint(
            "ck_transaction_recurrence_frequency",
            "recurrence_frequency IS NULL OR recurrence_frequency IN ('one_time','annual')",
        )
        batch.create_check_constraint(
            "ck_transaction_expected_amount_positive",
            "expected_amount_minor IS NULL OR expected_amount_minor > 0",
        )


def downgrade() -> None:
    existing_checks = {
        item["name"] for item in sa.inspect(op.get_bind()).get_check_constraints("transactions")
    }
    with op.batch_alter_table("transactions") as batch:
        for name in (
            "ck_transaction_expected_amount_positive",
            "ck_transaction_recurrence_frequency",
            "ck_transaction_planning_role",
        ):
            if name in existing_checks:
                batch.drop_constraint(name, type_="check")
        batch.drop_column("expected_amount_minor")
        batch.drop_column("next_expected_date")
        batch.drop_column("recurrence_frequency")
        batch.drop_column("planning_role")
