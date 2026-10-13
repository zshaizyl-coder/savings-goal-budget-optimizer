from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import time
import unittest

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db.session import create_session_factory, create_sqlite_engine
from backend.app.main import create_app
from backend.app.models import User


ROOT = Path(__file__).resolve().parents[1]
DEMO_BILL = ROOT / "data" / "sample" / "wechat_bill_demo.xlsx"


class WebApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.temp.name) / "web.sqlite3"
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", f"sqlite:///{cls.db.as_posix()}")
        command.upgrade(config, "head")
        cls.engine = create_sqlite_engine(cls.db)
        cls.factory = create_session_factory(cls.engine)
        settings = Settings(f"sqlite:///{cls.db.as_posix()}", "test-secret-key", 12 * 1024 * 1024,
                            ("http://testserver",))
        cls.client = TestClient(create_app(settings=settings, session_factory=cls.factory),
                                raise_server_exceptions=True)

    @classmethod
    def tearDownClass(cls):
        cls.client.close(); cls.engine.dispose(); cls.temp.cleanup()

    def register(self, username: str) -> dict[str, str]:
        response = self.client.post("/api/v1/auth/register", json={
            "username": username, "password": "demo-password", "timezone": "Asia/Shanghai",
        })
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def import_demo(self, headers):
        with DEMO_BILL.open("rb") as handle:
            response = self.client.post("/api/v1/imports/wechat", headers=headers,
                                        files={"file": ("synthetic-demo.xlsx", handle,
                                                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreater(response.json()["inserted_rows"], 300)
        return response.json()

    def create_goal_and_constraints(self, headers, *, target=1_000_000, income=300_000,
                                    end="2027-01-31"):
        response = self.client.post("/api/v1/goals", headers=headers, json={
            "current_savings_minor": 500_000, "start_date": "2026-10-19",
            "target_date": end, "target_savings_minor": target,
            "income_assumptions": [{"frequency": "monthly", "amount_minor": income,
                                    "start_date": "2026-10-19", "end_date": end,
                                    "expected_day": 10}],
        })
        self.assertEqual(response.status_code, 200, response.text); goal_id = response.json()["id"]
        categories = self.client.get("/api/v1/categories", headers=headers).json()["items"]
        constraints = [{"category_code": item["code"], "minimum_budget_minor": 0,
                        "maximum_budget_minor": None, "is_fixed": False,
                        "adjustability": "high"} for item in categories]
        response = self.client.put(f"/api/v1/goals/{goal_id}/constraints",
                                   headers=headers, json={"constraints": constraints})
        self.assertEqual(response.status_code, 200, response.text)
        return goal_id

    def test_01_health(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

    def test_02_registration_hashes_password_and_login_works(self):
        headers = self.register("auth-user")
        with self.factory() as session:
            user = session.query(User).filter_by(username="auth-user").one()
            self.assertNotEqual(user.password_hash, "demo-password")
            self.assertTrue(user.password_hash.startswith("pbkdf2_sha256$"))
        response = self.client.post("/api/v1/auth/login",
                                    json={"username": "auth-user", "password": "demo-password"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/users/me", headers=headers).status_code, 200)

    def test_03_invalid_login_uses_business_error(self):
        response = self.client.post("/api/v1/auth/login", json={"username": "missing", "password": "incorrect"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "INVALID_CREDENTIALS")

    def test_04_upload_rejects_non_xlsx(self):
        headers = self.register("bad-upload")
        response = self.client.post("/api/v1/imports/wechat", headers=headers,
                                    files={"file": ("bill.txt", b"not excel", "text/plain")})
        self.assertEqual(response.status_code, 415)

    def test_05_import_analysis_and_transactions(self):
        headers = self.register("analysis-user"); report = self.import_demo(headers)
        self.assertEqual(report["failed_rows"], 0)
        analysis = self.client.get("/api/v1/analytics/overview", headers=headers).json()
        self.assertTrue(analysis["has_data"]); self.assertGreaterEqual(len(analysis["weekly"]), 52)
        self.assertIn("anomaly_weeks", analysis)
        self.assertIn("category_weekly", analysis)
        transactions = self.client.get("/api/v1/transactions?page_size=10", headers=headers).json()
        self.assertGreater(transactions["total"], 300)
        history = self.client.get("/api/v1/imports", headers=headers).json()["items"]
        self.assertEqual(history[0]["inserted_rows"], report["inserted_rows"])

    def test_06_user_category_confirmation_recomputes_effective_category(self):
        headers = self.register("category-user"); self.import_demo(headers)
        rows = self.client.get("/api/v1/transactions?page_size=200", headers=headers).json()["items"]
        other = next(item for item in rows if item["effective_category"] == "other")
        response = self.client.patch(f"/api/v1/transactions/{other['id']}/category",
                                     headers=headers, json={"category_code": "food"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["user_confirmed"])

    def test_07_other_review_has_neutral_wording(self):
        headers = self.register("review-user"); self.import_demo(headers)
        provider = self.client.get(
            "/api/v1/classification/providers/deepseek", headers=headers,
        ).json()
        self.assertFalse(provider["ready"])
        disabled = self.client.post(
            "/api/v1/categories/review/deepseek-suggestions", headers=headers,
            json={"tier": "A", "limit": 10},
        )
        self.assertEqual(disabled.status_code, 503)
        self.assertEqual(disabled.json()["error"]["code"], "DEEPSEEK_NOT_CONFIGURED")
        payload = self.client.get("/api/v1/categories/review", headers=headers).json()
        self.assertIn("审核优先级", payload["meaning"])
        self.assertNotIn("不必要", payload["meaning"])
        self.assertGreater(payload["summary"]["current_other_count"], 0)
        self.assertTrue(payload["items"])
        self.assertIn("representative_transaction_id", payload["items"][0])
        self.assertIn("merchant_hint", payload["items"][0])
        group = payload["items"][0]
        response = self.client.patch(
            f"/api/v1/transactions/{group['representative_transaction_id']}/category",
            headers=headers, json={"category_code": "food"},
        )
        self.assertGreaterEqual(response.json()["updated_transactions"], 1)

    def test_08_forecast_calls_stage6_adapter(self):
        headers = self.register("forecast-user"); self.import_demo(headers)
        goal = self.create_goal_and_constraints(headers)
        response = self.client.get(f"/api/v1/forecasts/weekly?goal_id={goal}", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["model"], "total-weekly-forecast-v1")
        self.assertTrue(any(item["target_key"] == "total" for item in response.json()["items"]))

    def test_09_feasible_budget_full_flow(self):
        headers = self.register("budget-user"); self.import_demo(headers)
        goal = self.create_goal_and_constraints(headers)
        response = self.client.post("/api/v1/budgets/generate", headers=headers,
                                    json={"goal_id": goal, "adjustment_mode": "balanced"})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json(); self.assertTrue(result["feasible"]); self.assertGreater(len(result["items"]), 10)
        activated = self.client.post(f"/api/v1/budgets/{result['plan_id']}/activate", headers=headers)
        self.assertEqual(activated.json()["status"], "active")
        dashboard = self.client.get("/api/v1/dashboard", headers=headers).json()
        self.assertEqual(dashboard["plan"]["id"], result["plan_id"])
        self.assertIn("usage_bps", dashboard["current_week"])
        self.assertIsNotNone(dashboard["monthly_income_assumption_minor"])

    def test_10_infeasible_goal_is_business_result_then_adjustable(self):
        headers = self.register("infeasible-user"); self.import_demo(headers)
        impossible = self.create_goal_and_constraints(headers, target=10_000_000, income=0,
                                                       end="2026-12-01")
        response = self.client.post("/api/v1/budgets/generate", headers=headers,
                                    json={"goal_id": impossible, "adjustment_mode": "balanced"})
        self.assertEqual(response.status_code, 200); self.assertFalse(response.json()["feasible"])
        self.assertEqual(response.json()["reason_code"], "INFEASIBLE_GOAL")
        adjusted = self.create_goal_and_constraints(headers, target=700_000, income=300_000,
                                                     end="2027-01-31")
        response = self.client.post("/api/v1/budgets/generate", headers=headers,
                                    json={"goal_id": adjusted, "adjustment_mode": "balanced"})
        self.assertTrue(response.json()["feasible"])

    def test_11_user_data_isolation_for_transactions_and_plans(self):
        first = self.register("owner-user"); second = self.register("other-user")
        self.import_demo(first)
        transaction_id = self.client.get("/api/v1/transactions?page_size=1", headers=first).json()["items"][0]["id"]
        response = self.client.patch(f"/api/v1/transactions/{transaction_id}/category",
                                     headers=second, json={"category_code": "food"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/api/v1/transactions", headers=second).json()["total"], 0)
        goal = self.create_goal_and_constraints(first)
        plan = self.client.post("/api/v1/budgets/generate", headers=first,
                                json={"goal_id": goal, "adjustment_mode": "balanced"}).json()
        self.assertEqual(self.client.get(f"/api/v1/budgets/{plan['plan_id']}", headers=second).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/events", headers=second, json={
            "goal_id": goal, "event_type": "unplanned_expense", "amount_minor": 1000,
            "event_date": "2026-10-18",
        }).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/goals", headers=second).json()["items"], [])

    def test_12_replan_preview_confirm_and_history(self):
        headers = self.register("replan-user"); self.import_demo(headers)
        goal = self.create_goal_and_constraints(headers)
        generated = self.client.post("/api/v1/budgets/generate", headers=headers,
                                     json={"goal_id": goal, "adjustment_mode": "balanced"}).json()
        plan_id = generated["plan_id"]
        self.client.post(f"/api/v1/budgets/{plan_id}/activate", headers=headers)
        event = self.client.post("/api/v1/events", headers=headers, json={
            "goal_id": goal, "event_type": "unplanned_expense", "amount_minor": 50_000,
            "event_date": "2026-10-18", "safe_note": "synthetic shock",
        }).json()
        body = {"event_ids": [event["id"]], "current_savings_minor": 450_000,
                "next_open_week": "2026-10-19", "adjustment_mode": "balanced",
                "recovery_horizon_weeks": 4}
        preview = self.client.post(f"/api/v1/budgets/{plan_id}/replan-preview",
                                   headers=headers, json=body)
        self.assertEqual(preview.status_code, 200, preview.text)
        confirmed = self.client.post(f"/api/v1/budgets/{plan_id}/replans",
                                     headers=headers, json=body)
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        history = self.client.get("/api/v1/budgets", headers=headers).json()["items"]
        self.assertEqual(len(history), 2)
        current = next(item for item in history if item["is_current"])
        self.assertEqual(current["parent_plan_id"], plan_id)

    def test_13_key_api_performance_is_interactive(self):
        headers = self.register("performance-user"); self.import_demo(headers)
        start = time.perf_counter(); response = self.client.get("/api/v1/analytics/overview", headers=headers)
        elapsed = time.perf_counter() - start
        self.assertEqual(response.status_code, 200); self.assertLess(elapsed, 3.0)

    def test_14_execution_variance_is_computed_by_backend_policy(self):
        headers = self.register("execution-user"); self.import_demo(headers)
        goal = self.create_goal_and_constraints(headers, target=900_000, income=300_000,
                                                end="2026-12-31")
        # Move this synthetic goal into already-observed weeks so the execution
        # endpoint can compare plans and actuals without client-side arithmetic.
        with self.factory.begin() as session:
            from backend.app.models import BudgetConstraint, FinancialSnapshot, SavingGoal
            item = session.get(SavingGoal, goal); item.start_date = date(2026, 9, 7)
            session.get(FinancialSnapshot, item.baseline_snapshot_id).as_of_date = item.start_date
            for constraint in session.query(BudgetConstraint).filter_by(goal_id=goal):
                constraint.effective_from = item.start_date
        generated = self.client.post("/api/v1/budgets/generate", headers=headers,
                                     json={"goal_id": goal, "adjustment_mode": "balanced"}).json()
        self.client.post(f"/api/v1/budgets/{generated['plan_id']}/activate", headers=headers)
        response = self.client.get(f"/api/v1/budgets/{generated['plan_id']}/execution", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreater(len(response.json()["items"]), 0)
        self.assertIn(response.json()["items"][0]["status"],
                      {"normal", "minor_variance", "replan_recommended"})


if __name__ == "__main__":
    unittest.main()
