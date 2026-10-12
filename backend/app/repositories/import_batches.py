from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import ImportBatch


class ImportBatchRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, **values) -> ImportBatch:
        batch = ImportBatch(**values)
        self.session.add(batch)
        self.session.flush()
        return batch

    def prior_completed(self, user_id: int, source: str, file_fingerprint: str, exclude_id: int) -> ImportBatch | None:
        return self.session.scalar(select(ImportBatch).where(
            ImportBatch.user_id == user_id,
            ImportBatch.source == source,
            ImportBatch.file_fingerprint == file_fingerprint,
            ImportBatch.status.in_(("completed", "completed_with_warnings", "duplicate_file")),
            ImportBatch.id != exclude_id,
        ).order_by(ImportBatch.id))

    def finish(self, batch: ImportBatch, *, completed_at: datetime, **counts) -> None:
        batch.completed_at = completed_at
        for name, value in counts.items():
            setattr(batch, name, value)
        self.session.flush()
