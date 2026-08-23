"""
Deterministic decimal <-> fraction conversion.

Ground-truth evidence (delivery-format labelled rows) shows dimensional values are
expressed as mixed fractions in "whole-numerator/denominator" form:

    50.25    -> 50-1/4
    33.4375  -> 33-7/16
    22.625   -> 22-5/8
    0.5      -> 1/2
    0.015625 -> 1/64

This module is intentionally LLM-free: the conversion is exact arithmetic over a
binary denominator ladder, which an LLM cannot do more reliably than Python can.

If an official ``Decimal_Fraction.xlsx`` is supplied it is loaded as an *override*
table (see ``load_override_table``) so any house-specific rounding wins over the
computed ladder.  Absent that file the ladder below reproduces every conversion
observed in the labelled data.
"""
from __future__ import annotations

import re
from fractions import Fraction
from typing import Dict, Optional, Tuple

# Binary denominators used by US industrial/imperial dimensioning.
DEFAULT_DENOMINATORS: Tuple[int, ...] = (2, 4, 8, 16, 32, 64)

# Populated from Decimal_Fraction.xlsx when available: {decimal_string: fraction_string}
_OVERRIDES: Dict[str, str] = {}

_NUMBER_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)$")
_MIXED_RE = re.compile(r"^(?P<sign>[+-])?(?:(?P<whole>\d+)[-\s])?(?P<num>\d+)\s*/\s*(?P<den>\d+)$")


def load_override_table(pairs: Dict[str, str]) -> None:
    """Install exact decimal->fraction overrides from an official reference table."""
    _OVERRIDES.clear()
    for k, v in pairs.items():
        key = _canonical_decimal_key(k)
        if key is not None and v:
            _OVERRIDES[key] = str(v).strip()


def override_count() -> int:
    return len(_OVERRIDES)


def _canonical_decimal_key(value) -> Optional[str]:
    """Normalise '0.50', '.5', '0.5000' to one key so lookups are stable."""
    try:
        d = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return f"{d:.10f}".rstrip("0").rstrip(".") or "0"


def snap_denominator(value: float,
                     denominators: Tuple[int, ...] = DEFAULT_DENOMINATORS,
                     tolerance: float = 1e-6) -> Optional[Fraction]:
    """
    Exact Fraction for ``value`` on the smallest denominator representing it within
    ``tolerance``.  None when the value is off-ladder -- callers keep the decimal
    rather than invent precision (Golden Rule 1).
    """
    for den in denominators:
        scaled = value * den
        nearest = round(scaled)
        if abs(scaled - nearest) <= tolerance * max(1.0, abs(value)):
            return Fraction(int(nearest), den)
    return None


def decimal_to_fraction(value,
                        denominators: Tuple[int, ...] = DEFAULT_DENOMINATORS) -> Optional[str]:
    """
    Convert a decimal to Unilog mixed-fraction form.

        0.5      -> '1/2'
        50.25    -> '50-1/4'
        0.015625 -> '1/64'
        7.0      -> '7'      (whole numbers stay whole)
        3.14159  -> None     (off-ladder: do not invent)
    """
    key = _canonical_decimal_key(value)
    if key is None:
        return None
    if key in _OVERRIDES:
        return _OVERRIDES[key]

    d = float(key)
    sign = "-" if d < 0 else ""
    d = abs(d)

    whole = int(d)
    remainder = d - whole
    if remainder < 1e-12:
        return f"{sign}{whole}"

    frac = snap_denominator(remainder, denominators)
    if frac is None:
        return None
    if frac.denominator == 1:                      # 0.999999 rounded up
        return f"{sign}{whole + frac.numerator}"
    if whole == 0:
        return f"{sign}{frac.numerator}/{frac.denominator}"
    return f"{sign}{whole}-{frac.numerator}/{frac.denominator}"


def fraction_to_decimal(text: str) -> Optional[float]:
    """Parse '50-1/4', '1/2', '33 7/16', '7' back to float. None if unparseable."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    if _NUMBER_RE.match(s):
        return float(s)
    m = _MIXED_RE.match(s)
    if not m:
        return None
    den = int(m.group("den"))
    if den == 0:
        return None
    whole = int(m.group("whole") or 0)
    val = whole + int(m.group("num")) / den
    return -val if m.group("sign") == "-" else val


def normalize_measure(text: str,
                      denominators: Tuple[int, ...] = DEFAULT_DENOMINATORS) -> Optional[str]:
    """Normalise one numeric token to house style. None when not numeric."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    if _MIXED_RE.match(s):
        dec = fraction_to_decimal(s)                # '33 7/16' -> '33-7/16'
        return decimal_to_fraction(dec, denominators) if dec is not None else s
    if _NUMBER_RE.match(s):
        converted = decimal_to_fraction(s, denominators)
        return converted if converted is not None else s
    return None


_EMBEDDED_DECIMAL_RE = re.compile(r"(?<![\w/.])(\d+\.\d+)(?![\w/])")


def normalize_decimals_in_text(text: str,
                               denominators: Tuple[int, ...] = DEFAULT_DENOMINATORS) -> str:
    """
    Rewrite standalone decimals inside free text to fractions when they land exactly
    on the ladder; leave them alone otherwise.

        'Depth 50.25 in' -> 'Depth 50-1/4 in'
    """
    if not text:
        return text or ""

    def _sub(m):
        converted = decimal_to_fraction(m.group(1), denominators)
        return converted if converted is not None else m.group(1)

    return _EMBEDDED_DECIMAL_RE.sub(_sub, str(text))


def build_reference_table(max_denominator: int = 64) -> Dict[str, str]:
    """Full decimal->fraction table in lowest terms up to ``max_denominator``."""
    table: Dict[str, str] = {}
    den = 2
    while den <= max_denominator:
        for num in range(1, den):
            f = Fraction(num, den)
            if f.denominator != den:
                continue
            table[_canonical_decimal_key(float(f))] = f"{f.numerator}/{f.denominator}"
        den *= 2
    return table
