import json
from pathlib import Path
NB = Path(__file__).resolve().parent.parent / "census_ocr_colab_qwen.ipynb"
nb = json.loads(NB.read_text())
nb["cells"][0]["source"] = [
    "# Census OCR — Colab + Qwen2.5-VL\n\n",
    "Run cells in order. **New? Use UPLOAD mode in Section 3** — no zip, no Drive.\n",
]
for i, c in enumerate(nb["cells"]):
    if "## 4. Project data" in "".join(c.get("source", [])):
        nb["cells"][i]["source"] = [
            "## 3. Get your files (pick ONE mode)\n\n",
            "### UPLOAD — easiest, start here\n",
            "1. Next cell: `DATA_MODE = \"upload\"`\n",
            "2. Run it → **Choose Files** → pick `sheet_01.jpg`\n",
            "3. Run again when prompted → pick `Bastrop County 1950 Clean.xlsx`\n",
            "   (on Mac: `Downloads/poc_textual/data/ground_truth/`)\n\n",
            "### DRIVE — for all 11 sheets\n",
            "1. On Mac: go to drive.google.com → upload your `poc_textual` folder\n",
            "2. Colab: `DATA_MODE = \"drive\"` → run cell → authorize\n",
            "3. Fix `PROJECT_DIR` if folder isn't at `My Drive/poc_textual`\n\n",
            "### DRIVE_ZIP — if you uploaded the zip to Drive\n",
            "`DATA_MODE = \"drive_zip\"` → upload zip to Drive (not Colab), edit path in cell\n",
        ]
        idx = i + 1
        break
upload_py = Path(__file__).resolve().parent / "_upload_cell.py"
upload_py.write_text(open(__file__).read())  # placeholder
