#!/usr/bin/env python3
import zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "poc_textual_colab.zip"
paths = [
    "CLAUDE.md", "census_ocr_ide_colab.ipynb", "src", "schemas", "colab/ide_setup.py",
    "prompts", "data/ground_truth/Bastrop County 1950 Clean.xlsx",
    "data/raw_images/1950_11-1", "census_ocr_colab_qwen.ipynb", "colab/README.md",
]
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
    for p in paths:
        fp = ROOT / p
        if fp.is_dir():
            for f in fp.rglob("*"):
                if f.is_file():
                    zf.write(f, Path("poc_textual") / f.relative_to(ROOT))
        elif fp.is_file():
            zf.write(fp, Path("poc_textual") / fp.relative_to(ROOT))
print(f"Created {OUT} ({OUT.stat().st_size/1e6:.1f} MB)")
