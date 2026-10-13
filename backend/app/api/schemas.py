from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, model_validator


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=8, max_length=128)
    timezone: str = "Asia/Shanghai"


class LoginRequest(BaseModel):
    username: str
    password: str


class CategoryUpdate(BaseModel):
    category_code: str


class ModelCategoryBatchRequest(BaseModel):
    tier: str = Field(default="A", pattern="^(A|B|C)$")
    limit: int = Field(default=10, ge=1, le=20)


class SnapshotCreate(BaseModel):
    as_of_date: date
    available_savings_minor: int = Field(ge=0)


class IncomeInput(BaseModel):
    frequency: str = Field(pattern="^(one_time|weekly|monthly)$")
    amount_minor: int = Field(ge=0)
    start_date: date
    end_date: date
    expected_day: int | None = Field(default=None, ge=1, le=31)


class GoalCreate(BaseModel):
    current_savings_minor: int = Field(ge=0)
    start_date: date
    target_date: date
    target_savings_minor: int = Field(gt=0)
    income_assumptions: list[IncomeInput] = []

    @model_validator(mode="after")
    def dates_valid(self):
        if self.target_date <= self.start_date:
            raise ValueError("target_date must be after start_date")
        return self


class ConstraintInput(BaseModel):
    category_code: str
    minimum_budget_minor: int = Field(ge=0)
    maximum_budget_minor: int | None = Field(default=None, ge=0)
    is_fixed: bool = False
    adjustability: str = Field(default="medium", pattern="^(none|low|medium|high)$")

    @model_validator(mode="after")
    def bounds_valid(self):
        if self.maximum_budget_minor is not None and self.maximum_budget_minor < self.minimum_budget_minor:
            raise ValueError("maximum must be at least minimum")
        if self.is_fixed and self.maximum_budget_minor != self.minimum_budget_minor:
            raise ValueError("fixed category requires equal minimum and maximum")
        return self


class ConstraintSet(BaseModel):
    constraints: list[ConstraintInput]


class BudgetGenerateRequest(BaseModel):
    goal_id: int
    adjustment_mode: str = Field(default="balanced", pattern="^(goal_priority|balanced|life_stability)$")


class EventCreate(BaseModel):
    goal_id: int
    event_type: str
    amount_minor: int | None = Field(default=None, ge=0)
    event_date: date
    safe_note: str | None = Field(default=None, max_length=200)


class ReplanRequestBody(BaseModel):
    event_ids: list[int] = []
    current_savings_minor: int = Field(ge=0)
    next_open_week: date
    adjustment_mode: str = Field(default="balanced", pattern="^(goal_priority|balanced|life_stability)$")
    recovery_horizon_weeks: int | None = Field(default=None, ge=1)


class ConfirmPlanRequest(BaseModel):
    plan_id: int
