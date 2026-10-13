from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from math import ceil

from .interfaces import FeasibilityRequest, FeasibilityResult


def income_total_minor(request: FeasibilityRequest) -> int:
    if not request.planning_weeks:
        return 0
    period_start = min(request.planning_weeks)
    period_end = max(request.planning_weeks) + timedelta(days=6)
    total = 0
    for item in request.income_schedule:
        start, end = max(item.start_date, period_start), min(item.end_date, period_end)
        if end < start:
            continue
        if item.frequency == "one_time":
            if period_start <= item.start_date <= period_end:
                total += item.amount_minor
        elif item.frequency == "weekly":
            total += item.amount_minor * sum(
                start <= week + timedelta(days=6) and week <= end
                for week in request.planning_weeks
            )
        else:
            cursor = date(start.year, start.month, 1)
            while cursor <= end:
                expected = min(item.expected_day or 1, monthrange(cursor.year, cursor.month)[1])
                pay_date = date(cursor.year, cursor.month, expected)
                if start <= pay_date <= end:
                    total += item.amount_minor
                cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
    return total


def constraint_for_week(request: FeasibilityRequest, category: str, week: date):
    matches = [item for item in request.constraints if item.category_code == category
               and item.effective_from <= week <= item.effective_to]
    if len(matches) != 1:
        raise ValueError(f"constraint_count_invalid:{category}:{week.isoformat()}")
    return matches[0]


class SavingsGoalFeasibilityEvaluator:
    def evaluate(self, request: FeasibilityRequest) -> FeasibilityResult:
        weeks = tuple(sorted(request.planning_weeks))
        if not weeks or len(set(weeks)) != len(weeks) or any(week.weekday() != 0 for week in weeks):
            raise ValueError("invalid_planning_weeks")
        if request.goal.user_id != request.financial_snapshot.user_id:
            raise ValueError("user_mismatch")
        categories = sorted({item.category_code for item in request.constraints})
        minimum = sum(
            constraint_for_week(request, category, week).minimum_budget_minor
            for week in weeks for category in categories
        )
        income = income_total_minor(request)
        funds = request.financial_snapshot.available_savings_minor + income
        maximum_target = max(0, funds - minimum)
        shortfall = max(0, request.goal.target_savings_minor - maximum_target)
        binding = tuple(sorted({
            item.category_code for item in request.constraints
            if item.minimum_budget_minor > 0 or item.is_fixed
        }))
        if not shortfall:
            return FeasibilityResult(
                feasible=True, reason_code=None,
                maximum_achievable_target_minor=maximum_target,
                required_additional_income_minor=0,
                binding_constraint_codes=binding,
                explanations=("minimum_spending_scenario_meets_goal",),
                projected_income_minor=income,
                minimum_required_spending_minor=minimum,
                available_for_spending_minor=max(0, funds - request.goal.target_savings_minor),
            )
        average_net = (income - minimum) / len(weeks)
        recommended = None
        if average_net > 0:
            recommended = request.goal.target_date + timedelta(days=7 * ceil(shortfall / average_net))
        return FeasibilityResult(
            feasible=False, reason_code="INFEASIBLE_GOAL",
            maximum_achievable_target_minor=maximum_target,
            required_additional_income_minor=shortfall,
            recommended_deadline=recommended,
            binding_constraint_codes=binding,
            explanations=("funds_after_confirmed_minimums_below_target",),
            projected_income_minor=income,
            minimum_required_spending_minor=minimum,
            available_for_spending_minor=max(0, funds - request.goal.target_savings_minor),
        )
