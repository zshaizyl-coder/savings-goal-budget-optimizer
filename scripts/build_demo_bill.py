"""Generate a one-year, entirely synthetic WeChat-format demo bill."""

from datetime import datetime, timedelta
from pathlib import Path

from openpyxl import Workbook


OUTPUT = Path(__file__).resolve().parents[1] / "data" / "sample" / "wechat_bill_demo.xlsx"
HEADER = ["交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)",
          "支付方式", "当前状态", "交易单号", "商户单号", "备注"]


def build() -> Path:
    workbook = Workbook(); sheet = workbook.active; sheet.title = "微信支付账单"
    sheet.append(["微信支付账单（完全模拟数据，仅用于Demo和自动化测试）"])
    for index in range(2, 18):
        sheet.append([f"模拟说明行 {index}"])
    sheet.append(HEADER)
    start = datetime(2025, 10, 6, 8, 0)
    templates = (
        (0, 0, "模拟早餐餐厅", "早餐", "25.00"),
        (1, 10, "模拟生活超市", "日用品购物", "52.30"),
        (2, 8, "模拟公共交通", "公交乘车", "4.00"),
        (4, 4, "模拟咖啡店", "咖啡饮品", "18.00"),
        (5, 11, "模拟电影院", "电影票", "45.00"),
        (6, 9, "个人二维码", "普通二维码付款", "16.00"),
    )
    serial = 1
    for week in range(52):
        week_start = start + timedelta(days=7 * week)
        for day, hour, merchant, product, amount in templates:
            occurred = week_start + timedelta(days=day, hours=hour)
            sheet.append([
                occurred.strftime("%Y-%m-%d %H:%M:%S"),
                "扫二维码付款" if merchant == "个人二维码" else "商户消费",
                merchant, product, "支出", amount, "零钱", "支付成功",
                f"DEMO-T{serial:05d}", f"DEMO-M{serial:05d}", "/",
            ])
            serial += 1
        if week % 4 == 0:
            occurred = week_start + timedelta(days=3, hours=12)
            sheet.append([occurred.strftime("%Y-%m-%d %H:%M:%S"), "商户消费",
                          "模拟物业服务", "物业费", "支出", "120.00", "银行卡",
                          "支付成功", f"DEMO-T{serial:05d}", f"DEMO-M{serial:05d}", "/"])
            serial += 1
        if week % 13 == 0:
            occurred = week_start + timedelta(days=6, hours=13)
            sheet.append([occurred.strftime("%Y-%m-%d %H:%M:%S"), "商户消费-退款",
                          "模拟生活超市", "购物退款", "收入", "10.00", "零钱",
                          "已退款", f"DEMO-T{serial:05d}", f"DEMO-M{serial:05d}", "/"])
            serial += 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(OUTPUT)
    return OUTPUT


if __name__ == "__main__":
    print(build())
