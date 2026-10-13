from __future__ import annotations

from collections import defaultdict
from calendar import monthrange
from datetime import date, timedelta
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.analytics.consumption import build_consumption_datasets
from backend.app.domain.contracts import (
    BudgetConstraintSpec, BudgetPlanItem, BudgetPlanVersion, FinancialSnapshotSpec,
    IncomeAssumptionSpec, SavingsGoalSpec,
)
from backend.app.domain.enums import AdjustmentMode, BudgetStrategy
from backend.app.forecasting.interfaces import WeeklyActual
from backend.app.models import (
    BudgetConstraint, BudgetPlan, BudgetPlanItemModel, Category, FinancialSnapshot,
    IncomeAssumption, SavingGoal,
)
from backend.app.optimization.feasibility import SavingsGoalFeasibilityEvaluator
from backend.app.optimization.interfaces import BudgetOptimizationRequest, FeasibilityRequest
from backend.app.optimization.optimizer import PersonalizedBudgetOptimizer
from backend.app.replanning.forecast import RollingForecastUpdater
from backend.app.repositories.analysis import TransactionAnalysisRepository


ADJUSTABILITY_BPS = {"none": 0, "low": 2500, "medium": 5000, "high": 8000}


def monday_on_or_after(value: date) -> date:
    return value + timedelta(days=(-value.weekday()) % 7)


