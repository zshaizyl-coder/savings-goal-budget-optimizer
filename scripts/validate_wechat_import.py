"""Run a privacy-safe idempotency validation and print aggregates only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from alembic.config import Config

from backend.app.db import create_session_factory, create_sqlite_engine
from backend.app.services.import_wechat import WechatImportService


def summary(report) -> dict[str, object]:
    return {
        "status": report.status,
        "total_rows": report.total_rows,
        "parsed_rows": report.parsed_rows,
        "inserted_rows": report.inserted_rows,
        "duplicate_rows": report.duplicate_rows,
        "failed_rows": report.failed_rows,
        "warning_rows": report.warning_rows,
        "refund_rows": report.refund_rows,
        "transfer_rows": report.transfer_rows,
        "transfer_refund_rows": report.transfer_refund_rows,
        "internal_transfer_rows": report.internal_transfer_rows,
        "balance_transfer_rows": report.balance_transfer_rows,
        "linked_refunds": report.linked_refunds,
        "unresolved_refunds": report.unresolved_refunds,
        "possible_duplicate_links": report.possible_duplicate_links,
        "uncategorized_consumption_ratio": round(report.uncategorized_consumption_ratio, 6),
        "uncategorized_consumption_amount_ratio": round(report.uncategorized_consumption_amount_ratio, 6),
        "flow_type_counts": report.flow_type_counts,
        "earliest_transaction_time": report.earliest_transaction_time,
        "latest_transaction_time": report.latest_transaction_time,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--secret", default="local-validation-secret")
    args = parser.parse_args()
    config = Config("alembic.ini")
    normalized_database = args.database.replace("\\", "/")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{normalized_database}")
    command.upgrade(config, "head")
    engine = create_sqlite_engine(args.database)
    sessions = create_session_factory(engine)
    reports = []
    for _ in range(3):
        with sessions.begin() as session:
            reports.append(WechatImportService(session, args.secret.encode()).import_file(args.input))
    print(json.dumps({"imports": [summary(report) for report in reports]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
