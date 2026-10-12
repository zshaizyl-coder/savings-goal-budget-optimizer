from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
import random
from statistics import median
from zoneinfo import ZoneInfo

from backend.app.classification.features import time_bucket
from backend.app.classification.types import ALLOWED_CATEGORY_CODES


@dataclass(frozen=True)
class CategoryReviewItem:
    transaction_id: int
    counterparty_fingerprint: str
    source_type_code: str
    occurred_at_utc: datetime
    amount_minor: int
    counterparty_hint: str
    description_hint: str


@dataclass(frozen=True)
class CategoryReviewGroup:
    review_group_id: str
    counterparty_fingerprint: str
    representative_transaction_id: int
    source_types: str
    transaction_count: int
    total_amount_minor: int
    average_amount_minor: int
    minimum_amount_minor: int
    maximum_amount_minor: int
    first_transaction_at: datetime
    last_transaction_at: datetime
    weekend_ratio: float
    dominant_time_bucket: str
    counterparty_hint: str
    description_hint: str
    cloud_eligible: bool
    sample_priority: int
    sample_reason: str
    in_validation_sample: bool

    def as_private_dict(self) -> dict[str, object]:
        return asdict(self)


def build_review_groups(
    items: list[CategoryReviewItem],
    *,
    timezone_name: str = "Asia/Shanghai",
    top_amount_n: int = 20,
    high_frequency_n: int = 10,
    low_amount_n: int = 10,
    random_n: int = 10,
    random_seed: int = 20261012,
) -> list[CategoryReviewGroup]:
    grouped: dict[str, list[CategoryReviewItem]] = defaultdict(list)
    for item in items:
        # Missing fingerprints are never combined because that would create a
        # false merchant relationship.
        key = item.counterparty_fingerprint or f"missing-{item.transaction_id}"
        grouped[key].append(item)

    base = []
    for fingerprint, members in grouped.items():
        ordered = sorted(members, key=lambda item: (item.occurred_at_utc, item.transaction_id))
        amounts = [item.amount_minor for item in ordered]
        buckets = Counter(time_bucket(item.occurred_at_utc, timezone_name) for item in ordered)
        weekends = sum(
            item.occurred_at_utc.astimezone(ZoneInfo(timezone_name)).weekday() >= 5
            for item in ordered
        )
        source_types = sorted({item.source_type_code for item in ordered})
        counterparties = _representative_texts(item.counterparty_hint for item in ordered)
        descriptions = _representative_texts(item.description_hint for item in ordered)
        base.append({
            "fingerprint": fingerprint,
            "members": ordered,
            "source_types": source_types,
            "transaction_count": len(ordered),
            "total_amount_minor": sum(amounts),
            "average_amount_minor": round(sum(amounts) / len(amounts)),
            "minimum_amount_minor": min(amounts),
            "maximum_amount_minor": max(amounts),
            "first_transaction_at": ordered[0].occurred_at_utc,
            "last_transaction_at": ordered[-1].occurred_at_utc,
            "weekend_ratio": weekends / len(ordered),
            "dominant_time_bucket": sorted(buckets.items(), key=lambda pair: (-pair[1], pair[0]))[0][0],
            "counterparty_hint": " / ".join(counterparties),
            "description_hint": " / ".join(descriptions),
            "cloud_eligible": source_types == ["merchant_consumption"] and bool("".join(counterparties + descriptions).strip()),
        })

    selections = _sample_group_reasons(
        base,
        top_amount_n=top_amount_n,
        high_frequency_n=high_frequency_n,
        low_amount_n=low_amount_n,
        random_n=random_n,
        random_seed=random_seed,
    )
    result = []
    for row in base:
        fingerprint = str(row["fingerprint"])
        priority, reason = selections.get(fingerprint, (5, "backlog"))
        result.append(CategoryReviewGroup(
            review_group_id=f"{fingerprint[:6]}{fingerprint[-6:]}",
            counterparty_fingerprint=fingerprint,
            representative_transaction_id=row["members"][0].transaction_id,
            source_types=", ".join(row["source_types"]),
            transaction_count=row["transaction_count"],
            total_amount_minor=row["total_amount_minor"],
            average_amount_minor=row["average_amount_minor"],
            minimum_amount_minor=row["minimum_amount_minor"],
            maximum_amount_minor=row["maximum_amount_minor"],
            first_transaction_at=row["first_transaction_at"],
            last_transaction_at=row["last_transaction_at"],
            weekend_ratio=row["weekend_ratio"],
            dominant_time_bucket=row["dominant_time_bucket"],
            counterparty_hint=row["counterparty_hint"],
            description_hint=row["description_hint"],
            cloud_eligible=row["cloud_eligible"],
            sample_priority=priority,
            sample_reason=reason,
            in_validation_sample=fingerprint in selections,
        ))
    return sorted(
        result,
        key=lambda row: (
            row.sample_priority,
            -row.total_amount_minor,
            -row.transaction_count,
            row.review_group_id,
        ),
    )


