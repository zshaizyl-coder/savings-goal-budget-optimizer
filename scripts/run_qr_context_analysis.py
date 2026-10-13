"""Evaluate QR context rules and triage remaining OTHER groups.

Private output may contain short source text and must stay under data/private.
The aggregate output is safe to commit and never contains source text or hashes.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
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
from backend.app.classification.qr_context import (
    assign_other_importance,
    classify_qr_group,
)
from backend.app.db import create_session_factory, create_sqlite_engine
from backend.app.models import Category, Transaction, User
from backend.app.pipeline.category_rules import CATEGORY_RULE_VERSION
from backend.app.services.reclassify_wechat import WechatSourceReclassificationService


def _effective_coverage(session, user_id: int) -> dict[str, object]:
    codes = {row.id: row.code for row in session.scalars(select(Category)).all()}
    rows = session.scalars(select(Transaction).where(
        Transaction.user_id == user_id,
        Transaction.flow_type == "consumption",
    )).all()
    counts: Counter[str] = Counter()
    amounts: Counter[str] = Counter()
    for row in rows:
        code = codes.get(row.user_category_id or row.suggested_category_id, "other")
        counts[code] += 1
        amounts[code] += row.amount_minor
    total_count = len(rows)
    total_amount = sum(row.amount_minor for row in rows)
    return {
        "core_consumption_count": total_count,
        "core_consumption_amount_minor": total_amount,
        "other_count": counts["other"],
        "other_amount_minor": amounts["other"],
        "other_count_ratio": counts["other"] / total_count if total_count else 0.0,
        "other_amount_ratio": amounts["other"] / total_amount if total_amount else 0.0,
        "category_counts": dict(sorted(counts.items())),
        "category_amounts_minor": dict(sorted(amounts.items())),
    }


def _summarize_group_dimension(groups, value_by_id: dict[str, str]) -> dict[str, object]:
    group_counts: Counter[str] = Counter()
    transaction_counts: Counter[str] = Counter()
    amounts: Counter[str] = Counter()
    for group in groups:
        value = value_by_id[group.review_group_id]
        group_counts[value] += 1
        transaction_counts[value] += group.transaction_count
        amounts[value] += group.total_amount_minor
    return {
        "groups": dict(sorted(group_counts.items())),
        "transactions": dict(sorted(transaction_counts.items())),
        "amounts_minor": dict(sorted(amounts.items())),
    }


def _serialize(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--bill", required=True)
    parser.add_argument("--private-output", default="data/private/stage56-qr-review.json")
    parser.add_argument(
        "--aggregate-output",
        default="experiments/aggregates/qr_context_evaluation.json",
    )
    parser.add_argument("--username", default="local-user")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    secret = os.environ.get("TRANSACTION_FINGERPRINT_KEY", "")
    if not secret:
        raise SystemExit("TRANSACTION_FINGERPRINT_KEY is required")
    private_output = Path(args.private_output)
    if "private" not in {part.lower() for part in private_output.parts}:
        raise SystemExit("private output must be stored under a private directory")

    engine = create_sqlite_engine(Path(args.database))
    sessions = create_session_factory(engine)
    try:
        with sessions.begin() as session:
            user = session.scalar(select(User).where(User.username == args.username))
            if user is None:
                raise ValueError("user_not_found")
            before = _effective_coverage(session, user.id)
            service = WechatSourceReclassificationService(session, secret.encode("utf-8"))
            rule_report = service.reclassify(
                args.bill,
                user_id=user.id,
                dry_run=args.dry_run,
            )
            review_items = service.build_other_review_items(args.bill, user_id=user.id)
            groups = build_review_groups(review_items)
            after = _effective_coverage(session, user.id)
    finally:
        engine.dispose()

    qr_items = [item for item in review_items if item.source_type_code == "qr_payment"]
    qr_groups = [group for group in groups if "qr_payment" in group.source_types.split(", ")]
    qr_decisions = {group.review_group_id: classify_qr_group(group) for group in qr_groups}
    importance = {item.review_group_id: item for item in assign_other_importance(groups)}

    private_rows = []
    for group in groups:
        row = group.as_private_dict()
        row["importance"] = asdict(importance[group.review_group_id])
        if group.review_group_id in qr_decisions:
            row["qr_context"] = asdict(qr_decisions[group.review_group_id])
        row["confirmed_category"] = ""
        row["review_notes"] = ""
        private_rows.append(row)
    private_output.parent.mkdir(parents=True, exist_ok=True)
    private_output.write_text(json.dumps({
        "generated_at": datetime.now().astimezone().isoformat(),
        "warning": "PRIVATE: source hints are transient review evidence and must not enter Git.",
        "rules": {
            "time_is_weak_evidence_only": True,
            "qr_candidates_require_human_confirmation": True,
            "personal_or_opaque_qr_must_not_be_forced": True,
            "importance_is_financial_impact_not_life_necessity": True,
        },
        "rows": private_rows,
    }, ensure_ascii=False, indent=2, default=_serialize), encoding="utf-8")

    kind_by_id = {
        group_id: decision.counterparty_kind
        for group_id, decision in qr_decisions.items()
    }
    candidate_by_id = {
        group_id: decision.candidate_category or "abstained"
        for group_id, decision in qr_decisions.items()
    }
    tier_by_id = {group_id: decision.tier for group_id, decision in importance.items()}
    treatment_by_id = {
        group_id: decision.budget_treatment for group_id, decision in importance.items()
    }
    direct_qr_amount = sum(item.amount_minor for item in qr_items)
    qr_importance_by_id = {
        group.review_group_id: importance[group.review_group_id].tier
        for group in qr_groups
    }
    meal_windows = {"breakfast_window", "lunch_window", "dinner_window"}
    aggregate = {
        "dataset_version": "stage56-qr-context-v1",
        "category_rule_version": CATEGORY_RULE_VERSION,
        "api_experiment": {
            "status": "not_run",
            "api_called": False,
            "reason": "rules_and_triage_evaluation_only",
        },
        "effective_coverage_before": before,
        "effective_coverage_after": after,
        "rule_reclassification": asdict(rule_report),
        "rule_reclassification_scope": (
            "suggested_category_layer; effective coverage remains governed by user overrides"
        ),
        "remaining_other": {
            "groups": len(groups),
            "transactions": sum(group.transaction_count for group in groups),
            "amount_minor": sum(group.total_amount_minor for group in groups),
        },
        "direct_remaining_qr": {
            "transactions": len(qr_items),
            "amount_minor": direct_qr_amount,
            "transaction_share_of_other": len(qr_items) / len(review_items) if review_items else 0.0,
            "amount_share_of_other": (
                direct_qr_amount / sum(item.amount_minor for item in review_items)
                if review_items else 0.0
            ),
        },
        "qr_group_entity_kind": _summarize_group_dimension(qr_groups, kind_by_id),
        "qr_rule_candidate": _summarize_group_dimension(qr_groups, candidate_by_id),
        "qr_group_importance": _summarize_group_dimension(qr_groups, qr_importance_by_id),
        "qr_review_signals": {
            "dominant_meal_window_groups": sum(
                group.dominant_time_bucket in meal_windows for group in qr_groups
            ),
            "recurring_groups": sum(group.transaction_count >= 2 for group in qr_groups),
            "time_signal_can_assign_category": False,
        },
        "other_importance": _summarize_group_dimension(groups, tier_by_id),
        "budget_treatment": _summarize_group_dimension(groups, treatment_by_id),
        "time_only_assignments": 0,
        "privacy": {
            "aggregate_contains_source_text": False,
            "aggregate_contains_fingerprints": False,
            "private_review_output": str(private_output),
        },
    }
    aggregate_output = Path(args.aggregate_output)
    aggregate_output.parent.mkdir(parents=True, exist_ok=True)
    aggregate_output.write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps({
        "other_before": before["other_count"],
        "other_after": after["other_count"],
        "remaining_other_groups": len(groups),
        "direct_qr_transactions": len(qr_items),
        "private_output": str(private_output),
        "aggregate_output": str(aggregate_output),
        "dry_run": args.dry_run,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
