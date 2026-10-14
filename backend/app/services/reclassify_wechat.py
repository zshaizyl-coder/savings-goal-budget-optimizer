from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.classification.types import ClassificationContext
from backend.app.classification.evaluation import CategoryReviewItem
from backend.app.models import Category, Transaction
from backend.app.parsers.wechat import WechatBillParser
from backend.app.pipeline.category_rules import CATEGORY_RULE_VERSION
from backend.app.pipeline.wechat_normalizer import WechatTransactionNormalizer
from backend.app.repositories.categories import CategoryRepository


@dataclass(frozen=True)
class ReclassificationReport:
    matched_rows: int
    unmatched_rows: int
    changed_transactions: int
    other_before_count: int
    other_after_count: int
    other_before_amount_minor: int
    other_after_amount_minor: int
    resulting_category_counts: dict[str, int]


class WechatSourceReclassificationService:
    """Re-read a user-selected source file without persisting source text."""

    def __init__(self, session: Session, fingerprint_secret: bytes):
        self.session = session
        self.parser = WechatBillParser()
        self.normalizer = WechatTransactionNormalizer(fingerprint_secret)

    def reclassify(
        self,
        path: str | Path,
        *,
        user_id: int,
        dry_run: bool = True,
    ) -> ReclassificationReport:
        categories = CategoryRepository(self.session).ensure_defaults()
        category_ids = {code: item.id for code, item in categories.items()}
        category_codes = {item.id: code for code, item in categories.items()}
        transactions = self.session.scalars(select(Transaction).where(
            Transaction.user_id == user_id,
            Transaction.source == "wechat",
        )).all()
        by_fingerprint = {item.source_record_fingerprint: item for item in transactions}

        consumption = [item for item in transactions if item.flow_type == "consumption"]
        other_id = category_ids["other"]
        other_before = [item for item in consumption if item.suggested_category_id == other_id]
        before_count = len(other_before)
        before_amount = sum(item.amount_minor for item in other_before)

        changes: dict[int, int] = {}
        matched_fingerprints: set[str] = set()
        result_counts: Counter[str] = Counter()
        parsed = self.parser.parse(path)
        for row in parsed.rows:
            result = self.normalizer.normalize(row)
            draft = result.transaction
            if draft is None:
                continue
            item = by_fingerprint.get(draft.source_record_fingerprint)
            if item is None:
                continue
            matched_fingerprints.add(draft.source_record_fingerprint)
            new_category_id = category_ids[draft.suggested_category_code]
            result_counts[draft.suggested_category_code] += 1
            if item.suggested_category_id != new_category_id:
                changes[item.id] = new_category_id
            if not dry_run:
                item.suggested_category_id = new_category_id
                item.classification_version = CATEGORY_RULE_VERSION

        # Calculate the post-run OTHER coverage even during a dry run.
        after_other = []
        for item in consumption:
            candidate_id = changes.get(item.id, item.suggested_category_id)
            if candidate_id == other_id:
                after_other.append(item)
        if not dry_run:
            for refund in (item for item in transactions if item.flow_type == "expense_refund"):
                if refund.related_transaction_id:
                    original = self.session.get(Transaction, refund.related_transaction_id)
                    if original is not None:
                        refund.suggested_category_id = original.suggested_category_id
            self.session.flush()

        return ReclassificationReport(
            matched_rows=len(matched_fingerprints),
            unmatched_rows=max(0, len(by_fingerprint) - len(matched_fingerprints)),
            changed_transactions=len(changes),
            other_before_count=before_count,
            other_after_count=len(after_other),
            other_before_amount_minor=before_amount,
            other_after_amount_minor=sum(item.amount_minor for item in after_other),
            resulting_category_counts=dict(sorted(result_counts.items())),
        )

    def build_other_review_contexts(
        self,
        path: str | Path,
        *,
        user_id: int,
    ) -> list[ClassificationContext]:
        other_id = self.session.scalar(select(Category.id).where(Category.code == "other"))
        if other_id is None:
            return []
        transactions = self.session.scalars(select(Transaction).where(
            Transaction.user_id == user_id,
            Transaction.source == "wechat",
            Transaction.flow_type == "consumption",
            Transaction.user_category_id.is_(None),
            Transaction.suggested_category_id == other_id,
        )).all()
        by_fingerprint = {item.source_record_fingerprint: item for item in transactions}
        recurrence = Counter(
            item.counterparty_fingerprint for item in transactions
            if item.counterparty_fingerprint
        )
        contexts: list[ClassificationContext] = []
        seen: set[str] = set()
        for row in self.parser.parse(path).rows:
            result = self.normalizer.normalize(row)
            draft = result.transaction
            if draft is None or draft.source_record_fingerprint in seen:
                continue
            item = by_fingerprint.get(draft.source_record_fingerprint)
            if item is None:
                continue
            seen.add(draft.source_record_fingerprint)
            contexts.append(ClassificationContext(
                transaction_id=item.id,
                source_type_code=item.source_type_code,
                occurred_at_utc=item.occurred_at_utc,
                amount_minor=item.amount_minor,
                counterparty_hint=row.source_counterparty,
                description_hint=row.source_description,
                recurring_count=recurrence.get(item.counterparty_fingerprint, 1),
            ))
        return contexts

    def build_private_transaction_hints(
        self,
        path: str | Path,
        *,
        user_id: int,
    ) -> dict[int, dict[str, str]]:
        """Match source text to persisted rows for a local, Git-ignored detail view."""
        transactions = self.session.scalars(select(Transaction).where(
            Transaction.user_id == user_id,
            Transaction.source == "wechat",
        )).all()
        by_fingerprint = {item.source_record_fingerprint: item for item in transactions}
        hints: dict[int, dict[str, str]] = {}
        for row in self.parser.parse(path).rows:
            result = self.normalizer.normalize(row)
            draft = result.transaction
            if draft is None:
                continue
            item = by_fingerprint.get(draft.source_record_fingerprint)
            if item is None:
                continue
            hints[item.id] = {
                "counterparty_hint": row.source_counterparty or "",
                "description_hint": row.source_description or "",
            }
        return hints

    def build_other_review_items(
        self,
        path: str | Path,
        *,
        user_id: int,
    ) -> list[CategoryReviewItem]:
        """Build a transient private review queue without persisting source text."""
        contexts = self.build_other_review_contexts(path, user_id=user_id)
        transaction_ids = [item.transaction_id for item in contexts]
        if not transaction_ids:
            return []
        fingerprints = dict(self.session.execute(
            select(Transaction.id, Transaction.counterparty_fingerprint).where(
                Transaction.id.in_(transaction_ids),
                Transaction.user_id == user_id,
            )
        ).all())
        return [CategoryReviewItem(
            transaction_id=item.transaction_id,
            counterparty_fingerprint=fingerprints.get(item.transaction_id) or "",
            source_type_code=item.source_type_code,
            occurred_at_utc=item.occurred_at_utc,
            amount_minor=item.amount_minor,
            counterparty_hint=item.counterparty_hint,
            description_hint=item.description_hint,
        ) for item in contexts]
