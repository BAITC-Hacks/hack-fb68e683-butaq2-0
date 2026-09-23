# Multi-agent voice runtime

The browser's existing `/router/voice` and `/router/text` endpoints use one Python
orchestrator with two OpenAI Agents SDK agents: Router and Resolution. The Router
runs on every turn; Resolution runs only after Python accepts a route. Clarification
and requests for an operator do not add another agent call. Tool calls can add model
round trips inside Resolution's bounded run.

All runtime implementation lives in `src/multi_agent/`. The root application's
`RouterService` adapts its PostgreSQL catalog/settings and existing STT/TTS pipeline
to this package. `app.domain` re-exports the same contracts for API compatibility.
The root wheel includes this package; there is no dependency on `hack-tools` or on
an absolute developer path.

```text
Browser microphone → upload/STT ┐
Text fallback ──────────────────┴→ RouterService
                                  ↓
                         VoiceRouterOrchestrator
                           Router → Python policy
                                      ↓
                         Resolution + read-only tools
                                      ↓
                            reply + trace → TTS
```

## Run and check

From the repository root, with Python 3.12:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
python -m pytest -q
```

The normal application launch remains `docker compose up --build`. Configure
`V2V_API_KEY` and the existing database settings as described in the root README.
Model and prompt edits in the admin UI apply to the next turn. Keep the existing
model default until account access and voice latency have been measured.

Optional runtime settings:

```dotenv
MULTI_AGENT_TIMEOUT_SECONDS=20
MULTI_AGENT_RESOLUTION_MAX_TURNS=3
MULTI_AGENT_TRACING=false
```

Tests use fake gateways or fake provider responses and make no paid model calls.
The SDK tests also exercise its real runner and strict output schema. PostgreSQL
integration tests require `TEST_DATABASE_URL`; without it, they are skipped.

For a separately initiated live evaluation against a running app:

```bash
python app/data/demo/evaluate.py --limit 10
python app/data/demo/evaluate.py --live --limit 10
python app/data/demo/evaluate.py --live --dialogs --limit 5
```

Only `--live` sends model requests. Those fixtures are synthetic development data,
not the official starter kit or evidence of jury accuracy.

## Boundaries of this slice

- SDK Structured Outputs provide the Router shape; Python additionally validates
  scenario references and extracted parameter ownership. Python derives the final
  topic transition from the accepted scenario and prior state, correcting inconsistent
  model labels without an extra model call.
- Context contains the latest ten turns, pending topics, and parameters scoped to
  each scenario. A failed/cancelled model turn does not commit dialogue state.
- Same-session model turns are serialized. Sessions are currently in memory in
  one backend process; restart persistence and multiple workers come later.
- Resolution tools are read-only and scoped to the selected scenario. There is no
  payment, cancellation, or account mutation. Confirmation/execution is a later sprint.
- `handoff` reports the need for an operator; no queue or carrier transfer exists yet.
- Existing stage timings measure completed operations. Full audio is still buffered;
  `total_ms` is not speech-end-to-first-playback latency. Streaming and barge-in are
  separate sprints. A disconnected HTTP client is not guaranteed to cancel server work.
- Trace IDs and per-stage timings are returned in the turn result. External SDK
  tracing is opt-in and excludes sensitive model/tool payloads. Durable application
  event storage and the operator queue are still pending.

See [SPRINTS.md](SPRINTS.md) for implementation order and acceptance criteria.
SignalWire remains the selected phone carrier and a required later integration.

SDK implementation references: [agent definitions](https://developers.openai.com/api/docs/guides/agents/define-agents)
and [models and providers](https://developers.openai.com/api/docs/guides/agents/models).
