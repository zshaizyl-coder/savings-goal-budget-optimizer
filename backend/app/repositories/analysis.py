from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from backend.app.analytics.types import AnalysisTransaction
from backend.app.models import Category, Transaction, User


class TransactionAnalysisRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_user(self, username: str = "local-user") -> User:
        user = self.session.scalar(select(User).where(User.username == username))
        if user is None:
            raise ValueError("analysis user not found")
        return user

    def list_transactions(self, user_id: int) -> list[AnalysisTransaction]:
        suggested = aliased(Category)
        confirmed = aliased(Category)
        rows = self.session.execute(
            select(Transaction, suggested.code, confirmed.code)
            .outerjoin(suggested, Transaction.suggested_category_id == suggested.id)
            .outerjoin(confirmed, Transaction.user_category_id == confirmed.id)
            .where(Transaction.user_id == user_id)
            .order_by(Transaction.occurred_at_utc, Transaction.id)
        ).all()
        return [AnalysisTransaction(
            id=transaction.id,
            occurred_at_utc=transaction.occurred_at_utc,
            amount_minor=transaction.amount_minor,
            direction=transaction.direction,
            flow_type=transaction.flow_type,
            source_type_code=transaction.source_type_code,
            suggested_category_code=suggested_code,
            user_category_code=confirmed_code,
            related_transaction_id=transaction.related_transaction_id,
            is_abnormal=transaction.is_abnormal,
            planning_role=transaction.planning_role,
        ) for transaction, suggested_code, confirmed_code in rows]
