from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import sessionmaker

from backend.app.models import ImportBatch, Transaction, TransactionLink
from backend.app.parsers.wechat import WechatBillParser
from backend.app.pipeline.category_rules import classify_text
from backend.app.pipeline.consumption import signed_consumption_minor
from backend.app.pipeline.money import MoneyParseError, parse_cny_to_minor
from backend.app.pipeline.time_utils import business_month, business_week_start, parse_source_datetime
from backend.app.pipeline.wechat_rules import normalize_direction, normalize_source_type
from backend.app.services.import_wechat import WechatImportService


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample" / "wechat_bill_sample.xlsx"
OVERLAP = ROOT / "data" / "sample" / "wechat_bill_overlap.xlsx"


def migrated_session(database: Path):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


class MoneyAndTimeTests(unittest.TestCase):
    def test_decimal_amounts_convert_exactly_to_minor_units(self):
        cases = {"0.01": 1, "1.00": 100, "10": 1000, "10.10": 1010, "9999.99": 999999, "¥ 12.34": 1234}
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(parse_cny_to_minor(source), expected)

    def test_empty_and_invalid_amounts_fail(self):
        for value in (None, "", "abc", "12.345", "-1.00", "￥x"):
            with self.subTest(value=value), self.assertRaises(MoneyParseError):
                parse_cny_to_minor(value)

    def test_time_is_utc_and_business_week_is_monday(self):
        value = parse_source_datetime("2025-12-31 23:30:00")
        self.assertEqual(value.tzinfo, timezone.utc)
        self.assertEqual(business_week_start(value).isoformat(), "2025-12-29")
        self.assertEqual(business_month(value), "2025-12")
        monday = parse_source_datetime("2026-01-05 00:00:00")
        sunday = parse_source_datetime("2026-01-11 23:59:59")
        self.assertEqual(business_week_start(monday), business_week_start(sunday))

    def test_net_consumption_subtracts_refunds_not_income(self):
        expense = SimpleNamespace(amount_minor=10000, flow_type="consumption", direction="outflow")
        refund = SimpleNamespace(amount_minor=3000, flow_type="expense_refund", direction="inflow")
        income = SimpleNamespace(amount_minor=5000, flow_type="income", direction="inflow")
        self.assertEqual(sum(map(signed_consumption_minor, (expense, refund, income))), 7000)


class ParserAndRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = WechatBillParser().parse(SAMPLE)

    def test_header_detection_matches_real_export_shape(self):
        self.assertEqual(self.result.header_excel_row, 18)
        self.assertEqual(self.result.total_candidate_rows, 13)

    def test_normal_rows_are_source_dtos(self):
        self.assertEqual(len(self.result.rows), 13)
        self.assertEqual(self.result.rows[0].source_transaction_id, "SIM-T001")
        self.assertEqual(self.result.rows[0].source_amount, "12.34")

    def test_direction_and_flow_are_separate(self):
        direction = normalize_direction("支出")
        source_code, flow, warning = normalize_source_type("转账", direction)
        self.assertEqual(direction.value, "outflow")
        self.assertEqual(flow.value, "transfer")
        self.assertEqual(source_code, "transfer")
        self.assertFalse(warning)

    def test_refund_and_unknown_rules(self):
        refund = normalize_source_type("模拟商户-退款", normalize_direction("收入"))
        unknown = normalize_source_type("无法识别", normalize_direction("支出"))
        self.assertEqual(refund[1].value, "expense_refund")
        self.assertEqual(unknown[1].value, "other")
        self.assertTrue(unknown[2])

    def test_category_mapping_is_centralized_and_conservative(self):
        self.assertEqual(classify_text("merchant_consumption", "模拟餐厅", "晚餐")[0], "food")
        self.assertEqual(classify_text("merchant_consumption", "模拟对象", "无可靠信息")[0], "other")

    def test_unreadable_workbook_is_fatal_without_source_content(self):
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as invalid:
            invalid.write(b"not-an-excel-file")
            invalid.flush()
            with self.assertRaisesRegex(ValueError, "workbook_unreadable"):
                WechatBillParser().parse(invalid.name)


class MigrationAndImportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Path(self.temp_dir.name) / "test.sqlite3"
        self.engine, self.Session = migrated_session(self.db)

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_empty_database_migrates_and_can_round_trip(self):
        expected = {"users", "import_batches", "categories", "category_mappings", "transactions", "transaction_links", "alembic_version"}
        self.assertTrue(expected <= set(inspect(self.engine).get_table_names()))
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", f"sqlite:///{self.db.as_posix()}")
        command.downgrade(config, "base")
        self.assertNotIn("transactions", inspect(self.engine).get_table_names())
        command.upgrade(config, "head")
        self.assertIn("transactions", inspect(self.engine).get_table_names())

    def _import(self, path: Path):
        with self.Session.begin() as session:
            return WechatImportService(session, b"unit-test-secret").import_file(path)

    def test_single_row_error_does_not_abort_and_batch_stats_are_correct(self):
        report = self._import(SAMPLE)
        self.assertEqual(report.total_rows, 13)
        self.assertEqual(report.parsed_rows, 12)
        self.assertEqual(report.inserted_rows, 11)
        self.assertEqual(report.duplicate_rows, 1)
        self.assertEqual(report.failed_rows, 1)
        self.assertEqual(report.refund_rows, 2)
        self.assertEqual(report.linked_refunds, 2)
        with self.Session() as session:
            batch = session.get(ImportBatch, report.batch_id)
            self.assertEqual(batch.inserted_rows, 11)
            self.assertEqual(session.scalar(select(func.count()).select_from(Transaction)), 11)

    def test_same_file_import_is_idempotent(self):
        first = self._import(SAMPLE)
        second = self._import(SAMPLE)
        third = self._import(SAMPLE)
        self.assertEqual(first.inserted_rows, 11)
        self.assertEqual(second.inserted_rows, 0)
        self.assertEqual(third.inserted_rows, 0)
        self.assertEqual(second.status, "duplicate_file")
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Transaction)), 11)
            self.assertEqual(session.scalar(select(func.count()).select_from(ImportBatch)), 3)

    def test_overlapping_files_skip_exact_records_but_keep_possible_duplicate(self):
        self._import(SAMPLE)
        report = self._import(OVERLAP)
        self.assertEqual(report.duplicate_rows, 2)
        self.assertEqual(report.inserted_rows, 2)
        self.assertEqual(report.possible_duplicate_links, 1)
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Transaction)), 13)
            link = session.scalar(select(TransactionLink).where(TransactionLink.link_type == "possible_duplicate"))
            self.assertEqual(link.resolution_status, "pending")

    def test_refund_links_and_inherits_original_category(self):
        self._import(SAMPLE)
        with self.Session() as session:
            refunds = session.scalars(select(Transaction).where(Transaction.flow_type == "expense_refund")).all()
            self.assertEqual(len(refunds), 2)
            self.assertTrue(all(item.related_transaction_id is not None for item in refunds))
            for refund in refunds:
                original = session.get(Transaction, refund.related_transaction_id)
                self.assertEqual(refund.suggested_category_id, original.suggested_category_id)

    def test_unknown_type_is_kept_and_warned(self):
        report = self._import(SAMPLE)
        self.assertEqual(report.unknown_flow_rows, 1)
        self.assertTrue(any(issue["code"] == "flow_type_unknown" for issue in report.issues))

    def test_source_privacy_fields_are_not_persisted(self):
        columns = {column["name"] for column in inspect(self.engine).get_columns("transactions")}
        forbidden = {"merchant", "description", "transaction_id", "merchant_order_id", "note", "payment_method"}
        self.assertFalse(columns & forbidden)


class PrivacyIndependenceTests(unittest.TestCase):
    def test_tests_do_not_reference_real_bill_or_attachment_path(self):
        test_text = Path(__file__).read_text(encoding="utf-8")
        self.assertNotIn("codex-" + "remote-attachments", test_text)
        self.assertNotIn("20251001" + "-20261001", test_text)


if __name__ == "__main__":
    unittest.main()
