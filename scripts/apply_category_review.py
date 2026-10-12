"""Apply human-confirmed category review rows and calculate evaluation metrics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from sqlalchemy import select

from backend.app.classification.evaluation import evaluate_category_predictions
from backend.app.classification.types import ALLOWED_CATEGORY_CODES
from backend.app.analytics.consumption import RefundAllocationPolicy, build_consumption_datasets
from backend.app.analytics.quality import category_quality_table
from backend.app.db import create_session_factory, create_sqlite_engine
from backend.app.models import Category, Transaction, User
from backend.app.repositories.analysis import TransactionAnalysisRepository
from backend.app.services.hybrid_classification import HybridClassificationService


COLUMNS = {
    "sample": "验证样本",
    "reason": "抽样原因",
    "model": "模型候选",
    "rule": "规则候选",
    "label": "人工确认类别",
    "status": "审核状态",
    "amount": "总金额(元)",
    "transaction_id": "代表交易ID",
}


def clean(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def load_review_rows(workbook: Path) -> list[dict[str, object]]:
    frame = pd.read_excel(workbook, sheet_name="审核队列", header=5, dtype=object)
    missing = set(COLUMNS.values()) - set(frame.columns)
    if missing:
        raise ValueError(f"review_columns_missing:{','.join(sorted(missing))}")
    rows = []
    for _, source in frame.iterrows():
        if not clean(source.get("匿名组ID")):
            continue
        label = clean(source[COLUMNS["label"]])
        status = clean(source[COLUMNS["status"]])
        if label and label not in ALLOWED_CATEGORY_CODES:
            raise ValueError(f"invalid_confirmed_category:{label}")
        if status == "confirmed" and (not label or label == "other"):
            raise ValueError("confirmed_status_requires_specific_category")
        if status == "keep_other" and label != "other":
            raise ValueError("keep_other_status_requires_other_label")
        model_candidate_raw = clean(source[COLUMNS["model"]])
        rows.append({
            "in_validation_sample": clean(source[COLUMNS["sample"]]) == "是",
            "sample_reason": clean(source[COLUMNS["reason"]]),
            "rule_candidate": clean(source[COLUMNS["rule"]]) or "other",
            "model_candidate": model_candidate_raw or "other",
            "model_candidate_proposed": bool(model_candidate_raw),
            "confirmed_category": label,
            "review_status": status,
            "total_amount_minor": round(float(source[COLUMNS["amount"]]) * 100),
            "representative_transaction_id": int(source[COLUMNS["transaction_id"]]),
        })
    return rows


def high_amount_risk(rows: list[dict[str, object]], prediction_field: str) -> dict[str, object]:
    top = [row for row in rows if row["sample_reason"] == "金额前20" and row["confirmed_category"]]
    if not top:
        return {"status": "awaiting_human_labels", "labeled_groups": 0}
    total = sum(int(row["total_amount_minor"]) for row in top)
    wrong = sum(
        int(row["total_amount_minor"])
        for row in top
        if row[prediction_field] != row["confirmed_category"]
    )
    return {
        "status": "evaluated",
        "labeled_groups": len(top),
        "misclassified_amount_ratio": wrong / total if total else None,
    }


def remaining_other_coverage(session, user_id: int) -> dict[str, object]:
    other_id = session.scalar(select(Category.id).where(Category.code == "other"))
    transactions = session.scalars(select(Transaction).where(
        Transaction.user_id == user_id,
        Transaction.flow_type == "consumption",
    )).all()
    total_amount = sum(item.amount_minor for item in transactions)
    other = [
        item for item in transactions
        if (item.user_category_id or item.suggested_category_id) == other_id
    ]
    return {
        "other_count": len(other),
        "other_amount_minor": sum(item.amount_minor for item in other),
        "other_count_ratio": len(other) / len(transactions) if transactions else 0.0,
        "other_amount_ratio": sum(item.amount_minor for item in other) / total_amount if total_amount else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--workbook", required=True)
    parser.add_argument("--aggregate-output", default="experiments/aggregates/category_evaluation_final.json")
    parser.add_argument("--username", default="local-user")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()

    rows = load_review_rows(Path(args.workbook))
    sample = [row for row in rows if row["in_validation_sample"]]
    labeled_sample = [row for row in sample if row["confirmed_category"]]
    proposed_sample = [row for row in sample if row["model_candidate_proposed"]]
    if not args.allow_partial and len(labeled_sample) != len(sample):
        raise SystemExit(f"validation_sample_incomplete:{len(labeled_sample)}/{len(sample)}")

    engine = create_sqlite_engine(args.database)
    sessions = create_session_factory(engine)
    try:
        with sessions.begin() as session:
            user = session.scalar(select(User).where(User.username == args.username))
            if user is None:
                raise ValueError("user_not_found")
            service = HybridClassificationService(session)
            applied_groups = 0
            for row in rows:
                if row["review_status"] != "confirmed":
                    continue
                service.confirm_manual(
                    user_id=user.id,
                    transaction_id=row["representative_transaction_id"],
                    category_code=row["confirmed_category"],
                    scope="counterparty",
                )
                applied_groups += 1
            coverage = remaining_other_coverage(session, user.id)
        with sessions() as session:
            repository = TransactionAnalysisRepository(session)
            user = repository.get_user(args.username)
            transactions = repository.list_transactions(user.id)
            datasets = build_consumption_datasets(
                transactions, refund_policy=RefundAllocationPolicy.RELIABLE_LINKS
            )
            quality = category_quality_table(
                datasets.facts, datasets.category_weekly, datasets.weekly
            )
            quality_after_human = {}
            for code in ("food", "shopping", "other"):
                row = quality.loc[quality["category_code"] == code].iloc[0]
                quality_after_human[code] = {
                    "transaction_count": int(row["transaction_count"]),
                    "gross_amount_share": float(row["gross_amount_share"]),
                    "active_weeks": int(row["active_weeks"]),
                    "zero_week_ratio": float(row["zero_week_ratio"]),
                    "weekly_cv": float(row["weekly_cv"]),
                    "qualification": str(row["qualification"]),
                }
    finally:
        engine.dispose()

    result = {
        "dataset_version": "stage55-category-evaluation-v1",
        "review_groups": len(rows),
        "validation_sample_groups": len(sample),
        "labeled_validation_groups": len(labeled_sample),
        "applied_counterparty_rules": applied_groups,
        "rule_metrics": evaluate_category_predictions(sample, prediction_field="rule_candidate"),
        "model_candidate_metrics_all": evaluate_category_predictions(sample, prediction_field="model_candidate"),
        "model_candidate_metrics_when_proposed": evaluate_category_predictions(
            proposed_sample, prediction_field="model_candidate"
        ),
        "model_candidate_coverage": len(proposed_sample) / len(sample) if sample else 0.0,
        "rule_high_amount_risk": high_amount_risk(sample, "rule_candidate"),
        "model_high_amount_risk": high_amount_risk(sample, "model_candidate"),
        "after_human_confirmation": coverage,
        "category_quality_after_human": quality_after_human,
    }
    output = Path(args.aggregate_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "labeled_validation_groups": len(labeled_sample),
        "applied_counterparty_rules": applied_groups,
        "aggregate_output": str(output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
