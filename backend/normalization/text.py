"""
Text cleansing, placeholder detection and casing rules.

Everything here is deterministic and reversible-by-inspection: each transform
records what it did so the Evidence Graph can show 'raw -> transform -> value'.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Placeholder detection
# ---------------------------------------------------------------------------
# Observed literally in the supplied 1,000-row feed:
#   '-- Unbranded --' (799), '-- No Unilog Brand --' (1000),
#   '-- No DIB Brand --' (755), '-' (41 in Part_Manuf)
# These are *sentinels meaning absent*, not real values.  Treating them as brands
# is the single biggest silent-corruption risk in this dataset.
PLACEHOLDER_PATTERNS: Tuple[str, ...] = (
    r"^-+$",
    r"^n/?a$",
    r"^na$",
    r"^none$",
    r"^null$",
    r"^nil$",
    r"^unknown$",
    r"^tbd$",
    r"^t\.b\.d\.?$",
    r"^\.+$",
    r"^0+$",
    r"^x+$",
    r"^\?+$",
    r"^--\s*.*\s*--$",              # '-- Unbranded --', '-- No DIB Brand --'
    r"^no\s+\w+\s+brand$",
    r"^un-?branded$",
    r"^commodity\s*[-–]\s*unbranded$",
    r"^not\s+(applicable|available|found|specified)$",
    r"^see\s+(description|notes?)$",
    r"^blank$",
    r"^empty$",
)

_PLACEHOLDER_RE = re.compile("|".join(f"(?:{p})" for p in PLACEHOLDER_PATTERNS), re.IGNORECASE)


def is_placeholder(value) -> bool:
    """True when a populated-looking cell actually means 'no value'."""
    if value is None:
        return True
    s = str(value).strip()
    if not s:
        return True
    return bool(_PLACEHOLDER_RE.match(s))


def clean_value(value) -> Optional[str]:
    """Return the cleaned value, or None when it is empty/placeholder."""
    if value is None:
        return None
    s = normalize_whitespace(repair_mojibake(str(value)))
    if not s or is_placeholder(s):
        return None
    return s


# ---------------------------------------------------------------------------
# Encoding repair
# ---------------------------------------------------------------------------
# UTF-8 bytes misread as cp1252 produce 'CafÃ©', 'FRIGIDAIREÂ®', 'CleanBoostâ„¢'.
# The supplied files are clean UTF-8, but supplier feeds routinely are not, so the
# repair runs defensively -- and only when it round-trips cleanly, so correct text
# is never damaged.
_MOJIBAKE_SIGNALS = ("Ã", "Â", "â€", "â„", "Ã©", "Ã¢")


def repair_mojibake(text: str) -> str:
    """Undo one layer of utf-8-read-as-cp1252 corruption, when clearly present."""
    if not text or not any(sig in text for sig in _MOJIBAKE_SIGNALS):
        return text
    try:
        repaired = text.encode("cp1252", errors="strict").decode("utf-8", errors="strict")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    # Only accept the repair if it did not introduce replacement characters.
    return text if "�" in repaired else repaired


def normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace (incl. NBSP) and trim."""
    if not text:
        return ""
    s = unicodedata.normalize("NFC", str(text))
    s = s.replace(" ", " ").replace("​", "")
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Casing
# ---------------------------------------------------------------------------
# Words that stay lowercase inside a title unless they lead it.
_TITLE_MINORS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "in", "nor", "of", "on",
    "or", "per", "the", "to", "vs", "via", "with", "w/",
}

# Tokens whose canonical form is not simple title case.  Extended at runtime from
# resolved brand names and approved LOV values so it is data-driven, not a guess.
_ACRONYMS = {
    "led", "ul", "cul", "csa", "nsf", "asse", "cee", "gfci", "gfi", "afci", "usb",
    "pvc", "abs", "cpvc", "mdf", "osb", "hdpe", "epdm", "tpo", "ada", "nema",
    "ansi", "astm", "sae", "npt", "mnpt", "fnpt", "iso", "rohs", "sds", "mtr",
    "unspsc", "upc", "ean", "gtin", "cct", "cri", "hvac", "btu", "awg", "ip",
    "ss", "sst", "hp", "id", "od", "ac", "dc", "rv", "otr", "cf", "tpi",
}


def register_acronyms(words) -> None:
    """Teach the caser about acronyms discovered in reference data."""
    for w in words or ():
        t = str(w).strip().lower()
        if t:
            _ACRONYMS.add(t)


