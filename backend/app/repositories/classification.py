from __future__ import annotations

from datetime import datetime, timezone
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    Category,
    ClassificationSuggestion,
    Transaction,
    UserCategoryRule,
)
from backend.app.classification.types import ClassificationResult


class UserCategoryRuleRepository:
    def __init__(self, session: Session):
        self.session = session

    def active_category_ids(self, user_id: int, source: str) -> dict[str, int]:
        rows = self.session.execute(
            select(UserCategoryRule.counterparty_fingerprint, UserCategoryRule.category_id).where(
                UserCategoryRule.user_id == user_id,
                UserCategoryRule.source == source,
                UserCategoryRule.is_active.is_(True),
            )
        ).all()
        return dict(rows)

    def confirm_category(
        self,
        *,
        user_id: int,
        transaction_id: int,
        category_code: str,
        scope: str = "transaction",
        origin: str = "manual",
    ) -> int:
        if scope not in {"transaction", "counterparty"}:
            raise ValueError("invalid_confirmation_scope")
        transaction = self.session.get(Transaction, transaction_id)
        if transaction is None or transaction.user_id != user_id:
            raise ValueError("transaction_not_found")
        category = self.session.scalar(select(Category).where(Category.code == category_code))
        if category is None or category_code == "other":
            raise ValueError("invalid_confirmed_category")

        targets = [transaction]
        if scope == "counterparty":
            if not transaction.counterparty_fingerprint:
                raise ValueError("counterparty_scope_unavailable")
            rule = self.session.scalar(select(UserCategoryRule).where(
                UserCategoryRule.user_id == user_id,
                UserCategoryRule.source == transaction.source,
                UserCategoryRule.counterparty_fingerprint == transaction.counterparty_fingerprint,
            ))
            if rule is None:
                rule = UserCategoryRule(
                    user_id=user_id,
                    source=transaction.source,
                    counterparty_fingerprint=transaction.counterparty_fingerprint,
                    category_id=category.id,
                    origin=origin,
                    is_active=True,
                )
                self.session.add(rule)
            else:
                rule.category_id = category.id
                rule.origin = origin
                rule.is_active = True
                rule.updated_at = datetime.now(timezone.utc)
            targets = list(self.session.scalars(select(Transaction).where(
                Transaction.user_id == user_id,
                Transaction.source == transaction.source,
                Transaction.counterparty_fingerprint == transaction.counterparty_fingerprint,
            )).all())

        target_ids = [item.id for item in targets]
        for target in targets:
            target.user_category_id = category.id
        # Confirmed refunds follow the category of their original expense even
        # when the refund does not carry the same counterparty fingerprint.
        refunds = self.session.scalars(select(Transaction).where(
            Transaction.user_id == user_id,
            Transaction.related_transaction_id.in_(target_ids),
        )).all()
        for refund in refunds:
            refund.user_category_id = category.id
        self.session.flush()
        return len(target_ids)


class ClassificationSuggestionRepository:
    def __init__(self, session: Session):
        self.session = session

    def save(
        self,
        *,
        user_id: int,
        transaction_id: int,
        provider: str,
        model_name: str,
        prompt_version: str,
        result: ClassificationResult,
    ) -> ClassificationSuggestion:
        transaction = self.session.get(Transaction, transaction_id)
        if transaction is None or transaction.user_id != user_id:
            raise ValueError("transaction_not_found")
        category = self.session.scalar(
            select(Category).where(Category.code == result.category_code)
        )
        if category is None:
            raise ValueError("category_not_found")
        item = self.session.scalar(select(ClassificationSuggestion).where(
            ClassificationSuggestion.user_id == user_id,
            ClassificationSuggestion.transaction_id == transaction_id,
            ClassificationSuggestion.provider == provider,
            ClassificationSuggestion.model_name == model_name,
            ClassificationSuggestion.prompt_version == prompt_version,
        ))
        values = {
            "suggested_category_id": category.id,
            "confidence_bps": result.confidence_bps,
            "requires_review": True,
            "status": "pending",
            "evidence_codes": json.dumps(result.evidence_codes, ensure_ascii=True),
            "reviewed_at": None,
        }
        if item is None:
            item = ClassificationSuggestion(
                user_id=user_id,
                transaction_id=transaction_id,
                provider=provider,
                model_name=model_name,
                prompt_version=prompt_version,
                **values,
            )
            self.session.add(item)
        else:
            for key, value in values.items():
                setattr(item, key, value)
        self.session.flush()
        return item

    def review(self, suggestion_id: int, *, accept: bool) -> ClassificationSuggestion:
        item = self.session.get(ClassificationSuggestion, suggestion_id)
        if item is None:
            raise ValueError("suggestion_not_found")
        item.status = "accepted" if accept else "rejected"
        item.reviewed_at = datetime.now(timezone.utc)
        self.session.flush()
        return item
