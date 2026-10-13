from __future__ import annotations

from dataclasses import dataclass
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domain.contracts import (
    FinancialEventSpec,
    FinancialSnapshotSpec,
    SavingsGoalSpec,
)
from backend.app.models import (
    BudgetPlan,
    BudgetPlanEvent,
    BudgetPlanItemModel,
    Category,
    FinancialEvent,
    FinancialSnapshot,
    SavingGoal,
)
from backend.app.optimization.interfaces import ReplanResult


@dataclass(frozen=True)
class PersistReplanCommand:
    previous_plan_id: int
    snapshot: FinancialSnapshotSpec
    goal: SavingsGoalSpec
    events: tuple[FinancialEventSpec, ...]
    result: ReplanResult


class ReplanPersistenceService:
    """Persist a replan as one SQLite/SQLAlchemy transaction."""

    def save(self, session: Session, command: PersistReplanCommand, *,
             fail_after_retire_for_test: bool = False) -> int:
        with session.begin():
            old = session.get(BudgetPlan, command.previous_plan_id)
            if old is None or old.id != command.result.parent_plan_id:
                raise ValueError("previous_plan_not_found")
            if old.user_id != command.snapshot.user_id or old.user_id != command.goal.user_id:
                raise ValueError("user_mismatch")
            if any(session.get(FinancialEvent, event.id) is not None for event in command.events):
                raise ValueError("event_already_persisted")
            if not old.is_current or old.status != "active":
                raise ValueError("previous_plan_not_active")

            snapshot = FinancialSnapshot(
                id=command.snapshot.id,
                user_id=command.snapshot.user_id,
                as_of_date=command.snapshot.as_of_date,
                available_savings_minor=command.snapshot.available_savings_minor,
                currency=command.snapshot.currency,
                source=command.snapshot.source,
            )
            session.add(snapshot)
            session.flush()

            goal = session.get(SavingGoal, command.goal.id)
            if goal is None:
                goal = SavingGoal(
                    id=command.goal.id,
                    user_id=command.goal.user_id,
                    baseline_snapshot_id=snapshot.id,
                    previous_goal_id=old.goal_id,
                    start_date=command.goal.start_date,
                    target_date=command.goal.target_date,
                    target_savings_minor=command.goal.target_savings_minor,
                    currency=command.goal.currency,
                    status="active",
                    version=command.goal.version,
                )
                session.add(goal)
                session.flush()

            persisted_events = []
            for event in command.events:
                row = FinancialEvent(
                    id=event.id,
                    user_id=event.user_id,
                    goal_id=event.goal_id,
                    event_type=event.event_type.value,
                    amount_minor=event.amount_minor,
                    event_date=event.event_date,
                    impact_scope=event.impact_scope.value,
                    reason_code=event.reason_code,
                    safe_note=event.safe_note,
                    deduplication_key=f"goal:{event.goal_id}:event:{event.id}",
                )
                session.add(row)
                persisted_events.append(row)
            session.flush()

            optimization = command.result.optimization_result
            feasible = optimization.feasible
            goal_achieved = command.result.status_code == "GOAL_ACHIEVED"
            if feasible:
                old.status = "completed" if goal_achieved else "superseded"
                old.is_current = False
                if goal_achieved:
                    goal.status = "achieved"
            if fail_after_retire_for_test:
                raise RuntimeError("simulated_transaction_failure")

            infeasibility = optimization.infeasibility
            new_plan = BudgetPlan(
                user_id=old.user_id,
                goal_id=goal.id,
                snapshot_id=snapshot.id,
                parent_plan_id=old.id,
                version=command.result.new_version,
                strategy=optimization.strategy.value,
                adjustment_mode=optimization.optimization_mode.value,
                status="completed" if goal_achieved else "active" if feasible else "infeasible",
                plan_start_week=optimization.plan_start,
                plan_end_week=optimization.plan_end,
                target_savings_snapshot_minor=command.goal.target_savings_minor,
                target_date_snapshot=command.goal.target_date,
                is_feasible=feasible,
                infeasibility_code=infeasibility.reason_code if infeasibility else None,
                expected_final_savings_minor=optimization.expected_final_savings_minor,
                maximum_achievable_target_minor=(
                    infeasibility.maximum_achievable_target_minor if infeasibility else None
                ),
                required_additional_income_minor=(
                    infeasibility.required_additional_income_minor if infeasibility else None
                ),
                recommended_deadline=infeasibility.recommended_deadline if infeasibility else None,
                risk_level=optimization.risk_level,
                diagnostics_json=json.dumps({
                    "optimization": optimization.diagnostics,
                    "replan": command.result.explanations,
                    "status_code": command.result.status_code,
                    "model_reevaluation_due": command.result.model_reevaluation_due,
                }, ensure_ascii=False),
                algorithm_version=optimization.algorithm_version,
                is_current=feasible and not goal_achieved,
            )
            session.add(new_plan)
            session.flush()
            for index, event in enumerate(persisted_events):
                session.add(BudgetPlanEvent(
                    budget_plan_id=new_plan.id,
                    financial_event_id=event.id,
                    role="primary" if index == 0 else "supporting",
                ))
            categories = {
                item.code: item.id for item in session.scalars(select(Category)).all()
            }
            for item in optimization.items:
                if item.category_code not in categories:
                    raise ValueError(f"unknown_category:{item.category_code}")
                session.add(BudgetPlanItemModel(
                    budget_plan_id=new_plan.id,
                    week_start=item.week_start,
                    category_id=categories[item.category_code],
                    natural_spend_minor=item.natural_spend_minor,
                    recommended_budget_minor=item.recommended_budget_minor,
                    adjustment_minor=item.adjustment_minor,
                    minimum_constraint_minor=item.minimum_constraint_minor,
                    maximum_constraint_minor=item.maximum_constraint_minor,
                    adjustability_bps=item.adjustability_bps,
                    explanation_code="rolling_replan",
                ))
            session.flush()
            return new_plan.id
