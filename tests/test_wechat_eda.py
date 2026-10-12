import importlib.util
import sys
import unittest
from pathlib import Path

import pandas as pd


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "wechat_eda.py"
SPEC = importlib.util.spec_from_file_location("wechat_eda", MODULE_PATH)
wechat_eda = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = wechat_eda
SPEC.loader.exec_module(wechat_eda)


def sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "交易时间": "2026-01-05 12:00:00",
                "交易类型": "商户消费",
                "交易对方": "模拟餐饮商户",
                "商品": "午餐",
                "收/支": "支出",
                "金额(元)": 30.0,
                "支付方式": "模拟账户",
                "当前状态": "支付成功",
                "交易单号": "T1",
                "商户单号": "M1",
                "备注": "",
            },
            {
                "交易时间": "2026-01-06 12:00:00",
                "交易类型": "模拟商户-退款",
                "交易对方": "模拟餐饮商户",
                "商品": "退款",
                "收/支": "收入",
                "金额(元)": 10.0,
                "支付方式": "模拟账户",
                "当前状态": "已退款¥10.00",
                "交易单号": "T2",
                "商户单号": "M2",
                "备注": "",
            },
            {
                "交易时间": "2026-01-07 12:00:00",
                "交易类型": "零钱充值",
                "交易对方": "模拟账户",
                "商品": "充值",
                "收/支": "/",
                "金额(元)": 100.0,
                "支付方式": "模拟账户",
                "当前状态": "充值完成",
                "交易单号": "T3",
                "商户单号": "M3",
                "备注": "",
            },
            {
                "交易时间": "2026-01-08 12:00:00",
                "交易类型": "转账",
                "交易对方": "模拟对象",
                "商品": "转账",
                "收/支": "支出",
                "金额(元)": 50.0,
                "支付方式": "模拟账户",
                "当前状态": "对方已收钱",
                "交易单号": "T4",
                "商户单号": "M4",
                "备注": "",
            },
        ]
    )


class WeChatEdaTests(unittest.TestCase):
    def test_prepare_transactions_separates_internal_and_transfer_flows(self) -> None:
        data, diagnostics = wechat_eda.prepare_transactions(sample_frame())
        self.assertEqual(diagnostics["invalid_rows"], 0)
        self.assertEqual(data["消费毛额"].sum(), 30.0)
        self.assertEqual(data["退款冲减"].sum(), 10.0)
        self.assertEqual(data["消费净额"].sum(), 20.0)
        self.assertEqual(
            set(data["资金流分类"]),
            {"核心消费支出", "消费退款", "零钱充值/提现", "转账"},
        )

    def test_category_is_rule_based_and_generic(self) -> None:
        self.assertEqual(wechat_eda.classify_category("模拟餐饮 午餐"), "餐饮")
        self.assertEqual(wechat_eda.classify_category("无法识别的消费"), "其他消费")

    def test_complete_week_index_excludes_partial_boundary_weeks(self) -> None:
        result = wechat_eda.complete_week_index(
            pd.Timestamp("2025-10-01"), pd.Timestamp("2026-10-01")
        )
        self.assertEqual(len(result), 51)
        self.assertEqual(result[0], pd.Timestamp("2025-10-06"))
        self.assertEqual(result[-1], pd.Timestamp("2026-09-21"))


if __name__ == "__main__":
    unittest.main()
