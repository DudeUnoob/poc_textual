# Assistant workflow

Use the repository's Python environment; create `.venv` and install
`requirements.txt` if missing. Resolve paths from that checkout, not from a
particular user's computer. The cleaned workbooks normally live in
`data/ground_truth`. `--truth-dir` accepts another folder. Missing workbooks
cannot be replaced by model-generated answers.

## One-time Portkey setup

Check whether local `PORTKEY_API_KEY` or `PORTKEY_API_KEY_FILE`, `PORTKEY_MODEL`
and `PORTKEY_COMPARISON_MODEL` already exist. Never print credential contents.
The user can set them in `.env` using `.env.example`, or run:

```
python scripts/census_ground_test.py configure
```

This prompts for the key in a hidden local terminal, fetches `/models`, reuses
the configured model names under the user's own catalog when unambiguous, and
otherwise lets the user pick a listed Anthropic/OpenAI model by number. It writes
only local `.env`. Do not run an interactive setup in a noninteractive execution
tool and wait indefinitely. If setup is missing, direct the user to this single
local command or their existing secret file. Do not request a key in public files,
command arguments, a plan, or a shared ZIP. Catalog slugs are user-specific.
Available models establish access, not proven handwriting accuracy.

## Intake and alignment

Create a new output directory per intake. For years alone, the driver discovers
local scans. For ZIPs/folders use one or more `--input` arguments. Example:

```
python scripts/census_ground_test.py prepare --years 1880 1910 --output results/my-test
python scripts/census_ground_test.py prepare --input BATCH.zip --years 1880 --output results/my-test
```

Do not run both examples into the same directory. `--pages-per-year N` changes
the default three-scan sample; `--all-pages` honors an explicitly requested full
batch. `--model @catalog/model` can be repeated for explicit model choices.
Extracted images go to a new directory; archive instructions/settings are ignored.

The driver writes `plan.json`, `intake.csv` and `alignment-anchors.csv`. It deduplicates
images, detects years from folder/archive labels, and reuses reviewed mappings
from `configs/verified-alignments.json` only when image AND workbook hashes match.
Every other selected image goes in `pending_jobs`. Do not mark it verified just
because its filename has a page number. The anchors include candidate sheet starts
and printed-line 51 side starts. Inspect additional reference rows as needed.

For each unfamiliar selected scan, inspect the original image, match multiple
people and the form's page/side label, then move its job to `jobs`. Fill the actual
`year`, `workbook`, `sheet`, `gt_page` (one-based reset-block index), `fields`, and
`alignment_evidence`; set `alignment_verified=true`. Use the intersection of the
workbook's columns and `census_benchmark.CORE` unless the user declares other fields.
Do not claim unrequested variables were tested. `gt_row_start` and `gt_row_count`
are one-based data rows excluding the header and override `gt_page`. They are
essential when multiple scans share a line-reset block. In 1910, line 51 can
repeat on successive scanned sides; printed line alone is not a page key.
Retain the image/workbook SHA-256 values so preflight detects changed inputs.

Keep unresolved jobs in `pending_jobs`, update their intake statuses with reasons,
and proceed with verified jobs when useful. Ask the user only for missing
information that cannot be recovered from the files. Never move the reference
to fit a model's wrong answer. Verify page mappings before inference/scoring.

## Execute and export

Preflight first, then run the same selected pages through both models:

```
python scripts/census_ground_test.py run results/my-test/plan.json --output results/my-test
python scripts/census_ground_test.py run results/my-test/plan.json --output results/my-test --execute --node NODE_PATH
```

Use Codex's `load_workspace_dependencies` to locate Node and its bundled
`@oai/artifact-tool`; follow the installed spreadsheet skill's package-resolution
setup and run its authoring marker immediately before the first workbook export.
The exporter imports that package from a `scripts/node_modules` junction/symlink
to the bundled runtime. Do not install an unrelated npm dependency with a similar
name. If runtime dependencies are unavailable, deliver the JSON/CSVs and explicitly
say XLSX is pending; do not claim it exists.

Successful pages are cached using image, truth, plan, model, provider settings,
extractor and normalizer hashes. Request attempts and failures are preserved.
Output truncation is a failed read, not a partial transcript to score. If it occurs,
increase `PORTKEY_MAX_OUTPUT_TOKENS` within the model's supported limit and retry
once, keeping both models' settings comparable and the failed attempt recorded.
Numeric field confidence makes responses larger; the driver defaults to a
32,000-token allowance for this process. Use `--max-output-tokens N` if a selected
model needs a different limit. It does not change the workbench's environment file.
Do not silently change the requested model. Stop for
authentication, billing or access errors instead of retrying the batch.

`run` creates raw JSON/CSVs, then the granular audit and XLSX. To finish/export
existing results without another model request:

```
python scripts/census_ground_test.py finish results/my-test/plan.json --output results/my-test --node NODE_PATH
```

Inspect the rendered overview and representative discrepancy/cell rows. Check
that values checked = matched cleaning + differed from cleaning, complete-row
counts reconcile with the row ledger, and missing/extra rows never receive
exact agreement from blank placeholders. Check workbook tab row counts against
audit.json. The exporter includes Excel filter tables and dynamic scope notes.
`attempt_events.jsonl` records requests; started events without a terminal event
mean an interrupted/unresolved call, not a successful result.

## Evidence package and interpretation

```
python scripts/package_census_audit.py results/my-test/audit/audit.json results/my-test/evidence.zip --workbook results/my-test/audit/granular-audit.xlsx
```

The allowlisted packager includes exact tested images/workbooks, raw responses,
audit CSV/JSON, a portable plan and the source needed to reproduce scoring.
It rejects configured credentials, checks compressed Office file contents, and
writes SHA-256 checksums. No `.env`, key files or unrelated scan folders belong
in the package. Include `docs/WORKBOOK_GUIDE.md`; its numerical examples are from
the earlier four-scan pilot and are not results for every future run.

Confidence strata are grouped by model/year/field/label. Numeric reliability and
Brier use only actual numeric self-reports and compare against the declared
cleaning-agreement rule, which may differ from literal handwriting correctness.
Threshold tables describe nested alternative policies on nonblank predictions;
do not add their counts. Extra predictions count as errors. Missing people have
no confidence score and remain a separate risk. Bootstrap intervals resample
whole scans and are withheld below five scans per stratum. Five is a reporting
floor, not proof of generalizability. No automation policy is validated here.
