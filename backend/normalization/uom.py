"""
Unit-of-measure registry and normalisation engine.

House style, confirmed against the labelled delivery-format rows:

    '24 in'    correct     -- single space between magnitude and abbreviation
    '24in'     incorrect
    '120 V', '15 A', '47 dBA', '240 kW-hr'

The abbreviation is *case-sensitive* and must come from the approved list; the
engine never invents an abbreviation.  When an incoming unit cannot be resolved to
an approved UOM the original token is preserved and the field is flagged so a human
sees it, rather than being silently coerced.

Provenance
----------
``UOM_SEED`` below is a derived pack: every entry is either (a) observed directly in
the supplied labelled data, or (b) a standard industrial abbreviation.  When the
official ``Unilog_Master_UOM_Standards_Abbreviations_and_Terms.xlsx`` is dropped into
``data/reference/`` the loader replaces this seed wholesale -- see
``backend.reference.loader.load_uom_standards``.  ``UomRegistry.provenance`` records
which source is live so the UI never overstates authority.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

DQUOTE = chr(34)   # "  -- inch mark
SQUOTE = chr(39)   # '  -- foot mark

# (canonical_abbreviation, measurement_type, (synonyms/aliases, ...))
UOM_SEED: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    # --- Length -----------------------------------------------------------
    ("in",    "Length", ("in", "in.", "inch", "inches", DQUOTE, SQUOTE + SQUOTE)),
    ("ft",    "Length", ("ft", "ft.", "foot", "feet", SQUOTE)),
    ("yd",    "Length", ("yd", "yard", "yards")),
    ("mm",    "Length", ("mm", "millimeter", "millimetre", "millimeters", "millimetres")),
    ("cm",    "Length", ("cm", "centimeter", "centimetre", "centimeters", "centimetres")),
    ("m",     "Length", ("m", "meter", "metre", "meters", "metres")),
    ("mil",   "Length", ("mil", "mils", "thou")),
    # --- Area / Volume ----------------------------------------------------
    ("sq ft", "Area", ("sq ft", "sqft", "ft2", "square foot", "square feet")),
    ("sq in", "Area", ("sq in", "sqin", "in2", "square inch", "square inches")),
    ("cu ft", "Volume", ("cu ft", "cuft", "ft3", "cubic foot", "cubic feet", "cf")),
    ("gal",   "Volume", ("gal", "gallon", "gallons")),
    ("qt",    "Volume", ("qt", "quart", "quarts")),
    ("fl oz", "Volume", ("fl oz", "floz", "fluid ounce", "fluid ounces")),
    ("L",     "Volume", ("l", "liter", "litre", "liters", "litres")),
    ("mL",    "Volume", ("ml", "milliliter", "millilitre", "milliliters")),
    # --- Mass -------------------------------------------------------------
    ("lb",    "Weight", ("lb", "lbs", "lb.", "pound", "pounds")),
    ("oz",    "Weight", ("oz", "oz.", "ounce", "ounces")),
    ("kg",    "Weight", ("kg", "kilogram", "kilograms")),
    ("g",     "Weight", ("g", "gram", "grams")),
    # --- Electrical -------------------------------------------------------
    ("V",     "Voltage", ("v", "volt", "volts", "vac", "vdc", "v ac", "v dc")),
    ("A",     "Amperage", ("a", "amp", "amps", "ampere", "amperes")),
    ("mA",    "Amperage", ("ma", "milliamp", "milliamps", "milliampere")),
    ("W",     "Power", ("w", "watt", "watts")),
    ("kW",    "Power", ("kw", "kilowatt", "kilowatts")),
    ("hp",    "Power", ("hp", "horsepower")),
    ("kW-hr", "Energy", ("kw-hr", "kwh", "kw hr", "kilowatt hour", "kilowatt-hour", "kwhr")),
    ("Hz",    "Frequency", ("hz", "hertz", "cycles per second")),
    ("Ah",    "Charge", ("ah", "amp hour", "amp-hour", "amp hours", "ampere hour")),
    ("ohm",   "Resistance", ("ohm", "ohms")),
    ("VA",    "Apparent Power", ("va", "volt-ampere", "volt ampere")),
    # --- Light ------------------------------------------------------------
    ("lm",    "Luminous Flux", ("lm", "lumen", "lumens")),
    ("K",     "Color Temperature", ("k", "kelvin")),
    ("CRI",   "Color Rendering", ("cri",)),
    ("fc",    "Illuminance", ("fc", "footcandle", "foot-candle", "footcandles")),
    # --- Sound / Speed / Rate --------------------------------------------
    ("dBA",   "Sound Level", ("dba", "db(a)", "decibel a")),
    ("dB",    "Sound Level", ("db", "decibel", "decibels")),
    ("rpm",   "Rotational Speed", ("rpm", "revolutions per minute", "r/min")),
    ("spm",   "Stroke Rate", ("spm", "strokes per minute")),
    ("bpm",   "Blow Rate", ("bpm", "blows per minute")),
    ("fpm",   "Linear Speed", ("fpm", "feet per minute", "ft/min", "sfpm")),
    ("mph",   "Speed", ("mph", "miles per hour")),
    ("cfm",   "Air Flow", ("cfm", "cubic feet per minute", "ft3/min")),
    ("gpm",   "Flow Rate", ("gpm", "gallons per minute", "gal/min")),
    # --- Pressure / Torque / Force ---------------------------------------
    ("psi",   "Pressure", ("psi", "pounds per square inch", "lb/in2")),
    ("bar",   "Pressure", ("bar", "bars")),
    ("in-lb", "Torque", ("in-lb", "in lb", "inch pound", "inch-pounds", "in.lbs")),
    ("ft-lb", "Torque", ("ft-lb", "ft lb", "foot pound", "foot-pounds", "ft.lbs")),
    ("Nm",    "Torque", ("nm", "newton meter", "newton-meter", "n-m")),
    ("lbf",   "Force", ("lbf", "pound force", "pound-force")),
    # --- Temperature / Time ----------------------------------------------
    ("deg F", "Temperature", ("deg f", "degf", "fahrenheit")),
    ("deg C", "Temperature", ("deg c", "degc", "celsius", "centigrade")),
    ("hr",    "Time", ("hr", "hrs", "hour", "hours", "h")),
    ("min",   "Time", ("min", "mins", "minute", "minutes")),
    ("sec",   "Time", ("sec", "secs", "second", "seconds", "s")),
    ("yr",    "Time", ("yr", "yrs", "year", "years")),
    # --- Count / Packaging ------------------------------------------------
    ("ea",    "Count", ("ea", "each")),
    ("pk",    "Count", ("pk", "pack", "packs")),
    ("pc",    "Count", ("pc", "pcs", "piece", "pieces", "ct", "count")),
    ("bx",    "Count", ("bx", "box", "boxes")),
    ("rl",    "Count", ("rl", "roll", "rolls")),
    ("bdl",   "Count", ("bdl", "bundle", "bundles")),
    ("pr",    "Count", ("pr", "pair", "pairs")),
    ("set",   "Count", ("set", "sets")),
    ("gr",    "Abrasive Grit", ("grit", "gr")),
    ("BTU",   "Heat", ("btu", "btus", "british thermal unit")),
    ("gauge", "Gauge", ("gauge", "ga", "ga.", "gge")),
    ("AWG",   "Wire Gauge", ("awg", "american wire gauge")),
    ("tooth", "Tooth Count", ("tooth", "teeth", "tpi")),
)


def _key(text: str) -> str:
    """Alias lookup key: lowercase, punctuation-insensitive, whitespace-collapsed."""
    s = str(text or "").strip().lower()
    s = s.replace(chr(176), "deg ")          # degree sign
    s = re.sub(r"[\s._]+", " ", s)
    return s.strip()


@dataclass
class UomEntry:
    abbreviation: str
    measurement_type: str
    aliases: Tuple[str, ...] = ()


@dataclass
class UomResolution:
    """Outcome of resolving a raw unit token."""
    raw: str
    abbreviation: Optional[str]
    measurement_type: Optional[str]
    method: str            # exact | alias | unresolved
    confidence: float
    approved: bool

    @property
    def ok(self) -> bool:
        return self.approved and bool(self.abbreviation)


class UomRegistry:
    """Approved-UOM lookup with alias resolution. Never invents an abbreviation."""

    def __init__(self, entries: Optional[Iterable[UomEntry]] = None,
                 provenance: str = "derived-seed"):
        self.provenance = provenance
        self._by_abbrev: Dict[str, UomEntry] = {}
        self._alias: Dict[str, str] = {}
        self.load(entries if entries is not None else self._seed_entries())

    @staticmethod
    def _seed_entries() -> List[UomEntry]:
        return [UomEntry(a, t, tuple(al)) for a, t, al in UOM_SEED]

    def load(self, entries: Iterable[UomEntry]) -> None:
        self._by_abbrev.clear()
        self._alias.clear()
        for e in entries:
            if not e.abbreviation:
                continue
            self._by_abbrev[e.abbreviation] = e
            for alias in (e.abbreviation,) + tuple(e.aliases or ()):
                k = _key(alias)
                # First writer wins: seed order encodes precedence for collisions
                # such as 'm' (meter) vs 'min', or 's' (second) vs 'set'.
                self._alias.setdefault(k, e.abbreviation)

    # -- queries ---------------------------------------------------------
    def __len__(self) -> int:
        return len(self._by_abbrev)

    @property
    def measurement_types(self) -> List[str]:
        return sorted({e.measurement_type for e in self._by_abbrev.values() if e.measurement_type})

    def is_approved(self, abbrev: str) -> bool:
        return abbrev in self._by_abbrev

    def entry(self, abbrev: str) -> Optional[UomEntry]:
        return self._by_abbrev.get(abbrev)

    def resolve(self, raw: str) -> UomResolution:
        """Map a raw unit token to its approved abbreviation."""
        raw_s = str(raw or "").strip()
        if not raw_s:
            return UomResolution(raw_s, None, None, "unresolved", 0.0, False)

        if raw_s in self._by_abbrev:                       # already canonical
            e = self._by_abbrev[raw_s]
            return UomResolution(raw_s, e.abbreviation, e.measurement_type, "exact", 1.0, True)

        hit = self._alias.get(_key(raw_s))
        if hit:
            e = self._by_abbrev[hit]
            return UomResolution(raw_s, e.abbreviation, e.measurement_type, "alias", 0.95, True)

        return UomResolution(raw_s, None, None, "unresolved", 0.0, False)

    # -- formatting ------------------------------------------------------
    def format_measure(self, value, unit: Optional[str], compact: bool = False) -> str:
        """
        Render 'value unit' in house style.

            format_measure('24', 'inches')            -> '24 in'
            format_measure('120', 'v')                -> '120 V'
            format_measure('50-1/4', 'in', compact=1) -> '50-1/4IN'   (invoice style)

        An unresolved unit is emitted verbatim so nothing is silently dropped.
        """
        val = "" if value is None else str(value).strip()
        if not unit:
            return val
        res = self.resolve(unit)
        abbrev = res.abbreviation or str(unit).strip()
        if not val:
            return abbrev
        if compact:
            return f"{val}{abbrev.upper().replace(' ', '')}"
        return f"{val} {abbrev}"


# A parsed "<magnitude><unit>" token, e.g. '24in', '1/2"', '120V', '50-1/4 in'.
_MEASURE_RE = re.compile(
    r"^\s*(?P<value>[+-]?(?:\d+[-\s])?\d+(?:\.\d+)?(?:\s*/\s*\d+)?)"
    r"\s*(?P<unit>[A-Za-z" + DQUOTE + SQUOTE + chr(176) + r"][A-Za-z\-\s\."
    + DQUOTE + SQUOTE + chr(176) + r"]*)?\s*$"
)


def split_measure(text: str) -> Optional[Tuple[str, Optional[str]]]:
    """
    Split a measurement token into (value, unit).

        '24in'      -> ('24', 'in')
        '50-1/4 in' -> ('50-1/4', 'in')
        'Leg'       -> None
    """
    if text is None:
        return None
    m = _MEASURE_RE.match(str(text))
    if not m:
        return None
    value = re.sub(r"\s*/\s*", "/", m.group("value").strip())
    unit = (m.group("unit") or "").strip() or None
    return value, unit


_DEFAULT_REGISTRY: Optional[UomRegistry] = None


def default_registry() -> UomRegistry:
    """Process-wide registry; replaced in place when official standards load."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = UomRegistry()
    return _DEFAULT_REGISTRY


def set_default_registry(reg: UomRegistry) -> None:
    global _DEFAULT_REGISTRY
    _DEFAULT_REGISTRY = reg
