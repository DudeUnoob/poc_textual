# Census OCR Extraction — Proof of Concept
## CLAUDE.md — Full Project Context

---

## What This Project Is

A **proof-of-concept automated transcription pipeline** for handwritten U.S. Census
records (1850–1950) from Bastrop County, Texas. Part of the Central Texas Spatial
Demography Database Project at UT Austin (IRP).

The core problem: Ancestry.com's OCR transcripts of handwritten census scans have
significant errors. The goal is to use a modern vision LLM to read the raw scan
images directly and produce structured records — then measure accuracy against
human-cleaned ground truth data.

---

## What You Are Building (POC Scope)

1. **Extractor**: Takes a census scan image → calls vision LLM → outputs JSON records
2. **Validator**: Checks extracted fields against known-valid values per decade
3. **Comparator**: Diffs JSON output against human-cleaned XLSX ground truth → accuracy metrics
4. **Report**: Summarizes field-level and row-level accuracy; identifies failure modes

---

## Ground Truth Data (Already Available)

You have 10 human-cleaned XLSX files covering every decade:

| File | Rows | Key sheets |
|------|------|------------|
| Bastrop County 1850 Clean.xlsx | 2,223 | Not Stated Census, Slave Schedule |
| Bastrop County 1860 Clean.xlsx | 1,000 | Bastrop, Precinct 1–3 |
| Bastrop County 1870 Clean.xlsx | 1,201 | Bastrop |
| Bastrop County 1880 Clean.xlsx | 295 | Alum Creek, Bastrop 0008, Cedar Creek |
| Bastrop County 1900 Clean.xlsx | 1,318 | Bastrop 0001, 0003, Smithville, Elgin |
| Bastrop County 1910 Clean.xlsx | 790 | Bastrop 1, Elgin 0009 |
| Bastrop County 1920 Clean.xlsx | 1,232 | Bastrop 0018, Smithville, Precinct 2 |
| Bastrop County 1930 Clean.xlsx | 1,256 | Bastrop 0001–0002, Smithville |
| Bastrop County 1940 Clean.xlsx | 1,985 | Bastrop 0001, Elgin, Smithville |
| Bastrop County 1950 Clean.xlsx | 692 | Bastrop 11-2A, 11-2B, 11-3, Smithville, Elgin |

Also available: `Raw-Clean Comparison Document.xlsx` — contains paired Raw (Ancestry OCR)
and Clean (human-reviewed) sheets for 1850, 1860, 1870, 1880, 1900. This is your
**baseline**: it shows exactly what errors Ancestry's OCR makes, which you must beat.

### Column Schema Per Decade (verified exact column names from files — high-priority subset; full headers run longer, especially 1940/1950)

**1850**: Line Number, Dwelling Number, Family Number, Given Name, Surname, Age,
Birth Date, Gender, Race, Occupation, Industry, Value of Real Estate, Birth Place,
Married within the Year, Attended School, "Cannot Read, Write" (exact column name,
comma included), Condition, Fugitive, Manumitted (last two only on Slave Schedule sheet)

**1860**: Dwelling Number, Family Number, Surname, Given Name, Age, Birth Year,
Gender, Race, Occupation, Real Estate Value, Personal Estate Value, Birth Place,
Married Within Year, Attended School, "Cannot Read, Write" (exact column name,
comma included), Disability Condition. **No Line Number column exists anywhere
in this workbook** (verified across all 17 sheets).

