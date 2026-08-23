"""
Taxonomy: leaf-node catalogue, keyword classifier and attribute templates.

Classification is deterministic and *explainable*: every candidate carries the
keywords that fired, so the UI can show why a classpath was chosen and the review
queue can flag genuinely ambiguous products (top-2 scores close together) rather
than silently picking a winner.

Ambiguous and unclassifiable products are surfaced (``Classification.ambiguous``,
``method == 'fallback'``) and routed to human review.  An LLM tie-breaker for those
cases is a designed-for extension point, not yet implemented -- see README §10.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from backend.reference.schema import (
    PROVENANCE_DERIVED, LeafNode, LovAttribute, LovValue,
)

# Scoring weights.  A strong keyword is a category-defining noun ('dishwasher');
# a weak keyword is merely suggestive ('blade', 'box').
W_STRONG = 10.0
W_WEAK = 3.0
W_PRODUCT_NAME = 4.0
# Two candidates within this ratio of each other are treated as ambiguous.
AMBIGUITY_RATIO = 0.80


@dataclass
class Candidate:
    leaf: LeafNode
    score: float
    matched: List[str] = field(default_factory=list)

    @property
    def explanation(self) -> str:
        return ", ".join(self.matched) if self.matched else "fallback"


@dataclass
class Classification:
    leaf: Optional[LeafNode]
    score: float
    confidence: float
    candidates: List[Candidate]
    ambiguous: bool
    method: str                     # keyword | fallback | llm | unresolved
    matched: List[str] = field(default_factory=list)

    @property
    def runner_up(self) -> Optional[Candidate]:
        return self.candidates[1] if len(self.candidates) > 1 else None

    @property
    def explanation(self) -> str:
        """Human-readable reason for the chosen leaf, for the Evidence Graph."""
        if self.method == "fallback":
            return "no category keyword matched; generic template applied"
        if not self.matched:
            return self.method
        return "matched: " + ", ".join(self.matched)


def _compile_kw(word: str) -> re.Pattern:
    """Word-boundary matcher tolerant of internal spaces/hyphens."""
    parts = [re.escape(p) for p in re.split(r"\s+", word.strip()) if p]
    body = r"[\s\-/]*".join(parts)
    lead = r"(?<![A-Za-z0-9])"
    trail = r"(?![A-Za-z0-9])"
    return re.compile(lead + body + trail, re.IGNORECASE)


class Taxonomy:
    """Leaf-node catalogue with a deterministic keyword classifier."""

    def __init__(self, leaves: Sequence[LeafNode], fallback_id: str = "generic_product",
                 provenance: str = PROVENANCE_DERIVED):
        self.provenance = provenance
        self._leaves: List[LeafNode] = list(leaves)
        self._by_id: Dict[str, LeafNode] = {l.id: l for l in self._leaves}
        self._fallback_id = fallback_id
        self._patterns: Dict[str, Tuple[List[Tuple[re.Pattern, float, str]],
                                        List[re.Pattern]]] = {}
        self._compile()

    # -- construction -----------------------------------------------------
    @classmethod
    def from_pack(cls, path: Path) -> "Taxonomy":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        leaves = [cls._leaf_from_dict(d) for d in data.get("leaf_nodes", [])]
        fallback = next((d["id"] for d in data.get("leaf_nodes", []) if d.get("is_fallback")),
                        "generic_product")
        return cls(leaves, fallback, provenance=data.get("provenance", PROVENANCE_DERIVED))

    @staticmethod
    def _leaf_from_dict(d: Dict) -> LeafNode:
        attrs: List[LovAttribute] = []
        for a in d.get("attributes", []):
            values = tuple(
                LovValue(v["value"], tuple(v.get("synonyms") or ()), v.get("definition", ""))
                for v in a.get("values", [])
            )
            attrs.append(LovAttribute(
                label=a["label"],
                uom=a.get("uom"),
                measurement_type=a.get("measurement_type"),
                values=values,
                filtering=bool(a.get("filtering")),
                required=bool(a.get("required")),
                render=a.get("render", "{value} {uom} {label}"),
                extract_patterns=tuple(a.get("extract_patterns") or ()),
                definition=a.get("definition", ""),
            ))
        return LeafNode(
            id=d["id"],
            classpath=d.get("classpath", ""),
            dept=d.get("dept", ""),
            klass=d.get("class", ""),
            fine=d.get("fine", ""),
            product_name=d.get("product_name", ""),
            unspsc=d.get("unspsc", ""),
            attributes=tuple(attrs),
            keywords=tuple(d.get("keywords") or ()),
            strong_keywords=tuple(d.get("strong_keywords") or ()),
            exclude_keywords=tuple(d.get("exclude_keywords") or ()),
            short_desc_attributes=tuple(d.get("short_desc_attributes") or ()),
            invoice_attributes=tuple(d.get("invoice_attributes") or ()),
            provenance=d.get("provenance", PROVENANCE_DERIVED),
            source=d.get("source", "pack:taxonomy.json"),
        )

    def _compile(self) -> None:
        self._patterns = {}
        for leaf in self._leaves:
            pos: List[Tuple[re.Pattern, float, str]] = []
            for kw in leaf.strong_keywords:
                pos.append((_compile_kw(kw), W_STRONG, kw))
            for kw in leaf.keywords:
                pos.append((_compile_kw(kw), W_WEAK, kw))
            if leaf.product_name and leaf.id != self._fallback_id:
                pos.append((_compile_kw(leaf.product_name), W_PRODUCT_NAME, leaf.product_name))
            neg = [_compile_kw(kw) for kw in leaf.exclude_keywords]
            self._patterns[leaf.id] = (pos, neg)

    # -- access -----------------------------------------------------------
    def __len__(self) -> int:
        return len(self._leaves)

    @property
    def leaves(self) -> List[LeafNode]:
        return list(self._leaves)

    @property
    def fallback(self) -> Optional[LeafNode]:
        return self._by_id.get(self._fallback_id)

    def get(self, leaf_id: str) -> Optional[LeafNode]:
        return self._by_id.get(leaf_id)

    def by_classpath(self, classpath: str) -> Optional[LeafNode]:
        cp = str(classpath or "").strip().lower()
        for l in self._leaves:
            if l.classpath.lower() == cp:
                return l
        return None

    @property
    def classpaths(self) -> List[str]:
        return [l.classpath for l in self._leaves if l.classpath]

    # -- classification ---------------------------------------------------
    def score_all(self, text: str) -> List[Candidate]:
        """Score every leaf against the text; excluded leaves score zero."""
        s = str(text or "")
        out: List[Candidate] = []
        for leaf in self._leaves:
            if leaf.id == self._fallback_id:
                continue
            pos, neg = self._patterns[leaf.id]
            if any(rx.search(s) for rx in neg):
                continue
            score, matched = 0.0, []
            for rx, weight, label in pos:
                if rx.search(s):
                    score += weight
                    matched.append(label)
            if score > 0:
                # Longer, more specific keyword sets are better evidence.
                score += 0.5 * (len(matched) - 1)
                out.append(Candidate(leaf, score, matched))
        out.sort(key=lambda c: (-c.score, c.leaf.id))
        return out

    def classify(self, text: str) -> Classification:
        """
        Deterministic classification with explicit ambiguity detection.

        Confidence blends absolute evidence strength with the margin over the
        runner-up, so a single weak keyword never yields high confidence.
        """
        cands = self.score_all(text)
        if not cands:
            fb = self.fallback
            return Classification(fb, 0.0, 0.0, [], False,
                                  "fallback" if fb else "unresolved", [])

        top = cands[0]
        second = cands[1].score if len(cands) > 1 else 0.0
        ratio = (second / top.score) if top.score else 0.0
        ambiguous = ratio >= AMBIGUITY_RATIO

        # Evidence strength: one strong keyword ~= 10 -> 0.62; two -> 0.80.
        strength = min(1.0, top.score / 16.0)
        margin = 1.0 - ratio
        confidence = round(min(0.97, 0.55 * strength + 0.45 * (0.35 + 0.65 * margin)), 4)
        if ambiguous:
            confidence = min(confidence, 0.55)

        return Classification(top.leaf, top.score, confidence, cands[:5],
                              ambiguous, "keyword", list(top.matched))

    # -- attribute helpers -------------------------------------------------
    def attribute_labels(self, leaf_id: str) -> Tuple[str, ...]:
        leaf = self._by_id.get(leaf_id)
        return leaf.labels if leaf else ()

    def all_lov_values(self) -> int:
        return sum(len(a.values) for l in self._leaves for a in l.attributes)
