from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Transaction, TransactionLink
from backend.app.pipeline.dto import StandardTransactionDraft


class TransactionRepository:
    def __init__(self, session: Session):
        self.session = session

    def existing_fingerprints(self, user_id: int, source: str, fingerprints: set[str]) -> set[str]:
        found: set[str] = set()
        values = list(fingerprints)
        for start in range(0, len(values), 800):
            found.update(self.session.scalars(select(Transaction.source_record_fingerprint).where(
                Transaction.user_id == user_id,
                Transaction.source == source,
                Transaction.source_record_fingerprint.in_(values[start:start + 800]),
            )).all())
        return found

    def insert_drafts(
        self, user_id: int, import_batch_id: int, drafts: list[StandardTransactionDraft],
        category_ids: dict[str, int],
        user_category_ids_by_counterparty: dict[str, int] | None = None,
    ) -> tuple[list[tuple[StandardTransactionDraft, Transaction]], int]:
        existing = self.existing_fingerprints(user_id, "wechat", {d.source_record_fingerprint for d in drafts})
        seen = set(existing)
        inserted: list[tuple[StandardTransactionDraft, Transaction]] = []
        duplicate_count = 0
        user_rules = user_category_ids_by_counterparty or {}
        for draft in drafts:
            if draft.source_record_fingerprint in seen:
                duplicate_count += 1
                continue
            seen.add(draft.source_record_fingerprint)
            item = Transaction(
                user_id=user_id, import_batch_id=import_batch_id, source=draft.source.value,
                source_record_fingerprint=draft.source_record_fingerprint,
                occurred_at_utc=draft.occurred_at_utc, amount_minor=draft.amount_minor,
                currency="CNY", direction=draft.direction.value, flow_type=draft.flow_type.value,
                normalized_status=draft.normalized_status.value,
                source_type_code=draft.source_type_code, original_category=draft.original_category,
                suggested_category_id=category_ids[draft.suggested_category_code],
                user_category_id=user_rules.get(draft.counterparty_fingerprint or ""),
                classification_version=draft.classification_version,
                counterparty_fingerprint=draft.counterparty_fingerprint,
                source_account_fingerprint=draft.source_account_fingerprint,
                is_abnormal=draft.is_abnormal,
            )
            self.session.add(item)
            inserted.append((draft, item))
        self.session.flush()
        return inserted, duplicate_count

    def add_possible_duplicate_links(
        self, user_id: int, inserted: list[tuple[StandardTransactionDraft, Transaction]],
    ) -> int:
        if not inserted:
            return 0
        earliest = min(item.occurred_at_utc for _, item in inserted)
        latest = max(item.occurred_at_utc for _, item in inserted)
        all_candidates = self.session.scalars(select(Transaction).where(
            Transaction.user_id == user_id,
            Transaction.occurred_at_utc >= earliest,
            Transaction.occurred_at_utc <= latest,
        )).all()
        keyed: dict[tuple, list[Transaction]] = defaultdict(list)
        for item in all_candidates:
            keyed[(item.occurred_at_utc, item.amount_minor, item.direction, item.flow_type)].append(item)
        created = 0
        for _draft, item in inserted:
            for candidate in keyed[(item.occurred_at_utc, item.amount_minor, item.direction, item.flow_type)]:
                if candidate.id == item.id or candidate.source_record_fingerprint == item.source_record_fingerprint:
                    continue
                low, high = sorted((candidate.id, item.id))
                exists = self.session.scalar(select(TransactionLink.id).where(
                    TransactionLink.transaction_a_id == low,
                    TransactionLink.transaction_b_id == high,
                    TransactionLink.link_type == "possible_duplicate",
                ))
                if exists is None:
                    self.session.add(TransactionLink(
                        user_id=user_id, transaction_a_id=low, transaction_b_id=high,
                        link_type="possible_duplicate", confidence_bps=6500,
                        resolution_status="pending",
                        evidence_codes='["same_time","same_amount","same_direction","same_flow_type"]',
                    ))
                    created += 1
        self.session.flush()
        return created

    def link_refunds(self, user_id: int, inserted: list[tuple[StandardTransactionDraft, Transaction]]) -> tuple[int, int]:
        linked = unresolved = 0
        refunds = [item for _, item in inserted if item.flow_type == "expense_refund"]
        for refund in refunds:
            candidates = self.session.scalars(select(Transaction).where(
                Transaction.user_id == user_id,
                Transaction.flow_type == "consumption",
                Transaction.direction == "outflow",
                Transaction.counterparty_fingerprint == refund.counterparty_fingerprint,
                Transaction.occurred_at_utc <= refund.occurred_at_utc,
                Transaction.amount_minor >= refund.amount_minor,
                Transaction.normalized_status.in_(("refunded", "partial_refund")),
            )).all()
            if len(candidates) == 1:
                original = candidates[0]
                refund.related_transaction_id = original.id
                # A confirmed refund must offset the same analytical category.
                refund.suggested_category_id = original.suggested_category_id
                refund.user_category_id = original.user_category_id
                self.session.add(TransactionLink(
                    user_id=user_id, transaction_a_id=original.id, transaction_b_id=refund.id,
                    link_type="refund_of", confidence_bps=9000, resolution_status="confirmed",
                    evidence_codes='["same_counterparty_fingerprint","refund_status","amount_compatible"]',
                ))
                linked += 1
            else:
                unresolved += 1
        self.session.flush()
        return linked, unresolved
