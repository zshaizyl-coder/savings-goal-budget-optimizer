from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from backend.app.config import get_settings, sqlite_path
from backend.app.db.session import create_session_factory, create_sqlite_engine
from backend.app.models import ImportBatch, Transaction, User
from backend.app.services.category_review_queue import PrivateCategoryReviewQueue


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill Git-ignored transaction display hints from a user-selected WeChat bill."
    )
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    factory = create_session_factory(create_sqlite_engine(sqlite_path(settings.database_url)))
    rebuilt: list[tuple[int, int]] = []
    with factory() as session:
        for user in session.scalars(select(User).order_by(User.id)).all():
            matching = session.scalar(select(func.count(Transaction.id)).where(
                Transaction.user_id == user.id, Transaction.source == "wechat",
            )) or 0
            if not matching:
                continue
            batch_id = session.scalar(select(ImportBatch.id).where(
                ImportBatch.user_id == user.id, ImportBatch.source == "wechat",
            ).order_by(ImportBatch.started_at.desc()).limit(1))
            if batch_id is None:
                continue
            count = PrivateCategoryReviewQueue().rebuild(
                session, args.source, user_id=user.id,
                fingerprint_secret=settings.secret_key.encode(), batch_id=batch_id,
            )
            rebuilt.append((user.id, count))
    print(f"backfilled_users={len(rebuilt)} review_groups={sum(count for _, count in rebuilt)}")


if __name__ == "__main__":
    main()
