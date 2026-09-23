# Voice Router — backend

FastAPI API for the HackAlem / Halyk Bank **Voice Router** case. It uses the copied
`v2v/` library for STT and TTS. An LLM selects a scenario from the **current**
PostgreSQL catalog on every turn; this is not an intent classifier. The separate
`frontend/` project is maintained independently.

The shared runtime uses OpenAI Agents SDK **Router + Resolution** under
[`multi-agent/`](multi-agent/README.md). See its [V2V sprints](multi-agent/SPRINTS.md)
for the browser-first implementation and the subsequent SignalWire integration.

## Start

```bash
cp .env.example .env
# Set V2V_API_KEY and POSTGRES_PASSWORD in .env; ROUTER_ADMIN_TOKEN is optional for admin editing
make up
```

`make down` stops the stack without deleting its data; `make logs` follows the
logs. On the server (`129.151.210.13`), clone the repository once, configure
its `.env`, then run deployment **on the server**:

```bash
cp .env.example .env  # first deployment only; set real secrets
make deploy
```

`make deploy` fast-forwards the server's checked-out Git branch and rebuilds
the full Compose stack in place. It leaves `.env` and the PostgreSQL/Caddy
volumes intact. The server needs Docker with Compose and Git access to the
repository. Point the `owlpeer.com` DNS A record to `129.151.210.13` and
allow inbound TCP 80/443 for HTTPS.

The backend runs on http://localhost:8000 (`/docs` for OpenAPI); PostgreSQL
is reachable locally on port 5433. Compose also starts the separately maintained
frontend on http://localhost:3000. With DNS pointing `owlpeer.com` to the server
and inbound TCP ports 80/443 open, Caddy serves https://owlpeer.com with automatic
TLS. Requests to `/router/*`, `/health`, `/docs` and `/openapi.json` are proxied
to the backend under that same origin. Caddy certificates persist in named volumes.
The backend waits for PostgreSQL and applies
Alembic migrations before serving requests. Database data survives restarts in
the `postgres-data` volume.

An empty database automatically receives **40 synthetic insurance scenarios**,
a knowledge base and linked mock records from `app/data/demo/` on first API use.
Open `/voice/` and speak or type; no file upload or admin token is needed for the demo.
Existing catalogues and edits are preserved. This is an authored simulation, not
the official starter kit. See [demo dataset](app/data/demo/README.md) for examples,
10 annotated dialogues, 48 development utterances and optional live evaluation.

When the real starter kit is available, an administrator can replace the demo via
`POST /router/admin/catalog/import-files` (or use `/docs`):

```bash
curl -X POST http://localhost:8000/router/admin/catalog/import-files \
  -H "X-Admin-Token: YOUR_ROUTER_ADMIN_TOKEN" \
  -F scenarios=@scenarios.json \
  -F knowledge_base=@knowledge_base.json \
  -F mock_backend=@mock_backend.json
```

`scenarios.json` may be an array or an object with a `scenarios` array/map.
Each entry must have `id`, `scenario_id`, or `code`. The entire original entry
(including boundaries and RU/KZ examples) is stored as JSONB and passed to
the routing LLM. Import is atomic; it replaces the catalog and grounding facts
without changing prompts or model settings. Uploads are limited to 2 MB per file.

## API contract

- `POST /router/text` — JSON `{ "session_id": "demo", "text": "...", "synthesize": false }`.
  Returns `transcript`, `reply`, `action` (`route`, `clarify`, `handoff`),
  `scenario_id`, `scenario_title`, `confidence`, `reason`, `alternatives`,
  `pending_scenario_ids`, and `timings` (`stt_ms`, `routing_ms`, `response_ms`,
  `tts_ms`, `total_ms`). Set `synthesize: true` for `audio_base64` and
  `audio_content_type`. Routing metadata also includes `trace_id`, `language`,
  `topic_transition`, and scenario-scoped `extracted_parameters`.
- `POST /router/voice` — multipart `audio` and `session_id`. Returns the same
  structure, with transcript and base64 audio. The browser records the user's
  speech and plays the decoded audio. The source library checks the audio file
  signature and upload size.
- `GET /router/scenarios` — current catalog; `GET /router/sessions/{session_id}`
  — conversation context for the supervisor (held in process memory).
- `GET/PATCH /router/admin/settings` — read/edit `routing_prompt`, `answer_prompt`,
  `model`, `confidence_threshold`.
- `PUT/DELETE /router/admin/scenarios/{scenario_id}` — edit catalog entries;
  `POST /router/admin/catalog/import-files` — atomic bulk import. Admin calls
  require `X-Admin-Token` matching `ROUTER_ADMIN_TOKEN`.

Edits are read from PostgreSQL **on the next turn**, without restarting or
rebuilding. Default prompts and model settings are inserted only if absent.

## Routing behavior and limits

The routing prompt supplies the full catalog, last 10 turns, active scenario,
pending topics, and new RU/KZ or mixed-language utterance. The model returns
SDK Structured Outputs: one selected ID (validated against the catalog), confidence,
reason, alternatives, pending topics, and a customer-facing clarification or
handoff message. Below the configurable confidence threshold the bot asks for
clarification; after two uncertain turns it returns `handoff` and asks the customer
to contact an operator. An operator queue and actual transfer are not implemented
yet. It never executes irreversible actions. Resolution receives the selected
scenario, declared knowledge excerpts and scoped read-only lookup tools; the full
customer backend is not sent in the prompt. A clear intent with missing parameters
is routed so Resolution can collect them. Failed or cancelled model turns leave
the prior conversation intact.

The pipeline measures full STT, routing, response generation and full TTS time
separately. These are observed timings, **not guaranteed** 500 ms or 1.5 s
budgets. Confidence is the model's estimate, not a calibrated probability.
Dialogue context lives in one API process; use a shared session store for
multi-worker or persistent sessions.

## Local development and tests

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
docker compose up -d db
alembic upgrade head
uvicorn app.main:app --reload
```

```bash
TEST_DATABASE_URL=postgresql+psycopg://butaq:butaq-local@localhost:5433/butaq python -m pytest -q
```

The PostgreSQL integration test creates an isolated temporary schema, applies
the Alembic migration, and drops it afterwards. Model/STT/TTS tests use stubs
and do not require an OpenAI key. Set `FRONTEND_ORIGINS` to a comma-separated
list of allowed browser origins if the frontend runs on another host.
