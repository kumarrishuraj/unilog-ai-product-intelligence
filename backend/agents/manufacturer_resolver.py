"""
Agent 2 -- Manufacturer resolution.

Match cascade (Golden Rule: never blindly take the top fuzzy score):

  1. **code**       -- the embedded supplier code, e.g. 'Freud Inc (2435)' -> 2435
  2. **exact**      -- surface form identical to a master record
  3. **normalized** -- corporate suffixes stripped: 'FREUD, INC.' -> 'freud'
  4. **alias**      -- a known alias/variant of a master record
  5. **fuzzy**      -- token-set similarity, accepted only above a floor AND only
                       when the runner-up is clearly behind (margin test)
  6. **semantic**   -- optional embedding similarity, same margin discipline

A fuzzy match that is strong but *ambiguous* (two candidates within
``AMBIGUITY_MARGIN``) is deliberately rejected and routed to human review rather
than guessed.

Brand resolution is Agent 3, in :mod:`backend.agents.brand_resolver`.  Shared
primitives (``Resolution``, the method-confidence scale, the fuzzy helpers) live in
:mod:`backend.agents.resolution` so neither agent depends on the other.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from backend.agents.resolution import (
    AMBIGUITY_MARGIN, FUZZY_FLOOR, METHOD_CONFIDENCE, Resolution, best_fuzzy,
)
from backend.models.product import Evidence, FieldValue, SourceTier
from backend.normalization.text import clean_value
from backend.reference.bootstrap import match_key, split_name_code
from backend.reference.registry import ReferenceRegistry
from backend.reference.schema import PROVENANCE_OFFICIAL, ManufacturerRecord


class ManufacturerResolver:
    """Resolves a raw supplier string to an approved manufacturer record."""

    def __init__(self, registry: ReferenceRegistry):
        self.reg = registry
        self._keys = registry.manufacturer_keys

    def resolve(self, raw: Optional[str]) -> Resolution:
        cleaned = clean_value(raw)
        if not cleaned:
            return Resolution(None, None, "none", 0.0, [], detail="no supplier value supplied")

        name, code = split_name_code(cleaned)
        key = match_key(name)

        # 1. embedded code
        if code:
            rec = self.reg.manufacturer_by_code(code)
            if rec is not None:
                return self._hit(rec, "code", f"supplier code '{code}' matched master record")

        # 2/3. exact then normalized (match_key collapses both)
        rec = self.reg.manufacturer_by_key(key)
        if rec is not None:
            method = "exact" if rec.name == name else "normalized"
            return self._hit(rec, method,
                             f"'{name}' normalised to '{key}' and matched master record")

        # 4. alias
        for m in self.reg.manufacturers:
            if any(match_key(a) == key for a in m.aliases):
                return self._hit(m, "alias", f"'{name}' matched a known alias of '{m.name}'")

        # 5. fuzzy, with the margin test
        best, score, runner = best_fuzzy(key, self._keys)
        cands = [(best or "", score), ("", runner)]
        if best and score >= FUZZY_FLOOR:
            if score - runner < AMBIGUITY_MARGIN:
                return Resolution(None, None, "ambiguous", 0.0, cands, ambiguous=True,
                                  detail=(f"fuzzy match ambiguous: top {score:.0f} vs "
                                          f"runner-up {runner:.0f}; refusing to guess"))
            rec = self.reg.manufacturer_by_key(best)
            if rec is not None:
                conf = METHOD_CONFIDENCE["fuzzy"] * min(1.0, score / 100.0)
                return self._hit(rec, "fuzzy",
                                 f"token-set similarity {score:.0f} to '{rec.name}'",
                                 confidence=round(conf, 4))

        return Resolution(None, None, "unresolved", 0.0, cands,
                          detail=(f"no master record above the {FUZZY_FLOOR:.0f} "
                                  f"similarity floor (best {score:.0f})"))

    def _hit(self, rec: ManufacturerRecord, method: str, detail: str,
             confidence: Optional[float] = None) -> Resolution:
        conf = confidence if confidence is not None else METHOD_CONFIDENCE[method]
        # A derived master is inherently less authoritative than an official one.
        if rec.provenance != PROVENANCE_OFFICIAL:
            conf *= 0.94
        return Resolution(rec.name, rec.code, method, round(conf, 4),
                          [(rec.name, 100.0)], record=rec, detail=detail)


# ---------------------------------------------------------------------------
# FieldValue adapters
# ---------------------------------------------------------------------------
def to_field_value(res: Resolution, raw: Optional[str], source: str,
                   tier: str = SourceTier.MASTER_DATA.value) -> FieldValue:
    """Convert a Resolution into a FieldValue carrying its evidence."""
    fv = FieldValue(
        value=res.value,
        confidence=res.confidence,
        method=res.method,
        transformation=res.detail,
        raw=raw,
    )
    if res.value:
        fv.add_evidence(Evidence(source=source, tier=tier,
                                 snippet=str(raw or ""), locator=res.method))
    else:
        fv.notes.append(res.detail or "unresolved")
    return fv
