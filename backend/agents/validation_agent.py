"""
Agent 7 -- Validation Agent.

Thin agent wrapper over the deterministic :class:`ContentValidator`, exposing the
JSON contract the brief specifies:

    {"valid": bool, "errors": [...], "warnings": [...],
     "confidence": float, "needs_human_review": bool}

Two things make this an *agent* rather than a pass-through:

* it converts a pile of individual check results into a single publish/hold decision
  with a stated rationale, and
* it owns the **self-correction loop** -- deciding which failures are mechanically
  repairable (character overflow, an unapproved vocabulary value) versus which need
  a human, and re-validating after repair.

No LLM is used here by design.  Validation must be reproducible: the same record
must always produce the same verdict, which a sampled model cannot guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from backend.models.product import EnrichedProduct
from backend.normalization.text import truncate
from backend.reference.schema import LeafNode
from backend.validation.content_rules import ContentValidator, ValidationReport

# Failure codes the loop can repair without human judgement.
REPAIRABLE = {"char_limit_exceeded", "lov_violation", "uom_spacing"}

# Failure codes that always require a person: they mean data is absent, not malformed.
UNREPAIRABLE = {"required_field_missing", "evidence_missing", "schema_mismatch"}


@dataclass
class ValidationOutcome:
    """The brief's Agent 7 contract, plus the repair audit trail."""
    valid: bool = False
    errors: List[Dict[str, str]] = field(default_factory=list)
    warnings: List[Dict[str, str]] = field(default_factory=list)
    confidence: float = 0.0
    needs_human_review: bool = False
    repairs: List[str] = field(default_factory=list)
    checks_run: int = 0
    checks_passed: int = 0
    passes: int = 1

    @property
    def pass_rate(self) -> float:
        return self.checks_passed / self.checks_run if self.checks_run else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "confidence": round(self.confidence, 4),
            "needs_human_review": self.needs_human_review,
            "repairs": list(self.repairs),
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "pass_rate": round(self.pass_rate, 4),
            "validation_passes": self.passes,
        }


class ValidationAgent:
    """Agent 7. Validates, repairs what is mechanically repairable, re-validates."""

    def __init__(self, validator: ContentValidator,
                 limits: Optional[Dict[str, int]] = None,
                 max_passes: int = 2):
        self.validator = validator
        self.limits = dict(limits or {})
        self.max_passes = max(1, max_passes)

    # -- public -------------------------------------------------------------
    def validate(self, product: EnrichedProduct, row: Dict[str, str],
                 leaf: Optional[LeafNode],
                 rebuild_row: Optional[Callable[[], Dict[str, str]]] = None
                 ) -> ValidationOutcome:
        """
        Validate, then self-correct **only the failing fields** and re-validate.

        ``rebuild_row`` re-projects the product onto the delivery format after a
        repair; without it the loop still repairs the product but scores against the
        original row, so callers should always supply it.
        """
        report = self.validator.validate(product, row, leaf)
        outcome = self._to_outcome(report, product)
        if report.valid:
            return outcome

        for attempt in range(1, self.max_passes):
            repairs = self._repair(product, report)
            if not repairs:
                break
            outcome.repairs.extend(repairs)
            row = rebuild_row() if rebuild_row else row
            report = self.validator.validate(product, row, leaf)
            outcome = self._to_outcome(report, product)
            outcome.repairs = list(dict.fromkeys(outcome.repairs + repairs))
            outcome.passes = attempt + 1
            if report.valid:
                break

        return outcome

    # -- internals ----------------------------------------------------------
    @staticmethod
    def _to_outcome(report: ValidationReport,
                    product: EnrichedProduct) -> ValidationOutcome:
        blocking = [e for e in report.errors if e.get("code") in UNREPAIRABLE]
        return ValidationOutcome(
            valid=report.valid,
            errors=report.errors,
            warnings=report.warnings,
            confidence=round(report.pass_rate, 4),
            # A person is needed when something failed that no rule can fix, or when
            # anything at all still fails after the repair budget is spent.
            needs_human_review=bool(blocking) or not report.valid,
            checks_run=report.checks_run,
            checks_passed=report.checks_passed,
        )

    def _repair(self, product: EnrichedProduct,
                report: ValidationReport) -> List[str]:
        """Fix only the fields that failed; never regenerate the whole product."""
        done: List[str] = []
        for issue in report.errors + report.warnings:
            code = issue.get("code", "")
            field_name = issue.get("field", "")
            if code not in REPAIRABLE:
                continue

            if code == "char_limit_exceeded" and field_name in product.descriptions:
                limit = self.limits.get(field_name)
                fv = product.descriptions[field_name]
                if limit and fv.present:
                    trimmed = truncate(fv.value or "", limit)
                    if trimmed != fv.value:
                        fv.value = trimmed
                        fv.confidence = max(0.0, fv.confidence - 0.05)
                        fv.notes.append(
                            f"self-corrected: trimmed to the {limit}-character limit")
                        done.append(f"{field_name}: trimmed to {len(trimmed)} chars")

            elif code == "lov_violation" and field_name.startswith("ATTRIBUTE:"):
                label = field_name.split(":", 1)[1]
                av = product.attribute(label)
                if av is not None and av.present:
                    bad = av.value
                    av.value = None
                    av.confidence = 0.0
                    av.method = "cleared_lov_violation"
                    av.transformation = (
                        "value was not in the controlled vocabulary and was cleared "
                        "rather than published")
                    done.append(f"{label}: cleared unapproved value {bad!r}")

        for d in done:
            product.log_stage("self_correction", d)
        return done
