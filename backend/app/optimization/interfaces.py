"""Replaceable optimization contracts; no optimization implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, Sequence

from backend.app.domain.contracts import (
    BudgetConstraintSpec,
    BudgetPlanItem,
    BudgetPlanVersion,
    FinancialEventSpec,
    FinancialSnapshotSpec,
    IncomeAssumptionSpec,
    SavingsGoalSpec,
    WeeklyPredictionItem,
)
from backend.app.domain.enums import AdjustmentMode, BudgetStrategy
from backend.app.forecasting.interfaces import WeeklyActual


@dataclass(frozen=True)
class FeasibilityRequest:
    goal: SavingsGoalSpec
    financial_snapshot: FinancialSnapshotSpec
    income_schedule: Sequence[IncomeAssumptionSpec]
    predictions: Sequence[WeeklyPredictionItem]
    constraints: Sequence[BudgetConstraintSpec]
    planning_weeks: Sequence[date]


@dataclass(frozen=True)
class FeasibilityResult:
    feasible: bool
    reason_code: str | None
    maximum_achievable_target_minor: int | None = None
    required_additional_income_minor: int | None = None
    recommended_deadline: date | None = None
    binding_constraint_codes: tuple[str, ...] = ()
    explanations: tuple[str, ...] = ()


@dataclass(frozen=True)
class BudgetOptimizationRequest:
    goal: SavingsGoalSpec
    financial_snapshot: FinancialSnapshotSpec
    income_schedule: Sequence[IncomeAssumptionSpec]
    predictions: Sequence[WeeklyPredictionItem]
    constraints: Sequence[BudgetConstraintSpec]
    feasibility: FeasibilityResult
    adjustment_mode: AdjustmentMode
    planning_weeks: Sequence[date]


@dataclass(frozen=True)
class BudgetOptimizationResult:
    feasible: bool
    strategy: BudgetStrategy
    algorithm_version: str
    expected_final_savings_minor: int | None
    items: tuple[BudgetPlanItem, ...]
    diagnostics: tuple[str, ...]
    infeasibility: FeasibilityResult | None = None


@dataclass(frozen=True)
class ReplanRequest:
    previous_plan: BudgetPlanVersion
    locked_actuals: Sequence[WeeklyActual]
    events: Sequence[FinancialEventSpec]
    current_snapshot: FinancialSnapshotSpec
    remaining_goal: SavingsGoalSpec
    current_constraints: Sequence[BudgetConstraintSpec]
    latest_predictions: Sequence[WeeklyPredictionItem]
    next_open_week: date
    strategy: BudgetStrategy
    adjustment_mode: AdjustmentMode


@dataclass(frozen=True)
class ReplanResult:
    parent_plan_id: int
    new_version: int
    trigger_event_ids: tuple[int, ...]
    locked_through_week: date
    optimization_result: BudgetOptimizationResult
    future_delta_minor: int
    explanations: tuple[str, ...] = ()


class FeasibilityEvaluator(Protocol):
    def evaluate(self, request: FeasibilityRequest) -> FeasibilityResult:
        """Evaluate feasibility before any optimizer is called."""


class BudgetOptimizer(Protocol):
    def optimize(self, request: BudgetOptimizationRequest) -> BudgetOptimizationResult:
        """Produce a strategy-specific result from framework-free inputs."""


class BudgetReplanner(Protocol):
    def replan(self, request: ReplanRequest) -> ReplanResult:
        """Create a future-only replacement while preserving the old plan."""
