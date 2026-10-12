from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.parsers.wechat import PARSER_VERSION, WechatBillParser
from backend.app.pipeline.dto import StandardTransactionDraft
from backend.app.pipeline.fingerprints import file_sha256
from backend.app.pipeline.issues import DataIssue, FatalParseError, IssueSeverity
from backend.app.pipeline.wechat_normalizer import WechatTransactionNormalizer
from backend.app.repositories.categories import CategoryRepository
from backend.app.repositories.classification import UserCategoryRuleRepository
from backend.app.repositories.import_batches import ImportBatchRepository
from backend.app.repositories.transactions import TransactionRepository
from backend.app.repositories.users import UserRepository


@dataclass(frozen=True)
class ImportQualityReport:
    batch_id: int
    status: str
    total_rows: int
    parsed_rows: int
    inserted_rows: int
    duplicate_rows: int
    failed_rows: int
    warning_rows: int
    unknown_flow_rows: int
    refund_rows: int
    transfer_rows: int
    transfer_refund_rows: int
    internal_transfer_rows: int
    balance_transfer_rows: int
    possible_duplicate_links: int
    linked_refunds: int
    unresolved_refunds: int
    uncategorized_consumption_ratio: float
    uncategorized_consumption_amount_ratio: float
    earliest_transaction_time: str | None
    latest_transaction_time: str | None
    flow_type_counts: dict[str, int]
    issues: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class WechatImportService:
    def __init__(self, session: Session, fingerprint_secret: bytes):
        self.session = session
        self.parser = WechatBillParser()
        self.normalizer = WechatTransactionNormalizer(fingerprint_secret)

    def import_file(self, path: str | Path, *, username: str = "local-user") -> ImportQualityReport:
        now = datetime.now(timezone.utc)
        user = UserRepository(self.session).ensure_local(username)
        batch_repo = ImportBatchRepository(self.session)
        batch = batch_repo.create(
            user_id=user.id, source="wechat", source_file_type="xlsx",
            file_fingerprint=file_sha256(path), status="running", started_at=now,
            parser_version=PARSER_VERSION,
        )
        try:
            parsed = self.parser.parse(path)
        except FatalParseError as exc:
            issue = exc.issue
            batch_repo.finish(batch, completed_at=datetime.now(timezone.utc), status="failed",
                              failed_rows=1, error_summary_json=json.dumps([issue.safe_dict()], ensure_ascii=False))
            return self._empty_report(batch.id, "failed", (issue.safe_dict(),))

        issues: list[DataIssue] = list(parsed.issues)
        drafts: list[StandardTransactionDraft] = []
        for source_row in parsed.rows:
            result = self.normalizer.normalize(source_row)
            issues.extend(result.issues)
            if result.transaction is not None:
                drafts.append(result.transaction)

        prior = batch_repo.prior_completed(user.id, "wechat", batch.file_fingerprint, batch.id)
        failed_rows = sum(issue.severity == IssueSeverity.ROW_ERROR for issue in issues)
        earliest = min((draft.occurred_at_utc for draft in drafts), default=None)
        latest = max((draft.occurred_at_utc for draft in drafts), default=None)
        if prior is not None:
            status = "duplicate_file"
            warning_rows = self._warning_count(issues)
            batch_repo.finish(
                batch, completed_at=datetime.now(timezone.utc), status=status,
                total_rows=parsed.total_candidate_rows, parsed_rows=len(drafts), inserted_rows=0,
                duplicate_rows=len(drafts), failed_rows=failed_rows, warning_rows=warning_rows,
                earliest_transaction_time=earliest, latest_transaction_time=latest,
                error_summary_json=self._issues_json(issues),
            )
            return self._report(batch.id, status, parsed.total_candidate_rows, drafts, 0, len(drafts),
                                failed_rows, warning_rows, 0, 0, 0, issues, earliest, latest)

        categories = CategoryRepository(self.session).ensure_defaults()
        category_ids = {code: category.id for code, category in categories.items()}
        transaction_repo = TransactionRepository(self.session)
        user_rules = UserCategoryRuleRepository(self.session).active_category_ids(user.id, "wechat")
        inserted, duplicate_rows = transaction_repo.insert_drafts(
            user.id, batch.id, drafts, category_ids, user_rules,
        )
        possible_links = transaction_repo.add_possible_duplicate_links(user.id, inserted)
        linked_refunds, unresolved_refunds = transaction_repo.link_refunds(user.id, inserted)
        if possible_links:
            issues.append(DataIssue(IssueSeverity.WARNING, "possible_duplicate_requires_review", detail_code=str(possible_links)))
        if unresolved_refunds:
            issues.append(DataIssue(IssueSeverity.WARNING, "refund_link_unresolved", detail_code=str(unresolved_refunds)))
        warning_rows = self._warning_count(issues)
        status = "completed_with_warnings" if failed_rows or warning_rows else "completed"
        batch_repo.finish(
            batch, completed_at=datetime.now(timezone.utc), status=status,
            total_rows=parsed.total_candidate_rows, parsed_rows=len(drafts), inserted_rows=len(inserted),
            duplicate_rows=duplicate_rows, failed_rows=failed_rows, warning_rows=warning_rows,
            earliest_transaction_time=earliest, latest_transaction_time=latest,
            error_summary_json=self._issues_json(issues),
        )
        return self._report(batch.id, status, parsed.total_candidate_rows, drafts, len(inserted),
                            duplicate_rows, failed_rows, warning_rows, possible_links, linked_refunds,
                            unresolved_refunds, issues, earliest, latest)

    @staticmethod
    def _issues_json(issues: list[DataIssue]) -> str | None:
        if not issues:
            return None
        return json.dumps([issue.safe_dict() for issue in issues[:200]], ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _warning_count(issues: list[DataIssue]) -> int:
        row_numbers = {issue.row_number for issue in issues if issue.severity == IssueSeverity.WARNING and issue.row_number is not None}
        global_warnings = sum(issue.severity == IssueSeverity.WARNING and issue.row_number is None for issue in issues)
        return len(row_numbers) + global_warnings

    def _report(
        self, batch_id: int, status: str, total: int, drafts: list[StandardTransactionDraft],
        inserted: int, duplicates: int, failed: int, warnings: int, possible: int,
        linked: int, unresolved: int, issues: list[DataIssue], earliest: datetime | None, latest: datetime | None,
    ) -> ImportQualityReport:
        flows = Counter(draft.flow_type.value for draft in drafts)
        consumption = [draft for draft in drafts if draft.flow_type.value == "consumption"]
        uncategorized = sum(draft.suggested_category_code == "other" for draft in consumption)
        consumption_amount = sum(draft.amount_minor for draft in consumption)
        uncategorized_amount = sum(draft.amount_minor for draft in consumption if draft.suggested_category_code == "other")
        return ImportQualityReport(
            batch_id=batch_id, status=status, total_rows=total, parsed_rows=len(drafts),
            inserted_rows=inserted, duplicate_rows=duplicates, failed_rows=failed, warning_rows=warnings,
            unknown_flow_rows=sum(draft.source_type_code in {"unknown", "other"} for draft in drafts),
            refund_rows=flows.get("expense_refund", 0),
            transfer_rows=sum(draft.source_type_code == "transfer" for draft in drafts),
            transfer_refund_rows=sum(draft.source_type_code == "transfer_refund" for draft in drafts),
            internal_transfer_rows=flows.get("internal_transfer", 0),
            balance_transfer_rows=flows.get("balance_transfer", 0), possible_duplicate_links=possible,
            linked_refunds=linked, unresolved_refunds=unresolved,
            uncategorized_consumption_ratio=(uncategorized / len(consumption) if consumption else 0.0),
            uncategorized_consumption_amount_ratio=(uncategorized_amount / consumption_amount if consumption_amount else 0.0),
            earliest_transaction_time=earliest.isoformat() if earliest else None,
            latest_transaction_time=latest.isoformat() if latest else None,
            flow_type_counts=dict(sorted(flows.items())),
            issues=tuple(issue.safe_dict() for issue in issues[:200]),
        )

    @staticmethod
    def _empty_report(batch_id: int, status: str, issues: tuple[dict[str, object], ...]) -> ImportQualityReport:
        return ImportQualityReport(
            batch_id=batch_id, status=status, total_rows=0, parsed_rows=0, inserted_rows=0,
            duplicate_rows=0, failed_rows=1, warning_rows=0, unknown_flow_rows=0,
            refund_rows=0, transfer_rows=0, transfer_refund_rows=0, internal_transfer_rows=0,
            balance_transfer_rows=0, possible_duplicate_links=0, linked_refunds=0,
            unresolved_refunds=0, uncategorized_consumption_ratio=0.0,
            uncategorized_consumption_amount_ratio=0.0,
            earliest_transaction_time=None, latest_transaction_time=None, flow_type_counts={}, issues=issues,
        )
