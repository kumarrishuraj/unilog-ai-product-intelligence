"""
Agent -- Manufacturer research and evidence collection.

Golden Rule 3 is enforced structurally, not by prompt instruction: a candidate URL is
scored against the manufacturer-source hierarchy *before* it is fetched, and anything
that resolves to a marketplace or an unrelated distributor is discarded rather than
down-weighted.  A claim that survives is stored with its URL, the snippet it rests on
and the retrieval timestamp.

When research is disabled or no search provider is configured the agent returns an
empty, honest result -- the pipeline then leaves those fields blank and flags the
product, which is the correct behaviour under Golden Rule 1.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from backend.config import Settings
from backend.models.product import Evidence, SourceTier

# Hosts that must never be used as a manufacturer source.
BLOCKED_HOSTS = (
    "amazon.", "ebay.", "walmart.", "aliexpress.", "alibaba.", "etsy.", "temu.",
    "wish.com", "pinterest.", "reddit.", "facebook.", "twitter.", "x.com",
    "quora.", "blogspot.", "wordpress.com", "medium.com", "youtube.",
)

# Distributor/reseller hosts: usable only as an explicit last-resort fallback.
DISTRIBUTOR_HINTS = (
    "grainger.", "homedepot.", "lowes.", "acmetools.", "toolnut.", "zoro.",
    "supplyhouse.", "ferguson.", "fastenal.", "globalindustrial.",
)

DOC_EXTENSIONS = (".pdf", ".doc", ".docx")

# Document classification by URL/anchor keywords -> delivery-format asset column key.
DOC_KEYWORDS: Tuple[Tuple[str, str], ...] = (
    ("specification", "specification_sheet"),
    ("spec sheet", "specification_sheet"),
    ("submittal", "submittal"),
    ("installation", "instruction_manual"),
    ("instruction", "instruction_manual"),
    ("owner", "owners_manual"),
    ("user manual", "owners_manual"),
    ("use and care", "owners_manual"),
    ("service manual", "service_manual"),
    ("warranty", "warranty_information"),
    ("catalog", "catalog"),
    ("line drawing", "line_drawing"),
    ("dimension", "line_drawing"),
    ("sds", "sds"),
    ("safety data", "sds"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _domain_tokens(name: str) -> List[str]:
    """'Whirlpool Corporation' -> ['whirlpool', 'whirlpoolcorporation']."""
    words = [w for w in re.split(r"[^a-z0-9]+", str(name or "").lower()) if w]
    if not words:
        return []
    out = {words[0], "".join(words)}
    if len(words) > 1:
        out.add("".join(words[:2]))
    return sorted(out)


@dataclass
class SourceCandidate:
    url: str
    title: str = ""
    snippet: str = ""
    tier: str = SourceTier.UNVERIFIED.value
    score: float = 0.0
    reason: str = ""


@dataclass
class ResearchResult:
    """Everything research found, or an honest account of why it found nothing."""
    manufacturer_url: str = ""
    reference_urls: List[str] = field(default_factory=list)
    documents: Dict[str, str] = field(default_factory=dict)
    marketing_copy: str = ""
    features: List[str] = field(default_factory=list)
    approvals: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    attempted: bool = False
    skipped_reason: str = ""

    @property
    def found_anything(self) -> bool:
        return bool(self.manufacturer_url or self.reference_urls or self.documents
                    or self.marketing_copy)

    def as_dict(self) -> Dict[str, object]:
        return {
            "manufacturer_url": self.manufacturer_url,
            "reference_urls": list(self.reference_urls),
            "documents": dict(self.documents),
            "marketing_copy": self.marketing_copy,
            "features": list(self.features),
            "approvals": list(self.approvals),
            "attempted": self.attempted,
            "skipped_reason": self.skipped_reason,
            "evidence": [e.as_dict() for e in self.evidence],
        }


def classify_source(url: str, brand: Optional[str],
                    manufacturer: Optional[str]) -> SourceCandidate:
    """
    Score a URL against the manufacturer source hierarchy.

    Returns a candidate whose ``score`` is 0.0 when the URL must not be used.
    """
    cand = SourceCandidate(url=url)
    host = _host(url)
    if not host:
        cand.reason = "unparseable URL"
        return cand

    if any(b in host for b in BLOCKED_HOSTS):
        cand.reason = "marketplace or social host is not an approved source"
        return cand

    tokens = _domain_tokens(brand or "") + _domain_tokens(manufacturer or "")
    on_manufacturer_domain = any(t and t in host for t in tokens if len(t) >= 3)

    path = (urlparse(url).path or "").lower()
    is_doc = path.endswith(DOC_EXTENSIONS)

    if on_manufacturer_domain:
        if is_doc:
            cand.tier = SourceTier.MANUFACTURER_DOC.value
            cand.score = 0.98
            cand.reason = "technical document on the manufacturer domain"
        elif re.search(r"/(p|product|products|sku|item|model)/", path):
            cand.tier = SourceTier.MANUFACTURER_PRODUCT_PAGE.value
            cand.score = 1.0
            cand.reason = "product page on the manufacturer domain"
        else:
            cand.tier = SourceTier.MANUFACTURER_SITE.value
            cand.score = 0.9
            cand.reason = "manufacturer domain"
        return cand

    if any(d in host for d in DISTRIBUTOR_HINTS):
        cand.tier = SourceTier.DISTRIBUTOR.value
        cand.score = 0.35
        cand.reason = "distributor page; fallback use only"
        return cand

    cand.tier = SourceTier.UNVERIFIED.value
    cand.score = 0.15
    cand.reason = "host is not identifiable as the manufacturer"
    return cand


def classify_document(url: str, anchor_text: str = "") -> Optional[str]:
    """Map a document URL/anchor onto a delivery-format asset column key."""
    blob = f"{url} {anchor_text}".lower()
    for keyword, key in DOC_KEYWORDS:
        if keyword in blob:
            return key
    return "specification_sheet" if url.lower().endswith(".pdf") else None


class ResearchAgent:
    """
    Retrieves manufacturer evidence.

    The search backend is pluggable.  ``search_provider='none'`` (the default) makes
    the agent a no-op that reports why it did nothing, so the rest of the pipeline
    behaves identically online and offline.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None

    # -- backends ----------------------------------------------------------
    def _http(self):
        if self._client is None:
            import httpx
            self._client = httpx.Client(
                timeout=self.settings.research_timeout,
                follow_redirects=True,
                headers={"User-Agent": "UnilogProductIntelligence/1.0 (+enrichment-bot)"},
            )
        return self._client

    def _search(self, query: str) -> List[Dict[str, str]]:
        """Provider-specific web search. Returns [{url,title,snippet}]."""
        provider = (self.settings.search_provider or "none").lower()
        if provider == "none" or not self.settings.search_api_key:
            return []
        try:
            if provider == "tavily":
                r = self._http().post(
                    "https://api.tavily.com/search",
                    json={"api_key": self.settings.search_api_key, "query": query,
                          "max_results": self.settings.research_max_pages * 2})
                r.raise_for_status()
                return [{"url": x.get("url", ""), "title": x.get("title", ""),
                         "snippet": x.get("content", "")}
                        for x in (r.json().get("results") or [])]
            if provider == "serper":
                r = self._http().post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": self.settings.search_api_key},
                    json={"q": query, "num": self.settings.research_max_pages * 2})
                r.raise_for_status()
                return [{"url": x.get("link", ""), "title": x.get("title", ""),
                         "snippet": x.get("snippet", "")}
                        for x in (r.json().get("organic") or [])]
        except Exception:
            return []
        return []

    # -- public ------------------------------------------------------------
    def research(self, mpn: str, brand: Optional[str],
                 manufacturer: Optional[str],
                 product_name: str = "") -> ResearchResult:
        result = ResearchResult()

        if not self.settings.research_enabled:
            result.skipped_reason = ("manufacturer research disabled "
                                     "(set RESEARCH_ENABLED=true and a SEARCH_API_KEY)")
            return result
        if (self.settings.search_provider or "none").lower() == "none" \
                or not self.settings.search_api_key:
            result.skipped_reason = "no search provider configured"
            return result
        if not mpn:
            result.skipped_reason = "no part number to search on"
            return result

        result.attempted = True
        query = " ".join(t for t in (brand or manufacturer or "", mpn,
                                     product_name, "specifications") if t)
        hits = self._search(query)
        if not hits:
            result.skipped_reason = "search returned no results"
            return result

        scored: List[SourceCandidate] = []
        for h in hits:
            c = classify_source(h.get("url", ""), brand, manufacturer)
            c.title, c.snippet = h.get("title", ""), h.get("snippet", "")
            if c.score > 0:
                scored.append(c)
        scored.sort(key=lambda c: -c.score)

        approved = [c for c in scored
                    if c.tier != SourceTier.UNVERIFIED.value
                    and c.tier != SourceTier.DISTRIBUTOR.value]
        if not approved:
            result.skipped_reason = ("no approved manufacturer source found; "
                                     "distributor and marketplace results discarded")
            return result

        # The primary manufacturer URL is the highest-tier non-document page.
        page = next((c for c in approved
                     if c.tier in (SourceTier.MANUFACTURER_PRODUCT_PAGE.value,
                                   SourceTier.MANUFACTURER_SITE.value)), None)
        if page is not None:
            result.manufacturer_url = page.url
            result.evidence.append(Evidence(
                source="manufacturer_research", tier=page.tier,
                snippet=page.snippet[:400], url=page.url,
                locator=page.reason, retrieved_at=_now()))

        for c in approved[: self.settings.research_max_pages]:
            key = classify_document(c.url, c.title)
            if key and c.url.lower().endswith(DOC_EXTENSIONS):
                result.documents.setdefault(key, c.url)
                result.reference_urls.append(c.url)
                result.evidence.append(Evidence(
                    source="manufacturer_research", tier=c.tier,
                    snippet=c.title[:200], url=c.url, locator=key, retrieved_at=_now()))

        # Marketing copy is only accepted verbatim from an approved page's snippet.
        if page is not None and page.snippet and len(page.snippet) > 80:
            result.marketing_copy = page.snippet.strip()

        return result

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
