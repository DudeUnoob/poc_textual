# Running the Portkey workbench

Copy `.env.example` to `.env`. Set `PORTKEY_API_KEY`, `PORTKEY_MODEL`, and
`PORTKEY_COMPARISON_MODEL` to values from your own Portkey workspace. Models use
`@catalog-slug/model` syntax; the generic defaults are placeholders, not account
credentials. The web app, worker, CLI, and benchmark share `provider_config.py`.
Use `CENSUS_PROVIDER=gemini` plus `GEMINI_API_KEY` only for explicit legacy CLI
work or deliberately resuming a legacy run. While Portkey mode is active, the
worker rejects queued Gemini runs before making a request. The analyst form
always offers Anthropic, OpenAI or both through Portkey.

```
docker compose config --quiet
docker compose up --build -d
```

Open http://127.0.0.1:8000. `/healthz` checks the web process and database without
calling a model. The worker waits for web health before starting. Missing model
credentials block queueing with a configuration message; no credential values
are included in the health response. Both containers use the same local SQLite
database and files under `data/workbench`; back up that directory before moving
to another host. Run only one worker: the SQLite queue is not a distributed
multi-worker deployment. No database schema migration is required by this PR.

`PORTKEY_TIMEOUT_SECONDS`, `PORTKEY_MAX_ATTEMPTS` and
`PORTKEY_MAX_OUTPUT_TOKENS` bound requests. HTTP 429 and transient server/network
failures retry; 400/401/402/403 fail without retry. Provider errors redact the
Portkey credential. Restart web and worker after changing environment settings;
already queued runs keep their model ID. Successful pages remain checkpointed
when a run fails, is cancelled, or resumes. Do not publish `docker compose config`
output without redaction because environment expansion includes secrets.

For secret files, set `PORTKEY_API_KEY_FILE` to a container-visible file and
mount the same file read-only into both services using a local Compose override.
For example, set it to `/run/secrets/portkey_api_key` and add a read-only bind
from `./secrets/portkey_api_key` to that path in **both** services. Host paths do
not automatically become container paths. `.env`, secret directories, virtual
environments, generated reports and downloaded census scans are excluded from
the build context. Never put credentials in Docker build arguments or images.

Portkey runs use a full-page, decade-aware extraction contract and expose API
attempt/retry events to the existing progress stream. Numeric confidence is
preserved in candidate evidence. Portkey auto-acceptance is deliberately disabled:
existing calibration bands do not identify the model/prompt that produced them.
They cannot authorize a different model's predictions. A future model-scoped,
held-out calibration policy must be implemented before enabling that behavior.
The legacy Gemini fast-switch route is not exposed by the analyst workflow.

The browser review UI supports 1950 only because its evidence crops and field
schema are specific to that form. The benchmark CLI supports verified line-keyed
comparisons across decades; 1860 requires separate household alignment. See
[the workflow](../PORTKEY_WORKFLOW.md) and [the reading guide](WORKBOOK_GUIDE.md).

For a production host, add authentication and TLS at a trusted reverse proxy.
Compose binds the unauthenticated workbench to localhost by default. This change
does not provision a cloud host or deploy any production service.
