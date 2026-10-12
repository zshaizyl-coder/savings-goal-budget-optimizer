import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = "data/sample";
const headers = ["交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)", "支付方式", "当前状态", "交易单号", "商户单号", "备注"];
const instructions = Array.from({ length: 17 }, (_, index) => [index === 0 ? "微信支付账单（完全模拟数据，仅用于自动化测试）" : `模拟说明行 ${index + 1}`]);

const primaryRows = [
  ["2026-01-05 08:10:00", "商户消费", "模拟早餐店", "早餐", "支出", "12.34", "零钱", "支付成功", "SIM-T001", "SIM-M001", "/"],
  ["2026-01-05 08:10:00", "商户消费", "模拟早餐店", "早餐", "支出", "12.34", "零钱", "支付成功", "SIM-T001", "SIM-M001", "重复行"],
  ["2026-01-06 12:00:00", "二维码收款", "模拟付款人", "收款", "收入", "50.00", "零钱", "已收钱", "SIM-T002", "/", "/"],
  ["2026-01-07 20:00:00", "商户消费", "模拟书店", "教材", "支出", "100.00", "银行卡", "已全额退款", "SIM-T003", "SIM-M003", "/"],
  ["2026-01-08 09:00:00", "模拟书店-退款", "模拟书店", "退款", "收入", "100.00", "银行卡", "已退款¥100.00", "SIM-T004", "SIM-M004", "/"],
  ["2026-01-09 10:00:00", "商户消费", "模拟餐厅", "聚餐", "支出", "100.00", "零钱", "已退款(¥30.00)", "SIM-T011", "SIM-M011", "/"],
  ["2026-01-10 10:00:00", "模拟餐厅-退款", "模拟餐厅", "部分退款", "收入", "30.00", "零钱", "已退款¥30.00", "SIM-T012", "SIM-M012", "/"],
  ["2026-01-11 11:00:00", "转账", "模拟朋友", "转账", "支出", "200.00", "零钱", "对方已收钱", "SIM-T005", "/", "/"],
  ["2026-01-12 12:00:00", "零钱充值", "模拟账户", "充值", "/", "500.00", "银行卡", "充值完成", "SIM-T006", "/", "/"],
  ["2026-01-13 13:00:00", "零钱提现", "模拟账户", "提现", "/", "300.00", "零钱", "提现已到账", "SIM-T007", "/", "/"],
  ["2026-01-14 14:00:00", "微信红包", "模拟朋友", "红包", "支出", "8.88", "零钱", "已存入零钱", "SIM-T008", "/", "/"],
  ["2026-01-15 15:00:00", "未识别类型", "模拟对象", "未知", "支出", "1.00", "零钱", "未知状态", "SIM-T009", "/", "/"],
  ["2026-01-16 16:00:00", "商户消费", "模拟超市", "日用品", "支出", "非法金额", "零钱", "支付成功", "SIM-T010", "/", "/"],
];

const overlapRows = [
  primaryRows[3],
  primaryRows[4],
  ["2026-01-05 08:10:00", "商户消费", "模拟早餐店", "早餐", "支出", "12.34", "零钱", "支付成功", "SIM-T020", "SIM-M020", "同时间金额但不同真实交易"],
  ["2026-06-30 18:30:00", "扫二维码付款", "模拟便利店", "购物", "支出", "25.60", "零钱", "支付成功", "SIM-T021", "SIM-M021", "/"],
];

async function build(path, rows) {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("Sheet1");
  sheet.getRange("A1:K17").values = instructions.map((row) => [...row, ...Array(10).fill(null)]);
  sheet.getRange("A18:K18").values = [headers];
  sheet.getRange(`A19:K${18 + rows.length}`).values = rows;
  sheet.getRange("A1:K18").format.font = { name: "Microsoft YaHei", size: 10 };
  sheet.getRange("A18:K18").format = { fill: "#E2F0D9", font: { name: "Microsoft YaHei", bold: true, color: "#1F1F1F" } };
  sheet.getRange("A:K").format.columnWidth = 18;
  sheet.getRange("A:A").format.columnWidth = 22;
  sheet.freezePanes.freezeRows(18);
  workbook.recalculate();
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(path);
}

await fs.mkdir(outputDir, { recursive: true });
await build(`${outputDir}/wechat_bill_sample.xlsx`, primaryRows);
await build(`${outputDir}/wechat_bill_overlap.xlsx`, overlapRows);