def _representative_texts(values, limit: int = 3) -> list[str]:
    counts = Counter(str(value).strip() for value in values if str(value).strip())
    return [text for text, _count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]]


def _sample_group_reasons(
    rows: list[dict[str, object]],
    *,
    top_amount_n: int,
    high_frequency_n: int,
    low_amount_n: int,
    random_n: int,
    random_seed: int,
) -> dict[str, tuple[int, str]]:
    selected: dict[str, tuple[int, str]] = {}

    def take(candidates, count: int, priority: int, reason: str) -> None:
        for row in candidates:
            fingerprint = str(row["fingerprint"])
            if fingerprint in selected:
                continue
            selected[fingerprint] = (priority, reason)
            if sum(1 for value in selected.values() if value == (priority, reason)) >= count:
                break

    amount_order = sorted(rows, key=lambda row: (-int(row["total_amount_minor"]), -int(row["transaction_count"]), str(row["fingerprint"])))
    take(amount_order, min(top_amount_n, len(amount_order)), 1, "top_amount")

    frequency_order = sorted(rows, key=lambda row: (-int(row["transaction_count"]), -int(row["total_amount_minor"]), str(row["fingerprint"])))
    take(frequency_order, high_frequency_n, 2, "high_frequency")

    remaining = [row for row in rows if str(row["fingerprint"]) not in selected]
    if remaining:
        median_average = median(int(row["average_amount_minor"]) for row in remaining)
        low_order = sorted(
            (row for row in remaining if int(row["average_amount_minor"]) <= median_average),
            key=lambda row: (int(row["average_amount_minor"]), -int(row["transaction_count"]), str(row["fingerprint"])),
        )
        take(low_order, low_amount_n, 3, "low_amount")

    remaining = [row for row in rows if str(row["fingerprint"]) not in selected]
    rng = random.Random(random_seed)
    rng.shuffle(remaining)
    take(remaining, random_n, 4, "random")
    return selected


def evaluate_category_predictions(
    rows: list[dict[str, object]],
    *,
    prediction_field: str,
    label_field: str = "confirmed_category",
) -> dict[str, object]:
    labeled = []
    for row in rows:
        label = str(row.get(label_field) or "").strip()
        if not label:
            continue
        if label not in ALLOWED_CATEGORY_CODES:
            raise ValueError(f"invalid_confirmed_category:{label}")
        prediction = str(row.get(prediction_field) or "other").strip()
        if prediction not in ALLOWED_CATEGORY_CODES:
            raise ValueError(f"invalid_predicted_category:{prediction}")
        labeled.append((prediction, label, int(row.get("total_amount_minor") or 0)))

    if not labeled:
        return {
            "status": "awaiting_human_labels",
            "labeled_groups": 0,
            "accuracy": None,
            "amount_weighted_accuracy": None,
            "per_category": {},
            "confusion": {},
        }

    correct = sum(prediction == label for prediction, label, _amount in labeled)
    amount_total = sum(amount for _prediction, _label, amount in labeled)
    amount_correct = sum(amount for prediction, label, amount in labeled if prediction == label)
    labels = sorted({label for _prediction, label, _amount in labeled})
    per_category = {}
    confusion: dict[str, dict[str, int]] = {}
    for label in labels:
        actual = [(prediction, amount) for prediction, actual_label, amount in labeled if actual_label == label]
        per_category[label] = {
            "support": len(actual),
            "recall": sum(prediction == label for prediction, _amount in actual) / len(actual),
        }
        confusion[label] = dict(Counter(prediction for prediction, _amount in actual))
    return {
        "status": "evaluated",
        "labeled_groups": len(labeled),
        "accuracy": correct / len(labeled),
        "amount_weighted_accuracy": (amount_correct / amount_total) if amount_total else None,
        "per_category": per_category,
        "confusion": confusion,
    }
