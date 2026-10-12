from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backend.app.pipeline.issues import DataIssue, FatalParseError, IssueSeverity


PARSER_VERSION = "wechat-xlsx-v1"
REQUIRED_COLUMNS = (
    "交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)",
    "支付方式", "当前状态", "交易单号", "商户单号", "备注",
)


@dataclass(frozen=True)
class WechatSourceTransaction:
    source_row_number: int
    source_transaction_time: object
    source_transaction_type: str
    source_counterparty: str
    source_description: str
    source_direction: str
    source_amount: object
    source_payment_method: str
    source_status: str
    source_transaction_id: str
    source_merchant_order_id: str
    source_note: str


@dataclass(frozen=True)
class WechatParseResult:
    rows: tuple[WechatSourceTransaction, ...]
    issues: tuple[DataIssue, ...]
    sheet_name: str
    header_excel_row: int
    total_candidate_rows: int


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


class WechatBillParser:
    """Parse a WeChat-exported workbook into source-layer DTOs only."""

    def __init__(self, header_scan_limit: int = 50):
        self.header_scan_limit = header_scan_limit

    def parse(self, path: str | Path) -> WechatParseResult:
        try:
            with pd.ExcelFile(path) as workbook:
                if not workbook.sheet_names:
                    raise FatalParseError(DataIssue(IssueSeverity.FATAL, "workbook_has_no_sheet"))
                sheet_name = workbook.sheet_names[0]
            raw = pd.read_excel(path, sheet_name=sheet_name, header=None, dtype=object)
        except FatalParseError:
            raise
        except Exception as exc:
            raise FatalParseError(DataIssue(IssueSeverity.FATAL, "workbook_unreadable")) from exc
        header_index = self._find_header(raw)
        if header_index is None:
            raise FatalParseError(DataIssue(IssueSeverity.FATAL, "wechat_header_not_found"))
        try:
            frame = pd.read_excel(path, sheet_name=sheet_name, header=header_index, dtype=object)
        except Exception as exc:
            raise FatalParseError(DataIssue(IssueSeverity.FATAL, "worksheet_unreadable")) from exc
        frame.columns = [str(column).strip() for column in frame.columns]
        rows, issues = [], []
        for offset, record in frame.iterrows():
            excel_row = header_index + 2 + int(offset)
            values = [_text(record.get(column)) for column in REQUIRED_COLUMNS]
            if not any(values):
                continue
            if not _text(record.get("交易时间")) and not _text(record.get("金额(元)")):
                issues.append(DataIssue(IssueSeverity.WARNING, "non_transaction_row_skipped", excel_row))
                continue
            rows.append(WechatSourceTransaction(
                source_row_number=excel_row,
                source_transaction_time=record.get("交易时间"),
                source_transaction_type=_text(record.get("交易类型")),
                source_counterparty=_text(record.get("交易对方")),
                source_description=_text(record.get("商品")),
                source_direction=_text(record.get("收/支")),
                source_amount=record.get("金额(元)"),
                source_payment_method=_text(record.get("支付方式")),
                source_status=_text(record.get("当前状态")),
                source_transaction_id=_text(record.get("交易单号")),
                source_merchant_order_id=_text(record.get("商户单号")),
                source_note=_text(record.get("备注")),
            ))
        return WechatParseResult(tuple(rows), tuple(issues), sheet_name, header_index + 1, len(frame))

    def _find_header(self, frame: pd.DataFrame) -> int | None:
        required = set(REQUIRED_COLUMNS)
        for index, row in frame.head(self.header_scan_limit).iterrows():
            if required <= {_text(value) for value in row.tolist()}:
                return int(index)
        return None
