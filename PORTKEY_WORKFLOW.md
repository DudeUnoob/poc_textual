# Census tests through Portkey

The local default is Claude Opus 5.5, routed through the supplied Anthropic catalog.
GPT-6 Astra is the cross-provider comparison. A catalog listing establishes access,
not handwriting accuracy: choose using matched-page results, including race and
sex accuracy and row omissions, rather than general model rankings.

Set these values in local `.env` (never share this file):

```
CENSUS_PROVIDER=portkey
PORTKEY_API_KEY=your_own_key
PORTKEY_MODEL=@your_anthropic_catalog/claude-opus-5-5
```

The existing CLI and workbench route `@catalog/model` IDs through Portkey. Legacy
Gemini records remain readable; executing them requires explicit legacy mode.
Analysts choose Anthropic, OpenAI or both for independent reads. The comparison
page shows the latest drafts for each configured model, with provenance and
confidence; agreeing models are not treated as ground truth. Portkey currently uses a high-resolution full-page
pass, not the Gemini crop/retry strategy; model comparisons use identical input
preparation. The decade-specific extraction fields are separate from the old
1950-only response schema. The workbench's existing review UI remains primarily
1950 oriented; use the benchmark workflow below for cross-decade evaluations.

## Teammate workflow

In Codex, install the portable `skills/census-ground-test` folder under your own
`~/.codex/skills/`, then say:

> Use $census-ground-test. Test 1850, 1880, and 1910 from these scan folders.
> Start with one correctly matched page per year and compare the two models.

Give the folder paths; Codex handles the commands. Use your own Portkey key and
matching catalog IDs. Share this repository and skill, not `.env` or credentials.

For a manual run from the project folder (Windows PowerShell):

```
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe scripts/census_benchmark.py prepare --year 1880 --images data/raw_images/1880_008 --sheet "Bastrop 0008" --output configs/my-test.json
```

The plan and anchors CSV list images and ground-truth page starts. Select the
pages you want by removing other jobs. Verify at least two or three people and
the form page identifier, then enter `gt_page`, `alignment_verified: true`, and
`alignment_evidence`. Viewer numbers are not ground-truth block numbers.
Never send the ground-truth cell values to the vision model.

Some decades continue line numbering across two scan sides. For example, the
1910 Bastrop 1 tab starts 1-100 and later repeats 51-100. A reset-to-1 block can
therefore contain multiple images. When that occurs, inspect the rows and set
`gt_row_start` and `gt_row_count` explicitly (one-based data rows, excluding the
header). The pilot uses rows 1-50 for the first 1910 image. This override is saved
in the plan and must be verified against the people on the scan. Invalid or
duplicate keys are rejected during preflight before any API calls.

```
./.venv/Scripts/python.exe scripts/census_benchmark.py run configs/my-test.json --output results/my-test
./.venv/Scripts/python.exe scripts/census_benchmark.py run configs/my-test.json --output results/my-test --execute
```

The default guard is three pages; explicitly add `--max-pages N` for a larger
authorized batch. Successful results are cached by image, model, truth, plan,
and extractor hashes. Transport retries are bounded. A changed request/model
snapshot may yield different text on a future run; this is reproducible inputs
and scoring, not guaranteed bitwise deterministic model output.

Outputs: `report.json`, per-page extraction JSON, `summary.csv`, `fields.csv`,
`confidence.csv`, `cells.csv`, and failures when applicable. The field set is
declared before inference. Missing and extra rows are penalized, blank agreement
and nonblank accuracy are distinguished, and model confidence is not treated as
an accuracy probability. Raw race marks are preserved; normalization uses only
the project's explicit code mappings. Blank historical race codes, research
ethnic recoding, and known ground-truth typos can therefore still mismatch.

With the Codex-provided artifact-tool Node package available, export Excel:

```
node scripts/export_benchmark.mjs results/my-test/report.json results/my-test/benchmark.xlsx
```

The spreadsheet skill/loader supplies Node and `@oai/artifact-tool` in Codex. Run
the authoring marker from that skill before generating a workbook. The exporter
writes a summary preview for visual verification. CSVs are usable in Excel even
without the Codex artifact runtime. No additional pip spreadsheet writer is used.

## Granular confidence audit

After a run, produce the full scan/row/cell evidence and calibration exports:

```
./.venv/Scripts/python.exe scripts/census_audit.py results/my-test/report.json --output results/my-audit
node scripts/export_benchmark.mjs results/my-audit/audit.json results/my-audit/audit.xlsx
```

The audit includes stable entry IDs, original Excel row coordinates, raw and
normalized values, exact agreement and rule-based matches, every discrepancy
with its confidence, per-row errors, and per-model/year/field confidence strata.
`variable_coverage.csv` explicitly identifies variables and decades not tested.
No errors are excluded just because they might be researcher coding differences.

Historical model responses have ordinal confidence labels only. New extraction
pipeline version 2 additionally requests a numeric probability for each field,
stored verbatim as a self-report. Neither is assumed calibrated. Numeric
reliability bins and Brier scores measure agreement under the comparison rule;
this target can differ from literal handwriting correctness because the manual
spreadsheet contains normalization and researcher coding. Treat these as
diagnostics until a reviewed codebook reconciles those targets.

Threshold-policy tables report precision on accepted nonblank predictions,
acceptance coverage, and reference errors per 1000 accepted fields. Missing rows
remain a separate risk because they have no confidence score to threshold.
The reference projections assume new data follows the same tested distribution;
they are not validated forecasts for uncleaned districts. No automation policy
is marked validated by this pilot.

95% bootstrap intervals resample whole scans, with a reproducible seed. They are
withheld below five scans in a year/field/confidence stratum; that reporting
floor is not a sample-size recommendation or proof of generalizability. Expand
by randomly sampling multiple pages, enumerators, image qualities, and districts
within each decade. Separate calibration, policy-selection, and final held-out
district tests. Choose targets for race and sex separately from names and age;
retain random human review of high-confidence accepted records in new districts.
Do not use one page's many correlated cells as independent validation evidence.

Calibration background: https://arxiv.org/abs/2207.13770

1860 is deliberately unsupported by this line-keyed benchmark. It needs household
alignment. Ground-truth page splitting currently follows the existing comparator's
line-number-reset logic; ambiguous or malformed page blocks must be resolved before
scoring. Baseline improvement and production accuracy require separate held-out
pages and a matched Ancestry baseline, neither inferred automatically.

Documentation checked November 4, 2026:
- https://portkey.ai/docs/api-reference/inference-api/models/models
- https://portkey.ai/docs/product/ai-gateway/chat-completions
- https://platform.claude.com/docs/en/models/overview
