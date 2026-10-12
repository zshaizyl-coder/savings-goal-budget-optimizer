import fs from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { SpreadsheetFile, Workbook } = require("@oai/artifact-tool");

const inputPath = process.argv[2] || "data/private/category-review-stage55.json";
const outputPath = process.argv[3] || "data/private/category-review-stage55.xlsx";
const previewDir = process.argv[4] || "data/private/category-review-previews";
const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
const rows = payload.rows;
const categories = payload.category_codes;

const workbook = Workbook.create();
const queue = workbook.worksheets.add("审核队列");
const guide = workbook.worksheets.add("说明与类别");
queue.showGridLines = false;
guide.showGridLines = false;
queue.tabColor = "#1F4E78";
guide.tabColor = "#7F8C8D";

queue.getRange("A2:X2").merge();
queue.getRange("A2").values = [["阶段5.5 剩余OTHER匿名商户组审核队列"]];
queue.getRange("A2").format = {
  font: { name: "Arial", size: 14, bold: true, color: "#1F2937" },
  verticalAlignment: "center",
};
queue.getRange("A3:X3").format.borders = { bottom: { style: "thin", color: "#9CA3AF" } };
queue.getRange("A4:H5").values = [
  ["待审核商户组", rows.length, "验证样本组", rows.filter(row => row.in_validation_sample).length, "金额前20组", rows.filter(row => row.sample_reason === "top_amount").length, "人工已确认", null],
  ["说明", "模型建议不是标签；时间段只能作为弱证据；无法判断时选择 other。", null, null, null, null, "未确认", null],
];
queue.getRange("H4").formulas = [[`=COUNTIF(U7:U${rows.length + 6},"?*")`]];
queue.getRange("H5").formulas = [["=B4-H4"]];
queue.getRange("A4:H4").format = {
  fill: "#E8F0F8",
  font: { name: "Arial", size: 10, bold: true, color: "#1F2937" },
};
queue.getRange("A5:H5").format = {
  font: { name: "Arial", size: 10, color: "#374151" },
  wrapText: true,
};
queue.getRange("B5:F5").merge();

const headers = [
  "验证样本", "优先级", "抽样原因", "匿名组ID", "来源类型", "交易笔数",
  "总金额(元)", "平均金额(元)", "最小金额(元)", "最大金额(元)",
  "首次日期", "末次日期", "周末占比", "主要时段", "商户短文本（私有）",
  "商品短文本（私有）", "可进入远程候选", "规则候选", "模型候选",
  "模型置信度", "人工确认类别", "审核状态", "审核备注", "代表交易ID",
];
queue.getRange("A6:X6").values = [headers];
queue.getRange("A6:X6").format = {
  fill: "#1F4E78",
  font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { insideVertical: { style: "thin", color: "#FFFFFF" } },
};

const bucketLabels = {
  breakfast_window: "早餐时段",
  lunch_window: "午餐时段",
  dinner_window: "晚餐时段",
  late_evening: "夜间",
  other_time: "其他时段",
};
const reasonLabels = {
  top_amount: "金额前20",
  high_frequency: "高频代表",
  low_amount: "低金额代表",
  random: "随机样本",
  backlog: "后续待审",
};
const data = rows.map(row => [
  row.in_validation_sample ? "是" : "否",
  row.sample_priority,
  reasonLabels[row.sample_reason] || row.sample_reason,
  row.review_group_id,
  row.source_types,
  row.transaction_count,
  row.total_amount_minor / 100,
  row.average_amount_minor / 100,
  row.minimum_amount_minor / 100,
  row.maximum_amount_minor / 100,
  new Date(row.first_transaction_at),
  new Date(row.last_transaction_at),
  row.weekend_ratio,
  bucketLabels[row.dominant_time_bucket] || row.dominant_time_bucket,
  row.counterparty_hint,
  row.description_hint,
  row.cloud_eligible ? "是" : "否",
  row.rule_candidate,
  row.model_candidate,
  row.model_confidence === "" ? null : row.model_confidence,
  row.confirmed_category,
  row.review_status,
  row.review_notes,
  row.representative_transaction_id,
]);
const lastRow = rows.length + 6;
queue.getRange(`A7:X${lastRow}`).values = data;
queue.getRange(`A7:X${lastRow}`).format = {
  font: { name: "Arial", size: 10, color: "#1F2937" },
  verticalAlignment: "center",
  borders: { insideHorizontal: { style: "thin", color: "#E5E7EB" } },
};
queue.getRange(`G7:J${lastRow}`).format.numberFormat = "¥#,##0.00";
queue.getRange(`K7:L${lastRow}`).format.numberFormat = "yyyy-mm-dd";
queue.getRange(`M7:M${lastRow}`).format.numberFormat = "0.0%";
queue.getRange(`T7:T${lastRow}`).format.numberFormat = "0.0%";
queue.getRange(`A7:N${lastRow}`).format.horizontalAlignment = "center";
queue.getRange(`Q7:X${lastRow}`).format.horizontalAlignment = "center";
queue.getRange(`O7:P${lastRow}`).format.wrapText = false;
queue.getRange(`U7:W${lastRow}`).format.fill = "#FFF2CC";
queue.getRange(`A7:X${6 + rows.filter(row => row.in_validation_sample).length}`).format.fill = "#F7FAFC";
queue.getRange(`U7:W${lastRow}`).format.fill = "#FFF2CC";
queue.getRange(`U7:U${lastRow}`).dataValidation = {
  rule: { type: "list", formula1: `='说明与类别'!$A$6:$A$${categories.length + 5}` },
};
queue.getRange(`V7:V${lastRow}`).dataValidation = {
  rule: { type: "list", values: ["pending", "confirmed", "keep_other", "rejected_model"] },
};
queue.freezePanes.freezeRows(6);
queue.freezePanes.freezeColumns(4);
queue.tables.add(`A6:X${lastRow}`, true, "CategoryReviewQueue").style = "TableStyleMedium2";

