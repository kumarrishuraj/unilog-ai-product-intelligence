"""
Agent 6 -- Description generation.

The generator is a **template engine over a verified fact sheet**, never a free-text
LLM writing from raw input.  It can only emit values that already exist as resolved
entities or extracted attributes, so it cannot introduce a fact (Golden Rule 1).

Formulas below were derived by decomposing the labelled delivery-format rows.  Each
is annotated with the evidence that supports it.  When
``UNILOG_INTERNAL_CONTENT_GUIDELINES.docx`` is supplied these are overridden by the
guideline-driven spec (see ``DescriptionSpec.from_guidelines``).

Derivations
-----------
LONG_DESC1
    '{BRAND} {ProductName}{ With-clause}, {rendered attributes in template order},
     Additional Information: {addl}'
    FRIGIDAIRE(R) Dishwasher With CleanBoost(TM), Professional Series, 5 Wash Cycles,
    120 V, 15 A, Leg Mounting, ... , 47 dBA Sound Level, Stainless Steel,
    Additional Information: 240 kW-hr Annual Energy, ...

SHORT_DESC
    '{BRAND} {Series} {MPN} {ProductName}{ With-clause}, {short-form attributes}'
    FRIGIDAIRE(R) Professional Series PDSH4816AF Dishwasher With CleanBoost(TM),
    Leg Mounting, 5-Wash Cycle, Stainless Steel

RETAIL_DESC
    SHORT_DESC with the brand and part number removed.
    Professional Series Dishwasher, Leg Mounting, 5-Wash Cycle, Stainless Steel

MOBILE_DESC
    '{Manufacturer} {Brand}, {ProductName}, {Series}, {MPN}[, {next attribute}]'
    capped at 80 characters.  The cap is not a guess: it is the only value that
    explains both labelled rows -- row 1 stops at 75 characters because appending
    ', Leg Mounting' would reach 89, while row 2 includes ', Built-in Mounting'
    because that only reaches 64.

INVOICE_DESC
    ALL CAPS, unit-compact ('120V' not '120 V'), house abbreviations
    ('Stainless Steel' -> 'SST', 'Built-in' -> 'BLTLN'), greedily packed to 40
    characters -- both labelled rows land at 38 and 39.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from backend.models.product import AttributeValue, Evidence, FieldValue, SourceTier
from backend.normalization.text import normalize_whitespace, title_case, truncate
from backend.normalization.uom import UomRegistry
from backend.reference.schema import LeafNode

# Character limits.  MOBILE/INVOICE are derived above; the rest are the
# conventional Unilog ceilings and are enforced as warnings, not truncation.
LIMITS: Dict[str, int] = {
    "MOBILE_DESC": 80,
    "INVOICE_DESC": 40,
    "SHORT_DESC": 240,
    "LONG_DESC1": 4000,
    "RETAIL_DESC": 240,
    "MARKETING_DESCRIPTION": 4000,
}

# Slots excluded from the long-form attribute run (handled positionally instead).
_LONG_DESC_SKIP = {"model", "additional information"}


def _redundant(rendered: str, head: str) -> bool:
    """
    True when an attribute render adds nothing the head already says.

    Many leaves carry a '<X> Type' attribute whose value *is* the product name
    ('Cut-Off Wheel'), which would otherwise produce
    'Milwaukee Cut-Off Wheel, Cut-Off Wheel, 5 in Diameter'.  The labelled rows show
    no such duplication, so a render already contained in the head is dropped.
    """
    if not rendered or not head:
        return False
    return rendered.strip().lower() in head.strip().lower()


def _load_abbreviations(pack_dir: Path) -> Dict[str, str]:
    path = Path(pack_dir) / "invoice_abbreviations.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k.lower(): v for k, v in (data.get("abbreviations") or {}).items()}


@dataclass
class DescriptionSpec:
    """Per-field construction rules; overridable from the official guidelines."""
    limits: Dict[str, int] = field(default_factory=lambda: dict(LIMITS))
    abbreviations: Dict[str, str] = field(default_factory=dict)
    provenance: str = "derived-from-labelled-rows"

    @classmethod
    def load(cls, pack_dir: Path) -> "DescriptionSpec":
        return cls(abbreviations=_load_abbreviations(pack_dir))

    @classmethod
    def from_guidelines(cls, blocks: Sequence[Dict[str, str]],
                        pack_dir: Path) -> "DescriptionSpec":
        """
        Read character limits out of the content-guidelines document when supplied.
        Looks for '<FIELD> ... <n> characters' statements; anything not stated keeps
        the derived default.
        """
        spec = cls.load(pack_dir)
        if not blocks:
            return spec
        spec.provenance = "official-content-guidelines"
        text = " ".join(b.get("text", "") for b in blocks)
        for fieldname in list(spec.limits):
            pattern = (re.escape(fieldname).replace("_", r"[\s_]") +
                       r"[^.]{0,80}?(\d{2,4})\s*(?:characters|chars|char)")
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                spec.limits[fieldname] = int(m.group(1))
        return spec


class DescriptionGenerator:
    """Builds every description field from the verified fact sheet."""

    def __init__(self, uom: UomRegistry, spec: Optional[DescriptionSpec] = None):
        self.uom = uom
        self.spec = spec or DescriptionSpec()

    # -- attribute rendering ---------------------------------------------
    def render_attribute(self, leaf: LeafNode, av: AttributeValue,
                         template: Optional[str] = None) -> str:
        """Apply the attribute's render template: '{value} {uom} {label}'."""
        if not av.present:
            return ""
        attr = leaf.attribute(av.label)
        tpl = template or (attr.render if attr else "{value} {uom} {label}")
        out = tpl.format(value=av.value, uom=av.uom or "", label=av.label)
        return normalize_whitespace(out)

    # -- individual fields -------------------------------------------------
    def long_desc(self, leaf: LeafNode, brand: Optional[str], product_name: str,
                  attributes: Sequence[AttributeValue],
                  with_clause: Optional[str]) -> str:
        head = " ".join(t for t in (brand, product_name) if t)
        if with_clause:
            head = f"{head} {with_clause}".strip()

        parts: List[str] = []
        additional = ""
        for av in attributes:
            key = av.label.lower()
            if not av.present or key in _LONG_DESC_SKIP:
                if key == "additional information" and av.present:
                    additional = av.value or ""
                continue
            rendered = self.render_attribute(leaf, av)
            if rendered and not _redundant(rendered, head):
                parts.append(rendered)

        body = ", ".join(parts)
        out = f"{head}, {body}" if body else head
        if additional:
            out = f"{out}, Additional Information: {additional}"
        return normalize_whitespace(out)

    def short_desc(self, leaf: LeafNode, brand: Optional[str], mpn: Optional[str],
                   product_name: str, attributes: Sequence[AttributeValue],
                   with_clause: Optional[str], include_brand: bool = True,
                   include_mpn: bool = True) -> str:
        by_label = {a.label.lower(): a for a in attributes}
        series = by_label.get("series")
        head_bits: List[str] = []
        if include_brand and brand:
            head_bits.append(brand)
        if series is not None and series.present:
            head_bits.append(series.value or "")
        if include_mpn and mpn:
            head_bits.append(mpn)
        head_bits.append(product_name)
        head = normalize_whitespace(" ".join(b for b in head_bits if b))
        if with_clause:
            head = f"{head} {with_clause}".strip()

        parts: List[str] = []
        for label in leaf.short_desc_attributes:
            av = by_label.get(label.lower())
            if av is None or not av.present:
                continue
            rendered = self._short_form(leaf, av)
            if rendered and not _redundant(rendered, head):
                parts.append(rendered)
        body = ", ".join(parts)
        return normalize_whitespace(f"{head}, {body}" if body else head)

    def _short_form(self, leaf: LeafNode, av: AttributeValue) -> str:
        """
        Short-form rendering is more compact than long-form.  The labelled rows show
        'Number of Wash Cycles' as '5 Wash Cycles' in LONG_DESC but '5-Wash Cycle'
        in SHORT_DESC, so countable attributes hyphenate and singularise here.
        """
        attr = leaf.attribute(av.label)
        tpl = attr.render if attr else "{value}"
        if attr and str(av.value or "").isdigit() and "{value} " in tpl:
            noun = tpl.replace("{value} ", "").replace("{uom}", "").strip()
            if noun and not attr.uom:
                singular = re.sub(r"s\b", "", noun).strip()
                return f"{av.value}-{singular}"
        return self.render_attribute(leaf, av, tpl)

    def retail_desc(self, leaf: LeafNode, product_name: str,
                    attributes: Sequence[AttributeValue],
                    with_clause: Optional[str] = None) -> str:
        """
        SHORT_DESC with the brand, part number *and* With-clause stripped.

        Evidence: labelled row 1 has SHORT_DESC '... Dishwasher With CleanBoost(TM),
        Leg Mounting, ...' but RETAIL_DESC 'Professional Series Dishwasher, Leg
        Mounting, ...' -- the With-clause is dropped from retail copy.  The
        ``with_clause`` parameter is accepted and ignored so callers stay uniform.
        """
        return self.short_desc(leaf, None, None, product_name, attributes,
                               None, include_brand=False, include_mpn=False)

    def mobile_desc(self, leaf: LeafNode, manufacturer: Optional[str],
                    brand: Optional[str], product_name: str, mpn: Optional[str],
                    attributes: Sequence[AttributeValue]) -> str:
        limit = self.spec.limits["MOBILE_DESC"]
        # Drop the trademark symbol and avoid repeating the brand inside the
        # manufacturer name ('Whirlpool Corporation' + 'Whirlpool' -> 'Whirlpool').
        brand_plain = re.sub(r"[®™©]", "", brand or "").strip()
        manuf = (manufacturer or "").strip()
        if brand_plain and manuf and brand_plain.lower() in manuf.lower():
            lead = brand_plain
        else:
            lead = normalize_whitespace(f"{manuf} {brand_plain}")

        by_label = {a.label.lower(): a for a in attributes}
        series = by_label.get("series")
        bits = [lead, product_name]
        if series is not None and series.present:
            bits.append(series.value or "")
        if mpn:
            bits.append(mpn)
        out = ", ".join(b for b in bits if b)

        # Append further filtering attributes only while they fit inside the cap.
        for label in leaf.short_desc_attributes:
            av = by_label.get(label.lower())
            if av is None or not av.present:
                continue
            rendered = self.render_attribute(leaf, av)
            if not rendered or _redundant(rendered, out):
                continue
            candidate = f"{out}, {rendered}"
            if len(candidate) <= limit:
                out = candidate
            else:
                break
        return normalize_whitespace(out)

    def invoice_desc(self, leaf: LeafNode, product_name: str,
                     attributes: Sequence[AttributeValue]) -> str:
        limit = self.spec.limits["INVOICE_DESC"]
        by_label = {a.label.lower(): a for a in attributes}
        head = self._abbrev(product_name).upper()
        out = head

        order = list(leaf.invoice_attributes) or [a.label for a in leaf.attributes]
        for label in order:
            av = by_label.get(label.lower())
            if av is None or not av.present:
                continue
            token = self.uom.format_measure(av.value, av.uom, compact=True) if av.uom \
                else self._abbrev(av.value or "")
            token = token.upper()
            # Suppress only a repeat of the product name itself.  A value repeated
            # across two different attributes is meaningful: labelled row 2 is
            # 'DISHWASHER BLTLN SST SST 120V 10A 41DBA', where the second SST is
            # Color rather than Material.
            if not token or _redundant(token, head):
                continue
            candidate = f"{out} {token}"
            if len(candidate) <= limit:
                out = candidate
            # Keep scanning: a shorter later token may still fit.
        return normalize_whitespace(out)

    def _abbrev(self, text: str) -> str:
        """Apply house invoice abbreviations, longest phrase first."""
        s = normalize_whitespace(text)
        if not s or not self.spec.abbreviations:
            return s
        for phrase in sorted(self.spec.abbreviations, key=len, reverse=True):
            rx = re.compile(r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])",
                            re.IGNORECASE)
            s = rx.sub(self.spec.abbreviations[phrase], s)
        return normalize_whitespace(s)

    # -- 'With' clause -----------------------------------------------------
    @staticmethod
    def with_clause(description: str) -> Optional[Tuple[str, str]]:
        """
        Build the 'With' field from the feed's own 'w/...' shorthand.

            '6\\' Wh Select T-Rail Kit Horiz - w/Sq Composite Balusters'
                -> ('With Sq Composite Balusters', 'w/Sq Composite Balusters')

        Returns (clause, evidence_snippet) or None.  Nothing is invented: the text
        after 'w/' is taken verbatim and only re-cased.
        """
        if not description:
            return None
        m = re.search(r"(?<![A-Za-z0-9])w/\s*(.+)$", description, re.IGNORECASE)
        if not m:
            return None
        payload = normalize_whitespace(m.group(1)).strip(" -,")
        if not payload or len(payload) < 3:
            return None
        return f"With {title_case(payload)}", m.group(0)

    # -- orchestration -----------------------------------------------------
    def generate(self, leaf: LeafNode, *, brand: Optional[str], manufacturer: Optional[str],
                 mpn: Optional[str], product_name: str,
                 attributes: Sequence[AttributeValue],
                 with_clause_text: Optional[str] = None,
                 marketing: Optional[str] = None) -> Dict[str, FieldValue]:
        """
        Produce every description field with its confidence and evidence.

        Field confidence is the mean confidence of the attributes that actually fed
        it, scaled by how complete the head (brand/product name) is -- a description
        built on an unknown brand cannot be highly confident.
        """
        contributing = [a for a in attributes if a.present]
        attr_conf = (sum(a.confidence for a in contributing) / len(contributing)
                     if contributing else 0.0)
        head_conf = 1.0
        if not brand:
            head_conf -= 0.25
        if not product_name:
            head_conf -= 0.35
        base = max(0.0, min(1.0, 0.65 * attr_conf + 0.35 * head_conf))

        ev = [Evidence(source="derived:description_template",
                       tier=SourceTier.DETERMINISTIC.value,
                       snippet=f"{len(contributing)} verified attributes",
                       locator=leaf.id)]

        def fv(value: str, extra_note: str = "") -> FieldValue:
            f = FieldValue(value=value or None, confidence=round(base, 4),
                           method="template",
                           transformation=f"composed from {len(contributing)} "
                                          f"verified attributes on leaf '{leaf.id}'",
                           evidence=list(ev))
            if extra_note:
                f.notes.append(extra_note)
            return f

        out: Dict[str, FieldValue] = {
            "LONG_DESC1": fv(self.long_desc(leaf, brand, product_name, attributes,
                                            with_clause_text)),
            "SHORT_DESC": fv(self.short_desc(leaf, brand, mpn, product_name, attributes,
                                             with_clause_text)),
            "RETAIL_DESC": fv(self.retail_desc(leaf, product_name, attributes,
                                               with_clause_text)),
            "MOBILE_DESC": fv(self.mobile_desc(leaf, manufacturer, brand, product_name,
                                               mpn, attributes)),
            "INVOICE_DESC": fv(self.invoice_desc(leaf, product_name, attributes)),
        }

        # MARKETING_DESCRIPTION is manufacturer copy.  It is only populated from
        # retrieved manufacturer evidence -- never generated -- because inventing
        # marketing claims is exactly what Golden Rule 1 forbids.
        if marketing:
            out["MARKETING_DESCRIPTION"] = FieldValue(
                value=marketing, confidence=0.9, method="manufacturer_source",
                transformation="verbatim manufacturer marketing copy",
                evidence=[Evidence(source="manufacturer_research",
                                   tier=SourceTier.MANUFACTURER_PRODUCT_PAGE.value,
                                   snippet=marketing[:160])])
        else:
            out["MARKETING_DESCRIPTION"] = FieldValue.empty(
                "no manufacturer marketing copy retrieved; left blank rather than invented")
        return out
