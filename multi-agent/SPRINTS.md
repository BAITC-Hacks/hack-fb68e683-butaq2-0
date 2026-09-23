# V2V implementation sprints

Result-based sprints without fixed dates. This file records the implementation
sequence for `multi-agent/`; earlier proposals are design context. Browser first,
then SignalWire through the same decision service. Two online agents are enough
for the baseline: Router and Resolution. Deep Router is conditional on evaluation.

## Active delivery order — three separately authorized sprints

Sprint A is complete. The user has now authorized **Sprint B and Sprint C**,
including the RU/KZ language regression fix. Development subagents can work in parallel within
the active sprint; the runtime remains Router + Resolution.

### Sprint A — Terra for the shared conversation service (completed)

Scope:

- Replace Luna with `gpt-5.6-terra` for both Router and Resolution. Keep their
  common orchestrator, scoped tools and validated routing contract.
- Update application defaults, example configuration and the active database
  model setting; changing an environment seed alone does not update an existing
  database setting. Preserve prompts, catalog data and unrelated configuration.
- Set an explicitly supported reasoning effort and verify it reaches both agents.
  Speech recognition and synthesis retain their dedicated audio models.
- Run focused SDK, policy and adapter checks, then a live conversation smoke test.
  Record model, reasoning effort and observed stage timings without claiming that
  Terra necessarily improves latency or that a short smoke test measures accuracy.

Acceptance:

- The running service uses Terra for both agents, including when database settings
  override environment defaults; model selection is confirmed without exposing keys.
- The policy end-date follow-up, ambiguous money question and explicit refund ID
  are handled without invented customer records or invalid scenario transitions.
- Focused checks pass; unavailable live checks or regressions are reported explicitly.
- The result states that current voice responses are still buffered. No streaming,
  interruption, persistence or telephony implementation is included in this sprint.

Completed verification: Terra/low is active in the rebuilt backend and persisted
DB settings; 129 offline tests passed, 9 were skipped. The five-turn development
replay matched all expected route/action pairs, with one language-label defect
recorded. A fresh synthesized-input voice request returned speech in 7.39 seconds
of server time. This is not a streaming latency or broad quality claim. Raw results
and limitations are in [README.md](README.md#terra-migration). Sprint A is complete;
Sprint B and C are implemented; verification and limits are recorded below.

### Sprint B — Stream replies and begin audio playback sooner (implemented)

Scope: implement the streaming reply/audio work detailed in backlog **03**, retaining
one shared Router/Resolution decision service and the upload/text fallback. Emit
validated route events, then grounded response segments and ordered audio chunks;
never send unchecked model text directly to speech. Buffer a segment if its facts
cannot yet be validated. Keep the session/turn/event contract compatible with future
persistent trace storage, without pulling the durable-session backlog into scope.

Acceptance:

- The browser demonstrably starts playback before synthesis of the whole reply ends.
- First text, first audio byte and actual playback onset are measured separately
  from total response duration on recorded, comparable requests.
- Route and stage events reach the UI in order, and failed/disconnected streams
  have defined error handling. Existing upload/text requests continue to work.
- No fixed latency target is called achieved until measured. This sprint does not
  claim to solve interruption or continuous microphone input.

### Sprint C — Streaming microphone and interruption (implemented)

Scope: implement backlog **04** after the streaming output path is verified. Add
streaming input, transcript finalization and cancellation shared across browser
capture, playback and backend turn work. Keep a single routing path.

Acceptance:

- Speaking during a reply stops playback and invalidates remaining output for that
  turn; delayed audio/text cannot restart the cancelled reply or corrupt newer state.
- Final transcripts are deduplicated by input/turn ID. Partial transcripts cannot
  trigger duplicate Router decisions or tool execution.
- Disconnect/reconnect, silence, overlap and cancellation after model completion
  are covered. Actual RU, KK and mixed-language speech is checked by listening.
- Speech-end-to-first-playback latency is reported with sample counts and conditions.

### Verification of B and C

- 158 Python tests passed, 9 database/integration checks skipped; 10 frontend audio
  tests, TypeScript checks and both Docker production builds passed.
- Live provider test returned partial transcription during input and the correct
  final Russian question. Only its final transcript invoked Router.
- Live WebSocket test received 87 PCM chunks; first audio at 6.52 s preceded full
  response generation at 7.52 s. Interruption and a subsequent turn succeeded,
  with no old audio after cancellation acknowledgement or unheard assistant history.
- Chromium with a synthetic microphone exercised the actual AudioWorklet, VAD,
  ASR, Router/Resolution and PCM playback. Playback began at 5.56 s after commit,
  before synthesis completed. Speaking the next fixture during output stopped
  13 scheduled sources; cancellation acknowledgement took 2.4 ms on localhost.
  The next utterance routed correctly, and cancelled output was not acknowledged.
- The language regression replay returned `ru, ru, mixed, ru, ru` and matched all
  five expected action/scenario pairs. This reused development set is not an
  accuracy benchmark. Timing samples above are not p50/p95 or physical-device
  acoustic measurements; the 500 ms client silence window is outside commit timing.
- Real microphone/speaker echo, actual RU/KK/mixed listening quality and mobile
  browser behavior still need device checks. Session persistence and SignalWire
  remain later work. The production domain needs its own explicitly approved
  `FRONTEND_ORIGINS` entry; no origin permissions were expanded in this change.

See [implementation and measured artifacts](README.md#streaming-voice-and-interruption).

### Remaining beyond these three sprints

- **Durable sessions and real operator/action workflows:** backlog **02**. Current
  in-memory sessions do not survive restart or coordinate independent workers;
  operator messages alone do not establish a ticket or successful transfer.
- **Held-out quality and latency evaluation:** backlog **05**. Development replays
  and a synthesized Russian voice smoke test do not establish RU/KK/mixed microphone
  quality, production routing accuracy or p50/p95 latency.
- **SignalWire:** backlog **06**, still requiring carrier integration and a real
  inbound call through the shared service. Browser readiness is not phone readiness.

The numbered sections below retain the detailed original backlog. Their numbers
are references; the active execution order is A, followed by the now-authorized B and C.

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
- Unknown scenario IDs fail before state changes. Python derives transition
  metadata from the validated scenario choice and current session.
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
