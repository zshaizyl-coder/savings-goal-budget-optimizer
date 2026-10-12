"""Create the phase-4 transaction import schema.

Revision ID: 0001_stage4
Revises: None
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_stage4"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("display_name", sa.String(60), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("source_file_type", sa.String(20), nullable=False),
        sa.Column("file_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("parsed_rows", sa.Integer(), nullable=False),
        sa.Column("inserted_rows", sa.Integer(), nullable=False),
        sa.Column("duplicate_rows", sa.Integer(), nullable=False),
        sa.Column("failed_rows", sa.Integer(), nullable=False),
        sa.Column("warning_rows", sa.Integer(), nullable=False),
        sa.Column("earliest_transaction_time", sa.DateTime()),
        sa.Column("latest_transaction_time", sa.DateTime()),
        sa.Column("parser_version", sa.String(40), nullable=False),
        sa.Column("error_summary_json", sa.Text()),
        sa.CheckConstraint("total_rows >= 0 AND parsed_rows >= 0 AND inserted_rows >= 0", name="ck_batch_counts_nonnegative"),
        sa.CheckConstraint("duplicate_rows >= 0 AND failed_rows >= 0 AND warning_rows >= 0", name="ck_batch_issue_counts_nonnegative"),
    )
    op.create_index("ix_import_batches_user_id", "import_batches", ["user_id"])
    op.create_index("ix_import_batches_user_source_file", "import_batches", ["user_id", "source", "file_fingerprint"])
    op.create_table(
        "category_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("source_type_code", sa.String(80), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("rule_version", sa.String(40), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("source", "source_type_code", "rule_version", name="uq_category_mapping_rule"),
    )
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("import_batch_id", sa.Integer(), sa.ForeignKey("import_batches.id"), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("source_record_fingerprint", sa.String(64), nullable=False),
        sa.Column("occurred_at_utc", sa.DateTime(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("direction", sa.String(12), nullable=False),
        sa.Column("flow_type", sa.String(28), nullable=False),
        sa.Column("normalized_status", sa.String(24), nullable=False),
        sa.Column("source_type_code", sa.String(80), nullable=False),
        sa.Column("original_category", sa.String(80)),
        sa.Column("suggested_category_id", sa.Integer(), sa.ForeignKey("categories.id")),
        sa.Column("user_category_id", sa.Integer(), sa.ForeignKey("categories.id")),
        sa.Column("classification_version", sa.String(40), nullable=False),
        sa.Column("related_transaction_id", sa.Integer(), sa.ForeignKey("transactions.id")),
        sa.Column("canonical_transaction_id", sa.Integer(), sa.ForeignKey("transactions.id")),
        sa.Column("counterparty_fingerprint", sa.String(64)),
        sa.Column("source_account_fingerprint", sa.String(64)),
        sa.Column("is_abnormal", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("amount_minor >= 0", name="ck_transaction_amount_nonnegative"),
        sa.CheckConstraint("source IN ('wechat','alipay','bank')", name="ck_transaction_source"),
        sa.CheckConstraint("direction IN ('inflow','outflow','neutral')", name="ck_transaction_direction"),
        sa.UniqueConstraint("user_id", "source", "source_record_fingerprint", name="uq_transaction_source_fingerprint"),
    )
    op.create_index("ix_transactions_user_time", "transactions", ["user_id", "occurred_at_utc"])
    op.create_index("ix_transactions_user_flow_time", "transactions", ["user_id", "flow_type", "occurred_at_utc"])
    op.create_table(
        "transaction_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("transaction_a_id", sa.Integer(), sa.ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("transaction_b_id", sa.Integer(), sa.ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("link_type", sa.String(28), nullable=False),
        sa.Column("confidence_bps", sa.Integer(), nullable=False),
        sa.Column("resolution_status", sa.String(20), nullable=False),
        sa.Column("evidence_codes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime()),
        sa.CheckConstraint("confidence_bps >= 0 AND confidence_bps <= 10000", name="ck_link_confidence_bps"),
        sa.CheckConstraint("transaction_a_id <> transaction_b_id", name="ck_link_distinct_transactions"),
        sa.UniqueConstraint("transaction_a_id", "transaction_b_id", "link_type", name="uq_transaction_link_pair"),
    )
    op.create_index("ix_transaction_links_user_status", "transaction_links", ["user_id", "resolution_status", "link_type"])


def downgrade() -> None:
    op.drop_index("ix_transaction_links_user_status", table_name="transaction_links")
    op.drop_table("transaction_links")
    op.drop_index("ix_transactions_user_flow_time", table_name="transactions")
    op.drop_index("ix_transactions_user_time", table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("category_mappings")
    op.drop_index("ix_import_batches_user_source_file", table_name="import_batches")
    op.drop_index("ix_import_batches_user_id", table_name="import_batches")
    op.drop_table("import_batches")
    op.drop_table("categories")
    op.drop_table("users")
