"""
Core domain models.

Every enriched value is a ``FieldValue``: a value plus its confidence, its evidence
and the transformation that produced it.  Nothing in the pipeline passes a bare
string around, which is what makes the Evidence Graph and the explainability panel
possible without retro-fitting provenance later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------
class ConfidenceBand(str, Enum):
    HIGH = "HIGH"          # official source or exact master-data match
    MEDIUM = "MEDIUM"      # strong fuzzy/semantic match backed by evidence
    LOW = "LOW"            # inferred from limited information
    UNKNOWN = "UNKNOWN"    # no reliable evidence


HIGH_THRESHOLD = 0.85
MEDIUM_THRESHOLD = 0.65
LOW_THRESHOLD = 0.35


def band_for(confidence: Optional[float]) -> ConfidenceBand:
    if confidence is None:
        return ConfidenceBand.UNKNOWN
    if confidence >= HIGH_THRESHOLD:
        return ConfidenceBand.HIGH
    if confidence >= MEDIUM_THRESHOLD:
        return ConfidenceBand.MEDIUM
    if confidence >= LOW_THRESHOLD:
        return ConfidenceBand.LOW
    return ConfidenceBand.UNKNOWN


class ProcessingStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


class SourceTier(str, Enum):
    """Manufacturer source hierarchy (Golden Rule 3), strongest first."""
    MANUFACTURER_SITE = "manufacturer_site"
    MANUFACTURER_PRODUCT_PAGE = "manufacturer_product_page"
    MANUFACTURER_DOC = "manufacturer_documentation"
    MANUFACTURER_CATALOG = "manufacturer_catalog"
    MASTER_DATA = "master_data"
    INPUT_FEED = "input_feed"
    DETERMINISTIC = "deterministic_transform"
    CONTROLLED_VOCAB = "controlled_vocabulary"
    DISTRIBUTOR = "distributor"
    UNVERIFIED = "unverified"


# Weight applied to a claim's confidence based on where it came from.
SOURCE_TIER_WEIGHT: Dict[str, float] = {
    SourceTier.MANUFACTURER_PRODUCT_PAGE.value: 1.00,
    SourceTier.MANUFACTURER_DOC.value: 0.98,
    SourceTier.MANUFACTURER_SITE.value: 0.95,
    SourceTier.MANUFACTURER_CATALOG.value: 0.92,
    SourceTier.MASTER_DATA.value: 1.00,
    SourceTier.CONTROLLED_VOCAB.value: 0.98,
    SourceTier.DETERMINISTIC.value: 1.00,
    SourceTier.INPUT_FEED.value: 0.90,
    SourceTier.DISTRIBUTOR.value: 0.60,
    SourceTier.UNVERIFIED.value: 0.35,
}


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
@dataclass
class Evidence:
    """One supporting observation for a generated value."""
    source: str                       # e.g. 'input_feed:Part_Desc', 'lov:Material'
    tier: str = SourceTier.UNVERIFIED.value
    snippet: str = ""                 # the exact text the claim rests on
    url: str = ""
    locator: str = ""                 # field name, page number, selector
    retrieved_at: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source, "tier": self.tier, "snippet": self.snippet,
            "url": self.url, "locator": self.locator, "retrieved_at": self.retrieved_at,
        }


@dataclass
class FieldValue:
    """A single enriched field: value + confidence + evidence + transformation."""
    value: Optional[str] = None
    confidence: float = 0.0
    method: str = ""                  # exact | alias | fuzzy | semantic | llm | rule
    transformation: str = ""          # human-readable 'raw -> value' explanation
    raw: Optional[str] = None
    evidence: List[Evidence] = field(default_factory=list)
    validation: str = "PENDING"       # PASS | WARN | FAIL | PENDING
    notes: List[str] = field(default_factory=list)

    @property
    def band(self) -> ConfidenceBand:
        return band_for(self.confidence if self.value else None)

    @property
    def present(self) -> bool:
        return bool(self.value and str(self.value).strip())

    def add_evidence(self, ev: Evidence) -> "FieldValue":
        self.evidence.append(ev)
        return self

    def as_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value, "confidence": round(self.confidence, 4),
            "band": self.band.value, "method": self.method,
            "transformation": self.transformation, "raw": self.raw,
            "validation": self.validation, "notes": list(self.notes),
            "evidence": [e.as_dict() for e in self.evidence],
        }

    @classmethod
    def empty(cls, note: str = "") -> "FieldValue":
        return cls(value=None, confidence=0.0, method="none",
                   notes=[note] if note else [])


@dataclass
class AttributeValue:
    """One populated attribute slot from the leaf-node template."""
    label: str
    value: Optional[str] = None
    uom: Optional[str] = None
    raw: Optional[str] = None
    confidence: float = 0.0
    method: str = ""
    lov_compliant: Optional[bool] = None   # None when the attribute is open-vocabulary
    evidence: List[Evidence] = field(default_factory=list)
    transformation: str = ""

    @property
    def present(self) -> bool:
        return bool(self.value and str(self.value).strip())

    @property
    def band(self) -> ConfidenceBand:
        return band_for(self.confidence if self.present else None)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label, "value": self.value, "uom": self.uom,
            "raw": self.raw, "confidence": round(self.confidence, 4),
            "band": self.band.value, "method": self.method,
            "lov_compliant": self.lov_compliant, "transformation": self.transformation,
            "evidence": [e.as_dict() for e in self.evidence],
        }


# ---------------------------------------------------------------------------
# Validation / review
# ---------------------------------------------------------------------------
@dataclass
class ValidationIssue:
    field: str
    severity: str          # error | warning
    code: str
    message: str
    actual: Optional[str] = None
    expected: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field, "severity": self.severity, "code": self.code,
            "message": self.message, "actual": self.actual, "expected": self.expected,
        }


@dataclass
class ReviewFlag:
    reason: str
    field: str = ""
    detail: str = ""
    suggested_value: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"reason": self.reason, "field": self.field, "detail": self.detail,
                "suggested_value": self.suggested_value}


# ---------------------------------------------------------------------------
# The enriched product
# ---------------------------------------------------------------------------
@dataclass
class EnrichedProduct:
    """Everything the pipeline knows about one input row."""
    row_index: int
    raw: Dict[str, Any] = field(default_factory=dict)
    cleaned: Dict[str, Optional[str]] = field(default_factory=dict)
    placeholders: List[str] = field(default_factory=list)
    # Agent 1 output: the structured read of what the row literally says.
    parsed: Dict[str, Any] = field(default_factory=dict)

    # Resolved entities
    mpn: FieldValue = field(default_factory=FieldValue.empty)
    manufacturer: FieldValue = field(default_factory=FieldValue.empty)
    manufacturer_code: FieldValue = field(default_factory=FieldValue.empty)
    brand: FieldValue = field(default_factory=FieldValue.empty)
    supplier: FieldValue = field(default_factory=FieldValue.empty)

    # Classification
    leaf_id: Optional[str] = None
    classpath: FieldValue = field(default_factory=FieldValue.empty)
    dept: FieldValue = field(default_factory=FieldValue.empty)
    klass: FieldValue = field(default_factory=FieldValue.empty)
    fine: FieldValue = field(default_factory=FieldValue.empty)
    product_name: FieldValue = field(default_factory=FieldValue.empty)
    unspsc: FieldValue = field(default_factory=FieldValue.empty)
    classification_candidates: List[Dict[str, Any]] = field(default_factory=list)

    # Attributes in leaf-template order (blank slots preserved).
    attributes: List[AttributeValue] = field(default_factory=list)

    # Generated copy
    descriptions: Dict[str, FieldValue] = field(default_factory=dict)
    features: List[str] = field(default_factory=list)
    with_clause: FieldValue = field(default_factory=FieldValue.empty)
    approvals: FieldValue = field(default_factory=FieldValue.empty)

    # Research / assets
    urls: Dict[str, str] = field(default_factory=dict)
    assets: Dict[str, str] = field(default_factory=dict)

    # Quality
    issues: List[ValidationIssue] = field(default_factory=list)
    review_flags: List[ReviewFlag] = field(default_factory=list)
    confidence: float = 0.0
    confidence_breakdown: Dict[str, float] = field(default_factory=dict)
    status: ProcessingStatus = ProcessingStatus.PARTIAL
    stage_log: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    # -- helpers ----------------------------------------------------------
    @property
    def needs_review(self) -> bool:
        return bool(self.review_flags) or self.status in (
            ProcessingStatus.NEEDS_REVIEW, ProcessingStatus.FAILED)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def band(self) -> ConfidenceBand:
        return band_for(self.confidence)

    def attribute(self, label: str) -> Optional[AttributeValue]:
        for a in self.attributes:
            if a.label.lower() == str(label).lower():
                return a
        return None

    def attribute_value(self, label: str) -> Optional[str]:
        a = self.attribute(label)
        return a.value if a and a.present else None

    def populated_attributes(self) -> List[AttributeValue]:
        return [a for a in self.attributes if a.present]

    def description(self, key: str) -> Optional[str]:
        fv = self.descriptions.get(key)
        return fv.value if fv and fv.present else None

    def log_stage(self, stage: str, detail: str = "", **extra: Any) -> None:
        self.stage_log.append({"stage": stage, "detail": detail, **extra})

    def flag(self, reason: str, field_name: str = "", detail: str = "",
             suggested: Optional[str] = None) -> None:
        # Do not duplicate an identical flag.
        for f in self.review_flags:
            if f.reason == reason and f.field == field_name:
                return
        self.review_flags.append(ReviewFlag(reason, field_name, detail, suggested))

    def add_issue(self, field_name: str, severity: str, code: str, message: str,
                  actual: Optional[str] = None, expected: Optional[str] = None) -> None:
        self.issues.append(ValidationIssue(field_name, severity, code, message, actual, expected))

    def as_dict(self, include_raw: bool = True) -> Dict[str, Any]:
        return {
            "row_index": self.row_index,
            "raw": dict(self.raw) if include_raw else {},
            "cleaned": dict(self.cleaned),
            "placeholders": list(self.placeholders),
            "parsed": dict(self.parsed),
            "mpn": self.mpn.as_dict(),
            "manufacturer": self.manufacturer.as_dict(),
            "manufacturer_code": self.manufacturer_code.as_dict(),
            "brand": self.brand.as_dict(),
            "supplier": self.supplier.as_dict(),
            "leaf_id": self.leaf_id,
            "classpath": self.classpath.as_dict(),
            "dept": self.dept.as_dict(),
            "class": self.klass.as_dict(),
            "fine": self.fine.as_dict(),
            "product_name": self.product_name.as_dict(),
            "unspsc": self.unspsc.as_dict(),
            "classification_candidates": list(self.classification_candidates),
            "attributes": [a.as_dict() for a in self.attributes],
            "descriptions": {k: v.as_dict() for k, v in self.descriptions.items()},
            "features": list(self.features),
            "with_clause": self.with_clause.as_dict(),
            "approvals": self.approvals.as_dict(),
            "urls": dict(self.urls),
            "assets": dict(self.assets),
            "issues": [i.as_dict() for i in self.issues],
            "review_flags": [f.as_dict() for f in self.review_flags],
            "confidence": round(self.confidence, 4),
            "confidence_band": self.band.value,
            "confidence_breakdown": {k: round(v, 4) for k, v in self.confidence_breakdown.items()},
            "status": self.status.value,
            "stage_log": list(self.stage_log),
            "error": self.error,
        }