const widths = [10, 8, 14, 15, 22, 10, 13, 13, 13, 13, 13, 13, 10, 12, 28, 32, 15, 12, 12, 12, 14, 14, 28, 14];
widths.forEach((width, index) => queue.getRangeByIndexes(0, index, lastRow, 1).format.columnWidth = width);
queue.getRange("A2:X2").format.rowHeight = 26;
queue.getRange("A6:X6").format.rowHeight = 32;

guide.getRange("A2:D2").merge();
guide.getRange("A2").values = [["审核说明与统一类别"]];
guide.getRange("A2").format = { font: { name: "Arial", size: 14, bold: true, color: "#1F2937" } };
guide.getRange("A3:D3").format.borders = { bottom: { style: "thin", color: "#9CA3AF" } };
guide.getRange("A5:B5").values = [["类别代码", "含义"]];
const categoryNames = {
  food: "餐饮", shopping: "购物", transport: "交通", entertainment: "娱乐",
  housing: "居住", life_services: "生活服务", health: "医疗健康",
  education: "教育", communications: "通讯与数字服务",
  human_relations: "人情往来", other: "保留为其他/无法可靠判断",
};
guide.getRange(`A6:B${categories.length + 5}`).values = categories.map(code => [code, categoryNames[code] || code]);
guide.getRange("A5:B5").format = {
  fill: "#1F4E78", font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
};
guide.getRange(`A6:B${categories.length + 5}`).format = {
  font: { name: "Arial", size: 10, color: "#1F2937" },
  borders: { insideHorizontal: { style: "thin", color: "#E5E7EB" } },
};
guide.getRange("D5:E5").values = [["审核规则", "说明"]];
guide.getRange("D6:E11").values = [
  ["标签来源", "只有人工确认类别是真实标签；模型候选不是标签。"],
  ["时间证据", "早餐、午餐、晚餐时段只能作为弱证据，不能单独判定餐饮。"],
  ["个人二维码", "姓名、个人二维码或语义不足时不要强制分类，选择 other。"],
  ["批量规则", "确认商户组后，可将同一匿名商户指纹应用到历史交易和未来导入。"],
  ["验证样本", "优先完成金额前20、高频、低金额和随机样本，共50组。"],
  ["隐私", "本文件只能保存在 data/private/，不得提交Git或上传公开平台。"],
];
guide.getRange("D5:E5").format = {
  fill: "#1F4E78", font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
};
guide.getRange("D6:E11").format = {
  font: { name: "Arial", size: 10, color: "#1F2937" },
  verticalAlignment: "top", wrapText: true,
  borders: { insideHorizontal: { style: "thin", color: "#E5E7EB" } },
};
guide.getRange("A:A").format.columnWidth = 24;
guide.getRange("B:B").format.columnWidth = 30;
guide.getRange("C:C").format.columnWidth = 4;
guide.getRange("D:D").format.columnWidth = 18;
guide.getRange("E:E").format.columnWidth = 58;

workbook.recalculate();
const summaryCheck = await workbook.inspect({
  kind: "table", range: "审核队列!A2:H7", include: "values,formulas", tableMaxRows: 8, tableMaxCols: 8,
});
console.log(summaryCheck.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

await fs.mkdir(previewDir, { recursive: true });
for (const [sheetName, range, fileName] of [
  ["审核队列", "A1:X28", "review-queue.png"],
  ["说明与类别", "A1:E16", "guide.png"],
]) {
  const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(`${previewDir}/${fileName}`, new Uint8Array(await preview.arrayBuffer()));
}

await fs.mkdir(outputPath.substring(0, outputPath.lastIndexOf("/")) || ".", { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(JSON.stringify({ outputPath, rows: rows.length, previews: previewDir }));
