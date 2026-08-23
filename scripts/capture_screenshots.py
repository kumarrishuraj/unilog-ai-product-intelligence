#!/usr/bin/env python
"""
Capture real screenshots of the running dashboard for the deck.

Starts the API (which serves the built SPA), enriches a real batch, then walks the
UI with a headless browser. Every image is a genuine render of this project's own
interface showing this project's own data -- nothing is mocked up.

    python scripts/capture_screenshots.py
    python scripts/capture_screenshots.py --rows 400 --outdir docs/screenshots
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                       # pragma: no cover
    print("playwright is required:  pip install playwright && "
          "python -m playwright install chromium", file=sys.stderr)
    raise SystemExit(2)

import urllib.request
import json as _json

VIEWPORT = {"width": 1600, "height": 1000}


def wait_for(url: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.6)
    return False


def start_job(base: str, rows: int) -> Optional[str]:
    req = urllib.request.Request(f"{base}/api/process/sample?limit={rows}", method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        job = _json.loads(r.read())
    job_id = job["id"]
    for _ in range(240):
        with urllib.request.urlopen(f"{base}/api/jobs/{job_id}", timeout=10) as r:
            state = _json.loads(r.read())
        if state["status"] in ("done", "error"):
            return job_id if state["status"] == "done" else None
        time.sleep(0.5)
    return None


def capture(base: str, outdir: Path) -> List[Tuple[str, Path]]:
    outdir.mkdir(parents=True, exist_ok=True)
    shots: List[Tuple[str, Path]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
        page.goto(base, wait_until="networkidle")
        page.wait_for_timeout(2500)

        def shot(name: str, caption: str) -> None:
            path = outdir / f"{name}.png"
            page.screenshot(path=str(path))
            shots.append((caption, path))
            print(f"  captured {path.name}")

        def nav(text: str) -> bool:
            try:
                page.get_by_role("button", name=text, exact=True).first.click(timeout=8000)
                page.wait_for_timeout(2200)
                return True
            except Exception:
                print(f"  (could not open {text!r})")
                return False

        # The app auto-selects the most recent finished job and lands on Dashboard.
        if nav("Dashboard"):
            shot("01_dashboard", "Dashboard — live quality gates and provenance")
        if nav("Products"):
            shot("02_products", "Products — searchable, per-row confidence bands")
            # open the first product row for the detail view
            try:
                page.locator("table tbody tr").first.click(timeout=8000)
                page.wait_for_timeout(2200)
                shot("03_product_detail", "Product detail — Raw vs Enriched vs Evidence")
                for tab, name, cap in (
                    ("Evidence graph", "04_evidence", "Evidence graph — every value traced"),
                    ("Confidence", "05_confidence", "Confidence breakdown and review flags"),
                ):
                    try:
                        page.get_by_role("button", name=tab).first.click(timeout=6000)
                        page.wait_for_timeout(1600)
                        shot(name, cap)
                    except Exception:
                        print(f"  (tab {tab!r} unavailable)")
            except Exception:
                print("  (no product rows to open)")
        if nav("Human review"):
            shot("06_review", "Human review — one row per specific, actionable issue")
        if nav("Upload"):
            shot("07_upload", "Upload — input profiled before anything is enriched")

        browser.close()
    return shots


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=300)
    ap.add_argument("--port", type=int, default=8077)
    ap.add_argument("--outdir", default="docs/screenshots")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    if not (root / "frontend" / "dist" / "index.html").exists():
        print("frontend/dist not found — run:  cd frontend && npm run build",
              file=sys.stderr)
        return 2

    base = f"http://127.0.0.1:{args.port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.api.main:app",
         "--host", "127.0.0.1", "--port", str(args.port), "--log-level", "warning"],
        cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_for(f"{base}/api/health"):
            print("server did not start", file=sys.stderr)
            return 1
        print(f"server up on {base}; enriching {args.rows} rows...")
        job_id = start_job(base, args.rows)
        if not job_id:
            print("enrichment job failed", file=sys.stderr)
            return 1
        print(f"job {job_id} complete; capturing UI...")
        shots = capture(base, root / args.outdir)
        print(f"\n{len(shots)} screenshot(s) written to {args.outdir}")
        for caption, path in shots:
            print(f"  {path.name:24s} {caption}")
        return 0 if shots else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