**1870**: Line Number, Dwelling Number, Family Number, Surname, Given Name, Age,
Birth Year, Gender, Race, Occupation, Real Estate Value, Personal Estate Value,
Birthplace, Father of Foreign Birth, Mother of Foreign Birth, Birth Month,
Marriage Month, Attended School, Cannot Read, Cannot Write (split into two
separate columns, unlike 1850/1860's combined field), Condition, Male Citizen
Over 21, Denied Voting Rights

**1880**: Line Number, Street, Dwelling Number, Family Number, Surname, Given Name,
Race, Gender, Age, Birth Month, Birth Year, Relation to Head of House, Marital Status,
Married During Census Year, Occupation, Months Not Employed, Sick, Blind,
Deaf and Dumb, Idiotic, Insane, "Maimed, Crippled, or Bedridden", Attended School,
Cannot Read, Cannot Write, Birthplace, Father's Birthplace, Mother's Birthplace

**1900**: Line Number, Family Number, Street, House Number, Dwelling Number,
Surname, Given Name, Relationship, Race, Gender, Birth Month, Birth Year, Age,
Marital Status, Years Married, Number of Children Born, Number of Children Living,
Birthplace, Father's Birthplace, Mother's Birthplace, Immigration Year, Years in US,
Naturalization, Occupation, Months Not Employed, Attended School, Can Read, Can Write,
Can Speak English, House Owned or Rented, House Owned Free or Mortgaged, Farm or House

**1910**: Line Number, Street Name, Surname, Given Name, Relationship, Gender, Race,
Age, Estimated Birth Year, Number of Years of Present Marriage, Birthplace,
Father's Birthplace, Mother's Birthplace

**1920**: Line Number, Street Number, House Number, Family Number, Surname,
Given Name, Relationship, Home Owned or Rented, Home Free or Mortgaged, **Sex**
(not "Gender" — this is the one decade that uses "Sex"), Race, Age, Estimated Birth Year,
Marital Status, Immigration Year, Naturalization Status, Naturalization Year,
Attended School, Able to read, Able to Write, Birthplace, Native Tongue,
Father's Birthplace, Father's Native Tongue, Mother's Birthplace, Mother's Native Tongue,
Able to Speak English, Occupation, Industry, Employment Field

**1930**: Line Number, Surname, Given Name, Relation to Head, Gender, Race, Age,
Estimated Birth Year, Age at First Marriage, Birthplace, Father's Birthplace,
Mother's Birthplace, Language Spoken

**1940**: Line Number, Street Name, Surname, Given Name, Relation to Head of House,
Gender, Race, Age, Estimated Birth Year, Birthplace, Father's Birth Place,
Mother's Birth Place, Native Language (plus 40+ supplemental columns)

**1950**: Line Number, Street Name, House Number, Apartment Number, Dwelling Number,
Farm, Acres, Questionnaire Number, Surname, Given Name, Relation to Head of House,
Race, Gender, Age, Birth Date, Marital Status, Birth Place, Citizenship,
Occupation Category, Worked Last Week, Seeking Work, Employment Status, Hours Worked,
Occupation, Industry, Worker Class (plus 46 supplemental columns)

---

## Known OCR Error Patterns (from Raw-Clean Comparison Document)

These are the actual errors Ancestry's OCR makes — your model must avoid these:

| Decade | Error Type | Example |
|--------|-----------|---------|
| 1850 | Industry field hallucinated | RAW: 'Not Specified Retail Trade' → CLEAN: None |
| 1860 | Name completely wrong | RAW: 'Tamer' → CLEAN: 'James' |
| 1860 | Surname misspelled | RAW: 'Anmis' → CLEAN: 'Anms' |
| 1870 | Wrong person entirely | RAW: Schefsky age 40 farmer → CLEAN: Schaefer age 54 |
| 1870 | Occupation garbled | RAW: 'Teacher & DU Sea' → CLEAN: 'Farmer' |
| 1870 | Birthplace wrong | RAW: 'Hannover' → CLEAN: 'Bavaria / Bayern' |
| 1880 | Relationship missing | RAW: None → CLEAN: 'Boarder' |
| 1900 | Entire rows missing/extra | Household entries present in one version but not the other |

**Critical insight**: The 1870 and 1880 errors are severe — wrong names, ages,
occupations, birthplaces. This is the strongest evidence a modern vision model
can dramatically improve on Ancestry's baseline.

---

## VERIFIED Data Quality Findings (checked against actual files, not assumed)

These were confirmed by directly inspecting every sheet in every cleaned XLSX file.
Do not skip this section — it changes what "100% accuracy" can realistically mean.

### 1. Race is a research-team controlled vocabulary, not raw form text
The census form itself only ever has a handwritten single letter or short code
(e.g. "W", "Neg", "Mul"). The ground truth XLSX files use a **richer, decade-inconsistent
vocabulary** that the research team applies during cleaning:

| Decade | Actual observed Race values in ground truth |
|--------|-----------------------------------------------|
| 1850 | White, Black, Mulatto |
| 1860 | White, Black, Mulatto, **Indian (Native American)** |
| 1870 | White, Black, Mulatto |
| 1880 | White, Black, Mulatto, **Filipino** |
| 1900 | White, Black, Mulatto, Chinese, **Mexican (Latino)** |
| 1910 | White, Black, Mulatto, **Octoroon**, Mexican (Latino), Other |
| 1920 | White, Black, Mulatto, Mexican (Latino) |
| 1930 | White, Black, **and** Negro (Black), Mulatto, Mexican (Latino) |
| 1940 | White, Negro (Black) |
| 1950 | White, Negro (Black), Chinese, **W0 / WO** (see below) |

Implication: your extraction prompt should ask the model to transcribe the raw
handwritten code/word **exactly as written**. A separate normalization step then
maps that raw value to the research team's controlled vocabulary for the correct
decade. Do not expect the vision model to output "Mexican (Latino)" directly —
it will output "White" or "W" because that is what the enumerator actually
wrote. **Ask Jaden if a formal race-coding codebook exists** — if so, use it
verbatim in the normalization step instead of guessing.

Note also: 1930 ground truth contains BOTH "Black" and "Negro (Black)" as
separate values — this looks like inconsistent coding between cleaners across
time, not a real distinction. Confirm which is canonical before treating
mismatches between them as extraction errors.

### 2. "W0" / "WO" in the 1950 sheet is very likely NOT recoverable from the image
Every person coded "W0" or "WO" in the 1950 Race column has a Spanish-language
surname (Flores, Fuentes, Rosas, Chevarria, Santaanna, Garza, Santos, Villarial,
Hernandez, etc.). The 1950 census form did not have a "Mexican" racial category
(it was removed as a census race option after 1930) — these individuals would
have literally been marked "W" on the physical form. "W0"/"WO" appears to be a
**researcher-applied ethnic sub-code based on surname analysis**, not something
visible in the handwriting.

**Practical impact**: your image-only extraction pipeline has a hard accuracy
ceiling on this field for 1950 — it cannot output "W0" from the image alone.
Treat any mismatch here as an expected limitation, not a bug. If ethnic
sub-coding matters to the research team's downstream use, that logic needs to
be a separate post-processing step (e.g. a Spanish-surname list), not part of
OCR/vision extraction.

### 3. Ground truth itself has known typos — don't blame your pipeline for these
Scanning every 1950 sheet turned up stray values in the Race column that are
clearly data-entry mistakes in the "clean" file, not real categories:
`'Whiite'` (typo for White, in "Bastrop all Manipulated" and "Bastrop 11-2B"),
`'White0'` (Smithville 11-8), `'Wo'` (Smithville 11-9), and non-race junk values
`'1'`, `'72'`, `'S'` (Bastrop all Manipulated, Bastrop 11-1, Smithville 11-7,
Smithville 11-11 respectively — likely misaligned cells from spreadsheet editing).

**Bastrop 11-3, Bastrop 11-2A, Smithville 11-10, Elgin 11-20, and Elgin 11-21
have NO such stray values** — these are the cleanest sheets to validate against.

### 4. Confirmed: your uploaded sample images match "Bastrop 11-2A", physical page 1
The **first physical page** (rows 0–27, before Line Number resets back to 1)
within `Bastrop County 1950 Clean.xlsx`, sheet `Bastrop 11-2A`, reads
`Surname="Lewis", Given Name="Jasper H"` at Line 1 — exactly matching Line 1 of
the second uploaded image. **This is your confirmed starting point**, but note
it is page 1 of 24 concatenated physical pages within that sheet (see finding
#7 below) — you must extract page 1 specifically, not just "the sheet."

### 5. Column naming is NOT consistent across decades — do not hardcode one schema
Confirmed exact differences that will silently break a naive comparison script:

| Field | Decades using this exact name |
|-------|-------------------------------|
| `"Birth Place"` (with space) | 1850, 1860, 1950 |
| `"Birthplace"` (no space) | 1870, 1880, 1900, 1910, 1920, 1930, 1940 |
| `"Father's Birthplace"` | 1880, 1900, 1910, 1920, 1930 |
| `"Father's Birth Place"` | 1940 |
| `"Father Birth Place"` (no apostrophe) | 1950 |
| `"Gender"` | Most decades |
| `"Sex"` | 1920 only |
| `"Never Married (Single)"` for single people | 1950 only |
| `"Single"` for single people | 1880–1940 |

`compare.py` must resolve field names dynamically per decade (it does — see
IMPLEMENTATION.md) rather than assuming one fixed column name works everywhere.

### 6. No "Line Number" column exists anywhere in the 1860 workbook
All 17 sheets (Bastrop + 13 Precincts + 3 Slave Schedule sheets) lack a Line
Number field entirely. Row alignment for 1860 must fall back to household
order + Dwelling/Family Number, not line-number matching. This does not block
your POC (start with 1950), but will matter when you scale to earlier decades.

### 7. CRITICAL: each ground-truth sheet contains MULTIPLE physical census pages concatenated — Line Number alone is not a unique key
This was caught by actually running the comparison pipeline end-to-end, not by
inspecting headers. `Bastrop 11-2A` (679 rows) is not one physical scanned
sheet — it's **24 physical census sheets concatenated together**, because
each physical 1950 form only has ~28-30 lines, and Line Number **resets to 1
at the start of every new physical sheet**. The same pattern was confirmed in
other decades (1900: 24 resets in one district sheet, 1910: 19, 1920: 21) — a
combined district workbook sheet is always a stack of physical pages, and Line
Number is only unique *within* a page, never across the whole sheet.

**Consequence if you don't handle this**: naively building `{line_number: row}`
from the ground truth (as an early draft of this pipeline did) silently keeps
only the *last* row for each line number, 1–30, and drops all the others. Every
comparison against page 1 of a scan would then be checked against garbage data
from a random later page in the same district — this is exactly what happened
in initial testing (compared image 2, line 1 = "Lewis Jasper H" against ground
truth line 1 and got "Craney, Willie B" back, a person from a completely
different physical sheet 23 pages later).

**Fix**: detect page boundaries by watching for Line Number resetting to 1
(or dropping) as you scan down the sheet in original row order, split into
page-blocks, and compare against the correct block — the one whose physical
sheet number matches your source image (visible in the top-right "Sheet
Number" field on the scan itself, e.g. Image 1's handwritten "Sheet 3 Line
36" annotation, or the "SHEET NUMBER" printed field on Form P1). `compare.py`
and the Colab notebook below both implement this correctly.

---

## Architecture: Optimal & Scalable Approach

### Why NOT a custom trained deep learning model (for POC)
Training a custom HTR model requires annotated image+text pairs at scale.
We don't have that. It would take months and significant compute.

### Why vision LLM IS the right POC approach
Claude claude-sonnet-4-6 and GPT-4o can read handwriting directly from images with
zero training. They understand historical context, census form layouts, and
handle format variation across decades via prompting. Cost is ~$0.05–0.10/image.

### Scalable Production Architecture (build toward this)

```
Image Input
    │
    ▼
[Pre-processor]          ← Deskew, enhance contrast, crop to data area
    │                       (OpenCV / Pillow) — avoids wasting tokens on margins
    ▼
[Vision LLM Extractor]   ← Claude claude-sonnet-4-6 with decade-specific prompt
    │                       Returns structured JSON
    ▼
[Rule Validator]         ← Check race codes, sex values, age ranges per decade
    │                       Flag illegible fields, catch impossible values
    ▼
[Fuzzy Post-processor]   ← Normalize name spellings, standardize birthplace strings
    │                       Propagate ditto marks (— = same as above)
    ▼
[Structured Output]      ← JSON / CSV matching ground truth column schema
    │
    ▼
[Comparator]             ← Diff against ground truth XLSX → accuracy metrics
```

### Why this is optimal and scalable
- **No GPU needed**: runs via API, stateless, parallelizable
- **Decade-aware**: single prompt parameter change handles all format variations
- **Pre-processing**: crops margins before sending to LLM → fewer tokens → lower cost
- **Validation layer**: catches errors before they enter the database
- **Batch API**: Anthropic's batch API can process 1000s of images at 50% cost reduction

---

## Accuracy Strategy

### Priority order for accuracy (per research team needs)
1. **Race** — exact match required, most important for demographic research
2. **Sex/Gender** — exact match required
3. **Surname** — fuzzy match (85% threshold), critical for linking records
4. **Given Name** — fuzzy match
5. **Age** — ±1 year tolerance
6. **Relationship** — fuzzy match
7. **Birthplace** — fuzzy match (state name variations)
8. **Occupation** — fuzzy match (lowest priority)

### Handling the hardest cases
- **Ditto marks**: "——" or `"` means "same as row above" — propagate surname/birthplace
- **Vacant/No one at home**: include as null-field records (important for dwelling counts)
- **Slave schedules (1850)**: separate schema, different fields entirely
- **Mexican category (1930+)**: appears for first time — ensure prompt knows this
- **Mulatto category (pre-1930)**: disappears after 1920 — validate per decade

---

## File & Directory Structure

```
census-ocr-poc/
├── CLAUDE.md                    ← This file
├── README.md
├── PROMPTS.md                   ← Decade-specific LLM prompts
├── IMPLEMENTATION.md            ← All code
├── data/
│   ├── ground_truth/            ← The 10 cleaned XLSX files (from Cleaned_Data.zip)
│   │   ├── Bastrop County 1850 Clean.xlsx
│   │   ├── ...
│   │   └── Raw-Clean Comparison Document.xlsx   ← Baseline error analysis
│   ├── raw_images/              ← Census scan JPEGs from Ancestry
│   │   └── [ED]_[year]_[sheet].jpg
│   └── outputs/                 ← Pipeline JSON outputs
├── src/
│   ├── extract.py               ← Image → JSON via vision LLM
│   ├── compare.py               ← JSON vs XLSX ground truth → accuracy
│   ├── validate.py              ← Rule-based field validation
│   ├── preprocess.py            ← Image enhancement before LLM call
│   └── utils.py                 ← Schema loading, ditto propagation, normalization
├── prompts/
│   ├── 1850.txt
│   ├── 1860.txt  ...  1950.txt
│   └── system.txt               ← Shared system prompt
├── schemas/
│   └── columns_by_decade.json   ← Exact column names per year (from ground truth)
├── results/
│   └── accuracy_report.md
└── requirements.txt
```

---

## Tech Stack

```
anthropic>=0.25          # Vision LLM extraction (primary)
openai>=1.0              # GPT-4o fallback option
openpyxl                 # Read ground truth XLSX files
pandas                   # Data manipulation and comparison
opencv-python            # Image preprocessing (deskew, contrast)
Pillow                   # Image loading
fuzzywuzzy               # Fuzzy string matching for names
python-levenshtein       # Speed up fuzzywuzzy
python-dotenv            # API key management
tqdm                     # Progress bars for batch runs
jupyter                  # Demo notebook
```

---

## Immediate Steps (This Week)

1. [ ] Set up repo, install requirements
2. [ ] Run `python src/ingest_ground_truth.py` to index all cleaned XLSX files
3. [ ] Download 3–5 scan images from Ancestry (E.D. 11-2A, 1950, Bastrop Township)
4. [ ] Run `python src/extract.py` on one image
5. [ ] Run `python src/compare.py` → get first accuracy number
6. [ ] Check `Raw-Clean Comparison Document.xlsx` for baseline Ancestry accuracy
7. [ ] Document delta: your accuracy vs Ancestry baseline

---

## Team

| Person | Role | Contact |
|--------|------|---------|
| Jaden | Project lead, cleaning coordinator | Primary contact |
| Asher | Research team | — |
| Mia | Textual DB / infra supervisor | Your direct supervisor |
| Damodar | You — OCR pipeline + AWS DB infra | — |

**Rule**: Only test on already-cleaned places. Check with Jaden before using
any district as a test set. Bastrop Township (all decades) is confirmed safe.
