#!/usr/bin/env python3
"""Batch extract + compare for Bastrop ED 11-1 (1950). See docstring in repo README."""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
SRC, IMAGE_DIR = ROOT / "src", ROOT / "data/raw_images/1950_11-1"
OUTPUT_DIR, RESULTS_DIR = ROOT / "data/outputs/1950_11-1", ROOT / "results/1950_11-1"
GT, SHEET, YEAR = ROOT / "data/ground_truth/Bastrop County 1950 Clean.xlsx", "Bastrop 11-1", 1950

def run(cmd):
    print("$", " ".join(cmd)); subprocess.run(cmd, cwd=ROOT, check=True)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sheet", type=int)
    p.add_argument("--extract-only", action="store_true")
    p.add_argument("--compare-only", action="store_true")
    args = p.parse_args()
    if not (ROOT/".env").exists() and not args.compare_only:
        print("ERROR: cp .env.example .env and add ANTHROPIC_API_KEY"); sys.exit(1)
    sheets = [args.sheet] if args.sheet else list(range(1, 12))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True); RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summary = []
    for page in sheets:
        img = IMAGE_DIR / f"sheet_{page:02d}.jpg"
        out_json = OUTPUT_DIR / f"sheet_{page:02d}_extracted.json"
        result_json = RESULTS_DIR / f"sheet_{page:02d}_comparison.json"
        if not img.exists(): print(f"SKIP page {page}: missing {img.name}"); continue
        if not args.compare_only:
            run([sys.executable, str(SRC/"extract.py"), "--image", str(img), "--year", str(YEAR), "--output", str(out_json)])
        if args.extract_only: continue
        if not out_json.exists(): continue
        run([sys.executable, str(SRC/"compare.py"), "--extracted", str(out_json), "--ground-truth", str(GT), "--sheet", SHEET, "--year", str(YEAR), "--page", str(page), "--save", str(result_json)])
        m = json.load(open(result_json))["metrics"]
        summary.append({"page": page, "rows": m["rows_compared"], "row_accuracy": m["row_accuracy"], "field_accuracy": m["overall_field_accuracy"]})
    if summary:
        print("\nBATCH SUMMARY")
        for r in summary: print(f"  page {r['page']:2d}: row={r['row_accuracy']:.1%} field={r['field_accuracy']:.1%}")
        avg = sum(r["row_accuracy"] for r in summary)/len(summary)
        print(f"  avg row accuracy: {avg:.1%}")
        (RESULTS_DIR/"batch_summary.json").write_text(json.dumps({"pages": summary}, indent=2))
if __name__ == "__main__": main()
