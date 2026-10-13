from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.classification.evaluation import build_review_groups
from backend.app.classification.features import sanitize_source_text
from backend.app.classification.qr_context import assign_other_importance, classify_qr_group
from backend.app.services.reclassify_wechat import WechatSourceReclassificationService


class PrivateCategoryReviewQueue:
    """Transient review hints kept under Git-ignored data/private only."""

    def __init__(self, root: str | Path = "data/private"):
        self.root = Path(root)

    def _path(self, user_id: int) -> Path:
        return self.root / f"category-review-user-{user_id}.json"

    def rebuild(
        self, session: Session, source_path: str | Path, *, user_id: int,
        fingerprint_secret: bytes, batch_id: int,
    ) -> int:
        service = WechatSourceReclassificationService(session, fingerprint_secret)
        groups = build_review_groups(service.build_other_review_items(source_path, user_id=user_id))
        importance = {item.review_group_id: item for item in assign_other_importance(groups)}
        items = []
        for group in groups:
            decision = importance[group.review_group_id]
            qr_candidate = classify_qr_group(group)
            items.append({
                "group_id": group.review_group_id,
                "representative_transaction_id": group.representative_transaction_id,
                "tier": decision.tier,
                "amount_minor": group.total_amount_minor,
                "transaction_count": group.transaction_count,
                "merchant_hint": sanitize_source_text(group.counterparty_hint, max_length=60),
                "description_hint": sanitize_source_text(group.description_hint, max_length=60),
                "dominant_time_bucket": group.dominant_time_bucket,
                "local_candidate": qr_candidate.candidate_category,
                "candidate_requires_confirmation": qr_candidate.candidate_category is not None,
            })
        payload = {
            "user_id": user_id, "batch_id": batch_id,
            "generated_at": datetime.now(timezone.utc).isoformat(), "items": items,
        }
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._path(user_id); temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)
        return len(items)

    def load(self, user_id: int) -> dict:
        path = self._path(user_id)
        if not path.exists():
            return {"items": [], "generated_at": None, "batch_id": None}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"items": [], "generated_at": None, "batch_id": None}
        if payload.get("user_id") != user_id or not isinstance(payload.get("items"), list):
            return {"items": [], "generated_at": None, "batch_id": None}
        return payload
