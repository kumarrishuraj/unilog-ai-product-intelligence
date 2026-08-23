"""
Agent 5 -- Attribute extraction and normalisation.

Extraction order per attribute slot (first hit wins, and every hit records how it
was obtained so the Evidence Graph can explain it):

  1. **LOV synonym match** -- the attribute has a controlled value list and one of
     its synonyms appears in the text.  'SS' -> 'Stainless Steel'.  Highest trust,
     because the mapping comes from the vocabulary rather than from a guess.
  2. **Declared regex** -- the leaf template supplies ``extract_patterns``.
  3. **Dimension parsing** -- ordered imperial chains such as
     '5"x.045"x7/8"' are mapped onto the leaf's ordered dimension slots.
  4. **UOM-typed scan** -- the attribute declares a UOM; find '<number> <that unit>'.

Anything not found stays empty.  An empty attribute slot is *still emitted* with its
label, because the labelled delivery-format rows prove the label sequence belongs to
the category, not to what happened to be extracted.

Values are then normalised deterministically: fractions via the exact ladder, units
via the approved UOM registry, casing via house rules.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from backend.models.product import AttributeValue, Evidence, SourceTier
from backend.normalization import fractions as fx
from backend.normalization.text import normalize_whitespace, title_case
from backend.normalization.uom import UomRegistry, split_measure
from backend.reference.schema import LeafNode, LovAttribute

# Confidence by extraction method.
METHOD_CONFIDENCE = {
    "lov_synonym": 0.94,
    "lov_exact": 0.96,
    "regex": 0.88,
    "dimension_chain": 0.82,
    "uom_scan": 0.80,
    "llm": 0.70,
}

# Attribute labels that consume an ordered imperial dimension chain, in order.
DIMENSION_CHAINS: Dict[str, Tuple[str, ...]] = {
    "cutting_grinding_wheels": ("Diameter", "Thickness", "Arbor Size"),
    "saw_blades": ("Diameter", "Arbor Size"),
    "fasteners": ("Length",),
}

# '5"x.045"x7/8"' or '4-1/2 x 1/8 x 7/8'.  Alternation is ordered longest-first so
# '4-1/2' is not truncated to '4', and '\d*\.\d+' admits leading-dot decimals ('.045')
# which industrial thickness callouts use constantly.
_DIM_TOKEN = r"(?:\d+\s*-\s*\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d*\.\d+|\d+)"
_DIM_CHAIN_RE = re.compile(
    r"(?P<a>%s)\s*(?:\"|in)?\s*[xX]\s*(?P<b>%s)\s*(?:\"|in)?"
    r"(?:\s*[xX]\s*(?P<c>%s)\s*(?:\"|in)?)?" % (_DIM_TOKEN, _DIM_TOKEN, _DIM_TOKEN)
)

# Leading decimals such as '.045' must become '0.045' before conversion.
_BARE_DECIMAL_RE = re.compile(r"^\.(\d+)$")


@dataclass
class ExtractionHit:
    value: str
    method: str
    snippet: str
    uom: Optional[str] = None


def _normalise_number(token: str) -> str:
    """'.045' -> '0.045'; '4-1/2' stays; decimals snap onto the fraction ladder."""
    t = str(token or "").strip()
    m = _BARE_DECIMAL_RE.match(t)
    if m:
        t = "0." + m.group(1)
    converted = fx.normalize_measure(t)
    return converted if converted is not None else t


class AttributeExtractor:
    """Deterministic, explainable attribute extraction against a leaf template."""

    def __init__(self, uom: UomRegistry):
        self.uom = uom

    # -- public ----------------------------------------------------------
    def extract(self, leaf: LeafNode, text: str,
                extra_text: Sequence[str] = ()) -> List[AttributeValue]:
        """
        Return one AttributeValue per template slot, in template order.
        Slots with no evidence come back present-but-empty.
        """
        haystacks: List[Tuple[str, str]] = [("input_feed:Part_Desc", text or "")]
        haystacks += [(f"evidence:{i}", t) for i, t in enumerate(extra_text) if t]

        chain_values = self._dimension_chain(leaf, text or "")
        out: List[AttributeValue] = []
        for attr in leaf.attributes:
            av = self._extract_one(attr, haystacks, chain_values)
            out.append(av)
        return out

    # -- per-attribute ---------------------------------------------------
    def _extract_one(self, attr: LovAttribute, haystacks: Sequence[Tuple[str, str]],
                     chain_values: Dict[str, str]) -> AttributeValue:
        for source, text in haystacks:
            if not text:
                continue

            hit = (self._match_lov(attr, text)
                   or self._match_regex(attr, text)
                   or self._match_chain(attr, chain_values)
                   or self._match_uom(attr, text))
            if hit is None:
                continue

            value, uom_abbrev, transformation = self._normalise(attr, hit)
            if not value:
                continue

            lov_ok: Optional[bool] = None
            if attr.values:
                lov_ok = attr.match_value(value) is not None

            tier = (SourceTier.CONTROLLED_VOCAB.value if hit.method.startswith("lov")
                    else SourceTier.DETERMINISTIC.value)
            return AttributeValue(
                label=attr.label,
                value=value,
                uom=uom_abbrev,
                raw=hit.snippet,
                confidence=METHOD_CONFIDENCE.get(hit.method, 0.6),
                method=hit.method,
                lov_compliant=lov_ok,
                transformation=transformation,
                evidence=[Evidence(source=source, tier=tier, snippet=hit.snippet,
                                   locator=attr.label)],
            )

        return AttributeValue(label=attr.label, value=None, uom=None, confidence=0.0,
                              method="not_found",
                              lov_compliant=None if not attr.values else None)

    # -- matchers ---------------------------------------------------------
    @staticmethod
    def _match_lov(attr: LovAttribute, text: str) -> Optional[ExtractionHit]:
        """Longest synonym first so 'Black Stainless Steel' beats 'Black'."""
        if not attr.values:
            return None
        candidates: List[Tuple[str, str, str]] = []      # (surface, canonical, kind)
        for v in attr.values:
            candidates.append((v.value, v.value, "lov_exact"))
            for s in v.synonyms:
                candidates.append((s, v.value, "lov_synonym"))
        candidates.sort(key=lambda c: -len(c[0]))
        for surface, canonical, kind in candidates:
            if not surface:
                continue
            rx = re.compile(r"(?<![A-Za-z0-9])" + re.escape(surface).replace(r"\ ", r"[\s\-]*")
                            + r"(?![A-Za-z0-9])", re.IGNORECASE)
            m = rx.search(text)
            if m:
                return ExtractionHit(canonical, kind, m.group(0))
        return None

    @staticmethod
    def _match_regex(attr: LovAttribute, text: str) -> Optional[ExtractionHit]:
        for pat in attr.extract_patterns:
            try:
                m = re.search(pat, text, re.IGNORECASE)
            except re.error:
                continue
            if m:
                val = m.group(1) if m.groups() else m.group(0)
                if val:
                    return ExtractionHit(val.strip(), "regex", m.group(0))
        return None

    @staticmethod
    def _match_chain(attr: LovAttribute, chain: Dict[str, str]) -> Optional[ExtractionHit]:
        v = chain.get(attr.label)
        return ExtractionHit(v, "dimension_chain", v) if v else None

    def _match_uom(self, attr: LovAttribute, text: str) -> Optional[ExtractionHit]:
        """Find '<number> <declared unit>' anywhere in the text."""
        if not attr.uom:
            return None
        entry = self.uom.entry(attr.uom)
        aliases = [attr.uom] + list(entry.aliases if entry else ())
        alt = "|".join(sorted((re.escape(a) for a in aliases if a), key=len, reverse=True))
        if not alt:
            return None
        rx = re.compile(r"(?<![A-Za-z0-9])(\d+(?:-\d+/\d+)?(?:\.\d+)?(?:/\d+)?)\s*"
                        r"(?:%s)(?![A-Za-z0-9])" % alt, re.IGNORECASE)
        m = rx.search(text)
        if m:
            return ExtractionHit(m.group(1), "uom_scan", m.group(0), uom=attr.uom)
        return None

    # -- dimension chains -------------------------------------------------
    def _dimension_chain(self, leaf: LeafNode, text: str) -> Dict[str, str]:
        """
        Map an ordered imperial chain onto the leaf's dimension slots.

            '5"x.045"x7/8"' on a cut-off wheel
                -> Diameter 5 in, Thickness 0.045 in, Arbor Size 7/8 in
        """
        labels = DIMENSION_CHAINS.get(leaf.id)
        if not labels:
            return {}
        m = _DIM_CHAIN_RE.search(text or "")
        if not m:
            return {}
        raw = [m.group("a"), m.group("b"), m.group("c")]
        out: Dict[str, str] = {}
        for label, tok in zip(labels, raw):
            if tok:
                out[label] = _normalise_number(tok)
        return out

    # -- normalisation ----------------------------------------------------
    def _normalise(self, attr: LovAttribute,
                   hit: ExtractionHit) -> Tuple[Optional[str], Optional[str], str]:
        """
        Returns (value, uom_abbreviation, transformation_description).

        The value column never carries its unit -- the labelled rows keep them in
        separate ATTRIBUTE_VALUE / ATTRIBUTE_UOM columns.
        """
        raw = normalize_whitespace(hit.value)
        if not raw:
            return None, None, ""

        steps: List[str] = []

        # A controlled value is already canonical.
        if hit.method.startswith("lov"):
            if hit.snippet.strip().lower() != raw.lower():
                steps.append(f"LOV synonym '{hit.snippet}' -> '{raw}'")
            else:
                steps.append("matched approved LOV value")
            return raw, None, "; ".join(steps)

        # Split any unit the token carried with it.
        value, unit = raw, hit.uom
        parts = split_measure(raw)
        if parts:
            value, embedded = parts
            unit = embedded or unit

        # Exact fraction normalisation.
        normalised = _normalise_number(value)
        if normalised != value:
            steps.append(f"decimal '{value}' -> fraction '{normalised}'")
        value = normalised

        # Approved-UOM resolution: prefer the template's declared unit.
        abbrev: Optional[str] = None
        target = attr.uom or unit
        if target:
            res = self.uom.resolve(target)
            if res.ok:
                abbrev = res.abbreviation
                if res.method == "alias":
                    steps.append(f"unit '{target}' -> approved '{abbrev}'")
            else:
                abbrev = None
                steps.append(f"unit '{target}' is not an approved UOM; unit omitted")

        if not steps:
            steps.append(f"extracted by {hit.method}")
        return value, abbrev, "; ".join(steps)


# ---------------------------------------------------------------------------
# Post-extraction enrichment shared by several leaves
# ---------------------------------------------------------------------------
def apply_series_and_model(attributes: List[AttributeValue], description: str,
                           mpn: Optional[str]) -> None:
    """
    Fill the near-universal 'Series' / 'Model' slots.

    'Model' is the manufacturer part number when the template exposes the slot --
    that is a restatement of input data, not an invention.  'Series' is only filled
    from an explicit '<Word> Series' phrase; it is never guessed.
    """
    for av in attributes:
        if av.present:
            continue
        if av.label.lower() == "model" and mpn:
            av.value = mpn
            av.confidence = 0.95
            av.method = "input_restatement"
            av.transformation = "model number restated from Mfg_Part_Num"
            av.evidence.append(Evidence(source="input_feed:Mfg_Part_Num",
                                        tier=SourceTier.INPUT_FEED.value, snippet=mpn))
        elif av.label.lower() == "series":
            m = re.search(r"([A-Z][A-Za-z0-9+.]*(?:\s+[A-Z][A-Za-z0-9+.]*)?)\s+Series\b",
                          description or "")
            if m:
                av.value = title_case(m.group(1)) + " Series"
                av.confidence = 0.90
                av.method = "regex"
                av.transformation = "series name taken verbatim from the description"
                av.evidence.append(Evidence(source="input_feed:Part_Desc",
                                            tier=SourceTier.INPUT_FEED.value,
                                            snippet=m.group(0)))
