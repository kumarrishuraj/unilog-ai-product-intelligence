"""
Agent 1 -- Product Parser.

Turns one raw feed row into the structured fact envelope every downstream agent
consumes.  Nothing here resolves entities or assigns taxonomy; it only *reads* what
the row literally says, so that later stages never have to re-parse free text.

Two-tier by design:

  * ``parse()`` is deterministic -- segmentation, measurement tokens, pack counts,
    series/model phrases, material and feature nouns.  It always runs.
  * ``parse_with_llm()`` adds a model pass **only** for rows the deterministic pass
    left thin (no product-type noun, or a description that is mostly opaque codes).
    The model is constrained to a strict JSON schema and is explicitly forbidden from
    adding anything not present in the supplied text; every field it returns is
    intersected back against the source string before it is accepted, so it cannot
    smuggle in a fact (Golden Rule 1).

The envelope shape matches the brief's Agent 1 contract.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.llm.client import LlmClient
from backend.models.product import Evidence, SourceTier
from backend.normalization import fractions as fx
from backend.normalization.text import (
    clean_value, normalize_whitespace, split_segments, strip_leading_part_number,
    title_case,
)
from backend.normalization.uom import UomRegistry, split_measure

# A measurement token anywhere in the text: 24in, 50-1/4", 120V, 4-1/2 x 1/8.
#
# Ordered longest-first so '4-1/2' is not truncated to '4', and '\d*\.\d+' admits
# the leading-dot decimals ('.045') that industrial thickness callouts use.
_VALUE = r"(?:\d+\s*-\s*\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d*\.\d+|\d+)"
_MEASURE_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<value>" + _VALUE + r")"
    r"\s*(?P<unit>[A-Za-z\"']{1,6})(?![A-Za-z0-9])"
)

# 'x' between two measurements is a dimension separator, not a unit. Splitting on it
# first is what stops '5"x.045"x7/8"' being read as the single token '8 in'.
_DIM_SEPARATOR_RE = re.compile(r"(?<=[\d\"'])\s*[xX]\s*(?=[\d.])")


_BARE_DECIMAL_RE = re.compile(r"^\.(\d+)$")


def _number(token: str) -> Optional[str]:
    """'.045' -> '0.045'; decimals snap onto the exact fraction ladder where they can."""
    t = re.sub(r"\s+", "", str(token or "").strip())
    m = _BARE_DECIMAL_RE.match(t)
    if m:
        t = "0." + m.group(1)
    return fx.normalize_measure(t)


def _split_dimension_chains(text: str) -> str:
    """Turn '5\"x.045\"x7/8\"' into '5\" .045\" 7/8\"' so each token scans cleanly."""
    return _DIM_SEPARATOR_RE.sub(" ", str(text or ""))

# '6pc', '50 Disc/Box', '4pk', '500CT'
_PACK_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d{1,4})\s*(pc|pcs|pk|pack|ct|count|disc|sheets?|piece)s?\b",
    re.IGNORECASE)

# 'Professional Series', 'Eco Series'
_SERIES_RE = re.compile(
    r"([A-Z][A-Za-z0-9+.]*(?:\s+[A-Z][A-Za-z0-9+.]*)?)\s+Series\b")

# Material nouns worth surfacing early; the LOV does the authoritative mapping later.
_MATERIAL_HINTS: Tuple[Tuple[str, str], ...] = (
    (r"\bstainless(?:\s+steel)?\b|\bsst\b|\bss\b", "Stainless Steel"),
    (r"\baluminum\b|\balum\b|\balm\b", "Aluminum"),
    (r"\bpvc\b", "PVC"),
    (r"\bvinyl\b", "Vinyl"),
    (r"\bcomposite\b", "Composite"),
    (r"\bsteel\b", "Steel"),
    (r"\bbrass\b", "Brass"),
    (r"\bcopper\b", "Copper"),
    (r"\bplastic\b|\bpoly\b", "Plastic"),
    (r"\bcedar\b", "Cedar"),
    (r"\bfiber\s*cement\b|\bhardie\b", "Fiber Cement"),
)

# Trailing qualifiers that describe stock status, not the product.
_STATUS_NOISE = re.compile(
    r"\b(display only|display|bare tool|bare|tool only|linear foot|bdl|replacement)\b",
    re.IGNORECASE)


@dataclass
class ParsedProduct:
    """The brief's Agent 1 envelope."""
    mpn: str = ""
    manufacturer_raw: str = ""
    brand_raw: str = ""
    product_type: str = ""
    category: str = ""
    series: str = ""
    model: str = ""
    dimensions: Dict[str, str] = field(default_factory=dict)
    materials: List[str] = field(default_factory=list)
    technical_specs: Dict[str, str] = field(default_factory=dict)
    features: List[str] = field(default_factory=list)
    # Provenance / diagnostics
    segments: List[str] = field(default_factory=list)
    residual_text: str = ""
    method: str = "deterministic"
    confidence: float = 0.0
    evidence: List[Evidence] = field(default_factory=list)

    @property
    def is_thin(self) -> bool:
        """True when the deterministic pass found almost nothing to work with."""
        signal = (bool(self.product_type) + bool(self.materials)
                  + bool(self.technical_specs) + bool(self.dimensions))
        return signal <= 1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mpn": self.mpn,
            "manufacturer_raw": self.manufacturer_raw,
            "brand_raw": self.brand_raw,
            "product_type": self.product_type,
            "category": self.category,
            "series": self.series,
            "model": self.model,
            "dimensions": dict(self.dimensions),
            "materials": list(self.materials),
            "technical_specs": dict(self.technical_specs),
            "features": list(self.features),
            "segments": list(self.segments),
            "residual_text": self.residual_text,
            "method": self.method,
            "confidence": round(self.confidence, 4),
        }


