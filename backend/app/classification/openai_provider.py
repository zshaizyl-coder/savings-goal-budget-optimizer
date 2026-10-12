from __future__ import annotations

import json
from typing import Callable
from urllib import request

from .features import is_cloud_eligible, privacy_minimized_features
from .types import ALLOWED_CATEGORY_CODES, ClassificationContext, ClassificationResult


PROMPT_VERSION = "transaction-category-v1"
_EVIDENCE_CODES = frozenset({
    "counterparty_semantics", "description_semantics", "repeat_pattern",
    "amount_pattern", "time_weak_signal", "insufficient_evidence",
})


class ClassificationProviderError(ValueError):
    pass


Transport = Callable[[dict[str, object]], dict[str, object]]


class OpenAIResponsesClassificationProvider:
    """Optional OpenAI Responses adapter; it never auto-accepts a category."""

    provider_name = "openai_responses"

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        base_url: str = "https://api.openai.com/v1",
        timezone_name: str = "Asia/Shanghai",
        transport: Transport | None = None,
    ):
        if not api_key:
            raise ValueError("api_key_required")
        if not model_name:
            raise ValueError("model_name_required")
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.timezone_name = timezone_name
        self.transport = transport or self._http_transport

    def classify(self, context: ClassificationContext) -> ClassificationResult:
        features = privacy_minimized_features(context, self.timezone_name)
        if not is_cloud_eligible(features):
            raise ClassificationProviderError("context_not_cloud_eligible")
        response = self.transport(self._payload(features))
        parsed = self._parse_response(response)
        category = str(parsed.get("category_code", ""))
        if category not in ALLOWED_CATEGORY_CODES:
            raise ClassificationProviderError("invalid_category_code")
        try:
            confidence_bps = int(parsed["confidence_score"]) * 100
        except (KeyError, TypeError, ValueError) as exc:
            raise ClassificationProviderError("invalid_confidence") from exc
        if not 0 <= confidence_bps <= 10000:
            raise ClassificationProviderError("invalid_confidence")
        evidence = tuple(str(code) for code in parsed.get("evidence_codes", []))
        if not evidence or any(code not in _EVIDENCE_CODES for code in evidence):
            raise ClassificationProviderError("invalid_evidence_codes")
        strong = {"counterparty_semantics", "description_semantics", "repeat_pattern"}
        if category != "other" and not (set(evidence) & strong):
            raise ClassificationProviderError("weak_evidence_cannot_assign_category")
        return ClassificationResult(
            category_code=category,
            confidence_bps=confidence_bps,
            evidence_codes=evidence,
            # Human confirmation remains mandatory even at a high model score.
            requires_review=True,
        )

    def _payload(self, features: dict[str, object]) -> dict[str, object]:
        categories = sorted(ALLOWED_CATEGORY_CODES)
        system = (
            "Classify one personal transaction into exactly one allowed category. "
            "Be conservative: meal-time, weekend and amount are weak supporting "
            "signals and can never be the sole reason for a non-other category. "
            "If merchant semantics are ambiguous, return other."
        )
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "category_code": {"type": "string", "enum": categories},
                "confidence_score": {"type": "integer", "minimum": 0, "maximum": 100},
                "evidence_codes": {
                    "type": "array",
                    "items": {"type": "string", "enum": sorted(_EVIDENCE_CODES)},
                    "minItems": 1,
                },
            },
            "required": ["category_code", "confidence_score", "evidence_codes"],
        }
        return {
            "model": self.model_name,
            "store": False,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(features, ensure_ascii=False)},
            ],
            "text": {
                "format": {
                    "type": "json_schema", "name": "transaction_category",
                    "strict": True, "schema": schema,
                },
            },
        }

    def _http_transport(self, payload: dict[str, object]) -> dict[str, object]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/responses",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=30) as response:  # nosec: configured API endpoint
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _parse_response(response: dict[str, object]) -> dict[str, object]:
        text = response.get("output_text")
        if not isinstance(text, str):
            try:
                output = response["output"]
                text = output[0]["content"][0]["text"]  # type: ignore[index]
            except (KeyError, IndexError, TypeError) as exc:
                raise ClassificationProviderError("missing_structured_output") from exc
        try:
            value = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ClassificationProviderError("invalid_structured_output") from exc
        if not isinstance(value, dict):
            raise ClassificationProviderError("invalid_structured_output")
        return value
