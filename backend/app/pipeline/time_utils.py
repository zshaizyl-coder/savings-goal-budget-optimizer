from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


DEFAULT_USER_TIMEZONE = "Asia/Shanghai"
_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M")


class TimeParseError(ValueError):
    pass


def parse_source_datetime(value: object, timezone_name: str = DEFAULT_USER_TIMEZONE) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = "" if value is None else str(value).strip()
        parsed = None
        for fmt in _FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            raise TimeParseError("transaction_time_invalid")
    zone = ZoneInfo(timezone_name)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(timezone.utc)


def business_week_start(value_utc: datetime, timezone_name: str = DEFAULT_USER_TIMEZONE) -> date:
    if value_utc.tzinfo is None or value_utc.utcoffset() is None:
        raise ValueError("value_utc must be timezone-aware")
    local_date = value_utc.astimezone(ZoneInfo(timezone_name)).date()
    return local_date - timedelta(days=local_date.weekday())


def business_month(value_utc: datetime, timezone_name: str = DEFAULT_USER_TIMEZONE) -> str:
    local = value_utc.astimezone(ZoneInfo(timezone_name))
    return f"{local.year:04d}-{local.month:02d}"
