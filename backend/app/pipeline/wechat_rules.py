from __future__ import annotations

from backend.app.domain.enums import Direction, FlowType, NormalizedStatus


_DIRECTION = {"收入": Direction.INFLOW, "支出": Direction.OUTFLOW, "/": Direction.NEUTRAL}


def normalize_direction(value: str) -> Direction | None:
    return _DIRECTION.get(value.strip())


def normalize_source_type(value: str, direction: Direction) -> tuple[str, FlowType, bool]:
    value = value.strip()
    exact = {
        "商户消费": ("merchant_consumption", FlowType.CONSUMPTION),
        "扫二维码付款": ("qr_payment", FlowType.CONSUMPTION),
        "群收款": ("group_collection", FlowType.CONSUMPTION),
        "分分捐": ("donation", FlowType.CONSUMPTION),
        "转账": ("transfer", FlowType.TRANSFER),
        "微信红包": ("red_packet", FlowType.RED_PACKET),
        "微信红包（单发）": ("red_packet", FlowType.RED_PACKET),
        "零钱充值": ("balance_top_up", FlowType.BALANCE_TRANSFER),
        "零钱提现": ("balance_withdrawal", FlowType.BALANCE_TRANSFER),
        "二维码收款": ("qr_receipt", FlowType.INCOME_CANDIDATE),
    }
    if value in exact:
        source_code, flow = exact[value]
        if flow == FlowType.CONSUMPTION and direction != Direction.OUTFLOW:
            return source_code, FlowType.OTHER, value != "分分捐"
        return source_code, flow, False
    if value == "转账-退款":
        return "transfer_refund", FlowType.TRANSFER, False
    if value == "微信红包-退款":
        return "red_packet_refund", FlowType.RED_PACKET, False
    if value.endswith("-退款"):
        return "merchant_refund", FlowType.EXPENSE_REFUND, False
    if value == "其他":
        return "other", FlowType.OTHER, True
    return "unknown", FlowType.OTHER, True


def normalize_status(value: str) -> NormalizedStatus:
    value = value.strip()
    if value == "已全额退款" or value.startswith("已退款") or value == "对方已退还":
        return NormalizedStatus.PARTIAL_REFUND if "(" in value else NormalizedStatus.REFUNDED
    if value in {"待支付", "处理中"}:
        return NormalizedStatus.PENDING
    if value in {"支付失败", "已取消", "已关闭"}:
        return NormalizedStatus.FAILED
    if value in {"已撤销", "已冲正"}:
        return NormalizedStatus.REVERSED
    if value in {"支付成功", "对方已收钱", "已收钱", "已转账", "已到账", "已存入零钱", "充值完成", "提现已到账"}:
        return NormalizedStatus.COMPLETED
    return NormalizedStatus.UNKNOWN
