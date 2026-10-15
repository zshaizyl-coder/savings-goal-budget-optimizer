from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable
from urllib import error, parse, request

from .features import sanitize_source_text


class MerchantSearchError(RuntimeError):
    """Public merchant lookup failed without exposing the upstream response."""


@dataclass(frozen=True)
class MerchantSearchEvidence:
    query_name: str
    summary: str
    result_count: int


SearchTransport = Callable[[str], dict[str, object]]


class BraveMerchantSearchProvider:
    """Retrieve public merchant evidence without sending transaction metadata."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.search.brave.com/res/v1/web/search",
        transport: SearchTransport | None = None,
    ):
        if not api_key:
            raise ValueError("api_key_required")
        if not base_url.lower().startswith("https://"):
            raise ValueError("https_base_url_required")
        self.api_key = api_key
        self.base_url = base_url
        self.transport = transport or self._http_transport

    def search(self, merchant_hint: str) -> MerchantSearchEvidence:
        name = sanitize_source_text(merchant_hint, max_length=60)
        if len(name) < 2:
            raise MerchantSearchError("merchant_name_not_searchable")
        payload = self.transport(name)
        try:
            raw_results = payload.get("web", {}).get("results", [])  # type: ignore[union-attr]
        except AttributeError as exc:
            raise MerchantSearchError("invalid_search_response") from exc
        if not isinstance(raw_results, list):
            raise MerchantSearchError("invalid_search_response")
        snippets: list[str] = []
        for item in raw_results[:3]:
            if not isinstance(item, dict):
                continue
            title = sanitize_source_text(str(item.get("title") or ""), max_length=100)
            description = sanitize_source_text(
                str(item.get("description") or ""), max_length=220,
            )
            snippet = "：".join(part for part in (title, description) if part)
            if snippet:
                snippets.append(snippet)
        return MerchantSearchEvidence(
            query_name=name,
            summary="；".join(snippets)[:600],
            result_count=len(snippets),
        )

    def _http_transport(self, merchant_name: str) -> dict[str, object]:
        query = parse.urlencode({
            "q": f'"{merchant_name}" 商户 品牌 行业',
            "count": 3,
            "country": "CN",
            "search_lang": "zh-hans",
            "ui_lang": "zh-CN",
            "safesearch": "moderate",
        })
        req = request.Request(
            f"{self.base_url}?{query}",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
            },
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=12) as response:  # nosec: configured HTTPS API
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raise MerchantSearchError(f"upstream_http_{exc.code}") from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MerchantSearchError("upstream_unavailable") from exc
