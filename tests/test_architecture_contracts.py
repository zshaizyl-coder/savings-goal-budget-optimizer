import unittest
from datetime import date, datetime, timezone

from backend.app.domain import (
    AdjustmentMode,
    BudgetConstraintSpec,
    BudgetPlanItem,
    BudgetPlanVersion,
    BudgetStrategy,
    Direction,
    FinancialEventSpec,
    FinancialEventType,
    FinancialSnapshotSpec,
    FlowType,
    ImpactScope,
    NormalizedStatus,
    NormalizedTransaction,
    PaymentSource,
    SavingsGoalSpec,
    WeeklyPredictionItem,
)


def transaction(transaction_id: int, source: PaymentSource, flow: FlowType) -> NormalizedTransaction:
    direction = Direction.OUTFLOW
    if flow in {FlowType.INCOME, FlowType.INCOME_CANDIDATE, FlowType.EXPENSE_REFUND}:
        direction = Direction.INFLOW
    return NormalizedTransaction(
        id=transaction_id,
        user_id=1,
        import_batch_id=source.value.__hash__() % 1000,
        source=source,
        source_record_fingerprint=f"simulated-{source.value}-{transaction_id}",
        occurred_at_utc=datetime(2026, 1, transaction_id, 4, tzinfo=timezone.utc),
        amount_minor=1200,
        direction=direction,
        flow_type=flow,
        normalized_status=NormalizedStatus.COMPLETED,
        source_type_code="simulated",
        suggested_category_code="food",
    )


