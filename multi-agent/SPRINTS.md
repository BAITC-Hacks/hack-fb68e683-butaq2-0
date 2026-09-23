# V2V implementation sprints

Result-based sprints without fixed dates. This file records the implementation
sequence for `multi-agent/`; earlier proposals are design context. Browser first,
then SignalWire through the same decision service. Two online agents are enough
for the baseline: Router and Resolution. Deep Router is conditional on evaluation.

## 00 — Contracts and reproducible checks

**Current slice:** typed routing/context/result contracts, an injectable gateway,
Agents SDK dependency, package/Docker wiring, and offline checks.

Acceptance:

- A clean installation imports `multi_agent` from the built application wheel.
- Existing API fields remain compatible; new routing metadata is additive.
- Tests separate deterministic policy checks from live model accuracy.
- Synthetic demo fixtures stay explicitly labeled and outside routing logic.

## 01 — Browser voice through Router + Resolution

**Current slice:** SDK Router with Structured Outputs, Python policy, Resolution
with scenario-scoped read tools, and the existing microphone/STT/TTS adapter.

Acceptance:

- Every accepted transcript reaches Router; Resolution runs only for a valid route.
- Uncertain intent asks a short question; clear intent with a missing record ID is
  still routed so Resolution can collect the missing field.
- Unknown IDs and invalid transitions fail before state changes.
- Voice upload produces the existing audio response plus scenario/rationale/timings.
- A live RU, KK, and mixed-language microphone smoke test is recorded separately
  from offline test results. This live verification remains pending.

## 02 — Durable context, confirmations, and operator work

**Next:** build on the current in-memory scenario slots and topic switching.

- Persist session versions, scenario frames, results, and processed turn IDs in the
  existing PostgreSQL database. Serialize commits and deduplicate retries.
- Create operator tickets with language, transcript, active/pending topics and slots;
  expose status and operator takeover. Do not say a transfer succeeded before it does.
- Bind each proposed mutation to exact arguments and an explicit confirmation;
  expire approval on topic/argument changes and make execution idempotent.
- Verify ten-turn dialogues, restart/resume, two-worker contention, stale approvals,
  duplicate requests, catalog changes, cancellation, and failed execution.

Exit: context survives restart; an operator can accept a real queued request;
no mutation runs without a matching, current confirmation.

## 03 — Streaming reply, audio, and supervisor events

- Stream validated route events first, then approved Resolution text and TTS chunks.
- Assign session/turn/event IDs and persist ordered trace events, including errors.
- Keep upload/text fallback working and retain admin catalog editing.
- Measure routing, queue time, tools, first text, first audio byte and actual browser
  playback onset separately. Never present full TTS duration as first-audio latency.

Exit: the browser starts playback before the whole response is synthesized;
the supervisor sees each turn's route, alternatives and stage timings as they arrive.

## 04 — Streaming microphone and interruption

- Add PCM capture and streaming transcription using the currently verified API
  schema. Assemble final transcripts by input item ID; never route partial duplicates.
- Barge-in stops current playback, cancels pending work, and invalidates stale output.
- Keep one Router decision path for streaming and upload adapters.
- Exercise disconnect/reconnect, no-speech, overlap, delayed chunks and cancellation
  after model completion. Verify actual KK/RU/mixed speech and TTS by listening.

Exit: interruption works during playback and stale audio cannot resume; all three
language modes have recorded end-to-end outcomes and honest latency measurements.

## 05 — Evaluation and speed

- Import the official starter kit when available; freeze catalog/model/prompt versions
  for each evaluation run and separate development fixtures from held-out evaluation.
- Report counts, routing accuracy, confusion pairs, clarification/handoff behavior,
  and p50/p95 latency by language and warm/cold requests.
- Tune context/catalog size and model settings from measurements. Add Deep Router
  only if it improves measured accuracy enough to justify its additional latency.
- Treat 500 ms routing and 1.5 s speech-end-to-first-playback as targets until measured.

Exit: reproducible report with errors and sample counts; no fake accuracy or timings.

## 06 — SignalWire phone integration

**Required phone stage, after the shared browser flow works.**

- Vendor the necessary reviewed telephony adapter under `multi-agent/`; validate
  SignalWire Compatibility signatures and the actual configured REST/SIP paths.
- Connect phone transcript/delegation input to `VoiceRouterOrchestrator`; correlate
  carrier call ID, voice-provider session ID, conversation ID, and turn ID.
- Ensure only one handler accepts a call and duplicate callbacks do not duplicate turns.
- Implement actual operator transfer lifecycle and preserve the existing operator ticket.
- Test inbound call, interrupted reply, provider failure, hangup and transfer status
  with a real test number. Mocked signature tests do not establish carrier readiness.

Exit: one demonstrated SignalWire call exercises the same routing/context behavior
as the browser, with correlated traces and a verified end-of-call state.

## Extended — only after the V2V path

Offline Quality Analyst may summarize measured failures and propose catalog edits.
MCP, web/file search, computer use and vision need a concrete case requirement before
being added. Codex and development subagents help build the repository; they are not
the live conversation runtime.
