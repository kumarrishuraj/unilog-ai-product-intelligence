"""
Evaluation metrics against a labelled delivery-format file.

The comparison is deliberately conservative:

* **Comparable-only accuracy.**  A field is scored only when the gold row actually
  has a value.  Scoring blanks as correct would let an empty pipeline claim high
  accuracy, which is the classic way these numbers get inflated.
* **Precision and recall are reported separately** from accuracy, so a system that
  achieves accuracy by staying silent is visibly distinguishable from one that
  actually populates fields.
* **Semantic similarity** is used only for free-text fields, and is a token-level
  F1 (order-insensitive, stop-word aware) rather than an embedding score, so the
  number is reproducible with no model dependency.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# Fields compared by exact string match after light normalisation.
EXACT_FIELDS = (
    "MANUFACTURER_NAME", "BRAND_NAME", "MANUFACTURER_PART_NUMBER", "Classpath",
    "Dept", "Class", "Fine", "Product Name", "UNSPSC", "Mfg_Part_Num",
)

# Fields compared by semantic similarity.
SEMANTIC_FIELDS = (
    "LONG_DESC1", "MARKETING_DESCRIPTION", "SHORT_DESC", "RETAIL_DESC",
    "MOBILE_DESC", "INVOICE_DESC",
)

_STOP = {"a", "an", "the", "with", "and", "of", "for", "to", "in", "on", "by"}


def normalise(text: Optional[str]) -> str:
    """Case/space/punctuation-insensitive comparison key."""
    s = str(text or "").strip().lower()
    s = s.replace("®", "").replace("™", "").replace("©", "")
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .,;")


def tokens(text: Optional[str]) -> List[str]:
    s = normalise(text)
    return [t for t in re.split(r"[^a-z0-9/\-.]+", s) if t and t not in _STOP]


def token_f1(pred: Optional[str], gold: Optional[str]) -> float:
    """Order-insensitive token F1; 1.0 for identical bags, 0.0 for disjoint."""
    p, g = tokens(pred), tokens(gold)
    if not p and not g:
        return 1.0
    if not p or not g:
        return 0.0
    overlap = Counter(p) & Counter(g)
    common = sum(overlap.values())
    if not common:
        return 0.0
    precision = common / len(p)
    recall = common / len(g)
    return 2 * precision * recall / (precision + recall)


@dataclass
class FieldMetric:
    field: str
    gold_populated: int = 0
    pred_populated: int = 0
    comparable: int = 0          # gold populated
    correct: int = 0             # gold populated AND pred matches
    similarity_sum: float = 0.0
    kind: str = "exact"

    @property
    def accuracy(self) -> float:
        return self.correct / self.comparable if self.comparable else 0.0

    @property
    def coverage(self) -> float:
        """Of the fields the gold populates, how many did we populate at all?"""
        return min(1.0, self.pred_populated / self.comparable) if self.comparable else 0.0

    @property
    def mean_similarity(self) -> float:
        return self.similarity_sum / self.comparable if self.comparable else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field, "kind": self.kind,
            "gold_populated": self.gold_populated,
            "pred_populated": self.pred_populated,
            "comparable": self.comparable, "correct": self.correct,
            "accuracy": round(self.accuracy, 4),
            "coverage": round(self.coverage, 4),
            "mean_similarity": round(self.mean_similarity, 4),
        }


@dataclass
class EvaluationReport:
    rows_evaluated: int
    fields: List[FieldMetric] = field(default_factory=list)
    char_compliance: float = 0.0
    lov_compliance: float = 0.0
    evidence_coverage: float = 0.0
    review_rate: float = 0.0
    validation_pass_rate: float = 0.0
    mean_confidence: float = 0.0
    schema_conformant: bool = True
    notes: List[str] = field(default_factory=list)

    @property
    def exact_fields(self) -> List[FieldMetric]:
        return [f for f in self.fields if f.kind == "exact"]

    @property
    def semantic_fields(self) -> List[FieldMetric]:
        return [f for f in self.fields if f.kind == "semantic"]

    @property
    def macro_accuracy(self) -> float:
        scored = [f for f in self.exact_fields if f.comparable]
        return sum(f.accuracy for f in scored) / len(scored) if scored else 0.0

    @property
    def micro_accuracy(self) -> float:
        comparable = sum(f.comparable for f in self.exact_fields)
        correct = sum(f.correct for f in self.exact_fields)
        return correct / comparable if comparable else 0.0

    @property
    def mean_semantic(self) -> float:
        scored = [f for f in self.semantic_fields if f.comparable]
        return sum(f.mean_similarity for f in scored) / len(scored) if scored else 0.0

    def overall_quality(self) -> float:
        """
        Weighted quality score.  Structural correctness (schema, LOV, character
        limits) is weighted alongside accuracy because a beautifully-worded record
        that breaches the schema is worthless downstream.
        """
        components = {
            "field_accuracy": (self.micro_accuracy, 0.30),
            "semantic_similarity": (self.mean_semantic, 0.15),
            "lov_compliance": (self.lov_compliance, 0.15),
            "char_compliance": (self.char_compliance, 0.10),
            "validation_pass": (self.validation_pass_rate, 0.15),
            "evidence_coverage": (self.evidence_coverage, 0.15),
        }
        score = sum(v * w for v, w in components.values())
        return round(score * (1.0 if self.schema_conformant else 0.5), 4)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rows_evaluated": self.rows_evaluated,
            "macro_accuracy": round(self.macro_accuracy, 4),
            "micro_accuracy": round(self.micro_accuracy, 4),
            "mean_semantic_similarity": round(self.mean_semantic, 4),
            "char_compliance": round(self.char_compliance, 4),
            "lov_compliance": round(self.lov_compliance, 4),
            "evidence_coverage": round(self.evidence_coverage, 4),
            "validation_pass_rate": round(self.validation_pass_rate, 4),
            "review_rate": round(self.review_rate, 4),
            "mean_confidence": round(self.mean_confidence, 4),
            "schema_conformant": self.schema_conformant,
            "overall_quality": self.overall_quality(),
            "fields": [f.as_dict() for f in self.fields],
            "notes": list(self.notes),
        }


def compare_rows(pred_rows: Sequence[Dict[str, str]],
                 gold_rows: Sequence[Dict[str, str]],
                 headers: Sequence[str]) -> List[FieldMetric]:
    """Field-by-field comparison over aligned prediction/gold row pairs."""
    metrics: Dict[str, FieldMetric] = {}
    for h in headers:
        kind = ("semantic" if h in SEMANTIC_FIELDS
                else "exact" if h in EXACT_FIELDS
                else "other")
        metrics[h] = FieldMetric(field=h, kind=kind)

    for pred, gold in zip(pred_rows, gold_rows):
        for h in headers:
            m = metrics[h]
            g = (gold.get(h) or "").strip()
            p = (pred.get(h) or "").strip()
            if g:
                m.gold_populated += 1
                m.comparable += 1
            if p:
                m.pred_populated += 1
            if not g:
                continue
            if m.kind == "semantic":
                sim = token_f1(p, g)
                m.similarity_sum += sim
                if sim >= 0.95:
                    m.correct += 1
            else:
                if normalise(p) == normalise(g):
                    m.correct += 1
                    m.similarity_sum += 1.0
                else:
                    m.similarity_sum += token_f1(p, g)
    return [m for m in metrics.values() if m.gold_populated or m.pred_populated]


def align_by_key(pred_rows: Sequence[Dict[str, str]],
                 gold_rows: Sequence[Dict[str, str]],
                 key: str = "Mfg_Part_Num") -> Tuple[List[Dict], List[Dict], List[str]]:
    """
    Align predictions to gold rows by part number.

    Returns (aligned_pred, aligned_gold, unmatched_keys).  Alignment by key rather
    than by position means the evaluation still works when the pipeline processes a
    superset of the labelled rows -- which is exactly the case here, where the
    labelled file covers a handful of the 1,000 inputs.
    """
    by_key: Dict[str, Dict[str, str]] = {}
    for r in pred_rows:
        k = normalise(r.get(key))
        if k:
            by_key.setdefault(k, r)

    ap: List[Dict[str, str]] = []
    ag: List[Dict[str, str]] = []
    missing: List[str] = []
    for g in gold_rows:
        k = normalise(g.get(key))
        if not k:
            continue
        p = by_key.get(k)
        if p is None:
            missing.append(k)
            continue
        ap.append(p)
        ag.append(g)
    return ap, ag, missing


def compute_quality_metrics(products: Sequence[Any],
                            limits: Optional[Dict[str, int]] = None) -> Dict[str, float]:
    """Pipeline-level quality metrics that need no gold labels."""
    if not products:
        return {"char_compliance": 0.0, "lov_compliance": 0.0,
                "evidence_coverage": 0.0, "review_rate": 0.0,
                "validation_pass_rate": 0.0, "mean_confidence": 0.0}

    limits = limits or {}
    char_ok = char_total = 0
    lov_ok = lov_total = 0
    ev_ok = ev_total = 0

    for p in products:
        for name, fv in p.descriptions.items():
            if not fv.present:
                continue
            limit = limits.get(name)
            if limit:
                char_total += 1
                if len(fv.value or "") <= limit:
                    char_ok += 1
        for av in p.attributes:
            if av.present and av.lov_compliant is not None:
                lov_total += 1
                if av.lov_compliant:
                    lov_ok += 1
            if av.present:
                ev_total += 1
                if av.evidence:
                    ev_ok += 1
        for fv in (p.manufacturer, p.brand, p.classpath, p.mpn):
            if fv.present:
                ev_total += 1
                if fv.evidence:
                    ev_ok += 1

    reviewed = sum(1 for p in products if p.needs_review)
    valid = sum(1 for p in products if not p.errors)
    return {
        "char_compliance": char_ok / char_total if char_total else 1.0,
        "lov_compliance": lov_ok / lov_total if lov_total else 1.0,
        "evidence_coverage": ev_ok / ev_total if ev_total else 0.0,
        "review_rate": reviewed / len(products),
        "validation_pass_rate": valid / len(products),
        "mean_confidence": sum(p.confidence for p in products) / len(products),
    }
