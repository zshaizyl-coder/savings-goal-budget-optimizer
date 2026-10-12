from dataclasses import asdict, dataclass
from enum import StrEnum


class IssueSeverity(StrEnum):
    FATAL = "fatal"
    ROW_ERROR = "row_error"
    WARNING = "warning"


@dataclass(frozen=True)
class DataIssue:
    severity: IssueSeverity
    code: str
    row_number: int | None = None
    field: str | None = None
    detail_code: str | None = None

    def safe_dict(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}


class FatalParseError(ValueError):
    def __init__(self, issue: DataIssue):
        super().__init__(issue.code)
        self.issue = issue
