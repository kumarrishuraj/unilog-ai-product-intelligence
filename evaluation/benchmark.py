#!/usr/bin/env python
"""
Benchmark the pipeline against a labelled delivery-format file.

    python evaluation/benchmark.py \
        --labelled data/raw/delivery_format_labelled.csv \
        --input    data/raw/sample_1000_input.csv

The labelled file supplies both the gold values *and* the input columns
(``Mfg_Part_Num``, ``Part_Desc``, ``E1_Brand``, ``Unilog_Brand``, ``DIB_Brand``,
``Part_Manuf``), so the benchmark can run standalone: it re-derives the inputs from
the gold rows, enriches them, and compares.  Passing ``--input`` additionally
supplies the wider corpus, which matters because the part-number prefix learner and
the mined manufacturer master both improve with corpus size.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import get_settings
from backend.pipeline.io import read_table
from backend.pipeline.orchestrator import INPUT_FIELDS, Orchestrator
from evaluation.metrics import (
    EvaluationReport, align_by_key, compare_rows, compute_quality_metrics,
)


def inputs_from_gold(gold_rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Reconstruct the raw feed rows that the labelled file was produced from."""
    out = []
    for g in gold_rows:
        out.append({f: (g.get(f) or "") for f in INPUT_FIELDS})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluate against labelled data")
    ap.add_argument("--labelled", required=True, help="labelled delivery-format CSV/XLSX")
    ap.add_argument("--input", default=None,
                    help="wider corpus to enrich alongside (improves corpus learning)")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--outdir", default="data/processed")
    ap.add_argument("--json", action="store_true", help="print the full JSON report")
    args = ap.parse_args()

    gold_path = Path(args.labelled)
    if not gold_path.exists():
        print(f"labelled file not found: {gold_path}", file=sys.stderr)
        return 2

    gold_rows = read_table(gold_path, args.sheet)
    if not gold_rows:
        print("labelled file contains no data rows", file=sys.stderr)
        return 2

    eval_inputs = inputs_from_gold(gold_rows)
    corpus = list(eval_inputs)
    if args.input:
        extra = read_table(Path(args.input))
        # De-duplicate on part number so gold rows are not processed twice.
        seen = {r["Mfg_Part_Num"].strip().lower() for r in eval_inputs}
        corpus += [r for r in extra
                   if str(r.get("Mfg_Part_Num", "")).strip().lower() not in seen]

    settings = get_settings()
    settings.delivery_template = settings.delivery_template or gold_path

    orch = Orchestrator(settings)
    result = orch.run(corpus)
    orch.close()

    pred_rows, aligned_gold, missing = align_by_key(result.rows, gold_rows)
    headers = result.schema.headers

    metrics = compare_rows(pred_rows, aligned_gold, headers)
    quality = compute_quality_metrics(result.products, orch.description_spec.limits)

    report = EvaluationReport(
        rows_evaluated=len(pred_rows),
        fields=[m for m in metrics if m.comparable > 0],
        char_compliance=quality["char_compliance"],
        lov_compliance=quality["lov_compliance"],
        evidence_coverage=quality["evidence_coverage"],
        review_rate=quality["review_rate"],
        validation_pass_rate=quality["validation_pass_rate"],
        mean_confidence=quality["mean_confidence"],
        schema_conformant=all(len(r) == len(headers) for r in result.rows),
    )
    if missing:
        report.notes.append(f"{len(missing)} labelled row(s) had no matching prediction")
    report.notes.append(f"corpus size used for learning: {len(corpus)} rows")
    report.notes.append(
        "accuracy is computed only over fields the labelled data actually populates")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "evaluation_report.json").write_text(
        json.dumps(report.as_dict(), indent=2), encoding="utf-8")

    _print_report(report)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    return 0


def _print_report(r: EvaluationReport) -> None:
    print("=" * 74)
    print(f"EVALUATION  --  {r.rows_evaluated} labelled row(s)")
    print("=" * 74)
    print(f"  micro field accuracy   {r.micro_accuracy:>7.1%}")
    print(f"  macro field accuracy   {r.macro_accuracy:>7.1%}")
    print(f"  semantic similarity    {r.mean_semantic:>7.1%}")
    print(f"  LOV compliance         {r.lov_compliance:>7.1%}")
    print(f"  character compliance   {r.char_compliance:>7.1%}")
    print(f"  validation pass rate   {r.validation_pass_rate:>7.1%}")
    print(f"  evidence coverage      {r.evidence_coverage:>7.1%}")
    print(f"  human review rate      {r.review_rate:>7.1%}")
    print(f"  mean confidence        {r.mean_confidence:>7.1%}")
    print(f"  schema conformant      {str(r.schema_conformant):>7}")
    print(f"  OVERALL QUALITY        {r.overall_quality():>7.1%}")
    print()
    print(f"  {'field':32s} {'kind':9s} {'cmp':>4s} {'acc':>7s} {'sim':>7s} {'cov':>7s}")
    print("  " + "-" * 70)
    for m in sorted(r.fields, key=lambda x: (x.kind, -x.comparable)):
        print(f"  {m.field[:32]:32s} {m.kind:9s} {m.comparable:4d} "
              f"{m.accuracy:>7.1%} {m.mean_similarity:>7.1%} {m.coverage:>7.1%}")
    if r.notes:
        print()
        for n in r.notes:
            print(f"  note: {n}")


if __name__ == "__main__":
    raise SystemExit(main())