def planning_weeks(start: date, end: date) -> tuple[date, ...]:
    first = monday_on_or_after(start)
    if first > end:
        return ()
    return tuple(first + timedelta(days=7 * index)
                 for index in range(((end - first).days // 7) + 1))


def dataframe_records(frame) -> list[dict]:
    records = []
    for row in frame.to_dict(orient="records"):
        records.append({key: value.isoformat() if hasattr(value, "isoformat") else
                        value.item() if hasattr(value, "item") else value
                        for key, value in row.items()})
    return records


class WebFinanceService:
    """Web-facing adapter that composes the verified analysis and optimization modules."""

    def __init__(self, session: Session, user_id: int):
        self.session = session
        self.user_id = user_id

    def datasets(self):
        transactions = TransactionAnalysisRepository(self.session).list_transactions(self.user_id)
        return build_consumption_datasets(transactions)

    def analytics(self) -> dict:
        data = self.datasets()
        if data.facts.empty:
            return {"has_data": False, "weekly": [], "monthly": [], "categories": [],
                    "weekday_weekend": []}
        facts = data.facts
        categories = data.category_weekly.groupby("category_code", as_index=False).agg(
            net_consumption_minor=("net_consumption_minor", "sum"),
            consumption_count=("consumption_count", "sum"),
        )
        total = max(1, int(categories["net_consumption_minor"].clip(lower=0).sum()))
        categories["amount_share_bps"] = categories["net_consumption_minor"].map(
            lambda value: round(max(0, int(value)) / total * 10_000)
        )
        days = facts.groupby("is_weekend", as_index=False).agg(
            net_consumption_minor=("net_consumption_minor", "sum"),
            active_days=("local_date", "nunique"),
            transaction_count=("transaction_id", "count"),
        )
        days["daily_average_minor"] = (days["net_consumption_minor"] / days["active_days"]).round().astype(int)
        return {
            "has_data": True,
            "weekly": dataframe_records(data.weekly),
            "monthly": dataframe_records(data.monthly),
            "categories": dataframe_records(categories),
            "category_weekly": dataframe_records(data.category_weekly),
            "weekday_weekend": dataframe_records(days),
            "large_transaction_threshold_minor": data.thresholds.recommended_minor,
        }

    def goal_objects(self, goal_id: int):
        goal = self.session.scalar(select(SavingGoal).where(
            SavingGoal.id == goal_id, SavingGoal.user_id == self.user_id,
        ))
        if goal is None:
            raise LookupError("goal_not_found")
        snapshot = self.session.scalar(select(FinancialSnapshot).where(
            FinancialSnapshot.id == goal.baseline_snapshot_id,
            FinancialSnapshot.user_id == self.user_id,
        ))
        incomes = self.session.scalars(select(IncomeAssumption).where(
            IncomeAssumption.goal_id == goal.id, IncomeAssumption.is_active.is_(True),
        )).all()
        constraints = self.session.execute(
            select(BudgetConstraint, Category.code)
            .join(Category, Category.id == BudgetConstraint.category_id)
            .where(BudgetConstraint.goal_id == goal.id)
        ).all()
        goal_spec = SavingsGoalSpec(
            goal.id, goal.user_id, goal.baseline_snapshot_id, goal.start_date,
            goal.target_date, goal.target_savings_minor, goal.version, goal.currency,
        )
        snapshot_spec = FinancialSnapshotSpec(
            snapshot.id, snapshot.user_id, snapshot.as_of_date,
            snapshot.available_savings_minor, snapshot.source, snapshot.currency,
        )
        income_specs = tuple(IncomeAssumptionSpec(
            item.goal_id, item.frequency, item.amount_minor, item.start_date,
            item.end_date, item.expected_day,
        ) for item in incomes)
        constraint_specs = tuple(BudgetConstraintSpec(
            item.goal_id, code, item.minimum_budget_minor, item.maximum_budget_minor,
            item.is_fixed, item.adjustability_bps, item.effective_from,
            item.effective_to, item.version,
        ) for item, code in constraints)
        return goal, goal_spec, snapshot_spec, income_specs, constraint_specs

    def historical_actuals(self, cutoff: date | None = None) -> tuple[WeeklyActual, ...]:
        data = self.datasets()
        if data.weekly.empty:
            raise ValueError("insufficient_history")
        rows: list[WeeklyActual] = []
        category_by_week: defaultdict[date, list] = defaultdict(list)
        for row in data.category_weekly.itertuples(index=False):
            category_by_week[row.week_start].append(row)
        for row in data.weekly.itertuples(index=False):
            if not row.is_complete_week or (cutoff is not None and row.week_start >= cutoff):
                continue
            rows.append(WeeklyActual(row.week_start, "total", int(row.net_consumption_minor), True, False))
            for category in category_by_week[row.week_start]:
                if category.category_code == "unallocated_refund":
                    continue
                rows.append(WeeklyActual(
                    row.week_start, category.category_code,
                    max(0, int(category.net_consumption_minor)), True, False,
                ))
        return tuple(rows)

    def forecast_for_goal(self, goal_id: int, *, cutoff: date | None = None) -> tuple:
        goal, _goal_spec, _snapshot, _income, constraints = self.goal_objects(goal_id)
        weeks = planning_weeks(cutoff or goal.start_date, goal.target_date)
        if not weeks:
            raise ValueError("invalid_planning_period")
        categories = sorted({item.category_code for item in constraints})
        if not categories:
            raise ValueError("constraints_required")
        history = list(self.historical_actuals(weeks[0]))
        # The canonical category dataset contains explicit zero weeks only for
        # categories observed at least once. A user may still define a valid
        # zero-natural-spend constraint for another system category.
        history_weeks = sorted({item.week_start for item in history if item.target_key == "total"})
        existing = {(item.week_start, item.target_key) for item in history}
        history.extend(
            WeeklyActual(week, category, 0, True, False)
            for week in history_weeks for category in categories
            if (week, category) not in existing
        )
        return RollingForecastUpdater().forecast(history, weeks, categories)

    def optimize(self, goal_id: int, mode: str):
        _goal, goal, snapshot, incomes, constraints = self.goal_objects(goal_id)
        weeks = planning_weeks(goal.start_date, goal.target_date)
        predictions = self.forecast_for_goal(goal_id)
        feasibility_request = FeasibilityRequest(
            goal, snapshot, incomes, predictions, constraints, weeks,
        )
        feasibility = SavingsGoalFeasibilityEvaluator().evaluate(feasibility_request)
        request = BudgetOptimizationRequest(
            goal, snapshot, incomes, predictions, constraints, feasibility,
            AdjustmentMode(mode), weeks,
        )
        return PersonalizedBudgetOptimizer().optimize(request), feasibility

    def save_initial_plan(self, goal_id: int, result) -> BudgetPlan:
        goal = self.session.scalar(select(SavingGoal).where(
            SavingGoal.id == goal_id, SavingGoal.user_id == self.user_id,
        ))
        version = (self.session.scalar(select(func.max(BudgetPlan.version)).where(
            BudgetPlan.goal_id == goal_id
        )) or 0) + 1
        plan = BudgetPlan(
            user_id=self.user_id, goal_id=goal_id, snapshot_id=goal.baseline_snapshot_id,
            version=version, strategy=result.strategy.value,
            adjustment_mode=result.optimization_mode.value,
            status="draft" if result.feasible else "infeasible",
            plan_start_week=result.plan_start,
            plan_end_week=(max(item.week_start for item in result.items)
                           if result.items else result.plan_start),
            target_savings_snapshot_minor=goal.target_savings_minor,
            target_date_snapshot=goal.target_date, is_feasible=result.feasible,
            infeasibility_code=result.infeasibility.reason_code if result.infeasibility else None,
            expected_final_savings_minor=result.expected_final_savings_minor,
            maximum_achievable_target_minor=(result.infeasibility.maximum_achievable_target_minor
                                             if result.infeasibility else None),
            required_additional_income_minor=(result.infeasibility.required_additional_income_minor
                                              if result.infeasibility else None),
            recommended_deadline=(result.infeasibility.recommended_deadline
                                  if result.infeasibility else None),
            risk_level=result.risk_level,
            diagnostics_json=json.dumps(result.diagnostics, ensure_ascii=False),
            algorithm_version=result.algorithm_version, is_current=False,
        )
        self.session.add(plan); self.session.flush()
        categories = {item.code: item.id for item in self.session.scalars(select(Category)).all()}
        for item in result.items:
            self.session.add(BudgetPlanItemModel(
                budget_plan_id=plan.id, week_start=item.week_start,
                category_id=categories[item.category_code],
                natural_spend_minor=item.natural_spend_minor,
                recommended_budget_minor=item.recommended_budget_minor,
                adjustment_minor=item.adjustment_minor,
                minimum_constraint_minor=item.minimum_constraint_minor,
                maximum_constraint_minor=item.maximum_constraint_minor,
                adjustability_bps=item.adjustability_bps,
                explanation_code="initial_plan",
            ))
        self.session.flush()
        return plan

    def monthly_summary(self, goal_id: int, result) -> list[dict]:
        _goal, _goal_spec, snapshot, incomes, _constraints = self.goal_objects(goal_id)
        monthly: defaultdict[str, dict[str, int]] = defaultdict(
            lambda: {"natural_consumption_minor": 0, "recommended_consumption_minor": 0,
                     "expected_income_minor": 0}
        )
        for item in result.items:
            for field, amount in (("natural_consumption_minor", item.natural_spend_minor),
                                  ("recommended_consumption_minor", item.recommended_budget_minor)):
                base, remainder = divmod(amount, 7)
                for offset in range(7):
                    day = item.week_start + timedelta(days=offset)
                    monthly[day.strftime("%Y-%m")][field] += base + (offset < remainder)
        for income in incomes:
            if income.frequency == "one_time":
                monthly[income.start_date.strftime("%Y-%m")]["expected_income_minor"] += income.amount_minor
            elif income.frequency == "weekly":
                cursor = income.start_date
                while cursor <= income.end_date:
                    monthly[cursor.strftime("%Y-%m")]["expected_income_minor"] += income.amount_minor
                    cursor += timedelta(days=7)
            else:
                cursor = date(income.start_date.year, income.start_date.month, 1)
                while cursor <= income.end_date:
                    pay_day = min(income.expected_day or 1, monthrange(cursor.year, cursor.month)[1])
                    pay_date = date(cursor.year, cursor.month, pay_day)
                    if income.start_date <= pay_date <= income.end_date:
                        monthly[pay_date.strftime("%Y-%m")]["expected_income_minor"] += income.amount_minor
                    cursor = date(cursor.year + (cursor.month == 12),
                                  1 if cursor.month == 12 else cursor.month + 1, 1)
        cumulative = snapshot.available_savings_minor
        output = []
        for key in sorted(monthly):
            row = monthly[key]
            expected_saved = row["expected_income_minor"] - row["recommended_consumption_minor"]
            cumulative += expected_saved
            output.append({"year_month": key, **row, "expected_saving_minor": expected_saved,
                           "cumulative_savings_minor": cumulative})
        return output

    def plan_version(self, plan: BudgetPlan) -> BudgetPlanVersion:
        rows = self.session.execute(
            select(BudgetPlanItemModel, Category.code)
            .join(Category, Category.id == BudgetPlanItemModel.category_id)
            .where(BudgetPlanItemModel.budget_plan_id == plan.id)
        ).all()
        items = tuple(BudgetPlanItem(
            row.week_start, code, row.natural_spend_minor,
            row.recommended_budget_minor, row.adjustment_minor,
            row.minimum_constraint_minor, row.maximum_constraint_minor,
            row.adjustability_bps,
        ) for row, code in rows)
        return BudgetPlanVersion(
            plan.id, plan.user_id, plan.goal_id, plan.version,
            BudgetStrategy(plan.strategy), AdjustmentMode(plan.adjustment_mode),
            plan.plan_start_week, plan.plan_end_week, plan.is_feasible, items,
            parent_plan_id=plan.parent_plan_id,
            expected_final_savings_minor=plan.expected_final_savings_minor,
            infeasibility_code=plan.infeasibility_code,
            maximum_achievable_target_minor=plan.maximum_achievable_target_minor,
            required_additional_income_minor=plan.required_additional_income_minor,
            recommended_deadline=plan.recommended_deadline, snapshot_id=plan.snapshot_id,
            risk_level=plan.risk_level, status=plan.status,
            target_date_snapshot=plan.target_date_snapshot,
        )


def serialize_optimization(result, plan_id: int | None = None) -> dict:
    infeasible = result.infeasibility
    return {
        "plan_id": plan_id,
        "feasible": result.feasible,
        "reason_code": infeasible.reason_code if infeasible else None,
        "maximum_achievable_target_minor": infeasible.maximum_achievable_target_minor if infeasible else None,
        "required_additional_income_minor": infeasible.required_additional_income_minor if infeasible else None,
        "recommended_deadline": infeasible.recommended_deadline.isoformat() if infeasible and infeasible.recommended_deadline else None,
        "expected_final_savings_minor": result.expected_final_savings_minor,
        "risk_level": result.risk_level,
        "algorithm_version": result.algorithm_version,
        "natural_spending_minor": result.natural_spending_minor,
        "recommended_spending_minor": result.recommended_spending_minor,
        "monthly_budget": [{"year_month": month, "recommended_budget_minor": value}
                           for month, value in result.monthly_budget_minor],
        "items": [{
            "week_start": item.week_start.isoformat(), "category_code": item.category_code,
            "natural_spend_minor": item.natural_spend_minor,
            "recommended_budget_minor": item.recommended_budget_minor,
            "adjustment_minor": item.adjustment_minor,
        } for item in result.items],
    }
