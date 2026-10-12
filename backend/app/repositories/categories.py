from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Category, CategoryMapping
from backend.app.pipeline.category_rules import CATEGORY_RULE_VERSION, DEFAULT_CATEGORIES


_SOURCE_MAPPINGS = {
    "merchant_consumption": "other",
    "qr_payment": "other",
    "group_collection": "other",
    "donation": "human_relations",
    "red_packet": "human_relations",
    "red_packet_refund": "human_relations",
    "merchant_refund": "other",
}


class CategoryRepository:
    def __init__(self, session: Session):
        self.session = session

    def ensure_defaults(self) -> dict[str, Category]:
        existing = {item.code: item for item in self.session.scalars(select(Category)).all()}
        for code, name, order in DEFAULT_CATEGORIES:
            if code not in existing:
                item = Category(code=code, display_name=name, sort_order=order, is_active=True)
                self.session.add(item)
                existing[code] = item
        self.session.flush()
        mapping_keys = set(self.session.execute(select(
            CategoryMapping.source_type_code, CategoryMapping.rule_version
        ).where(CategoryMapping.source == "wechat")).all())
        for source_code, category_code in _SOURCE_MAPPINGS.items():
            if (source_code, CATEGORY_RULE_VERSION) not in mapping_keys:
                self.session.add(CategoryMapping(
                    source="wechat", source_type_code=source_code,
                    category_id=existing[category_code].id, priority=100,
                    rule_version=CATEGORY_RULE_VERSION, is_active=True,
                ))
        self.session.flush()
        return existing
