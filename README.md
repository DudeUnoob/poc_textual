# Census OCR — Proof of Concept

**Central Texas Spatial Demography Database Project**  
UT Austin IRP | Bastrop County Census Records (1850–1950)

---

## What This Is

A pipeline that reads scanned, handwritten U.S. Census records and automatically
extracts structured data (names, race, sex, age, etc.) using modern vision AI —
then measures how accurately it does so compared to human-cleaned records.

**The problem it solves:** Ancestry.com's existing OCR (transcription) of these
records is error-prone because it was built on older technology and struggles with
19th–20th century handwriting. This project tests whether modern AI can do better.

---

## Quick Start

```bash
git clone <repo>
cd census-ocr-poc
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add your ANTHROPIC_API_KEY

# Run extraction on one sheet
python src/extract.py --image data/raw_images/your_scan.jpg --year 1950

# Compare against ground truth
# --sheet and --page are both required: a ground-truth workbook sheet
# concatenates many physical census pages (Line Number resets every ~30 rows),
# so you must specify which page matches your image. See CLAUDE.md finding #7.
python src/compare.py --extracted data/outputs/result.json \
  --ground-truth "data/ground_truth/Bastrop County 1950 Clean.xlsx" \
  --sheet "Bastrop 11-2A" --year 1950 --page 1
```

---

## Project Structure

```
census-ocr-poc/
├── CLAUDE.md           ← Full project context for AI coding assistants
├── IMPLEMENTATION.md   ← Technical implementation details and code
├── PROMPTS.md          ← Extraction prompts for each census decade (1850–1950)
├── README.md           ← This file
├── data/
│   ├── raw_images/     ← Census scan JPEGs from Ancestry
│   ├── ground_truth/   ← Human-cleaned CSVs from Box (do not modify)
│   ├── ancestry_ocr/   ← Ancestry's existing transcripts (baseline comparison)
│   └── outputs/        ← Pipeline output JSON files
├── src/
│   ├── extract.py      ← Main extraction pipeline (image → JSON)
│   ├── compare.py      ← Accuracy comparison (extracted vs. ground truth)
│   └── validate.py     ← Rule-based field validation
├── prompts/            ← Decade-specific prompt text files
├── results/            ← Accuracy reports and summaries
└── requirements.txt
```

---

## Census Years & Format Changes

| Year | Key Format Changes |
|------|-------------------|
| 1850 | Earliest. No relationship column. Slave Schedule is separate. |
| 1860 | Similar to 1850. No relationship column. |
| 1870 | Adds foreign-born parent columns. |
| 1880 | **Adds relationship column** (first time). |
| 1900 | Records birth month + year separately instead of age. |
| 1910 | Adds years-married column. |
| 1920 | Adds citizenship/language columns. |
| 1930 | **"Mexican" added as racial category** for first time. |
| 1940 | Adds employment status, income columns. |
| 1950 | Form P1 — two-section format with separate "sample lines" at bottom. |

---

## Accuracy Metrics

The pipeline compares field-by-field against human-cleaned ground truth:

- **Race accuracy** — exact match required (most important for research)
- **Sex accuracy** — exact match required
- **Age accuracy** — ±1 year tolerance
- **Name accuracy** — fuzzy match (85% similarity threshold)
- **Birthplace accuracy** — fuzzy match
- **Row accuracy** — all fields match for a given person

Aggregate counts are also validated against IPUMS benchmark data.

---

## Data Sources

- **Scans**: Downloaded from Ancestry.com (team account, see Jaden for credentials)
- **Ground truth**: Human-cleaned CSVs in Box (Bastrop Township fully cleaned — start here)
- **Tracking sheet**: Google Sheet shared by Jaden — green = cleaned, "X" = in Box
- **IPUMS benchmarks**: `CTX_Cleaning_Information_Sheet2_.csv` and `Sheet3_.csv`

---

## Contact

Damodar — SDE Intern, UT Austin IRP  
Supervisor: Mia (Textual Database / infra)  
Project Lead: Jaden  

---

## Google Colab + Qwen2.5-VL (open source)

For GPU-based testing with **Qwen2.5-VL-32B-Instruct** (4-bit) or 7B fallback:

```bash
python colab/make_bundle.py   # creates poc_textual_colab.zip (~18 MB)
```

1. Upload `census_ocr_colab_qwen.ipynb` to Colab (or use the zip)
2. Runtime → **GPU** (A100 for 32B; T4 auto-uses 7B)
3. Mount Drive → set `PROJECT_DIR` to your `poc_textual` folder
4. Run all cells — batch mode tests all ED 11-1 sheets

See **`colab/README.md`** for full instructions.
