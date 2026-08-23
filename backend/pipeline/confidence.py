"""
Confidence and human-review policy.

Product confidence is a weighted blend of six components.  The weights follow the
brief's baseline, with two deliberate changes:

  * **Evidence and validation are multiplicative gates, not additive terms.**
    Additively, a product with a perfect manufacturer match but a failing
    description could still score ~0.8.  That is misleading: a record that fails
    validation is not 80% publishable, it is unpublishable.  Validation failures
    therefore scale the whole score.
  * **Attribute confidence is coverage-weighted.**  Extracting two attributes at
    0.95 from a 15-slot template is weaker evidence than extracting twelve at 0.90,
    so the component multiplies mean confidence by slot coverage.

Every component is reported separately in ``confidence_breakdown`` so the UI can
show which part of the pipeline limited the score.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from backend.models.product import (
    ConfidenceBand, EnrichedProduct, ProcessingStatus, SourceTier, band_for,
)

# Additive component weights (sum to 1.0).
WEIGHTS: Dict[str, float] = {
    "manufacturer": 0.22,
    "brand": 0.15,
    "classification": 0.22,
    "attributes": 0.24,
    "descriptions": 0.10,
    "evidence": 0.07,
}

# Review thresholds.
MANUFACTURER_REVIEW_BELOW = 0.60
BRAND_REVIEW_BELOW = 0.60
CLASSIFICATION_REVIEW_BELOW = 0.60
PRODUCT_REVIEW_BELOW = 0.62
MIN_ATTRIBUTE_COVERAGE = 0.25

# A validation failure caps the record here regardless of everything else.
VALIDATION_FAILURE_CAP = 0.45


@dataclass
class ConfidenceResult:
    score: float
    breakdown: Dict[str, float]
    band: ConfidenceBand


def _attribute_component(product: EnrichedProduct) -> float:
    total = len(product.attributes)
    if not total:
        return 0.0
    populated = product.populated_attributes()
    if not populated:
        return 0.0
    mean_conf = sum(a.confidence for a in populated) / len(populated)
    coverage = len(populated) / total
    # Coverage is dampened: a template with many optional slots should not be
    # punished as hard as one where the core attributes are missing.
    return mean_conf * (0.45 + 0.55 * coverage)


def _description_component(product: EnrichedProduct) -> float:
    fields = [fv for fv in product.descriptions.values() if fv.present]
    if not fields:
        return 0.0
    mean_conf = sum(fv.confidence for fv in fields) / len(fields)
    # Penalise per description-level error found by the validator.
    desc_errors = sum(1 for i in product.errors if i.field in product.descriptions)
    return max(0.0, mean_conf - 0.25 * desc_errors)


def _evidence_component(product: EnrichedProduct) -> float:
    """Share of populated fields whose evidence is manufacturer- or master-grade."""
    strong_tiers = {
        SourceTier.MANUFACTURER_PRODUCT_PAGE.value, SourceTier.MANUFACTURER_DOC.value,
        SourceTier.MANUFACTURER_SITE.value, SourceTier.MANUFACTURER_CATALOG.value,
        SourceTier.MASTER_DATA.value, SourceTier.CONTROLLED_VOCAB.value,
    }
    considered = 0
    strong = 0
    for fv in (product.manufacturer, product.brand, product.classpath, product.mpn):
        if fv.present:
            considered += 1
            if any(e.tier in strong_tiers for e in fv.evidence):
                strong += 1
    for av in product.populated_attributes():
        considered += 1
        if any(e.tier in strong_tiers for e in av.evidence):
            strong += 1
    return strong / considered if considered else 0.0


def score_product(product: EnrichedProduct,
                  validation_failed: bool = False) -> ConfidenceResult:
    breakdown = {
        "manufacturer": product.manufacturer.confidence if product.manufacturer.present else 0.0,
        "brand": product.brand.confidence if product.brand.present else 0.0,
        "classification": product.classpath.confidence if product.classpath.present else 0.0,
        "attributes": _attribute_component(product),
        "descriptions": _description_component(product),
        "evidence": _evidence_component(product),
    }
    base = sum(WEIGHTS[k] * v for k, v in breakdown.items())

    if validation_failed:
        base = min(base, VALIDATION_FAILURE_CAP)
        breakdown["validation_penalty"] = 1.0

    score = round(max(0.0, min(1.0, base)), 4)
    return ConfidenceResult(score, {k: round(v, 4) for k, v in breakdown.items()},
                            band_for(score))


def apply_review_policy(product: EnrichedProduct) -> ProcessingStatus:
    """
    Decide the record's processing status and record every reason it needs a human.

    Reasons are additive and specific -- 'Manufacturer ambiguity' rather than 'low
    confidence' -- so the review queue is actionable.
    """
    if product.error:
        product.flag("Processing error", detail=product.error)
        return ProcessingStatus.FAILED

    if not product.manufacturer.present:
        product.flag("Manufacturer unresolved", "MANUFACTURER_NAME",
                     product.manufacturer.notes[0] if product.manufacturer.notes
                     else "no manufacturer of record could be established")
    elif product.manufacturer.confidence < MANUFACTURER_REVIEW_BELOW:
        product.flag("Manufacturer confidence below threshold", "MANUFACTURER_NAME",
                     f"{product.manufacturer.confidence:.2f} < {MANUFACTURER_REVIEW_BELOW}",
                     product.manufacturer.value)

    if not product.brand.present:
        product.flag("Brand unresolved", "BRAND_NAME",
                     product.brand.notes[0] if product.brand.notes
                     else "no brand evidence found")
    elif product.brand.confidence < BRAND_REVIEW_BELOW:
        product.flag("Brand confidence below threshold", "BRAND_NAME",
                     f"{product.brand.confidence:.2f} < {BRAND_REVIEW_BELOW}",
                     product.brand.value)

    if not product.classpath.present:
        product.flag("Product could not be classified", "Classpath",
                     "no taxonomy leaf matched; generic template applied")
    elif product.classpath.confidence < CLASSIFICATION_REVIEW_BELOW:
        product.flag("Classification confidence below threshold", "Classpath",
                     f"{product.classpath.confidence:.2f} < {CLASSIFICATION_REVIEW_BELOW}",
                     product.classpath.value)

    if len(product.classification_candidates) > 1:
        top, second = product.classification_candidates[0], product.classification_candidates[1]
        if second.get("score", 0) >= 0.8 * max(top.get("score", 1), 1e-9):
            product.flag("Multiple taxonomy candidates scored similarly", "Classpath",
                         f"{top.get('leaf_id')} ({top.get('score'):.1f}) vs "
                         f"{second.get('leaf_id')} ({second.get('score'):.1f})")

    total_slots = len(product.attributes) or 1
    coverage = len(product.populated_attributes()) / total_slots
    if coverage < MIN_ATTRIBUTE_COVERAGE:
        product.flag("Attribute coverage below threshold", "__attributes__",
                     f"{coverage:.0%} of the {total_slots} template slots populated")

    for av in product.attributes:
        if av.present and av.lov_compliant is False:
            product.flag("Attribute value outside the controlled vocabulary",
                         f"ATTRIBUTE:{av.label}", f"'{av.value}' is not an approved value",
                         av.value)

    for issue in product.errors:
        product.flag("Validation error", issue.field, issue.message)

    if not any(fv.present for fv in product.descriptions.values()):
        product.flag("No description could be generated", "SHORT_DESC",
                     "insufficient verified facts to compose copy")

    if product.confidence < PRODUCT_REVIEW_BELOW:
        product.flag("Product confidence below publication threshold", "",
                     f"{product.confidence:.2f} < {PRODUCT_REVIEW_BELOW}")

    if product.errors:
        return ProcessingStatus.NEEDS_REVIEW
    if product.review_flags:
        return ProcessingStatus.NEEDS_REVIEW
    if product.warnings:
        return ProcessingStatus.PARTIAL
    return ProcessingStatus.SUCCESS
