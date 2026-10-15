from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable
from urllib import error, request

from .features import sanitize_source_text


class MerchantSearchError(RuntimeError):
    """Public merchant lookup failed without exposing the upstream response."""


@dataclass(frozen=True)
class MerchantSearchEvidence:
    query_name: str
    summary: str
    result_count: int


SearchTransport = Callable[[str], dict[str, object]]


class VolcengineMerchantSearchProvider:
    """Retrieve public merchant evidence from Doubao Search Custom edition."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://open.feedcoopapi.com/search_api/web_search",
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
            metadata = payload.get("ResponseMetadata", {})
            if isinstance(metadata, dict) and metadata.get("Error"):
                raise MerchantSearchError("upstream_api_error")
            result = payload.get("Result", {})
            raw_results = result.get("WebResults", [])  # type: ignore[union-attr]
        except AttributeError as exc:
            raise MerchantSearchError("invalid_search_response") from exc
        if not isinstance(raw_results, list):
            raise MerchantSearchError("invalid_search_response")
        snippets: list[str] = []
        for item in raw_results[:3]:
            if not isinstance(item, dict):
                continue
            title = sanitize_source_text(str(item.get("Title") or ""), max_length=100)
            description = sanitize_source_text(
                str(item.get("Summary") or item.get("Snippet") or ""), max_length=220,
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
        body = json.dumps({
            "Query": f"{merchant_name} 是什么品牌或商户类型",
            "SearchType": "web",
            "Count": 3,
            "Filter": {"NeedContent": False, "NeedUrl": True},
            "QueryControl": {"QueryRewrite": True},
        }, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self.base_url,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=12) as response:  # nosec: configured HTTPS API
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raise MerchantSearchError(f"upstream_http_{exc.code}") from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MerchantSearchError("upstream_unavailable") from exc
