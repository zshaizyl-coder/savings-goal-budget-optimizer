"""Add minimal password authentication.

Revision ID: 0004_web_auth
Revises: 0003_rolling_replan
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_web_auth"
down_revision = "0003_rolling_replan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("password_hash", sa.String(length=180), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("password_hash")
