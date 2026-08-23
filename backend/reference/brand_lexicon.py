"""
Brand-mention detection inside free-text product descriptions.

Why a lexicon rather than an LLM: brand detection is a closed-vocabulary lookup and
must be *precise*.  Substring matching produces silent corruption -- on the supplied
feed, naive matching turned 'Square Drive Bit' into the Square D brand and
'Phillips Drive Bit' into Philips.  Word-boundary matching plus per-alias context
guards removes both classes of error deterministically.

The lexicon asserts only that a token is a known trade name.  It never asserts a
fact about a specific product, so it does not breach Golden Rule 1.  It is replaced
wholesale by the official UniCat brand list when that workbook is supplied.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from backend.normalization.text import normalize_whitespace


@dataclass
class BrandMention:
    canonical: str
    alias: str
    start: int
    end: int
    symbol: str = ""
    confidence: float = 0.0

    @property
    def display(self) -> str:
        return f"{self.canonical}{self.symbol}" if self.symbol else self.canonical


@dataclass
class LexiconEntry:
    canonical: str
    aliases: Tuple[str, ...]
    reject_after: Tuple[str, ...] = ()
    # When set, the alias only counts as a brand if the NEXT word is in this list.
    # Used for short homographs: 'LG' is the brand in 'LG Dishwasher' but the size
    # Large in 'Heated Glove Blk LG'.
    require_after: Tuple[str, ...] = ()
    symbol: str = ""
    provenance: str = "seed"


class BrandLexicon:
    """Word-boundary brand matcher with homograph guards."""

    def __init__(self, entries: Optional[Iterable[LexiconEntry]] = None,
                 global_reject_after: Sequence[str] = (),
                 provenance: str = "seed"):
        self.provenance = provenance
        self._global_reject = tuple(w.lower() for w in global_reject_after)
        self._entries: List[LexiconEntry] = list(entries or ())
        self._compiled: List[Tuple[re.Pattern, LexiconEntry, str]] = []
        self._compile()

    # -- construction -----------------------------------------------------
    @classmethod
    def from_pack(cls, path: Path) -> "BrandLexicon":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = [
            LexiconEntry(
                canonical=b["canonical"],
                aliases=tuple(b.get("aliases") or (b["canonical"],)),
                reject_after=tuple(b.get("reject_after") or ()),
                require_after=tuple(b.get("require_after") or ()),
                symbol=b.get("symbol", ""),
                provenance=data.get("provenance", "seed"),
            )
            for b in data.get("brands", [])
        ]
        guards = (data.get("guards") or {}).get("global_reject_after") or []
        return cls(entries, guards, provenance=data.get("provenance", "seed"))

    def extend(self, entries: Iterable[LexiconEntry]) -> None:
        """Merge additional entries (e.g. brands mined from the feed's own columns)."""
        known = {e.canonical.lower() for e in self._entries}
        for e in entries:
            if e.canonical.lower() in known:
                continue
            self._entries.append(e)
            known.add(e.canonical.lower())
        self._compile()

    def _compile(self) -> None:
        self._compiled = []
        # Longest alias first so 'kitchen aid' wins over a shorter overlapping alias.
        pairs: List[Tuple[str, LexiconEntry]] = []
        for e in self._entries:
            for a in e.aliases:
                a = a.strip()
                if a:
                    pairs.append((a, e))
        pairs.sort(key=lambda p: -len(p[0]))
        for alias, entry in pairs:
            # \b fails next to non-word chars, so bound on the alias's own edges.
            lead = r"\b" if alias[:1].isalnum() else ""
            trail = r"\b" if alias[-1:].isalnum() else ""
            self._compiled.append(
                (re.compile(lead + re.escape(alias) + trail, re.IGNORECASE), entry, alias)
            )

    # -- matching ---------------------------------------------------------
    def _blocked(self, text: str, entry: LexiconEntry, end: int) -> bool:
        """True when surrounding context marks this as a homograph, not a brand."""
        tail = text[end:end + 40].lstrip(" -,/")
        nxt = re.match(r"[A-Za-z]+", tail)
        word = nxt.group(0).lower() if nxt else ""

        if entry.require_after:
            # Short ambiguous alias: only a listed following noun makes it a brand.
            return word not in tuple(w.lower() for w in entry.require_after)

        if not word:
            return False
        return word in self._global_reject or word in tuple(w.lower() for w in entry.reject_after)

    def find(self, text: str) -> List[BrandMention]:
        """All non-overlapping brand mentions, earliest and longest first."""
        if not text:
            return []
        s = normalize_whitespace(text)
        taken: List[Tuple[int, int]] = []
        out: List[BrandMention] = []
        for rx, entry, alias in self._compiled:
            for m in rx.finditer(s):
                if any(not (m.end() <= a or m.start() >= b) for a, b in taken):
                    continue                          # overlaps a longer match
                if self._blocked(s, entry, m.end()):
                    continue
                taken.append((m.start(), m.end()))
                # A match at position 0 is usually the MPN echo; slightly lower trust.
                conf = 0.90 if len(alias) >= 4 else 0.75
                out.append(BrandMention(entry.canonical, alias, m.start(), m.end(),
                                        entry.symbol, conf))
        out.sort(key=lambda x: x.start)
        return out

    def best(self, text: str) -> Optional[BrandMention]:
        """Highest-confidence mention, tie-broken by earliest position."""
        hits = self.find(text)
        if not hits:
            return None
        return sorted(hits, key=lambda h: (-h.confidence, h.start))[0]

    def symbol_for(self, canonical: str) -> str:
        for e in self._entries:
            if e.canonical.lower() == str(canonical).lower():
                return e.symbol
        return ""

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def canonicals(self) -> List[str]:
        return [e.canonical for e in self._entries]