def _cap_token(tok: str) -> str:
    """Capitalise one token, preserving intra-token punctuation (built-in, 5-wash)."""
    if not tok:
        return tok
    if tok.lower() in _ACRONYMS:
        return tok.upper()
    # Keep tokens that already carry internal capitals (CleanBoost, HardiePlank).
    if any(c.isupper() for c in tok[1:]):
        return tok
    if re.search(r"\d", tok):
        # '5-wash' -> '5-Wash'; '120v' left to the UOM engine.
        return re.sub(r"(?<=[\d\-])([a-z])", lambda m: m.group(1).upper(), tok[:1].upper() + tok[1:])
    return tok[:1].upper() + tok[1:]


def title_case(text: str) -> str:
    """
    Unilog title case: capitalise each word, keep minor words lowercase mid-phrase,
    uppercase known acronyms, and never destroy existing internal capitals.
    """
    if not text:
        return ""
    words = normalize_whitespace(text).split(" ")
    out: List[str] = []
    for i, w in enumerate(words):
        parts = re.split(r"([/\-])", w)          # keep separators
        rebuilt = []
        for j, p in enumerate(parts):
            if p in ("/", "-"):
                rebuilt.append(p)
                continue
            low = p.lower()
            lead = (i == 0 and j == 0)
            if not lead and low in _TITLE_MINORS and len(parts) == 1:
                rebuilt.append(low)
            else:
                rebuilt.append(_cap_token(p))
        out.append("".join(rebuilt))
    return " ".join(out)


def sentence_case(text: str) -> str:
    """First letter upper, rest untouched (preserves brand/acronym casing)."""
    s = normalize_whitespace(text)
    return s[:1].upper() + s[1:] if s else ""


def upper_case(text: str) -> str:
    return normalize_whitespace(text).upper()


# ---------------------------------------------------------------------------
# Tokenisation helpers used by parsers/extractors
# ---------------------------------------------------------------------------
def strip_leading_part_number(desc: str, part_number: Optional[str]) -> str:
    """
    Supplier descriptions almost always repeat the MPN at the front:
        'PDSH4816AF Dishwasher SS - Display Only' -> 'Dishwasher SS - Display Only'
    Removing it stops the MPN polluting keyword classification.
    """
    s = normalize_whitespace(desc)
    if not s or not part_number:
        return s
    pn = normalize_whitespace(part_number)
    if not pn:
        return s
    if s.lower().startswith(pn.lower()):
        return s[len(pn):].lstrip(" -,:")
    # Some feeds mangle case/punctuation of the MPN; compare alphanumerics only.
    squash = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())
    head = s.split(" ", 1)
    if len(head) == 2 and squash(head[0]) == squash(pn):
        return head[1].lstrip(" -,:")
    return s


def split_segments(desc: str) -> List[str]:
    """
    Supplier descriptions use ' - ' as a soft field separator:
        '3/8" 16"x16' Smart Pan Cedar - No-Groove Sq Edge B&B'
    Returns the trimmed segments in order.
    """
    if not desc:
        return []
    parts = re.split(r"\s+[-–]\s+", normalize_whitespace(desc))
    return [p.strip() for p in parts if p.strip()]


def truncate(text: str, limit: int, ellipsis: bool = False) -> str:
    """
    Hard character cap that cuts on a word boundary where possible.  Used by the
    validation self-correction loop, never to silently hide overflow.
    """
    if text is None:
        return ""
    s = str(text)
    if limit <= 0 or len(s) <= limit:
        return s
    cut = s[:limit]
    if " " in cut[max(0, limit - 20):]:
        cut = cut[:cut.rstrip().rfind(" ")].rstrip(" ,;-")
    return (cut + "…") if ellipsis and len(cut) < limit else cut


@dataclass
class Transform:
    """One recorded normalisation step, for the Evidence Graph."""
    field: str
    raw: str
    value: str
    method: str
    detail: str = ""


@dataclass
class CleanResult:
    """Result of cleaning a whole input record."""
    values: Dict[str, Optional[str]] = field(default_factory=dict)
    placeholders: List[str] = field(default_factory=list)
    transforms: List[Transform] = field(default_factory=list)

    def get(self, key: str) -> Optional[str]:
        return self.values.get(key)


def clean_record(record: Dict[str, object], fields: Sequence[str]) -> CleanResult:
    """Clean the named fields of one raw input row, recording every transform."""
    res = CleanResult()
    for f in fields:
        raw = record.get(f)
        raw_s = "" if raw is None else str(raw)
        cleaned = clean_value(raw_s)
        res.values[f] = cleaned
        if raw_s.strip() and cleaned is None:
            res.placeholders.append(f)
            res.transforms.append(Transform(f, raw_s, "", "placeholder_removed",
                                            "sentinel value treated as absent"))
        elif cleaned is not None and cleaned != raw_s:
            res.transforms.append(Transform(f, raw_s, cleaned, "text_cleanup",
                                            "whitespace/encoding normalised"))
    return res
