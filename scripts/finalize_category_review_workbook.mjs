import fs from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { FileBlob, SpreadsheetFile } = require("@oai/artifact-tool");

const workbookPath = process.argv[2] || "data/private/category-review-stage55.xlsx";
const firstRow = Number(process.argv[3] || 7);
const lastRow = Number(process.argv[4] || 56);

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
const sheet = workbook.worksheets.getItem("审核队列");
const labels = sheet.getRange(`U${firstRow}:U${lastRow}`).values;
const statuses = labels.map(([value]) => {
  const label = String(value || "").trim();
  if (!label) return ["pending"];
  return [label === "other" ? "keep_other" : "confirmed"];
});
sheet.getRange(`V${firstRow}:V${lastRow}`).values = statuses;
workbook.recalculate();

const check = await workbook.inspect({
  kind: "table",
  range: `审核队列!U${firstRow}:V${lastRow}`,
  include: "values,formulas",
  tableMaxRows: lastRow - firstRow + 1,
  tableMaxCols: 2,
});
console.log(check.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

const preview = await workbook.render({
  sheetName: "审核队列",
  range: `T4:W${Math.min(lastRow, 20)}`,
  scale: 1.5,
  format: "png",
});
await fs.writeFile("data/private/category-review-status-preview.png", new Uint8Array(await preview.arrayBuffer()));

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(workbookPath);
