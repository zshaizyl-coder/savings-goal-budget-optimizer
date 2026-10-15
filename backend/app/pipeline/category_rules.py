from __future__ import annotations

from dataclasses import dataclass
import re


CATEGORY_RULE_VERSION = "wechat-category-v4"
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


@dataclass(frozen=True)
class CategoryDecision:
    category_code: str
    evidence_code: str
    confidence_bps: int


# Public, widely identifiable brands/platforms may be used as versioned aliases.
# This list must not contain a user's private contacts or one-off local names.
_PUBLIC_MERCHANT_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("food", (
        "大家乐", "必胜客", "肯德基", "麦当劳", "真功夫", "蜜雪冰城",
        "古茗", "1点点", "蒙自源", "大快活", "茶百道", "瑞幸", "luckin coffee",
        "蛙来哒", "茶宜乐",
    )),
    ("shopping", (
        "转转", "百果园", "大润发", "耐克", "蕉下", "名创优品", "抖音电商",
        "罗森", "LILY", "THE COLORIST", "调色师",
    )),
    ("transport", (
        "12306", "中铁网络", "铁旅科技", "小桔充电", "中油", "公共交通",
    )),
    ("entertainment", (
        "交易猫", "腾讯天游", "王者荣耀", "猫眼", "酷狗音乐", "Valve", "Steam Purchase",
    )),
    ("communications", ("中国联通", "中国移动", "中国电信")),
    ("life_services", ("顺丰速运",)),
)


# Generic business semantics only; private merchant names must never be added.
# Ordering is deliberate: specific meanings win before broad retail words.
_SEMANTIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("human_relations", ("寺院账户", "寺庙捐赠", "公益捐赠")),
    ("health", (
        "医院", "诊所", "卫生院", "药房", "药店", "医药", "医疗", "体检",
        "口腔", "牙科", "眼科", "挂号", "医保", "健康保险",
    )),
    ("transport", (
        "滴滴", "顺风车", "网约车", "出租车", "打车", "地铁", "公交",
        "铁路", "火车", "高铁", "车票", "客运", "汽车票", "机场",
        "航空", "航班", "机票", "加油", "石油", "停车", "高速通行",
        "高速公路", "过路费", "通行费", "租车", "共享单车", "充电桩",
        "充电站", "充电订单", "电动车充电", "电动自行车充电", "新能源充电",
        "乘车付", "接驳车",
    )),
    ("housing", (
        "物业", "房租", "租金", "水费", "电费", "燃气", "购气", "供暖",
        "酒店", "宾馆", "旅馆", "住宿", "民宿", "公寓",
    )),
    ("education", (
        "学校", "教育", "大学", "学院", "考试", "书店", "图书",
        "课程", "培训", "学费", "文具", "简历服务", "简历付费",
    )),
    ("communications", (
        "话费", "手机缴费", "通信", "通讯", "宽带", "流量包", "云服务",
        "软件服务", "应用商店", "数字服务", "会员服务", "会员续费", "订阅",
        "连续包月", "自动续费", "apple.com/bill", "IMEI码", "序列号查询",
    )),
    ("food", (
        "餐饮", "餐厅", "餐馆", "饭店", "饭馆", "食堂", "快餐", "小吃", "早餐",
        "午餐", "晚餐", "夜宵", "外卖", "美食", "火锅", "烧烤", "烤肉",
        "面馆", "面包", "蛋糕", "甜品", "咖啡", "奶茶", "茶饮", "饮品",
        "果汁", "汉堡", "披萨", "炸鸡", "米粉", "米线", "粉汤", "酸粉",
        "后安粉", "饺子", "馄饨", "铁板烧", "黄焖鸡", "酸菜鱼", "茶楼",
        "手抓饼", "牛扒", "牛排", "炸货", "糟粕醋", "瓦罐煨汤", "点餐",
        "冷饮", "烘焙", "螺蛳粉", "锅巴土豆", "丸子", "烙饼", "魔厨",
        "云吞", "叉烧", "汤粉", "甜粥", "鸡柳", "烤面筋", "焖面",
    )),
    ("shopping", (
        "淘宝", "天猫", "京东", "拼多多", "唯品会", "闲鱼", "得物",
        "商城", "商场", "超市", "便利店", "便利商店", "百货", "免税", "购物",
        "服装", "服饰", "鞋业", "鞋服", "箱包", "眼镜", "珠宝", "饰品",
        "彩妆", "护肤", "化妆品", "数码", "电脑", "手机", "电器", "家电",
        "家居", "家具", "五金", "日用品", "母婴", "宠物用品", "水果",
        "蔬菜", "生鲜", "粮油", "副食", "食品店", "烟酒", "售货机",
        "宠物生活馆", "宠物店",
    )),
    ("entertainment", (
        "电影", "影院", "影城", "ktv", "游戏", "电竞", "网吧",
        "景区", "乐园", "演出", "音乐会", "剧院", "票务", "桌游", "密室",
        "彩票", "游艺", "台球", "观光列车",
    )),
    ("life_services", (
        "快递", "物流", "邮政", "洗衣", "洗鞋", "理发", "美发", "美容",
        "维修", "修理", "家政", "保洁", "打印", "复印", "摄影", "照相",
        "图文店", "图文广告", "打印店", "充电宝", "开锁", "搬家", "按摩", "护理服务",
    )),
)

_ELIGIBLE_SOURCE_TYPES = {
    "merchant_consumption", "qr_payment", "group_collection", "merchant_refund",
}


def classify_text_decision(source_type_code: str, *text_parts: str) -> CategoryDecision:
    if source_type_code in {"red_packet", "red_packet_refund", "donation"}:
        return CategoryDecision("human_relations", "source_type", 10000)
    if source_type_code not in _ELIGIBLE_SOURCE_TYPES:
        return CategoryDecision("other", "not_consumption_category", 10000)

    text = " ".join(str(part).strip() for part in text_parts if part and str(part).strip())
    for code, aliases in _PUBLIC_MERCHANT_ALIASES:
        if any(re.search(re.escape(alias), text, re.IGNORECASE) for alias in aliases):
            return CategoryDecision(code, "public_merchant_alias", 9500)
    for code, keywords in _SEMANTIC_RULES:
        if any(re.search(re.escape(keyword), text, re.IGNORECASE) for keyword in keywords):
            return CategoryDecision(code, "generic_semantic_rule", 9200)
    return CategoryDecision("other", "no_reliable_rule", 0)


def classify_text(source_type_code: str, *text_parts: str) -> tuple[str, str]:
    """Compatibility wrapper used by the stage-4 normalizer."""
    decision = classify_text_decision(source_type_code, *text_parts)
    return decision.category_code, decision.evidence_code
