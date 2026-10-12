"""Small repository protocols; SQLAlchemy implementations come later."""

from datetime import datetime
from typing import Protocol, Sequence

from backend.app.domain.contracts import BudgetPlanVersion, NormalizedTransaction


class TransactionRepository(Protocol):
    def list_for_analysis(
        self, user_id: int, start_utc: datetime, end_utc: datetime
    ) -> Sequence[NormalizedTransaction]:
        """Return only the user's standardized records in the requested range."""


class BudgetPlanRepository(Protocol):
    def list_versions(self, user_id: int, goal_id: int) -> Sequence[BudgetPlanVersion]:
        """Return immutable plan versions in ascending order."""

    def save_new_current_version(self, plan: BudgetPlanVersion) -> None:
        """Atomically retire the old current plan and save the new version."""
