from __future__ import annotations

from typing import Protocol


class ConsumptionLike(Protocol):
    amount_minor: int
    direction: object
    flow_type: object


def _value(value: object) -> str:
    return str(getattr(value, "value", value))


def signed_consumption_minor(transaction: ConsumptionLike) -> int:
    """Return the auditable net-consumption contribution of one record."""
    flow_type = _value(transaction.flow_type)
    direction = _value(transaction.direction)
    if flow_type == "consumption" and direction == "outflow":
        return transaction.amount_minor
    if flow_type == "expense_refund" and direction == "inflow":
        return -transaction.amount_minor
    return 0
