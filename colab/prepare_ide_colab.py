#!/usr/bin/env python3
"""One-command setup for census_ocr_ide_colab.ipynb (run locally before Colab)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NB = ROOT / "census_ocr_ide_colab.ipynb"
ZIP_URL = "https://huggingface.co/datasets/damkamanii/poc-textual-colab/resolve/main/poc_textual_colab.zip"


def load_token() -> str:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    token = os.environ.get("HF_TOKEN", "")
    if not token:
        raise SystemExit("HF_TOKEN missing in .env — copy from .env.example and paste your token.")
    return token


def rebuild_zip() -> None:
    subprocess.check_call([sys.executable, str(ROOT / "colab" / "make_bundle.py")])


def patch_notebook(token: str) -> None:
    nb = json.loads(NB.read_text())
    for cell in nb["cells"]:
        src = "".join(cell.get("source", []))
        if "bootstrap_project()" not in src or "REMOTE_ZIP_URL" not in src:
            continue
        src = re.sub(
            r'HF_TOKEN = os\.environ\.get\("HF_TOKEN"\) or "[^"]*"',
            f'HF_TOKEN = os.environ.get("HF_TOKEN") or "{token}"',
            src,
        )
        src = re.sub(
            r'REMOTE_ZIP_URL = "[^"]*"',
            f'REMOTE_ZIP_URL = "{ZIP_URL}"',
            src,
        )
        cell["source"] = [line + "\n" for line in src.split("\n")[:-1]]
        if src.split("\n")[-1]:
            cell["source"].append(src.split("\n")[-1] + "\n")
        cell["outputs"] = []
        cell["execution_count"] = None
        break
    NB.write_text(json.dumps(nb, indent=1))


def main() -> None:
    rebuild_zip()
    token = load_token()
    patch_notebook(token)
    print("Ready:", NB.name)
    print("  Zip URL:", ZIP_URL)
    print("  HF token injected from .env")
    print("\nNext: open census_ocr_ide_colab.ipynb in Colab extension → GPU runtime → Run All")


if __name__ == "__main__":
    main()
