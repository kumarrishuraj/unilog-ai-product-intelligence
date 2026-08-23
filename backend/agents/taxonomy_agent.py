"""
Agent 4 -- Taxonomy Agent.

Wraps the deterministic classifier with a model tie-breaker that fires **only** on
the two cases the keyword scorer genuinely cannot settle:

  * ``Classification.ambiguous`` -- the top two leaves scored within 80 % of each
    other, so picking the higher one would be arbitrary.
  * ``method == 'fallback'`` -- nothing matched at all.

Everything else is answered deterministically and never costs a call.  On the
supplied 1,000-row feed that is ~5 % of rows, which is the whole point: the model is
a tie-breaker, not the classifier.

The model is given **only the candidate leaves already retrieved** (adaptive
retrieval, brief §24-D) and must choose one of them by id, or return
``"none"``.  It cannot invent a classpath, because its answer is validated against
the candidate set before use.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from backend.llm.client import LlmClient
from backend.models.product import Evidence, SourceTier
from backend.reference.taxonomy import Candidate, Classification, Taxonomy

# Model confidence is capped below the deterministic path: a tie-break is a
# judgement call on genuinely ambiguous evidence, not a master-data match.
LLM_MAX_CONFIDENCE = 0.72
LLM_CANDIDATE_LIMIT = 6

CLASSIFY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["leaf_id", "confidence", "reasoning_summary"],
    "properties": {
        "leaf_id": {"type": "string",
                    "description": "exactly one id from the supplied candidates, "
                                   "or the literal string 'none'"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning_summary": {"type": "string"},
    },
}

CLASSIFY_SYSTEM = (
    "You are a product taxonomist for an industrial catalogue. You are given one "
    "product description and a shortlist of candidate categories. Choose the single "
    "best candidate by its id. You MUST pick from the shortlist or answer 'none'. "
    "Never invent a category id or a classpath. If the description is too opaque to "
    "classify, answer 'none' -- that is a correct and useful answer, not a failure."
)


@dataclass
class TaxonomyDecision:
    classification: Classification
    escalated: bool = False
    llm_used: bool = False
    note: str = ""

    @property
    def leaf(self):
        return self.classification.leaf


class TaxonomyAgent:
    """Agent 4. Deterministic classifier + narrowly-scoped model tie-breaker."""

    def __init__(self, taxonomy: Taxonomy, llm: Optional[LlmClient] = None):
        self.taxonomy = taxonomy
        self.llm = llm
        self.escalations = 0
        self.llm_resolutions = 0

    # -- public ------------------------------------------------------------
    def classify(self, description: str,
                 product_type_hint: str = "") -> TaxonomyDecision:
        base = self.taxonomy.classify(description)

        needs_help = base.ambiguous or base.method == "fallback"
        if not needs_help:
            return TaxonomyDecision(base, escalated=False, llm_used=False,
                                    note="settled deterministically")

        self.escalations += 1
        if self.llm is None or not self.llm.available:
            return TaxonomyDecision(
                base, escalated=True, llm_used=False,
                note=("ambiguous or unmatched; no model available, so the record is "
                      "flagged for human review rather than guessed"))

        candidates = self._shortlist(base, description, product_type_hint)
        if not candidates:
            return TaxonomyDecision(base, escalated=True, llm_used=False,
                                    note="no candidate leaves to choose between")

        chosen, conf, reason = self._ask(description, product_type_hint, candidates)
        if chosen is None:
            return TaxonomyDecision(
                base, escalated=True, llm_used=True,
                note=f"model declined to classify ({reason})")

        self.llm_resolutions += 1
        resolved = Classification(
            leaf=chosen,
            score=base.score,
            confidence=round(min(LLM_MAX_CONFIDENCE, max(0.4, conf)), 4),
            candidates=base.candidates,
            ambiguous=False,
            method="llm_tiebreak",
            matched=list(base.matched),
        )
        return TaxonomyDecision(resolved, escalated=True, llm_used=True,
                                note=f"model tie-break: {reason}")

    # -- internals ---------------------------------------------------------
    def _shortlist(self, base: Classification, description: str,
                   hint: str) -> List[Candidate]:
        """
        Adaptive retrieval: prefer the leaves the scorer already surfaced.  When the
        scorer found nothing, re-score against the parser's product-type hint before
        falling back to the whole catalogue.
        """
        if base.candidates:
            return base.candidates[:LLM_CANDIDATE_LIMIT]
        if hint:
            hinted = self.taxonomy.score_all(hint)
            if hinted:
                return hinted[:LLM_CANDIDATE_LIMIT]
        # Nothing scored: offer the catalogue's leaves, minus the fallback.
        fb = self.taxonomy.fallback
        return [Candidate(l, 0.0, []) for l in self.taxonomy.leaves
                if fb is None or l.id != fb.id][:LLM_CANDIDATE_LIMIT * 3]

    def _ask(self, description: str, hint: str, candidates: Sequence[Candidate]):
        by_id = {c.leaf.id: c.leaf for c in candidates}
        listing = "\n".join(
            f"- id={c.leaf.id!r} | classpath={c.leaf.classpath!r} "
            f"| product_name={c.leaf.product_name!r}"
            for c in candidates)
        prompt = (
            f"Product description: {description!r}\n"
            f"Parsed product-type hint: {hint!r}\n\n"
            f"Candidate categories:\n{listing}\n\n"
            "Which single candidate id best describes this product?"
        )
        resp = self.llm.complete_json(CLASSIFY_SYSTEM, prompt, CLASSIFY_SCHEMA,
                                      max_tokens=400)
        if not resp.ok or not resp.data:
            return None, 0.0, resp.error or "no response"

        leaf_id = str(resp.data.get("leaf_id") or "").strip()
        reason = str(resp.data.get("reasoning_summary") or "")[:180]
        if leaf_id.lower() in ("", "none", "null"):
            return None, 0.0, reason or "declined"

        # Validation: the answer must be one of the ids we offered.
        leaf = by_id.get(leaf_id)
        if leaf is None:
            return None, 0.0, f"model returned id {leaf_id!r} which was not offered"

        try:
            conf = float(resp.data.get("confidence") or 0.5)
        except (TypeError, ValueError):
            conf = 0.5
        return leaf, conf, reason

    # -- reporting ----------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        return {
            "escalations": self.escalations,
            "llm_resolutions": self.llm_resolutions,
            "llm_available": bool(self.llm and self.llm.available),
        }

    @staticmethod
    def evidence_for(decision: TaxonomyDecision, description: str) -> Evidence:
        tier = (SourceTier.CONTROLLED_VOCAB.value if not decision.llm_used
                else SourceTier.UNVERIFIED.value)
        return Evidence(source=("taxonomy:keyword_classifier" if not decision.llm_used
                                else "taxonomy:llm_tiebreak"),
                        tier=tier, snippet=description[:160],
                        locator=decision.note)
