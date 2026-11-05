# Census OCR proof of concept

Read handwritten Bastrop County census scans through Portkey, retain model
confidence and raw text, compare drafts against manually cleaned records, and
review every unresolved field before export.

## Analyst workbench

Copy `.env.example` to `.env` and configure your own Portkey API key and two
catalog model routes:

```
PORTKEY_MODEL=@your_anthropic_catalog/claude-opus-5-5
PORTKEY_COMPARISON_MODEL=@your_openai_catalog/gpt-6-astra
```

Start the web app and single checkpointing worker:

```
docker compose config --quiet
docker compose up --build -d
```

Open http://127.0.0.1:8000. Import a folder of authorized 1950 scans, confirm page
order, and identify covers. Choose **Anthropic**, **OpenAI**, or **Both** when
starting extraction. Both performs an independent read with each model; it does
not silently replace one provider with another. Use **Compare model drafts** to
inspect corresponding scan, line and field values. The normal review queue uses
the latest run with saved candidates; earlier drafts and reviewer decisions are
preserved. Model agreement and model confidence do not establish correctness.

The browser's evidence crops and review schema currently support 1950 only.
Cross-decade ground-truth tests use the benchmark CLI below. Portkey predictions
remain subject to human review; Gemini calibration bands cannot authorize them
for automatic acceptance. Existing Gemini runs and the explicit legacy CLI mode
remain supported, but new analyst choices are Anthropic and OpenAI only.

See [deployment settings](docs/DEPLOYMENT.md) for health checks, persistent
storage, secret-file mounting, retry limits, and local setup. This is a local
single-worker application; a public host needs authentication and TLS.

## Local Python setup

```
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```

On macOS or Linux, use `.venv/bin/python`. Run the web app and worker in separate
terminals with `PYTHONPATH=src`:

```
python -m uvicorn workbench.web:app --host 127.0.0.1 --port 8000
python -m workbench.worker
```

Provider configuration is shared across web, worker, extractor and benchmark.
The extractor also accepts an explicit catalog model:

```
python src/extract.py IMAGE_PATH 1950 OUTPUT_JSON @your_anthropic_catalog/claude-opus-5-5
```

Set `CENSUS_PROVIDER=gemini` and `GEMINI_API_KEY` only for the legacy extraction
CLI. No GPU or downloaded model is required.

## Reproducible ground-truth tests

Use [the Portkey workflow](PORTKEY_WORKFLOW.md) and the portable
[teammate skill](skills/census-ground-test/SKILL.md). The teammate supplies years,
a batch ZIP, or a scan folder. Codex handles the plan and verifies image-to-Excel
alignment, then runs both configured models and delivers the granular XLSX and
evidence ZIP. The default sample is three scans per year; request the full batch
explicitly to test every supplied scan. Their own Portkey key and catalog routes
are configured once with `python scripts/census_ground_test.py configure`.

```
python scripts/census_ground_test.py prepare --years 1880 1910 --output results/my-test
python scripts/census_ground_test.py run results/my-test/plan.json --output results/my-test
```

Verify the plan's page alignment and select the requested pages before adding
`--execute`. Example configs use generic catalog names; regenerate them with
`prepare` or replace those names with your own. Exact, previously reviewed image
and workbook hashes reuse saved mappings; Codex verifies unfamiliar scans.
These tests require locally available
images and cleaned workbooks; private keys and new downloads are not committed.

```
python scripts/census_ground_test.py finish results/my-test/plan.json --output results/my-test --node NODE_PATH
```

The [workbook reading guide](docs/WORKBOOK_GUIDE.md) explains denominators,
confidence, fuzzy matching, discrepancies and limitations. A
[Word copy](docs/census-workbook-reading-guide.docx) is included. These are pilot
agreement tests, not validated forecasts for uncleaned districts. The 1860
workbooks lack line numbers and require household alignment. Some other decades
need explicit reference-row ranges because numbering continues across scans.

Raw extraction JSON, CSV ledgers, hashes and source coordinates are preserved.
An optional Codex artifact-tool exporter creates the Excel review workbook;
CSV/JSON analysis needs only the Python dependencies. Credentials, reports and
newly downloaded source scans remain outside source control and container images.

## Validation

```
python -m pytest -q
docker compose config --quiet
docker build -t census-ocr:test .
```

Tests use mocked provider responses and an isolated database; they do not make
paid inference requests. GitHub Actions runs the tests and container build.
