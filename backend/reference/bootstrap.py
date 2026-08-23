"""
Derive manufacturer/brand master data from the supplied working data.

This module exists because the reference pack (UniCat manufacturer/brand list,
Unicat LOV, UOM standards, ...) is not always present.  Rather than invent master
data -- which would breach Golden Rule 1 -- it *mines* what the working data
actually evidences:

  * ``Part_Manuf`` carries an embedded code: 'Freud Inc (2435)' -> name+code.
  * ``E1_Brand`` / ``DIB_Brand`` carry brand names once placeholders are stripped.
  * Co-occurrence of (brand, Part_Manuf) across rows yields an evidence-backed
    brand -> manufacturer edge.  On the supplied feed this recovers non-obvious
    real relationships such as Diablo -> Freud Inc and Carlon -> Thomas & Betts.
  * A supplier seen against many unrelated brands is a distributor/co-op, not the
    manufacturer.  That distinction matters: the labelled rows show Part_Manuf
    'Appliance Dealers Cooperative (APPDE)' is a buying co-op while the true
    MANUFACTURER_NAME is the brand owner.

Everything produced here is tagged ``provenance='derived'`` and every edge keeps
its supporting row count, so the UI can show *why* a mapping exists.
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from backend.normalization.text import clean_value, normalize_whitespace
from backend.reference.schema import (
    PROVENANCE_DERIVED, BrandRecord, ManufacturerRecord,
)

# 'Freud Inc (2435)' / 'Boise Cascade Building Materials (BOICA)'
_CODE_SUFFIX_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<code>[A-Za-z0-9\-]{2,10})\)\s*$")

# Corporate suffixes stripped when building match keys.
_CORP_SUFFIXES = (
    "incorporated", "corporation", "corp", "company", "compagnie", "co", "inc",
    "llc", "ltd", "limited", "lp", "llp", "plc", "gmbh", "sa", "nv", "bv", "ag",
    "holdings", "group", "industries", "industrial", "international", "intl",
    "manufacturing", "mfg", "products", "prod", "brands", "usa", "us", "na",
    "north america", "enterprises", "solutions", "supply", "systems",
)

# A supplier carrying at least this many distinct brands is treated as a
# distributor rather than the manufacturer of record.
DISTRIBUTOR_BRAND_FANOUT = 3


def split_name_code(raw: str) -> Tuple[str, Optional[str]]:
    """'Freud Inc (2435)' -> ('Freud Inc', '2435'); plain names pass through."""
    s = normalize_whitespace(raw or "")
    if not s:
        return "", None
    m = _CODE_SUFFIX_RE.match(s)
    if m:
        return normalize_whitespace(m.group("name")), m.group("code")
    return s, None


def match_key(name: str) -> str:
    """
    Aggressive comparison key for entity resolution: lowercase, punctuation-free,
    corporate suffixes removed.  'FREUD, INC.', 'Freud Inc (2435)' and 'Freud'
    all collapse to 'freud'.
    """
    s = str(name or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)                    # drop embedded codes
    s = re.sub(r"[^a-z0-9]+", " ", s)
    tokens = [t for t in s.split() if t]
    while tokens and tokens[-1] in _CORP_SUFFIXES:    # strip trailing suffixes
        tokens.pop()
    if not tokens:                                    # name was only suffixes
        tokens = [t for t in s.split() if t]
    return " ".join(tokens)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")


@dataclass
class MinedMasters:
    manufacturers: List[ManufacturerRecord] = field(default_factory=list)
    brands: List[BrandRecord] = field(default_factory=list)
    # (brand, manufacturer) -> supporting row count
    edges: Dict[Tuple[str, str], int] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


def mine_masters(rows: Sequence[Dict[str, object]],
                 manufacturer_fields: Sequence[str] = ("Part_Manuf",),
                 brand_fields: Sequence[str] = ("E1_Brand", "DIB_Brand", "Unilog_Brand"),
                 ) -> MinedMasters:
    """
    Build manufacturer and brand masters plus their evidence-backed relationships
    from raw feed rows.
    """
    manuf_rows: Dict[str, Dict[str, object]] = {}
    brand_rows: Dict[str, Dict[str, object]] = {}
    edges: collections.Counter = collections.Counter()
    manuf_brands: Dict[str, set] = collections.defaultdict(set)

    for row in rows:
        manufs: List[Tuple[str, Optional[str]]] = []
        for f in manufacturer_fields:
            cleaned = clean_value(row.get(f))
            if cleaned:
                manufs.append(split_name_code(cleaned))

        brands: List[str] = []
        for f in brand_fields:
            cleaned = clean_value(row.get(f))
            if cleaned:
                brands.append(normalize_whitespace(cleaned))

        for name, code in manufs:
            if not name:
                continue
            key = match_key(name)
            slot = manuf_rows.setdefault(key, {"name": name, "code": code, "count": 0,
                                               "variants": set()})
            slot["count"] = int(slot["count"]) + 1
            slot["variants"].add(name)
            if code and not slot.get("code"):
                slot["code"] = code

        for b in brands:
            key = match_key(b)
            if not key:
                continue
            slot = brand_rows.setdefault(key, {"name": b, "count": 0, "variants": set()})
            slot["count"] = int(slot["count"]) + 1
            slot["variants"].add(b)
            # Prefer the most common surface form as canonical later.
            for name, _code in manufs:
                if name:
                    edges[(key, match_key(name))] += 1
                    manuf_brands[match_key(name)].add(key)

    # -- manufacturers ----------------------------------------------------
    manufacturers: List[ManufacturerRecord] = []
    for key, slot in manuf_rows.items():
        fanout = len(manuf_brands.get(key, ()))
        manufacturers.append(ManufacturerRecord(
            name=str(slot["name"]),
            code=slot.get("code"),                       # type: ignore[arg-type]
            aliases=tuple(sorted(slot["variants"])),     # type: ignore[index]
            provenance=PROVENANCE_DERIVED,
            brand_fanout=fanout,
            is_distributor=fanout >= DISTRIBUTOR_BRAND_FANOUT,
            source="mined:Part_Manuf",
        ))

    # -- brands -----------------------------------------------------------
    manuf_name_by_key = {k: str(v["name"]) for k, v in manuf_rows.items()}
    brands_out: List[BrandRecord] = []
    for key, slot in brand_rows.items():
        linked = sorted(
            ((mk, n) for (bk, mk), n in edges.items() if bk == key),
            key=lambda x: -x[1],
        )
        # Prefer non-distributor manufacturers as the brand owner.
        ranked: List[str] = []
        for mk, _n in linked:
            rec_fanout = len(manuf_brands.get(mk, ()))
            if rec_fanout < DISTRIBUTOR_BRAND_FANOUT:
                ranked.append(manuf_name_by_key.get(mk, mk))
        for mk, _n in linked:                            # distributors last
            nm = manuf_name_by_key.get(mk, mk)
            if nm not in ranked:
                ranked.append(nm)
        brands_out.append(BrandRecord(
            name=str(slot["name"]),
            aliases=tuple(sorted(slot["variants"])),     # type: ignore[index]
            manufacturers=tuple(ranked),
            provenance=PROVENANCE_DERIVED,
            source="mined:E1_Brand+DIB_Brand",
        ))

    notes = [
        f"mined {len(manufacturers)} manufacturers and {len(brands_out)} brands "
        f"from {len(rows)} rows",
        f"{sum(1 for m in manufacturers if m.is_distributor)} suppliers classified as "
        f"distributors (brand fan-out >= {DISTRIBUTOR_BRAND_FANOUT})",
    ]
    return MinedMasters(manufacturers=manufacturers, brands=brands_out,
                        edges=dict(edges), notes=notes)


# ---------------------------------------------------------------------------
# Brand mention mining from free-text descriptions
# ---------------------------------------------------------------------------
# Short forms suppliers use in Part_Desc.  Each entry is only accepted when the
# expansion is *also* evidenced elsewhere in the feed (see mine_description_aliases),
# so this is a candidate list, not an assertion.
_ABBREVIATION_CANDIDATES: Tuple[Tuple[str, str], ...] = (
    ("milw", "Milwaukee"),
    ("dewalt", "DEWALT"),
    ("makita", "Makita"),
    ("bosch", "Bosch"),
    ("festool", "Festool"),
    ("kichler", "Kichler"),
    ("satco", "Satco"),
    ("nuvo", "Nuvo"),
    ("philips", "Philips"),
    ("phillips", "Philips"),
    ("leviton", "Leviton"),
    ("southwire", "Southwire"),
    ("diablo", "Diablo"),
    ("freud", "Freud"),
    ("trex", "TREX"),
    ("azek", "AZEK"),
    ("timbertech", "TIMBERTECH"),
    ("velux", "Velux"),
    ("senco", "Senco"),
    ("paslode", "Paslode"),
    ("kreg", "Kreg"),
    ("mirka", "Mirka"),
    ("hunter", "Hunter"),
    ("dremel", "Dremel"),
    ("irwin", "Irwin"),
    ("vessel", "Vessel"),
    ("grizzly", "Grizzly"),
    ("oliver", "Oliver"),
    ("jet", "Jet"),
    ("whiteside", "Whiteside"),
    ("amana", "Amana"),
    ("cmt", "CMT"),
    ("prebena", "Prebena"),
    ("wera", "Wera"),
    ("stealthmounts", "StealthMounts"),
    ("feit", "Feit Electric"),
    ("lutron", "Lutron"),
    ("square d", "Square D"),
    ("carlon", "Carlon"),
    ("hardie", "JAMESHARDIE"),
    ("smartside", "LP SMARTSIDE"),
    ("provia", "PROVIA"),
    ("andersen", "ANDERSEN"),
    ("speed queen", "Speed Queen"),
    ("whirlpool", "Whirlpool"),
    ("frigidaire", "FRIGIDAIRE"),
    ("kitchen aid", "KitchenAid"),
    ("kitchenaid", "KitchenAid"),
    ("maytag", "Maytag"),
    ("cafe", "Cafe"),
    ("beko", "Beko"),
    ("element", "Element"),
    ("lithonia", "Lithonia Lighting"),
    ("streamlight", "Streamlight"),
    ("malco", "Malco"),
    ("nicholson", "Nicholson"),
    ("woodpeckers", "Woodpeckers"),
    ("marshalltown", "Marshalltown"),
    ("sawstop", "SawStop"),
    ("saw stop", "SawStop"),
    ("mafell", "Mafell"),
)


def mine_description_aliases(rows: Sequence[Dict[str, object]],
                             known_brands: Iterable[BrandRecord],
                             desc_field: str = "Part_Desc",
                             manuf_field: str = "Part_Manuf",
                             min_support: int = 1) -> Dict[str, str]:
    """
    Discover brand aliases that appear inside descriptions.

    A candidate abbreviation is accepted only when the description token co-occurs
    with a manufacturer or brand whose match key contains it -- i.e. the feed itself
    evidences the link.  'Milw ... Milwaukee Accessory (4031)' is accepted;
    an unsupported guess is not.

    Returns ``{lowercase_alias: canonical_brand_name}``.
    """
    brand_keys = {match_key(b.name): b.name for b in known_brands}
    support: collections.Counter = collections.Counter()
    resolved: Dict[str, str] = {}

    for row in rows:
        desc = (clean_value(row.get(desc_field)) or "").lower()
        if not desc:
            continue
        manuf_name, _ = split_name_code(clean_value(row.get(manuf_field)) or "")
        manuf_key = match_key(manuf_name)
        for alias, canonical in _ABBREVIATION_CANDIDATES:
            if alias not in desc:
                continue
            ck = match_key(canonical)
            # Evidence: the canonical brand is a known brand, OR the alias/canonical
            # is a prefix of the supplier name on the same row.
            evidenced = (
                ck in brand_keys
                or (manuf_key and (manuf_key.startswith(ck) or ck.startswith(manuf_key)))
                or (manuf_key and alias in manuf_key)
            )
            if evidenced:
                support[(alias, canonical)] += 1

    for (alias, canonical), n in support.items():
        if n >= min_support:
            # Prefer the canonical spelling already present in the brand master.
            resolved[alias] = brand_keys.get(match_key(canonical), canonical)
    return resolved
