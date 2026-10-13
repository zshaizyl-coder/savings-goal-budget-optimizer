from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from backend.app.classification.features import (
    amount_bucket,
    privacy_minimized_features,
    sanitize_source_text,
    time_bucket,
)
from backend.app.classification.openai_provider import (
    ClassificationProviderError,
    OpenAIResponsesClassificationProvider,
)
from backend.app.classification.types import ClassificationContext, ClassificationResult
from backend.app.models import (
    Category,
    ClassificationSuggestion,
    ImportBatch,
    Transaction,
    UserCategoryRule,
)
from backend.app.pipeline.category_rules import CATEGORY_RULE_VERSION, classify_text
from backend.app.repositories.classification import UserCategoryRuleRepository
from backend.app.services.hybrid_classification import HybridClassificationService
from backend.app.services.import_wechat import WechatImportService


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample" / "wechat_bill_sample.xlsx"


def migrated_session(database: Path):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


class SemanticRuleTests(unittest.TestCase):
    def test_v2_rules_cover_generic_real_world_semantics(self):
        cases = {
            "某某奶茶店": "food",
            "机场航班机票": "transport",
            "社区药房": "health",
            "品牌免税商城": "shopping",
            "共享充电宝": "life_services",
            "手机话费缴费": "communications",
            "电影院票务": "entertainment",
        }
        self.assertEqual(CATEGORY_RULE_VERSION, "wechat-category-v3")
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(classify_text("merchant_consumption", text)[0], expected)

    def test_ambiguous_platform_and_personal_qr_stay_other(self):
        self.assertEqual(classify_text("merchant_consumption", "综合平台订单")[0], "other")
        self.assertEqual(classify_text("qr_payment", "张某", "付款")[0], "other")


class PrivacyFeatureTests(unittest.TestCase):
    def test_source_text_is_sanitized_before_cloud_use(self):
        value = sanitize_source_text("店铺 13800138000 订单123456789 user@example.com")
        self.assertNotIn("13800138000", value)
        self.assertNotIn("123456789", value)
        self.assertNotIn("user@example.com", value)
        self.assertIn("[PHONE]", value)

    def test_amount_and_time_are_coarse_features(self):
        value = datetime(2026, 1, 5, 10, 30, tzinfo=timezone.utc)  # 18:30 Shanghai
        self.assertEqual(amount_bucket(12345), "100_to_500_cny")
        self.assertEqual(time_bucket(value), "dinner_window")

    def test_exact_amount_and_timestamp_are_not_exported(self):
        context = ClassificationContext(
            transaction_id=1,
            source_type_code="merchant_consumption",
            occurred_at_utc=datetime(2026, 1, 5, 10, 30, tzinfo=timezone.utc),
            amount_minor=12345,
            counterparty_hint="模拟餐饮店订单99887766",
            description_hint="消费",
        )
        serialized = json.dumps(privacy_minimized_features(context), ensure_ascii=False)
        self.assertNotIn("12345", serialized)
        self.assertNotIn("2026-01-05", serialized)
        self.assertNotIn("99887766", serialized)


class OpenAIProviderTests(unittest.TestCase):
    def _context(
        self,
        source_type: str = "merchant_consumption",
        *,
        counterparty_hint: str = "模拟咖啡店",
        description_hint: str = "饮品",
    ) -> ClassificationContext:
        return ClassificationContext(
            transaction_id=1,
            source_type_code=source_type,
            occurred_at_utc=datetime(2026, 1, 5, 10, 30, tzinfo=timezone.utc),
            amount_minor=2500,
            counterparty_hint=counterparty_hint,
            description_hint=description_hint,
            recurring_count=3,
        )

    def test_structured_output_is_used_and_result_requires_review(self):
        captured = {}

        def fake_transport(payload):
            captured.update(payload)
            return {"output_text": json.dumps({
                "category_code": "food",
                "confidence_score": 91,
                "evidence_codes": ["counterparty_semantics", "time_weak_signal"],
            })}

        provider = OpenAIResponsesClassificationProvider(
            api_key="test-key", model_name="test-model", transport=fake_transport,
        )
        result = provider.classify(self._context())
        self.assertEqual(result.category_code, "food")
        self.assertEqual(result.confidence_bps, 9100)
        self.assertTrue(result.requires_review)
        self.assertFalse(captured["store"])
        self.assertTrue(captured["text"]["format"]["strict"])

    def test_personal_qr_is_not_sent_to_cloud_by_default(self):
        called = False

        def fake_transport(_payload):
            nonlocal called
            called = True
            return {}

        provider = OpenAIResponsesClassificationProvider(
            api_key="test-key", model_name="test-model", transport=fake_transport,
        )
        with self.assertRaisesRegex(ClassificationProviderError, "context_not_cloud_eligible"):
            provider.classify(self._context(
                "qr_payment",
                counterparty_hint="张某",
                description_hint="二维码收款",
            ))
        self.assertFalse(called)

    def test_time_alone_cannot_assign_food(self):
        provider = OpenAIResponsesClassificationProvider(
            api_key="test-key",
            model_name="test-model",
            transport=lambda _payload: {"output_text": json.dumps({
                "category_code": "food",
                "confidence_score": 70,
                "evidence_codes": ["time_weak_signal"],
            })},
        )
        with self.assertRaisesRegex(ClassificationProviderError, "weak_evidence"):
            provider.classify(self._context())


class HybridPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Path(self.temp_dir.name) / "test.sqlite3"
        self.engine, self.Session = migrated_session(self.db)
        with self.Session.begin() as session:
            WechatImportService(session, b"unit-test-secret").import_file(SAMPLE)

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _add_other_pair(self, session):
        batch = session.scalar(select(ImportBatch))
        other = session.scalar(select(Category).where(Category.code == "other"))
        first = Transaction(
            user_id=1, import_batch_id=batch.id, source="wechat",
            source_record_fingerprint="a" * 64,
            occurred_at_utc=datetime(2026, 2, 1, tzinfo=timezone.utc),
            amount_minor=1000, currency="CNY", direction="outflow",
            flow_type="consumption", normalized_status="success",
            source_type_code="merchant_consumption", original_category="商户消费",
            suggested_category_id=other.id, classification_version=CATEGORY_RULE_VERSION,
            counterparty_fingerprint="c" * 64, is_abnormal=False,
        )
        second = Transaction(
            user_id=1, import_batch_id=batch.id, source="wechat",
            source_record_fingerprint="b" * 64,
            occurred_at_utc=datetime(2026, 2, 2, tzinfo=timezone.utc),
            amount_minor=2000, currency="CNY", direction="outflow",
            flow_type="consumption", normalized_status="success",
            source_type_code="merchant_consumption", original_category="商户消费",
            suggested_category_id=other.id, classification_version=CATEGORY_RULE_VERSION,
            counterparty_fingerprint="c" * 64, is_abnormal=False,
        )
        session.add_all((first, second))
        session.flush()
        return first, second

    def test_migration_adds_only_pseudonymous_classification_tables(self):
        tables = set(inspect(self.engine).get_table_names())
        self.assertIn("user_category_rules", tables)
        self.assertIn("classification_suggestions", tables)
        columns = {item["name"] for item in inspect(self.engine).get_columns("classification_suggestions")}
        self.assertFalse({"prompt", "response", "merchant", "description"} & columns)

    def test_manual_counterparty_confirmation_updates_group_and_rule(self):
        with self.Session.begin() as session:
            first, second = self._add_other_pair(session)
            count = HybridClassificationService(session).confirm_manual(
                user_id=1, transaction_id=first.id, category_code="shopping",
                scope="counterparty",
            )
            self.assertEqual(count, 2)
            shopping = session.scalar(select(Category).where(Category.code == "shopping"))
            self.assertEqual(first.user_category_id, shopping.id)
            self.assertEqual(second.user_category_id, shopping.id)
            rules = UserCategoryRuleRepository(session).active_category_ids(1, "wechat")
            self.assertEqual(rules["c" * 64], shopping.id)

    def test_model_suggestion_is_audited_but_only_applied_after_acceptance(self):
        with self.Session.begin() as session:
            first, _second = self._add_other_pair(session)
            service = HybridClassificationService(session)
            suggestion_id = service.record_model_suggestion(
                user_id=1, transaction_id=first.id,
                provider="fake", model_name="fake-model",
                result=ClassificationResult("food", 8800, ("counterparty_semantics",)),
            )
            self.assertIsNone(first.user_category_id)
            service.accept_suggestion(suggestion_id, scope="transaction")
            food = session.scalar(select(Category).where(Category.code == "food"))
            self.assertEqual(first.user_category_id, food.id)
            suggestion = session.get(ClassificationSuggestion, suggestion_id)
            self.assertEqual(suggestion.status, "accepted")

    def test_confirmed_rule_is_available_for_future_imports(self):
        with self.Session.begin() as session:
            first, _second = self._add_other_pair(session)
            HybridClassificationService(session).confirm_manual(
                user_id=1, transaction_id=first.id, category_code="shopping",
                scope="counterparty",
            )
        with self.Session() as session:
            rules = session.scalars(select(UserCategoryRule)).all()
            self.assertEqual(len(rules), 1)
            self.assertTrue(rules[0].is_active)


if __name__ == "__main__":
    unittest.main()
