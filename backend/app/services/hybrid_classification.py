from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.classification.openai_provider import PROMPT_VERSION
from backend.app.classification.types import ClassificationResult
from backend.app.repositories.classification import (
    ClassificationSuggestionRepository,
    UserCategoryRuleRepository,
)


class HybridClassificationService:
    """Coordinates model suggestions and explicit user confirmation."""

    def __init__(self, session: Session):
        self.session = session
        self.suggestions = ClassificationSuggestionRepository(session)
        self.rules = UserCategoryRuleRepository(session)

    def record_model_suggestion(
        self,
        *,
        user_id: int,
        transaction_id: int,
        provider: str,
        model_name: str,
        result: ClassificationResult,
    ) -> int:
        item = self.suggestions.save(
            user_id=user_id,
            transaction_id=transaction_id,
            provider=provider,
            model_name=model_name,
            prompt_version=PROMPT_VERSION,
            result=result,
        )
        return item.id

    def accept_suggestion(self, suggestion_id: int, *, scope: str = "transaction") -> int:
        item = self.suggestions.review(suggestion_id, accept=True)
        category_code = item_category_code(self.session, item.suggested_category_id)
        return self.rules.confirm_category(
            user_id=item.user_id,
            transaction_id=item.transaction_id,
            category_code=category_code,
            scope=scope,
            origin="llm_confirmed",
        )

    def reject_suggestion(self, suggestion_id: int) -> None:
        self.suggestions.review(suggestion_id, accept=False)

    def confirm_manual(
        self,
        *,
        user_id: int,
        transaction_id: int,
        category_code: str,
        scope: str = "transaction",
    ) -> int:
        return self.rules.confirm_category(
            user_id=user_id,
            transaction_id=transaction_id,
            category_code=category_code,
            scope=scope,
            origin="manual",
        )


def item_category_code(session: Session, category_id: int) -> str:
    from backend.app.models import Category

    category = session.get(Category, category_id)
    if category is None:
        raise ValueError("category_not_found")
    return category.code
