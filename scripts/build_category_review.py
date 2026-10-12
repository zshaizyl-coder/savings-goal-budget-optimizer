"""Build a private merchant-group review queue and privacy-safe aggregates."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from backend.app.classification.evaluation import build_review_groups
from backend.app.classification.types import ALLOWED_CATEGORY_CODES
from backend.app.db import create_session_factory, create_sqlite_engine
from backend.app.models import Category, Transaction, User
from backend.app.services.reclassify_wechat import WechatSourceReclassificationService


def category_coverage(session, user_id: int) -> dict[str, object]:
    category_rows = dict(session.execute(select(Category.id, Category.code)).all())
    code_by_id = {category_id: code for category_id, code in category_rows.items()}
    transactions = session.scalars(select(Transaction).where(
        Transaction.user_id == user_id,
        Transaction.flow_type == "consumption",
    )).all()
    counts: dict[str, int] = {}
    amounts: dict[str, int] = {}
    for transaction in transactions:
        category_id = transaction.user_category_id or transaction.suggested_category_id
        code = code_by_id.get(category_id, "other")
        counts[code] = counts.get(code, 0) + 1
        amounts[code] = amounts.get(code, 0) + transaction.amount_minor
    total_count = len(transactions)
    total_amount = sum(transaction.amount_minor for transaction in transactions)
    other_count = counts.get("other", 0)
    other_amount = amounts.get("other", 0)
    return {
        "core_consumption_count": total_count,
        "core_consumption_amount_minor": total_amount,
        "other_count": other_count,
        "other_amount_minor": other_amount,
        "other_count_ratio": other_count / total_count if total_count else 0.0,
        "other_amount_ratio": other_amount / total_amount if total_amount else 0.0,
        "category_counts": dict(sorted(counts.items())),
        "category_amounts_minor": dict(sorted(amounts.items())),
    }


def load_coverage(database: Path, username: str) -> dict[str, object]:
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    try:
        with sessions() as session:
            user = session.scalar(select(User).where(User.username == username))
            if user is None:
                raise ValueError("user_not_found")
            return category_coverage(session, user.id)
    finally:
        engine.dispose()


def serialize(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--baseline-database", required=True)
    parser.add_argument("--bill", required=True)
    parser.add_argument("--private-output", default="data/private/category-review-stage55.json")
    parser.add_argument("--aggregate-output", default="experiments/aggregates/category_evaluation_baseline.json")
    parser.add_argument("--model-candidates")
    parser.add_argument("--username", default="local-user")
    args = parser.parse_args()

    secret = os.environ.get("TRANSACTION_FINGERPRINT_KEY", "")
    if not secret:
        raise SystemExit("TRANSACTION_FINGERPRINT_KEY is required")

    current_database = Path(args.database)
    baseline_database = Path(args.baseline_database)
    engine = create_sqlite_engine(current_database)
    sessions = create_session_factory(engine)
    try:
        with sessions.begin() as session:
            user = session.scalar(select(User).where(User.username == args.username))
            if user is None:
                raise ValueError("user_not_found")
            service = WechatSourceReclassificationService(session, secret.encode("utf-8"))
            reclassification = service.reclassify(args.bill, user_id=user.id, dry_run=False)
            review_items = service.build_other_review_items(args.bill, user_id=user.id)
            groups = build_review_groups(review_items)
            current = category_coverage(session, user.id)
    finally:
        engine.dispose()

    model_candidates = {}
    if args.model_candidates:
        candidate_payload = json.loads(Path(args.model_candidates).read_text(encoding="utf-8"))
        model_candidates = candidate_payload.get("candidates", {})

    private_rows = []
    for group in groups:
        candidate = model_candidates.get(group.review_group_id, {})
        candidate_code = str(candidate.get("category_code") or "")
        if candidate_code and candidate_code not in ALLOWED_CATEGORY_CODES:
            raise ValueError(f"invalid_model_candidate:{candidate_code}")
        row = group.as_private_dict()
        row.update({
            "rule_candidate": "other",
            "model_candidate": candidate_code,
            "model_confidence": candidate.get("confidence", ""),
            "model_evidence": candidate.get("evidence", ""),
            "confirmed_category": "",
            "review_status": "pending",
            "review_notes": "",
        })
        private_rows.append(row)

    private_payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "category_codes": [
            "food", "shopping", "transport", "entertainment", "housing",
            "life_services", "health", "education", "communications",
            "human_relations", "other",
        ],
        "instructions": {
            "model_candidate_is_not_ground_truth": True,
            "time_is_weak_evidence_only": True,
            "blank_confirmed_category_means_unreviewed": True,
            "other_means_keep_unclassified": True,
        },
        "rows": private_rows,
    }
    private_output = Path(args.private_output)
    private_output.parent.mkdir(parents=True, exist_ok=True)
    private_output.write_text(
        json.dumps(private_payload, ensure_ascii=False, indent=2, default=serialize),
        encoding="utf-8",
    )

    baseline = load_coverage(baseline_database, args.username)
    sample_groups = [group for group in groups if group.in_validation_sample]
    top20 = [group for group in groups if group.sample_reason == "top_amount"]
    candidate_sample_groups = [
        group for group in sample_groups if group.review_group_id in model_candidates
    ]
    aggregate = {
        "dataset_version": "stage55-category-evaluation-v1",
        "baseline_rules": baseline,
        "current_rules": current,
        "rule_reclassification": asdict(reclassification),
        "remaining_other_groups": len(groups),
        "validation_sample_groups": len(sample_groups),
        "sample_group_counts": {
            reason: sum(group.sample_reason == reason for group in groups)
            for reason in ("top_amount", "high_frequency", "low_amount", "random")
        },
        "top20_other_amount_share": (
            sum(group.total_amount_minor for group in top20) / current["other_amount_minor"]
            if current["other_amount_minor"] else 0.0
        ),
        "cloud_eligible_groups": sum(group.cloud_eligible for group in groups),
        "model_experiment": {
            "status": "offline_candidates_generated" if model_candidates else "not_run",
            "candidate_groups": len(model_candidates),
            "validation_group_coverage": (
                len(candidate_sample_groups) / len(sample_groups) if sample_groups else 0.0
            ),
            "validation_amount_coverage": (
                sum(group.total_amount_minor for group in candidate_sample_groups)
                / sum(group.total_amount_minor for group in sample_groups)
                if sample_groups else 0.0
            ),
            "api_called": False,
            "accuracy_status": "awaiting_human_labels",
        },
        "human_evaluation": {
            "status": "awaiting_human_labels",
            "labeled_groups": 0,
        },
    }
    aggregate_output = Path(args.aggregate_output)
    aggregate_output.parent.mkdir(parents=True, exist_ok=True)
    aggregate_output.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "remaining_other_groups": len(groups),
        "validation_sample_groups": len(sample_groups),
        "private_output": str(private_output),
        "aggregate_output": str(aggregate_output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
