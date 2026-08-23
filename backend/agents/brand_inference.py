"""
Corpus-derived brand inference from manufacturer part-number structure.

Motivation
----------
Products bought through a co-op arrive with every brand column set to a placeholder
and no brand token in the description:

    PDSH4816AF  Dishwasher SS - Display Only      Appliance Dealers Cooperative (APPDE)

Nothing in that row names a brand.  But sibling rows in the *same feed* often do:

    KDTS424SBE  Kitchen Aid Dishwasher Bk         (brand visible)
    KDTS324SPS  Kitchen Aid Dishwasher SS         (brand visible)
    KDPS624SJP  Dishwasher Juniper - Display Only (brand NOT visible)

Manufacturers use structured part numbers, so an alphabetic prefix shared with rows
whose brand *is* known is real evidence.  This module mines those prefixes and
applies them with explicitly LOW confidence.

Guardrails -- this is inference, not fact.  An early unguarded version of this
module produced 'SQ Washer -> Edge Eyewear' purely because both part numbers begin
'TC', so the rules are now scoped:

  * prefixes are at least ``MIN_PREFIX_LEN`` characters;
  * a rule needs ``MIN_SUPPORT`` distinct supporting rows;
  * the prefix must map to exactly one brand (no mixed-brand prefixes);
  * **the candidate must share the supporting rows' category or supplier** -- a
    washer never inherits a brand from safety glasses;
  * confidence is capped at ``MAX_CONFIDENCE`` (below the MEDIUM band) and the
    supporting row count travels with the result as evidence.
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

MIN_PREFIX_LEN = 3
MAX_PREFIX_LEN = 5
MIN_SUPPORT = 2            # distinct sibling rows required
MAX_CONFIDENCE = 0.55      # inference can never reach the HIGH band
PURITY_THRESHOLD = 1.0     # prefix must map to a single brand


def _alpha_prefix(mpn: str, length: int) -> Optional[str]:
    """Leading alphabetic run of a part number, e.g. 'KDTS424SBE' -> 'KDTS'."""
    m = re.match(r"^([A-Za-z]{%d,})" % length, str(mpn or "").strip())
    if not m:
        return None
    head = m.group(1)
    return head[:length].upper() if len(head) >= length else None


@dataclass(frozen=True)
class Observation:
    mpn: str
    brand: Optional[str]
    category: Optional[str] = None
    supplier: Optional[str] = None


@dataclass
class PrefixRule:
    prefix: str
    brand: str
    support: int
    examples: Tuple[str, ...]
    categories: FrozenSet[str] = field(default_factory=frozenset)
    suppliers: FrozenSet[str] = field(default_factory=frozenset)

    @property
    def confidence(self) -> float:
        # More supporting siblings and a longer prefix mean stronger evidence.
        base = 0.30 + 0.06 * self.support + 0.03 * len(self.prefix)
        return round(min(MAX_CONFIDENCE, base), 4)

    def scope_detail(self, category: Optional[str], supplier: Optional[str]) -> str:
        if category and category in self.categories:
            return f"same category ({category})"
        if supplier and supplier in self.suppliers:
            return f"same supplier ({supplier})"
        return "unscoped"

    def detail(self, category: Optional[str] = None, supplier: Optional[str] = None) -> str:
        ex = ", ".join(self.examples[:3])
        return (f"part-number prefix '{self.prefix}' maps to {self.brand} in "
                f"{self.support} other rows of this feed sharing the "
                f"{self.scope_detail(category, supplier)} ({ex})")


class MpnBrandInference:
    """Learns brand<-part-number-prefix rules from rows whose brand is known."""

    def __init__(self) -> None:
        self._rules: Dict[str, PrefixRule] = {}

    def fit(self, observations: Sequence[Observation]) -> "MpnBrandInference":
        """Teach from rows that already have a confidently resolved brand."""
        buckets: Dict[str, Dict[str, List[Observation]]] = collections.defaultdict(
            lambda: collections.defaultdict(list))
        for obs in observations:
            if not obs.mpn or not obs.brand:
                continue
            for length in range(MIN_PREFIX_LEN, MAX_PREFIX_LEN + 1):
                p = _alpha_prefix(obs.mpn, length)
                if p:
                    buckets[p][obs.brand].append(obs)

        rules: Dict[str, PrefixRule] = {}
        for prefix, by_brand in buckets.items():
            total = sum(len(v) for v in by_brand.values())
            brand, members = max(by_brand.items(), key=lambda kv: len(kv[1]))
            purity = len(members) / total if total else 0.0
            if len(members) >= MIN_SUPPORT and purity >= PURITY_THRESHOLD:
                rules[prefix] = PrefixRule(
                    prefix=prefix,
                    brand=brand,
                    support=len(members),
                    examples=tuple(sorted(o.mpn for o in members)[:5]),
                    categories=frozenset(o.category for o in members if o.category),
                    suppliers=frozenset(o.supplier for o in members if o.supplier),
                )
        self._rules = rules
        return self

    def infer(self, mpn: str, category: Optional[str] = None,
              supplier: Optional[str] = None,
              require_scope: bool = True) -> Optional[PrefixRule]:
        """
        Longest matching prefix rule for this part number.

        With ``require_scope`` (the default) the rule only fires when the candidate
        shares a category or supplier with the rows that taught it.  A rule whose
        entire support is this row itself is always rejected, so a product can never
        be evidence for its own brand.
        """
        if not mpn:
            return None
        for length in range(MAX_PREFIX_LEN, MIN_PREFIX_LEN - 1, -1):
            p = _alpha_prefix(mpn, length)
            if not p:
                continue
            rule = self._rules.get(p)
            if rule is None:
                continue
            others = [m for m in rule.examples if m.upper() != str(mpn).upper()]
            effective = rule.support - (len(rule.examples) - len(others))
            if effective < MIN_SUPPORT:
                continue
            if require_scope:
                in_category = bool(category) and category in rule.categories
                in_supplier = bool(supplier) and supplier in rule.suppliers
                if not (in_category or in_supplier):
                    continue
            return rule
        return None

    @property
    def rules(self) -> List[PrefixRule]:
        return sorted(self._rules.values(), key=lambda r: (-r.support, r.prefix))

    def __len__(self) -> int:
        return len(self._rules)
