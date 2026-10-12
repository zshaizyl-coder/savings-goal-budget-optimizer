"""Privacy-safe local CLI for importing a WeChat bill into a migrated database."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.db import create_session_factory, create_sqlite_engine
from backend.app.services.import_wechat import WechatImportService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", default="local-user")
    args = parser.parse_args()
    secret = os.getenv("TRANSACTION_FINGERPRINT_KEY")
    if not secret:
        raise SystemExit("TRANSACTION_FINGERPRINT_KEY is required")
    engine = create_sqlite_engine(args.database)
    session_factory = create_session_factory(engine)
    with session_factory.begin() as session:
        report = WechatImportService(session, secret.encode("utf-8")).import_file(args.input, username=args.user)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
