"""
Reference-data record types shared by the loaders, the bootstrap miner and the
resolvers.  Every record carries ``provenance`` so the UI can distinguish an
official master-data hit from a derived/mined one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Where a reference record came from, strongest first.
PROVENANCE_OFFICIAL = "official"    # loaded from a supplied Unilog reference workbook
PROVENANCE_DERIVED = "derived"      # mined from the supplied working data
PROVENANCE_COMPUTED = "computed"    # produced by deterministic maths (fractions)
PROVENANCE_SEED = "seed"            # standard industry values shipped with the app

PROVENANCE_RANK = {
    PROVENANCE_OFFICIAL: 1.00,
    PROVENANCE_DERIVED: 0.85,
    PROVENANCE_COMPUTED: 1.00,
    PROVENANCE_SEED: 0.70,
}


@dataclass
class ManufacturerRecord:
    name: str
    code: Optional[str] = None
    aliases: Tuple[str, ...] = ()
    provenance: str = PROVENANCE_DERIVED
    # Number of distinct brands seen against this supplier in the working data.
    # High fan-out implies a distributor/co-op rather than the actual manufacturer.
    brand_fanout: int = 0
    is_distributor: bool = False
    source: str = ""


@dataclass
class BrandRecord:
    name: str
    code: Optional[str] = None
    aliases: Tuple[str, ...] = ()
    # Manufacturer names this brand co-occurs with, strongest first.
    manufacturers: Tuple[str, ...] = ()
    provenance: str = PROVENANCE_DERIVED
    # Registered/trademark suffix as it must appear in generated copy, e.g. '®'.
    symbol: str = ""
    source: str = ""

    @property
    def display(self) -> str:
        """Brand exactly as it should appear in descriptions, incl. any symbol."""
        return f"{self.name}{self.symbol}" if self.symbol else self.name


@dataclass
class LovValue:
    """One approved value for an attribute, with the synonyms that map onto it."""
    value: str
    synonyms: Tuple[str, ...] = ()
    definition: str = ""


@dataclass
class LovAttribute:
    """
    One attribute slot on a leaf node.

    ``render`` controls how the attribute appears in generated long-form copy.  The
    labelled data shows the pattern is per-attribute, not global:

        Voltage Rating       '120 V'                     -> '{value} {uom}'
        Number of Wash Cycles'5 Wash Cycles'             -> '{value} Wash Cycles'
        Depth With Door Open '50-1/4 in Depth With Door Open'
                                                         -> '{value} {uom} {label}'
        Size                 '24 in W x 24-1/4 in D'     -> '{value}'
    """
    label: str
    uom: Optional[str] = None
    measurement_type: Optional[str] = None
    values: Tuple[LovValue, ...] = ()
    filtering: bool = False
    required: bool = False
    render: str = "{value} {uom} {label}"
    # Regex/keyword hints used by the deterministic extractor before any LLM call.
    extract_patterns: Tuple[str, ...] = ()
    definition: str = ""

    @property
    def allowed_values(self) -> Tuple[str, ...]:
        return tuple(v.value for v in self.values)

    def match_value(self, raw: str) -> Optional[str]:
        """Map a raw value onto an approved LOV value (exact or synonym). None if open."""
        if not raw:
            return None
        r = str(raw).strip().lower()
        for v in self.values:
            if v.value.lower() == r:
                return v.value
            for s in v.synonyms:
                if s.strip().lower() == r:
                    return v.value
        return None


@dataclass
class LeafNode:
    """
    A taxonomy leaf plus its attribute template.

    The attribute template is what makes the 252-column output reproducible: the
    labelled rows emit ATTRIBUTE_LABEL n even when ATTRIBUTE_VALUE n is blank, so
    the label sequence is a property of the *category*, not of what was extracted.
    """
    id: str
    classpath: str
    dept: str = ""
    klass: str = ""
    fine: str = ""
    product_name: str = ""
    unspsc: str = ""
    attributes: Tuple[LovAttribute, ...] = ()
    # Classification signals.
    keywords: Tuple[str, ...] = ()
    strong_keywords: Tuple[str, ...] = ()
    exclude_keywords: Tuple[str, ...] = ()
    # Ordered attribute labels used to build SHORT_DESC / RETAIL_DESC.
    short_desc_attributes: Tuple[str, ...] = ()
    # Abbreviations for INVOICE_DESC (ALL CAPS, <=40 chars).
    invoice_attributes: Tuple[str, ...] = ()
    provenance: str = PROVENANCE_DERIVED
    source: str = ""

    def attribute(self, label: str) -> Optional[LovAttribute]:
        for a in self.attributes:
            if a.label.lower() == str(label).lower():
                return a
        return None

    @property
    def labels(self) -> Tuple[str, ...]:
        return tuple(a.label for a in self.attributes)


@dataclass
class ReferenceStats:
    """Counts surfaced in the UI so provenance is never overstated."""
    manufacturers: int = 0
    brands: int = 0
    leaf_nodes: int = 0
    lov_values: int = 0
    uom_entries: int = 0
    fraction_entries: int = 0
    sources: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {
            "manufacturers": self.manufacturers,
            "brands": self.brands,
            "leaf_nodes": self.leaf_nodes,
            "lov_values": self.lov_values,
            "uom_entries": self.uom_entries,
            "fraction_entries": self.fraction_entries,
            "sources": dict(self.sources),
            "warnings": list(self.warnings),
        }
