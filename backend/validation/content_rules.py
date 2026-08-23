"""
Agent 7 -- Validation.

Checks are grouped so the self-correction loop can regenerate only the offending
field rather than the whole product:

  schema      -- every template column present, nothing extra, attribute slots fit
  content     -- character limits, casing, forbidden filler text
  uom         -- every emitted ATTRIBUTE_UOM is an approved abbreviation
  lov         -- controlled attributes carry an approved value
  required    -- fields that must be present for a publishable record
  evidence    -- externally-sourced claims carry a traceable source
  consistency -- generated copy never states a fact absent from the attributes

Severity is either ``error`` (blocks publication) or ``warning`` (publishable but
flagged).  Nothing here silently rewrites data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from backend.models.product import EnrichedProduct, SourceTier
from backend.normalization.uom import UomRegistry
from backend.reference.schema import LeafNode
from backend.validation.schema import OutputSchema

# Two classes of forbidden content.
#
# SUBSTRING_FORBIDDEN is unambiguous wherever it appears.
# WHOLE_FIELD_FORBIDDEN is only a defect when it is the *entire* field, because
# short sentinel-looking abbreviations occur inside legitimate names.  An earlier
# version flagged the real supplier "Wera Tools NA Inc" because the pattern
# /\bN\/?A\b/ matched the "NA" of "North America", so bare abbreviations are no
# longer matched mid-string.
SUBSTRING_FORBIDDEN = (
    (r"\bN/A\b", "placeholder_text"),
    (r"\blorem ipsum\b", "filler_text"),
    (r"\bundefined\b", "placeholder_text"),
    (r"\bnot found\b", "placeholder_text"),
    (r"--\s*(?:no\s+)?\w+(?:\s+\w+)?\s*--", "sentinel_text"),
    (r"\{\w+\}", "unrendered_template_token"),
)

WHOLE_FIELD_FORBIDDEN = (
    (r"n/?a", "placeholder_text"),
    (r"tbd", "placeholder_text"),
    (r"null", "placeholder_text"),
    (r"none", "placeholder_text"),
    (r"unknown", "placeholder_text"),
    (r"-+", "sentinel_text"),
)

# Retained so existing imports of the old name keep working.
FORBIDDEN_PATTERNS = SUBSTRING_FORBIDDEN

# Fields required for a record to be publishable at all.
REQUIRED_FIELDS = ("MANUFACTURER_PART_NUMBER", "Classpath", "Product Name", "SHORT_DESC")


@dataclass
class ValidationReport:
    valid: bool
    errors: List[Dict[str, str]]
    warnings: List[Dict[str, str]]
    checks_run: int
    checks_passed: int

    @property
    def pass_rate(self) -> float:
        return self.checks_passed / self.checks_run if self.checks_run else 0.0

    def as_dict(self) -> Dict[str, object]:
        return {
            "valid": self.valid, "errors": self.errors, "warnings": self.warnings,
            "checks_run": self.checks_run, "checks_passed": self.checks_passed,
            "pass_rate": round(self.pass_rate, 4),
        }


class ContentValidator:
    """Validates an enriched product and its projected delivery-format row."""

    def __init__(self, schema: OutputSchema, uom: UomRegistry,
                 limits: Optional[Dict[str, int]] = None):
        self.schema = schema
        self.uom = uom
        self.limits = dict(limits or {})

    def validate(self, product: EnrichedProduct, row: Dict[str, str],
                 leaf: Optional[LeafNode]) -> ValidationReport:
        product.issues.clear()
        run = passed = 0

        run, passed = self._check_schema(product, row, run, passed)
        run, passed = self._check_limits(product, row, run, passed)
        run, passed = self._check_forbidden(product, row, run, passed)
        run, passed = self._check_uom(product, row, run, passed)
        run, passed = self._check_lov(product, leaf, run, passed)
        run, passed = self._check_required(product, row, run, passed)
        run, passed = self._check_evidence(product, run, passed)
        run, passed = self._check_consistency(product, run, passed)

        errors = [i.as_dict() for i in product.errors]
        warnings = [i.as_dict() for i in product.warnings]
        return ValidationReport(not errors, errors, warnings, run, passed)

    # -- individual check groups -----------------------------------------
    def _check_schema(self, p, row, run, passed):
        run += 1
        missing = [h for h in self.schema.headers if h not in row]
        extra = self.schema.unknown_columns(row)
        if missing or extra:
            p.add_issue("__schema__", "error", "schema_mismatch",
                        f"{len(missing)} missing and {len(extra)} unexpected columns",
                        actual=f"{len(row)} columns",
                        expected=f"{len(self.schema.headers)} columns")
        else:
            passed += 1

        run += 1
        slots = self.schema.attribute_slots
        if len(p.attributes) > slots:
            p.add_issue("__attributes__", "warning", "attribute_overflow",
                        f"leaf defines {len(p.attributes)} attributes but the template "
                        f"exposes {slots} slots; the excess was dropped")
        else:
            passed += 1
        return run, passed

    def _check_limits(self, p, row, run, passed):
        for fieldname, limit in self.limits.items():
            if not self.schema.has(fieldname):
                continue
            run += 1
            text = row.get(fieldname) or ""
            if len(text) > limit:
                p.add_issue(fieldname, "error", "char_limit_exceeded",
                            f"{len(text)} characters exceeds the {limit}-character limit",
                            actual=str(len(text)), expected=f"<= {limit}")
            else:
                passed += 1
        return run, passed

    def _check_forbidden(self, p, row, run, passed):
        text_cols = [c for c in ("MOBILE_DESC", "INVOICE_DESC", "SHORT_DESC", "LONG_DESC1",
                                 "RETAIL_DESC", "MARKETING_DESCRIPTION", "Product Name",
                                 "MANUFACTURER_NAME", "BRAND_NAME")
                     if self.schema.has(c)]
        for col in text_cols:
            run += 1
            value = row.get(col) or ""
            stripped = value.strip()
            hit = next(((pat, code) for pat, code in SUBSTRING_FORBIDDEN
                        if re.search(pat, value, re.IGNORECASE)), None)
            if hit is None and stripped:
                hit = next(((pat, code) for pat, code in WHOLE_FIELD_FORBIDDEN
                            if re.fullmatch(pat, stripped, re.IGNORECASE)), None)
            if hit:
                p.add_issue(col, "error", hit[1],
                            f"generated text contains disallowed content matching "
                            f"/{hit[0]}/", actual=value[:80])
            else:
                passed += 1
        return run, passed

    def _check_uom(self, p, row, run, passed):
        for col in self.schema.family_columns("attribute_uom"):
            value = (row.get(col) or "").strip()
            if not value:
                continue
            run += 1
            if self.uom.is_approved(value):
                passed += 1
            else:
                p.add_issue(col, "error", "uom_not_approved",
                            f"'{value}' is not an approved UOM abbreviation",
                            actual=value)
        # House spacing rule: a value column must not embed its own unit.
        for col in self.schema.family_columns("attribute_value"):
            value = (row.get(col) or "").strip()
            if not value:
                continue
            run += 1
            if re.fullmatch(r"\d+(?:[-.]\d+(?:/\d+)?)?[A-Za-z]{1,4}", value):
                p.add_issue(col, "warning", "uom_spacing",
                            f"'{value}' looks like a magnitude fused to a unit; the "
                            f"unit belongs in the matching ATTRIBUTE_UOM column",
                            actual=value)
            else:
                passed += 1
        return run, passed

    def _check_lov(self, p, leaf, run, passed):
        if leaf is None:
            return run, passed
        for av in p.attributes:
            attr = leaf.attribute(av.label)
            if attr is None or not attr.values or not av.present:
                continue
            run += 1
            if attr.match_value(av.value or "") is not None:
                passed += 1
            else:
                p.add_issue(f"ATTRIBUTE:{av.label}", "error", "lov_violation",
                            f"'{av.value}' is not an approved value for '{av.label}'",
                            actual=av.value,
                            expected=", ".join(attr.allowed_values[:6]))
        return run, passed

    def _check_required(self, p, row, run, passed):
        for col in REQUIRED_FIELDS:
            if not self.schema.has(col):
                continue
            run += 1
            if (row.get(col) or "").strip():
                passed += 1
            else:
                p.add_issue(col, "error", "required_field_missing",
                            f"'{col}' is required for a publishable record")
        return run, passed

    def _check_evidence(self, p, run, passed):
        """Every externally-sourced claim must name a traceable source."""
        external = {SourceTier.MANUFACTURER_SITE.value,
                    SourceTier.MANUFACTURER_PRODUCT_PAGE.value,
                    SourceTier.MANUFACTURER_DOC.value,
                    SourceTier.MANUFACTURER_CATALOG.value}
        for name, fv in list(p.descriptions.items()) + [
                ("MANUFACTURER_NAME", p.manufacturer), ("BRAND_NAME", p.brand)]:
            if not getattr(fv, "present", False):
                continue
            run += 1
            ext = [e for e in fv.evidence if e.tier in external]
            if ext and not any(e.url or e.snippet for e in ext):
                p.add_issue(name, "error", "evidence_missing",
                            "value claims a manufacturer source but stores no locator")
            else:
                passed += 1
        return run, passed

    def _check_consistency(self, p, run, passed):
        """
        Generated copy must not assert an attribute value the fact sheet lacks.

        Cheap structural guard: every comma-separated clause of SHORT_DESC should be
        traceable to the brand, product name, series, part number or a populated
        attribute.
        """
        short = p.description("SHORT_DESC")
        if not short:
            return run, passed
        run += 1
        known = {str(a.value).lower() for a in p.attributes if a.present}
        for fv in (p.brand, p.mpn, p.product_name, p.with_clause):
            if getattr(fv, "present", False):
                known.add(str(fv.value).lower())
        unexplained = []
        for clause in [c.strip() for c in short.split(",")[1:]]:
            low = clause.lower()
            if not any(k and k in low for k in known):
                unexplained.append(clause)
        if unexplained:
            p.add_issue("SHORT_DESC", "warning", "unverified_clause",
                        "clause(s) not traceable to a verified attribute: "
                        + "; ".join(unexplained[:3]))
        else:
            passed += 1
        return run, passed