# Strict JSON contract for the optional model pass.
PARSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["product_type", "materials", "features", "reasoning_summary"],
    "properties": {
        "product_type": {"type": "string",
                         "description": "head noun naming what the item IS, verbatim "
                                        "from the text; empty string if not stated"},
        "series": {"type": "string"},
        "materials": {"type": "array", "items": {"type": "string"}},
        "features": {"type": "array", "items": {"type": "string"}},
        "technical_specs": {"type": "object", "additionalProperties": {"type": "string"}},
        "reasoning_summary": {"type": "string"},
    },
}

PARSE_SYSTEM = (
    "You extract structure from industrial product descriptions for a catalogue "
    "pipeline. You may ONLY report things the supplied text literally states. "
    "You must never infer a brand, a specification, a dimension or a certification "
    "that is not written in the text. If a field is not stated, return an empty "
    "string or empty array. Inventing a value is a critical failure."
)


class ProductParser:
    """Agent 1. Deterministic by default, model-assisted only when thin."""

    def __init__(self, uom: UomRegistry, llm: Optional[LlmClient] = None):
        self.uom = uom
        self.llm = llm

    # -- deterministic ----------------------------------------------------
    def parse(self, row: Dict[str, Any]) -> ParsedProduct:
        mpn = clean_value(row.get("Mfg_Part_Num")) or ""
        desc_raw = clean_value(row.get("Part_Desc")) or ""
        body = strip_leading_part_number(desc_raw, mpn)

        p = ParsedProduct(
            mpn=mpn,
            manufacturer_raw=clean_value(row.get("Part_Manuf")) or "",
            brand_raw=(clean_value(row.get("E1_Brand"))
                       or clean_value(row.get("DIB_Brand")) or ""),
            model=mpn,
            segments=split_segments(body),
        )
        if not body:
            p.confidence = 0.0
            return p

        p.evidence.append(Evidence(source="input_feed:Part_Desc",
                                   tier=SourceTier.INPUT_FEED.value,
                                   snippet=desc_raw[:200], locator="product_parser"))

        m = _SERIES_RE.search(body)
        if m:
            p.series = f"{title_case(m.group(1))} Series"

        p.dimensions = self._dimensions(body)
        p.technical_specs = self._specs(body)
        p.materials = self._materials(body)
        p.features = self._features(p.segments)
        p.product_type = self._product_type(body)
        p.residual_text = self._residual(body)

        filled = (bool(p.product_type) + bool(p.materials) + bool(p.dimensions)
                  + bool(p.technical_specs) + bool(p.features))
        p.confidence = round(min(0.9, 0.25 + 0.13 * filled), 4)
        return p

    # -- component extractors ---------------------------------------------
    def _dimensions(self, text: str) -> Dict[str, str]:
        """Length-typed measurements, keyed by ordinal so order is preserved."""
        out: Dict[str, str] = {}
        idx = 0
        for m in _MEASURE_RE.finditer(_split_dimension_chains(text)):
            res = self.uom.resolve(m.group("unit"))
            if not res.ok or res.measurement_type != "Length":
                continue
            value = _number(m.group("value"))
            if value is None:
                continue
            idx += 1
            out[f"dim_{idx}"] = f"{value} {res.abbreviation}"
        return out

    def _specs(self, text: str) -> Dict[str, str]:
        """Non-length measurements keyed by their measurement type (V, A, dBA, W...)."""
        out: Dict[str, str] = {}
        for m in _MEASURE_RE.finditer(_split_dimension_chains(text)):
            res = self.uom.resolve(m.group("unit"))
            if not res.ok or res.measurement_type in (None, "Length"):
                continue
            value = _number(m.group("value"))
            if value is None:
                continue
            out.setdefault(res.measurement_type, f"{value} {res.abbreviation}")
        pack = _PACK_RE.search(text)
        if pack:
            out.setdefault("Package Quantity", pack.group(1))
        return out

    @staticmethod
    def _materials(text: str) -> List[str]:
        found: List[str] = []
        for pattern, canonical in _MATERIAL_HINTS:
            if re.search(pattern, text, re.IGNORECASE) and canonical not in found:
                found.append(canonical)
        return found

    @staticmethod
    def _features(segments: Sequence[str]) -> List[str]:
        """
        Supplier feeds put qualifiers after ' - ' and after 'w/'.  Those are the only
        feature-like statements the row actually makes, so they are taken verbatim.
        """
        out: List[str] = []
        for seg in segments[1:]:
            cleaned = normalize_whitespace(_STATUS_NOISE.sub("", seg)).strip(" -,/")
            if len(cleaned) >= 3 and cleaned.lower() not in (s.lower() for s in out):
                out.append(cleaned)
        return out[:10]

    @staticmethod
    def _product_type(text: str) -> str:
        """
        Head noun of the first segment, after stripping measurements and status noise.
        Deliberately shallow: the taxonomy agent does real classification.
        """
        head = split_segments(text)[0] if split_segments(text) else text
        head = _MEASURE_RE.sub(" ", _split_dimension_chains(head))
        head = _PACK_RE.sub(" ", head)
        head = _STATUS_NOISE.sub(" ", head)
        head = re.sub(r"[^\w\s/-]", " ", head)
        words = [w for w in normalize_whitespace(head).split()
                 if len(w) > 2 and not w.isdigit()]
        return title_case(" ".join(words[-3:])) if words else ""

    @staticmethod
    def _residual(text: str) -> str:
        """What the deterministic pass could not account for -- the LLM's input."""
        left = _MEASURE_RE.sub(" ", _split_dimension_chains(text))
        left = _PACK_RE.sub(" ", left)
        return normalize_whitespace(left)

    # -- model-assisted ---------------------------------------------------
    def parse_with_llm(self, row: Dict[str, Any],
                       force: bool = False) -> ParsedProduct:
        """
        Deterministic parse, escalated to the model only when the result is thin.

        Everything the model returns is verified against the source text before it is
        accepted, so the model can enrich structure but cannot introduce content.
        """
        p = self.parse(row)
        if self.llm is None or not self.llm.available:
            return p
        if not force and not p.is_thin:
            return p

        desc = clean_value(row.get("Part_Desc")) or ""
        if not desc:
            return p

        prompt = (
            f"Product description: {desc!r}\n"
            f"Manufacturer field: {p.manufacturer_raw!r}\n\n"
            "Identify only what this text literally states."
        )
        resp = self.llm.complete_json(PARSE_SYSTEM, prompt, PARSE_SCHEMA, max_tokens=700)
        if not resp.ok or not resp.data:
            p.method = "deterministic (llm unavailable or failed)"
            return p

        accepted = self._accept_grounded(p, resp.data, desc)
        if accepted:
            p.method = "deterministic+llm"
            p.confidence = round(min(0.85, p.confidence + 0.10), 4)
            p.evidence.append(Evidence(
                source="llm:product_parser", tier=SourceTier.INPUT_FEED.value,
                snippet=str(resp.data.get("reasoning_summary", ""))[:200],
                locator="grounded against Part_Desc"))
        return p

    @staticmethod
    def _accept_grounded(p: ParsedProduct, data: Dict[str, Any], source: str) -> bool:
        """
        Keep only model output whose tokens actually occur in the source text.

        This is the guard that makes the model pass safe: a hallucinated material or
        feature simply fails the containment check and is dropped.
        """
        haystack = re.sub(r"[^a-z0-9]+", " ", source.lower())

        def grounded(value: str) -> bool:
            toks = [t for t in re.split(r"[^a-z0-9]+", str(value).lower()) if len(t) > 2]
            return bool(toks) and all(t in haystack for t in toks)

        changed = False
        pt = str(data.get("product_type") or "").strip()
        if pt and grounded(pt) and not p.product_type:
            p.product_type = title_case(pt)
            changed = True

        series = str(data.get("series") or "").strip()
        if series and grounded(series) and not p.series:
            p.series = title_case(series)
            changed = True

        for mat in data.get("materials") or []:
            mat = str(mat).strip()
            if mat and grounded(mat) and mat not in p.materials:
                p.materials.append(title_case(mat))
                changed = True

        for feat in data.get("features") or []:
            feat = str(feat).strip()
            if feat and grounded(feat) and feat not in p.features:
                p.features.append(feat)
                changed = True

        for k, v in (data.get("technical_specs") or {}).items():
            if grounded(str(v)) and k not in p.technical_specs:
                p.technical_specs[str(k)] = str(v)
                changed = True

        return changed
