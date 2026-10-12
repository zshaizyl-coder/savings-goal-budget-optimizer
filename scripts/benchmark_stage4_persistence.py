"""Small, repeatable persistence benchmark for expected personal-data scale."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sys
import tempfile
from time import perf_counter

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.db import Base, create_session_factory, create_sqlite_engine
from backend.app.domain.enums import Direction, FlowType, NormalizedStatus, PaymentSource
from backend.app.models import ImportBatch
from backend.app.pipeline.category_rules import CATEGORY_RULE_VERSION
from backend.app.pipeline.dto import StandardTransactionDraft
from backend.app.repositories.categories import CategoryRepository
from backend.app.repositories.transactions import TransactionRepository
from backend.app.repositories.users import UserRepository


def draft(index: int) -> StandardTransactionDraft:
    return StandardTransactionDraft(
        source_row_number=index + 19,
        source=PaymentSource.WECHAT,
        source_record_fingerprint=hashlib.sha256(f"simulated-{index}".encode()).hexdigest(),
        occurred_at_utc=datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index),
        amount_minor=100 + index % 10000,
        direction=Direction.OUTFLOW,
        flow_type=FlowType.CONSUMPTION,
        normalized_status=NormalizedStatus.COMPLETED,
        source_type_code="merchant_consumption",
        suggested_category_code="other",
        classification_version=CATEGORY_RULE_VERSION,
        original_category="商户消费",
    )


def run(size: int) -> tuple[float, int]:
    with tempfile.TemporaryDirectory() as directory:
        engine = create_sqlite_engine(Path(directory) / "benchmark.sqlite3")
        Base.metadata.create_all(engine)
        sessions = create_session_factory(engine)
        started = perf_counter()
        with sessions.begin() as session:
            user = UserRepository(session).ensure_local()
            categories = CategoryRepository(session).ensure_defaults()
            batch = ImportBatch(
                user_id=user.id, source="wechat", source_file_type="xlsx",
                file_fingerprint=hashlib.sha256(f"file-{size}".encode()).hexdigest(),
                status="running", started_at=datetime.now(timezone.utc), parser_version="benchmark",
            )
            session.add(batch)
            session.flush()
            inserted, duplicates = TransactionRepository(session).insert_drafts(
                user.id, batch.id, [draft(index) for index in range(size)],
                {code: item.id for code, item in categories.items()},
            )
        elapsed = perf_counter() - started
        engine.dispose()
        return elapsed, len(inserted) - duplicates


if __name__ == "__main__":
    for count in (1000, 5000, 10000):
        seconds, inserted = run(count)
        print(f"{count}: inserted={inserted}, seconds={seconds:.3f}, rows_per_second={inserted / seconds:.0f}")
