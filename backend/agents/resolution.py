"""
Shared entity-resolution primitives.

Agents 2 (manufacturer) and 3 (brand) both produce a ``Resolution`` and both score
matches on the same method scale.  Those live here rather than in either agent so
the two modules stay independent -- importing one must never require the other.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

try:
    from rapidfuzz import fuzz, process as rf_process
    _HAVE_RAPIDFUZZ = True
except Exception:                       # pragma: no cover
    _HAVE_RAPIDFUZZ = False
    import difflib

# Fuzzy acceptance floor and the margin the winner must have over the runner-up.
FUZZY_FLOOR = 88.0
AMBIGUITY_MARGIN = 4.0

# Confidence assigned per match method.
METHOD_CONFIDENCE = {
    "code": 0.99,
    "exact": 0.98,
    "normalized": 0.95,
    "alias": 0.92,
    "fuzzy": 0.78,
    "semantic": 0.70,
}


def similarity(a: str, b: str) -> float:
    if _HAVE_RAPIDFUZZ:
        return float(fuzz.token_set_ratio(a, b))
    return difflib.SequenceMatcher(None, a, b).ratio() * 100.0


def best_fuzzy(query: str, choices: Sequence[str]) -> Tuple[Optional[str], float, float]:
    """Return (best_choice, best_score, runner_up_score)."""
    if not query or not choices:
        return None, 0.0, 0.0
    if _HAVE_RAPIDFUZZ:
        hits = rf_process.extract(query, list(choices), scorer=fuzz.token_set_ratio, limit=2)
        if not hits:
            return None, 0.0, 0.0
        best = hits[0]
        runner = hits[1][1] if len(hits) > 1 else 0.0
        return best[0], float(best[1]), float(runner)
    scored = sorted(((c, similarity(query, c)) for c in choices), key=lambda x: -x[1])[:2]
    if not scored:
        return None, 0.0, 0.0
    return scored[0][0], scored[0][1], (scored[1][1] if len(scored) > 1 else 0.0)


@dataclass
class Resolution:
    """Outcome of one entity-resolution attempt."""
    value: Optional[str]
    code: Optional[str]
    method: str
    confidence: float
    candidates: List[Tuple[str, float]]
    ambiguous: bool = False
    record: Optional[object] = None
    detail: str = ""
