# Census OCR — Proof of Concept

**Central Texas Spatial Demography Database Project**  
UT Austin IRP | Bastrop County Census Records (1850–1950)

---

## What This Is

A pipeline that reads scanned, handwritten U.S. Census records and automatically
extracts structured data (names, race, sex, age, etc.) using a modern vision LLM —
then measures how accurately it does so compared to human-cleaned records.

**The problem it solves:** Ancestry.com's existing OCR of these records is
error-prone on 19th–20th century handwriting. This project tests whether a
frontier vision model can do better.

**Model:** Google **Gemini 3.5 Flash** (`gemini-3.5-flash`) via the API. The local
workbench reads three row bands concurrently and retains Gemini 3.1 Pro as an
accuracy-escalation option.
No GPU and no model download required — extraction is a stateless API call.
Gemini currently leads independent handwritten-form benchmarks (lowest free-text
error rate), which is why it is the primary extractor here.

---

## How Extraction Works

Each census page is dense (~30 rows), so a single pass tends to drop rows. The
pipeline maximizes accuracy with:

1. **Full-page pass** — one schema-constrained call for a complete first read.
2. **Overlapping row-block crops** — the page is split into vertical bands with
   overlap, each read at higher effective resolution.
3. **Reconciliation** — results are merged by line number, preferring the
   clearest, most complete copy of each row and dropping duplicates.
4. **Targeted retry** — any still-missing line triggers one focused crop retry.
5. **Normalization** — per-decade race/gender/marital normalization and ditto
   propagation, producing JSON keyed by the ground-truth column names.

Output is schema-constrained via Pydantic (`response_schema`), so the model
returns valid JSON directly — no markdown fences or structural drift.

The same pipeline supports population schedules for 1850, 1860, 1870, 1880,
1900, 1910, 1920, 1930, 1940, and 1950. The 1850 and 1860 slave schedules are
also separate supported form types. Exact fields, row counts, column aliases,
comparison priorities, and crop geometry live in `src/census_schemas.py`.
1950 has calibrated crop geometry; other forms use a safe full-page pass until
representative scans are calibrated.

---

## Quick Start

```bash
git clone <repo>
cd poc_textual
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add your GEMINI_API_KEY

# Extract one sheet (positional args: IMAGE YEAR [OUTPUT] [MODEL] [nocrops])
python src/extract.py data/raw_images/1950_11-1/sheet_01.jpg 1950 \
  data/outputs/1950_11-1/sheet_01_extracted.json

# Slave schedules use the same command with an explicit form type
python src/extract.py path/to/1860_slave.jpg 1860 output.json --schedule slave

# Compare against ground truth.
# --sheet and --page are both required: a ground-truth workbook sheet
# concatenates many physical census pages (Line Number resets every ~30 rows),
# so you must specify which page matches your image. See CLAUDE.md finding #7.
python src/compare.py --extracted data/outputs/1950_11-1/sheet_01_extracted.json \
  --ground-truth "data/ground_truth/Bastrop County 1950 Clean.xlsx" \
  --sheet "Bastrop 11-1" --year 1950 --page 1

# Batch all 11 pages of ED 11-1 (extract + compare + summary)
python scripts/run_11_1_batch.py
```

Get a key at https://aistudio.google.com/apikey. Optionally set
`GEMINI_MODEL` in `.env` to pin a different Gemini model.

---

## Project Structure

```
poc_textual/
├── CLAUDE.md              ← Full project context for AI coding assistants
├── IMPLEMENTATION.md      ← Technical implementation details
├── PROMPTS.md             ← Extraction prompts for each census decade
├── README.md              ← This file
├── census_ocr_gemini.ipynb ← Runnable demo notebook (API, no GPU)
├── data/
│   ├── raw_images/        ← Census scan JPEGs
│   ├── ground_truth/      ← Human-cleaned XLSX (do not modify)
│   └── outputs/           ← Pipeline output JSON
├── src/
│   ├── models.py          ← Pydantic extraction contracts + GT column mapping
│   ├── preprocess.py      ← Full-page prep + overlapping row-block crops
│   ├── extract.py         ← Gemini client + full extraction pipeline
│   ├── reconcile.py       ← Merge passes, dedup, missing-line retry, diagnostics
│   ├── compare.py         ← Accuracy comparison (extracted vs. ground truth)
│   ├── validate.py        ← Rule-based field validation
│   └── utils.py           ← Normalization + ditto propagation
├── scripts/
│   └── run_11_1_batch.py  ← Batch extract + compare over ED 11-1
├── tests/                 ← pytest unit tests (mocked Gemini client)
├── prompts/               ← Decade-specific prompt text files
├── results/               ← Accuracy reports and summaries
└── requirements.txt
```

---

## Accuracy Metrics

The pipeline compares field-by-field against human-cleaned ground truth:

- **Race accuracy** — exact match required (most important for research)
- **Sex accuracy** — exact match required
- **Age accuracy** — ±1 year tolerance
- **Name accuracy** — fuzzy match (85–90% similarity threshold)
- **Birthplace accuracy** — fuzzy match
- **Row accuracy** — all compared fields match for a given person
- **Extraction coverage** — fraction of expected lines actually extracted
  (surfaced from the pipeline's reconciliation diagnostics)

---

## Testing

```bash
pip install pytest
python -m pytest tests/ -q
```

Tests cover structured-response parsing, crop boundaries/overlap, reconciliation
(dedup, missing lines, targeted retry, out-of-range handling), normalization,
and API error/retry behavior — all with a mocked Gemini client, so no API key or
network is needed.

---

## Human Review Workbench (v3)

The local workbench turns a Gemini draft into a field-level review queue. It
keeps the original scan, extraction run, confidence evidence, reviewer decision
history, and a versioned export separate from the supplied clean workbooks.

```bash
# One-time setup
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export GEMINI_API_KEY=...

# Terminal 1: browser application
PYTHONPATH=src uvicorn workbench.web:app --reload

# Terminal 2: durable local extraction worker
PYTHONPATH=src python -m workbench.worker
```

Open http://127.0.0.1:8000, select a supported year and schedule type, import
the image folder, confirm each page's
physical order/type, and queue a run. The worker processes only confirmed census
pages. Reviewers are required to enter a name or initials; every decision is
append-only. Exports are written under `data/workbench/exports/` as CSV, XLSX,
JSON, and an audit log.

Each run card updates live with queue position, completed/current page, extraction
pass, API attempt, retry count, elapsed step time, prepared review fields, and a
recent event log. Every completed page is committed as a checkpoint. If an API
call exhausts its three automatic retries, the run pauses with its exact error and
a resume action; if the worker restarts, interrupted runs are automatically
re-queued and continue from the first unfinished page.

For reproducible local startup, place `GEMINI_API_KEY` in `.env` and run:

```bash
docker compose up --build
```

Before high-confidence fields can be auto-accepted, calibrate an extraction on
held-out cleaned pages. The gate requires at least 30 observations and measured
precision of 98% or more for a field/confidence band; until then every field
remains reviewable.

```bash
PYTHONPATH=src python -m workbench.calibrate \
  --extracted data/outputs/1950_11-1/sheet_01_extracted.json \
  --ground-truth "data/ground_truth/Bastrop County 1950 Clean.xlsx" \
  --sheet "Bastrop 11-1" --page 1 --year 1950
```

---

## Contact

Damodar — SDE Intern, UT Austin IRP  
Supervisor: Mia (Textual Database / infra)  
Project Lead: Jaden
