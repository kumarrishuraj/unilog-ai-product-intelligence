#!/usr/bin/env python
"""Render the deck to PNGs via PowerPoint COM so the slides can be eyeballed."""
from __future__ import annotations
import argparse, sys
from pathlib import Path

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("deck", nargs="?", default="UniHack_Unilog_Product_Intelligence.pptx")
    ap.add_argument("--outdir", default="docs/deck_render")
    args = ap.parse_args()

    import win32com.client as win32
    deck = Path(args.deck).resolve()
    out = Path(args.outdir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    app = win32.Dispatch("PowerPoint.Application")
    pres = None
    try:
        pres = app.Presentations.Open(str(deck), WithWindow=False)
        for i, slide in enumerate(pres.Slides, start=1):
            slide.Export(str(out / f"slide_{i:02d}.png"), "PNG", 1600, 900)
        print(f"rendered {pres.Slides.Count} slides to {out}")
    finally:
        if pres is not None:
            pres.Close()
        app.Quit()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
