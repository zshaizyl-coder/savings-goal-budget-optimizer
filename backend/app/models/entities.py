from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
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
