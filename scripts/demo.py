#!/usr/bin/env python
"""
End-to-end demo: takes one raw row all the way to the delivery format and prints
each pipeline stage with its evidence.

    python scripts/demo.py                       # default demo product
    python scripts/demo.py --mpn 49-94-0013      # any part number in the feed
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Generated copy legitimately contains characters such as (R) and (TM); a Windows
# cp1252 console would otherwise abort the demo mid-run.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

from backend.config import get_settings
from backend.pipeline.io import read_table
from backend.pipeline.orchestrator import Orchestrator

RULE = "=" * 78


def section(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/raw/sample_1000_input.csv")
    ap.add_argument("--mpn", default="49-94-0013")
    ap.add_argument("--context", type=int, default=1000,
                    help="corpus rows to load so mined masters are realistic")
    args = ap.parse_args()

    rows = read_table(Path(args.input))[: args.context]
    target = next((i for i, r in enumerate(rows)
                   if str(r.get("Mfg_Part_Num", "")).strip().lower()
                   == args.mpn.strip().lower()), None)
    if target is None:
        print(f"part number {args.mpn!r} not found in {args.input}", file=sys.stderr)
        return 2

    settings = get_settings()
    orch = Orchestrator(settings)
    result = orch.run(rows)
    orch.close()

    p = result.products[target]
    row = result.rows[target]

    section("1. RAW INPUT")
    for k, v in p.raw.items():
        print(f"  {k:16s} {v}")

    section("2. CLEANING  --  placeholder sentinels neutralised")
    for k, v in p.cleaned.items():
        mark = "  (placeholder removed)" if k in p.placeholders else ""
        print(f"  {k:16s} {v if v is not None else '<none>'}{mark}")

    section("3. ENTITY RESOLUTION")
    for label, fv in (("Supplier", p.supplier), ("Brand", p.brand),
                      ("Manufacturer", p.manufacturer)):
        print(f"  {label}")
        print(f"      value       {fv.value}")
        print(f"      method      {fv.method}   confidence {fv.confidence:.2f} ({fv.band.value})")
        print(f"      reasoning   {fv.transformation or fv.notes}")

    section("4. CLASSIFICATION")
    print(f"  classpath   {p.classpath.value}")
    print(f"  dept/class/fine  {p.dept.value} / {p.klass.value} / {p.fine.value}")
    print(f"  confidence  {p.classpath.confidence:.2f}   {p.classpath.transformation}")
    print("  candidates:")
    for c in p.classification_candidates[:3]:
        print(f"      {c['score']:5.1f}  {c['leaf_id']:26s} matched {c['matched']}")

    section("5. ATTRIBUTE EXTRACTION + NORMALIZATION")
    print(f"  {'label':26s} {'value':22s} {'uom':6s} {'method':16s} conf  LOV")
    for a in p.attributes:
        if not a.present:
            continue
        lov = {True: "PASS", False: "FAIL", None: "open"}[a.lov_compliant]
        print(f"  {a.label[:26]:26s} {str(a.value)[:22]:22s} {(a.uom or '-'):6s} "
              f"{a.method:16s} {a.confidence:.2f}  {lov}")
        if a.transformation:
            print(f"      -> {a.transformation}")
    empty = [a.label for a in p.attributes if not a.present]
    if empty:
        print(f"  empty slots still emitted (category template): {', '.join(empty)}")

    section("6. EVIDENCE")
    for a in p.populated_attributes()[:6]:
        for e in a.evidence:
            print(f"  {a.label:24s} <- {e.tier:24s} {e.snippet[:40]!r}")

    section("7. DESCRIPTION GENERATION")
    for name, fv in p.descriptions.items():
        if fv.present:
            print(f"  {name}  ({len(fv.value)} chars)")
            print(f"      {fv.value}")
        else:
            print(f"  {name}  <blank: {fv.notes[0] if fv.notes else 'no data'}>")

    section("8. VALIDATION")
    if not p.issues:
        print("  all checks passed")
    for i in p.issues:
        print(f"  [{i.severity}] {i.code}  {i.field}: {i.message}")

    section("9. CONFIDENCE")
    for k, v in p.confidence_breakdown.items():
        bar = "#" * int(v * 30)
        print(f"  {k:22s} {v:.3f}  {bar}")
    print(f"  {'PRODUCT':22s} {p.confidence:.3f}  ({p.band.value})")

    section("10. HUMAN REVIEW")
    print(f"  status: {p.status.value}")
    for f in p.review_flags:
        print(f"  - {f.reason}" + (f" [{f.field}]" if f.field else ""))
        if f.detail:
            print(f"      {f.detail}")
    if not p.review_flags:
        print("  no flags -- publishable")

    section("11. DELIVERY-FORMAT ROW  (populated columns of 252)")
    for k, v in row.items():
        if v:
            print(f"  {k:28s} {v[:88]}")

    print(f"\n{RULE}")
    print(f"corpus: {len(rows)} rows enriched in {result.stats.seconds:.1f}s "
          f"({result.stats.as_dict()['throughput_per_sec']}/s)")
    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