class ArchitectureContractTests(unittest.TestCase):
    def test_one_transaction_contract_supports_all_sources(self) -> None:
        records = [
            transaction(1, PaymentSource.WECHAT, FlowType.CONSUMPTION),
            transaction(2, PaymentSource.ALIPAY, FlowType.CONSUMPTION),
            transaction(3, PaymentSource.BANK, FlowType.CONSUMPTION),
        ]
        self.assertEqual({record.source for record in records}, set(PaymentSource))
        self.assertTrue(all(type(record.amount_minor) is int for record in records))

    def test_flow_types_distinguish_income_expense_refund_and_transfers(self) -> None:
        flows = {
            FlowType.CONSUMPTION,
            FlowType.INCOME,
            FlowType.EXPENSE_REFUND,
            FlowType.TRANSFER,
            FlowType.INTERNAL_TRANSFER,
        }
        records = [transaction(index, PaymentSource.WECHAT, flow) for index, flow in enumerate(flows, 1)]
        self.assertEqual({record.flow_type for record in records}, flows)

    def test_money_contract_rejects_float(self) -> None:
        with self.assertRaises(TypeError):
            NormalizedTransaction(
                id=1,
                user_id=1,
                import_batch_id=1,
                source=PaymentSource.WECHAT,
                source_record_fingerprint="simulated",
                occurred_at_utc=datetime.now(timezone.utc),
                amount_minor=12.5,  # type: ignore[arg-type]
                direction=Direction.OUTFLOW,
                flow_type=FlowType.CONSUMPTION,
                normalized_status=NormalizedStatus.COMPLETED,
                source_type_code="simulated",
            )

    def test_twelve_month_goal_uses_financial_snapshot(self) -> None:
        snapshot = FinancialSnapshotSpec(
            id=1,
            user_id=1,
            as_of_date=date(2026, 1, 1),
            available_savings_minor=5_000_000,
        )
        goal = SavingsGoalSpec(
            id=1,
            user_id=1,
            baseline_snapshot_id=snapshot.id,
            start_date=date(2026, 1, 1),
            target_date=date(2027, 1, 1),
            target_savings_minor=10_000_000,
        )
        self.assertEqual(goal.baseline_snapshot_id, snapshot.id)
        self.assertEqual((goal.target_date - goal.start_date).days, 365)

    def test_weekly_prediction_supports_total_and_category(self) -> None:
        items = (
            WeeklyPredictionItem(date(2026, 1, 5), "total", 75_540),
            WeeklyPredictionItem(date(2026, 1, 5), "category:food", 8_000),
        )
        self.assertEqual({item.target_key for item in items}, {"total", "category:food"})

    def test_constraint_has_explainable_adjustability(self) -> None:
        constraint = BudgetConstraintSpec(
            goal_id=1,
            category_code="food",
            minimum_budget_minor=20_000,
            maximum_budget_minor=50_000,
            is_fixed=False,
            adjustability_bps=5_000,
            effective_from=date(2026, 1, 1),
            effective_to=date(2027, 1, 1),
        )
        self.assertEqual(constraint.adjustability_bps, 5_000)

    def test_multiple_budget_versions_keep_parent_and_trigger(self) -> None:
        item = BudgetPlanItem(
            week_start=date(2026, 1, 5),
            category_code="food",
            natural_spend_minor=12_000,
            recommended_budget_minor=10_000,
            adjustment_minor=-2_000,
            minimum_constraint_minor=8_000,
            maximum_constraint_minor=None,
            adjustability_bps=5_000,
        )
        initial = BudgetPlanVersion(
            id=10,
            user_id=1,
            goal_id=1,
            version=1,
            strategy=BudgetStrategy.PERSONALIZED,
            adjustment_mode=AdjustmentMode.BALANCED,
            plan_start_week=date(2026, 1, 5),
            plan_end_week=date(2026, 12, 28),
            is_feasible=True,
            items=(item,),
            expected_final_savings_minor=10_200_000,
        )
        event = FinancialEventSpec(
            id=7,
            user_id=1,
            goal_id=1,
            event_type=FinancialEventType.UNEXPECTED_EXPENSE,
            amount_minor=200_000,
            event_date=date(2026, 1, 20),
            impact_scope=ImpactScope.ACTUAL_SPEND,
            reason_code="SIMULATED_UNPLANNED",
        )
        revised_item = BudgetPlanItem(
            week_start=date(2026, 1, 26),
            category_code="food",
            natural_spend_minor=12_000,
            recommended_budget_minor=9_000,
            adjustment_minor=-3_000,
            minimum_constraint_minor=8_000,
            maximum_constraint_minor=None,
            adjustability_bps=7_500,
        )
        revised = BudgetPlanVersion(
            id=11,
            user_id=1,
            goal_id=1,
            version=2,
            parent_plan_id=initial.id,
            trigger_event_ids=(event.id,),
            strategy=BudgetStrategy.PERSONALIZED,
            adjustment_mode=AdjustmentMode.GOAL_PRIORITY,
            plan_start_week=date(2026, 1, 26),
            plan_end_week=date(2026, 12, 28),
            is_feasible=True,
            items=(revised_item,),
            expected_final_savings_minor=10_000_000,
        )
        self.assertEqual(revised.parent_plan_id, initial.id)
        self.assertEqual(revised.trigger_event_ids, (event.id,))
        self.assertEqual(revised.version, initial.version + 1)

    def test_infeasible_budget_has_diagnostics_and_no_fake_items(self) -> None:
        plan = BudgetPlanVersion(
            id=12,
            user_id=1,
            goal_id=1,
            version=1,
            strategy=BudgetStrategy.PERSONALIZED,
            adjustment_mode=AdjustmentMode.BALANCED,
            plan_start_week=date(2026, 1, 5),
            plan_end_week=date(2026, 12, 28),
            is_feasible=False,
            items=(),
            infeasibility_code="MINIMUM_SPEND_EXCEEDS_AVAILABLE_FUNDS",
            maximum_achievable_target_minor=8_600_000,
            required_additional_income_minor=1_400_000,
            recommended_deadline=date(2027, 4, 1),
        )
        self.assertFalse(plan.is_feasible)
        self.assertEqual(plan.items, ())
        self.assertIsNotNone(plan.maximum_achievable_target_minor)

    def test_week_start_must_be_monday(self) -> None:
        with self.assertRaises(ValueError):
            WeeklyPredictionItem(date(2026, 1, 6), "total", 10_000)


if __name__ == "__main__":
    unittest.main()
