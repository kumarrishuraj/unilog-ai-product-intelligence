#!/usr/bin/env python
"""
Run the enrichment pipeline over an input file and export the delivery format.

    python scripts/run_pipeline.py --input data/raw/sample_1000_input.csv
    python scripts/run_pipeline.py --input feed.xlsx --limit 200 --format xlsx
    python scripts/run_pipeline.py --input feed.csv --disable manufacturer_research
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import STAGE_ORDER, get_settings
from backend.pipeline.io import (
    profile_input, read_table, write_csv, write_review_csv, write_xlsx,
)
from backend.pipeline.orchestrator import Orchestrator


def main() -> int:
    ap = argparse.ArgumentParser(description="Unilog product intelligence pipeline")
    ap.add_argument("--input", required=True, help="input CSV or XLSX")
    ap.add_argument("--sheet", default=None, help="sheet name for XLSX input")
    ap.add_argument("--outdir", default="data/processed", help="output directory")
    ap.add_argument("--limit", type=int, default=0, help="process only the first N rows")
    ap.add_argument("--format", choices=["csv", "xlsx", "both"], default="csv")
    ap.add_argument("--disable", action="append", default=[],
                    choices=STAGE_ORDER, help="disable a pipeline stage (repeatable)")
    ap.add_argument("--profile-only", action="store_true",
                    help="report the input profile and exit")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    settings = get_settings()
    for stage in args.disable:
        settings.enable(stage, False)

    src = Path(args.input)
    if not src.exists():
        print(f"input not found: {src}", file=sys.stderr)
        return 2

    rows = read_table(src, args.sheet)
    if args.limit:
        rows = rows[: args.limit]

    profile = profile_input(rows, str(src))
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "input_profile.json").write_text(
        json.dumps(profile.as_dict(), indent=2), encoding="utf-8")

    if not args.quiet:
        print(f"input      : {src.name}")
        print(f"rows       : {profile.row_count}   columns: {len(profile.columns)}")
        for w in profile.warnings:
            print(f"  warning  : {w}")

    if args.profile_only:
        return 0

    def progress(done: int, total: int, phase: str) -> None:
        if args.quiet:
            return
        pct = done / total if total else 1.0
        bar = "#" * int(pct * 28)
        print(f"\r  {phase:18s} [{bar:<28}] {done}/{total}", end="", flush=True)
        if done == total:
            print()

    orch = Orchestrator(settings)
    started = time.time()
    result = orch.run(rows, progress=progress)
    orch.close()

    stem = src.stem
    written = []
    if args.format in ("csv", "both"):
        written.append(write_csv(result.rows, result.schema,
                                 outdir / f"{stem}_delivery_format.csv"))
    if args.format in ("xlsx", "both"):
        written.append(write_xlsx(result.rows, result.schema,
                                  outdir / f"{stem}_delivery_format.xlsx"))
    review_path = write_review_csv(result.review_queue, outdir / f"{stem}_review_queue.csv")

    report = {
        "input": str(src),
        "stats": result.stats.as_dict(),
        "schema": result.schema.summary(),
        "reference": result.reference_summary,
        "settings": settings.as_dict(),
    }
    (outdir / f"{stem}_run_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    (outdir / f"{stem}_products.json").write_text(
        json.dumps([p.as_dict(include_raw=False) for p in result.products], indent=2),
        encoding="utf-8")

    if not args.quiet:
        s = result.stats
        print()
        print(f"processed  : {s.total} rows in {time.time() - started:.1f}s "
              f"({s.as_dict()['throughput_per_sec']}/s)")
        print(f"  SUCCESS      {s.success}")
        print(f"  PARTIAL      {s.partial}")
        print(f"  NEEDS_REVIEW {s.needs_review}")
        print(f"  FAILED       {s.failed}")
        print(f"columns    : {len(result.schema)} (template: {result.schema.source})")
        for p in written:
            print(f"written    : {p}")
        print(f"review     : {review_path} ({len(result.review_queue)} products)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
