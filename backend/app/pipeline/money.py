from decimal import Decimal, InvalidOperation
import re


class MoneyParseError(ValueError):
    pass


_CURRENCY_DECIMAL = re.compile(r"^(?:[¥￥]\s*)?(\d+(?:\.\d{1,2})?)(?:\s*元)?$")


def parse_cny_to_minor(value: object) -> int:
    if value is None:
        raise MoneyParseError("amount_empty")
    text = str(value).strip().replace(",", "")
    if not text or text.lower() == "nan":
        raise MoneyParseError("amount_empty")
    match = _CURRENCY_DECIMAL.fullmatch(text)
    if match is None:
        raise MoneyParseError("amount_invalid_format")
    try:
        amount = Decimal(match.group(1))
    except InvalidOperation as exc:
        raise MoneyParseError("amount_invalid_decimal") from exc
    return int(amount * Decimal(100))
