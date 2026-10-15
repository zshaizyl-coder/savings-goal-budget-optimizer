from __future__ import annotations

import json
from typing import Callable, Sequence
from urllib import error, request

from .features import is_cloud_eligible, privacy_minimized_features
from .openai_provider import ClassificationProviderError
from .types import ALLOWED_CATEGORY_CODES, ClassificationContext, ClassificationResult


DEEPSEEK_PROMPT_VERSION = "transaction-category-deepseek-v2"
_EVIDENCE_CODES = frozenset({
    "counterparty_semantics", "description_semantics", "repeat_pattern",
    "amount_pattern", "time_weak_signal", "public_web_evidence", "insufficient_evidence",
})
_STRONG_EVIDENCE = frozenset({
    "counterparty_semantics", "description_semantics", "repeat_pattern", "public_web_evidence",
})
_EVIDENCE_ALIASES = {
    "merchant_semantics": "counterparty_semantics",
    "merchant_name": "counterparty_semantics",
    "description_hint": "description_semantics",
    "description": "description_semantics",
    "recurrence_pattern": "repeat_pattern",
    "recurring_pattern": "repeat_pattern",
    "amount": "amount_pattern",
    "time": "time_weak_signal",
    "web_search": "public_web_evidence",
    "web_evidence": "public_web_evidence",
    "insufficient": "insufficient_evidence",
}

Transport = Callable[[dict[str, object]], dict[str, object]]


class DeepSeekClassificationProvider:
    """Privacy-minimized DeepSeek candidate classifier.

    Results are candidates only. This adapter neither updates a transaction nor
    creates a user rule; the caller must persist and explicitly review them.
    """

    provider_name = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str = "deepseek-flash",
        base_url: str = "https://api.deepseek.com",
        timezone_name: str = "Asia/Shanghai",
        transport: Transport | None = None,
    ):
        if not api_key:
            raise ValueError("api_key_required")
        if not model_name:
            raise ValueError("model_name_required")
        if not base_url.lower().startswith("https://"):
            raise ValueError("https_base_url_required")
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.timezone_name = timezone_name
        self.transport = transport or self._http_transport

    def classify(self, context: ClassificationContext) -> ClassificationResult:
        return self.classify_many([context])[0]

    def classify_many(
        self, contexts: Sequence[ClassificationContext],
    ) -> list[ClassificationResult]:
        if not contexts or len(contexts) > 20:
            raise ClassificationProviderError("batch_size_must_be_1_to_20")
        features = [privacy_minimized_features(item, self.timezone_name) for item in contexts]
        if any(not is_cloud_eligible(item) for item in features):
            raise ClassificationProviderError("context_not_cloud_eligible")
        response = self.transport(self._payload(features))
        return self._parse_response(response, expected_count=len(features))

    def _payload(self, features: list[dict[str, object]]) -> dict[str, object]:
        indexed = [{"item_index": index, **item} for index, item in enumerate(features)]
        category_guide = {
            "food": "餐饮、饮品、食品", "shopping": "商品购买、零售购物",
            "transport": "公交、打车、铁路、航空、加油", "entertainment": "影音、游戏、休闲娱乐",
            "housing": "房租、物业、家居固定费用", "life_services": "生活服务、维修、美容理发",
            "health": "医疗、药品、健康", "education": "教育、培训、书籍学习",
            "communications": "话费、流量、宽带", "human_relations": "礼金、人情往来",
            "other": "证据不足或不属于以上类别",
        }
        system = (
            "你是个人账单的商户行业分类助手。请只输出 JSON。"
            "应积极使用已知公共品牌、明确店名和行业词判断类别，不要因为缺少精确交易说明"
            "就把明显品牌机械地返回 other。广为人知的品牌知识属于"
            "counterparty_semantics；若输入含 public_web_evidence，应优先结合该公开检索摘要，"
            "并使用 public_web_evidence 证据代码。"
            "例如茶饮品牌归 food，美妆或精品零售品牌归 shopping。"
            "但聚合支付平台（如美团平台商户）、纯英文缩写、个人姓名和语义不明名称仍应"
            "返回 other，不得为了降低 OTHER 比例强制猜测。"
            "时段、周末和金额只是弱证据，不得单独据此分为餐饮或其他具体类别。"
            "个人姓名、二维码收款或语义不明时不得强制猜测。"
            "evidence_codes只允许使用：counterparty_semantics、description_semantics、"
            "repeat_pattern、amount_pattern、time_weak_signal、public_web_evidence、"
            "insufficient_evidence。"
            "输出格式示例："
            '{"items":[{"item_index":0,"category_code":"other",'
            '"confidence_score":60,"evidence_codes":["insufficient_evidence"]}]}'
        )
        user = json.dumps({"category_guide": category_guide, "items": indexed}, ensure_ascii=False)
        return {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "temperature": 0.1,
            "max_tokens": min(3000, 180 + len(indexed) * 120),
            "stream": False,
        }

    def _http_transport(self, payload: dict[str, object]) -> dict[str, object]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=60) as response:  # nosec: configured API endpoint
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            # Do not expose the upstream body: it may echo request content.
            raise ClassificationProviderError(f"upstream_http_{exc.code}") from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ClassificationProviderError("upstream_unavailable") from exc

    @classmethod
    def _parse_response(
        cls, response: dict[str, object], *, expected_count: int,
    ) -> list[ClassificationResult]:
        try:
            text = response["choices"][0]["message"]["content"]  # type: ignore[index]
            value = json.loads(text)
            items = value["items"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ClassificationProviderError("invalid_structured_output") from exc
        if not isinstance(items, list) or len(items) != expected_count:
            raise ClassificationProviderError("invalid_result_count")
        by_index: dict[int, ClassificationResult] = {}
        for item in items:
            if not isinstance(item, dict):
                raise ClassificationProviderError("invalid_result_item")
            try:
                index = int(item["item_index"])
                category = str(item["category_code"])
                confidence_bps = int(item["confidence_score"]) * 100
                evidence = tuple(
                    _EVIDENCE_ALIASES.get(str(code), str(code))
                    for code in item["evidence_codes"]
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ClassificationProviderError("invalid_result_item") from exc
            if index in by_index or not 0 <= index < expected_count:
                raise ClassificationProviderError("invalid_item_index")
            if category not in ALLOWED_CATEGORY_CODES:
                raise ClassificationProviderError("invalid_category_code")
            if not 0 <= confidence_bps <= 10000:
                raise ClassificationProviderError("invalid_confidence")
            if not evidence or any(code not in _EVIDENCE_CODES for code in evidence):
                raise ClassificationProviderError("invalid_evidence_codes")
            if category != "other" and not (_STRONG_EVIDENCE & set(evidence)):
                raise ClassificationProviderError("weak_evidence_cannot_assign_category")
            by_index[index] = ClassificationResult(
                category_code=category, confidence_bps=confidence_bps,
                evidence_codes=evidence, requires_review=True,
            )
        if set(by_index) != set(range(expected_count)):
            raise ClassificationProviderError("missing_item_index")
        return [by_index[index] for index in range(expected_count)]
