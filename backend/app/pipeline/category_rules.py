from __future__ import annotations

import re


CATEGORY_RULE_VERSION = "wechat-category-v1"
DEFAULT_CATEGORIES = (
    ("food", "餐饮", 10),
    ("shopping", "购物", 20),
    ("transport", "交通", 30),
    ("entertainment", "娱乐", 40),
    ("housing", "居住", 50),
    ("life_services", "生活服务", 60),
    ("health", "医疗健康", 70),
    ("education", "教育", 80),
    ("communications", "通讯与数字服务", 90),
    ("human_relations", "人情往来", 100),
    ("other", "其他 / 未分类", 999),
)

# Rules use generic semantic words only. Source text is inspected in memory and
# never persisted. Ordering is deliberate and covered by tests.
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("food", ("餐", "饭", "美食", "外卖", "咖啡", "奶茶", "茶饮", "烧烤", "火锅", "面包", "蛋糕", "甜品", "酸奶", "食堂")),
    ("transport", ("滴滴", "高德", "顺风", "铁路", "铁旅", "火车", "机场", "航空", "地铁", "公交", "加油", "石油", "停车", "打车", "车票")),
    ("shopping", ("淘宝", "京东", "拼多多", "商城", "超市", "便利", "百货", "商店", "购物", "服饰", "鞋", "彩妆", "护肤", "车品")),
    ("housing", ("物业", "房租", "水费", "电费", "燃气", "酒店", "住宿", "公寓")),
    ("health", ("医院", "诊所", "药房", "药店", "医疗", "健康", "体检")),
    ("education", ("学校", "教育", "大学", "学院", "考试", "书店", "课程", "培训")),
    ("communications", ("话费", "宽带", "icloud", "apple", "云服务", "软件", "会员", "订阅", "充值中心")),
    ("entertainment", ("电影", "影院", "ktv", "游戏", "景区", "乐园", "演出", "票务")),
    ("life_services", ("快递", "洗衣", "理发", "维修", "家政", "打印")),
)


def classify_text(source_type_code: str, *text_parts: str) -> tuple[str, str]:
    if source_type_code in {"red_packet", "red_packet_refund", "donation"}:
        return "human_relations", "source_type"
    if source_type_code not in {"merchant_consumption", "qr_payment", "group_collection", "merchant_refund"}:
        return "other", "not_consumption_category"
    text = " ".join(part for part in text_parts if part)
    for code, keywords in _KEYWORDS:
        if any(re.search(re.escape(keyword), text, re.IGNORECASE) for keyword in keywords):
            return code, "keyword_rule"
    return "other", "no_reliable_rule"
