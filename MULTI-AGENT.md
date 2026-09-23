> **Review update — 2026-09-23:** Use [PLAN.md](PLAN.md) as the implementation plan. **SignalWire is the selected carrier.** The original proposal below is retained for reference; the reviewed plan takes precedence where they differ.
>
> The code review found reusable SignalWire compatibility settings and an existing GPT-Live sideband runner in `hack-tools/telephony`. Its offline suite passed 89 tests. Remaining work includes shared case-2 routing, actual call validation, confirmation enforcement and carrier/session correlation. The `codex-agent` gateway implements a subset of Responses semantics and is unsuitable as the strict live-routing endpoint without changes.
>
> Build browser voice, traces and evaluations from the first working slice. Combine execution planning and response composition initially. Keep browser Realtime and phone GPT-Live in distinct adapters. Accuracy/latency figures below are illustrative; model choices and thresholds require measurement. The capability matrix in `PLAN.md` gives every requested OpenAI feature a concrete role.

Yes. I’ve thought through the system end-to-end. The main architectural decision is: **do not build 40 agents for 40 scenarios**. Build one controlled orchestration layer, a few specialized agents, and keep the 40 scenarios as **data**.

That fits the hackathon much better because the core thing being evaluated is the **LLM scenario-selection layer**, including topic changes, Russian/Kazakh/mixed speech, uncertainty, alternatives, explanation, and latency.

# Voice Router — Multi-Agent Architecture Plan

## 1. Core idea

Our system has three separate layers:

```text
┌──────────────────────────────────────────────┐
│               CHANNEL LAYER                  │
│                                              │
│   Browser Microphone        SignalWire       │
│        WebRTC               Telephony        │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│                VOICE LAYER                   │
│                                              │
│   VAD → STT → Audio events → TTS             │
│                                              │
│   OpenAI Realtime / transcription            │
└──────────────────────┬───────────────────────┘
                       │ transcript
                       ▼
┌──────────────────────────────────────────────┐
│             MULTI-AGENT CORE                 │
│                                              │
│   Conversation State                         │
│          ↓                                   │
│   Router Agent                               │
│          ↓                                   │
│   Clarifier / Executor / Response            │
│          ↓                                   │
│   Tools + Knowledge Base                     │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
              Supervisor Trace
```

The important rule is:

> **Voice is transport. Routing is intelligence.**

SignalWire must never contain routing logic.

OpenAI Realtime handles persistent audio interaction, streaming audio, tool calls and interruptions; the Agents SDK also supports persistent realtime sessions. ([GitHub][1])

---

# 2. What actually counts as an agent

I would use **four real LLM agents**.

```text
                ORCHESTRATOR
                     │
                     ▼
               ROUTER AGENT
                     │
       ┌─────────────┼─────────────┐
       │             │             │
       ▼             ▼             ▼
 CLARIFIER       EXECUTOR       ESCALATION
   AGENT           AGENT          POLICY
                     │
                     ▼
               RESPONSE AGENT
```

And importantly:

```text
NOT agents:

ConversationState
ScenarioRegistry
CandidateSelector
LatencyTracker
TraceCollector
ToolRegistry
SignalWireAdapter
KnowledgeBaseRepository
MockBackendRepository
```

Those should be normal deterministic Python services.

This distinction matters.

Otherwise you end up with:

```text
agent calls agent
      ↓
agent calls agent
      ↓
agent thinks
      ↓
agent calls agent
```

and suddenly nobody knows why routing took 2.5 seconds.

OpenAI's own Agents SDK documentation distinguishes two common approaches: a central manager that keeps control and invokes specialized agents, versus handoffs where another agent takes over. For this project, I would use primarily the **manager/orchestrator pattern** because we need deterministic measurements and a single place to collect the routing trace. ([GitHub][2])

---

# 3. Full production pipeline

This is the complete path for one customer utterance.

```text
PHONE / BROWSER
      │
      │ audio
      ▼
┌─────────────────────┐
│ 1. Voice Gateway    │
│                     │
│ SignalWire / WebRTC │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 2. VAD / STT        │
│                     │
│ partial transcript  │
│ final transcript    │
└──────────┬──────────┘
           │
           ▼
┌──────────────────────────┐
│ 3. Conversation State    │
│                          │
│ history                  │
│ active scenario          │
│ suspended scenarios      │
│ collected parameters     │
│ previous route           │
└─────────────┬────────────┘
              │
              ▼
┌──────────────────────────┐
│ 4. Routing Input Builder │
│                          │
│ utterance                │
│ short history            │
│ active scenario          │
│ scenario catalogue       │
└─────────────┬────────────┘
              │
              ▼
┌──────────────────────────┐
│ 5. FAST ROUTER AGENT     │
│                          │
│ structured output        │
└─────────────┬────────────┘
              │
         confidence?
       ┌──────┴───────┐
       │              │
     high           uncertain
       │              │
       │              ▼
       │      ┌─────────────────┐
       │      │ 6. DEEP ROUTER  │
       │      └────────┬────────┘
       │               │
       └───────┬───────┘
               ▼
┌──────────────────────────┐
│ 7. Routing Policy        │
│                          │
│ accept?                  │
│ clarify?                 │
│ escalate?                │
│ topic changed?           │
└─────────────┬────────────┘
              │
       ┌──────┼──────────┐
       │      │          │
       ▼      ▼          ▼
   EXECUTE  CLARIFY   ESCALATE
       │
       ▼
┌──────────────────────────┐
│ 8. Scenario Executor     │
│                          │
│ identify needed actions  │
│ collect missing params   │
│ call typed tools         │
└─────────────┬────────────┘
              │
              ▼
┌──────────────────────────┐
│ 9. Tools                 │
│                          │
│ knowledge base           │
│ mock backend             │
│ scenario actions         │
└─────────────┬────────────┘
              │
              ▼
┌──────────────────────────┐
│ 10. Response Agent       │
│                          │
│ RU / KZ / mixed          │
│ short conversational     │
│ answer                   │
└─────────────┬────────────┘
              │
              ▼
┌──────────────────────────┐
│ 11. Voice Output         │
│                          │
│ TTS / Realtime audio     │
└─────────────┬────────────┘
              │
              ▼
             USER

EVERY COMPONENT
       │
       └────────────→ TRACE COLLECTOR
                            │
                            ▼
                     SUPERVISOR UI
```

---

# 4. Router Agent — the heart of the project

This is the agent we optimize hardest.

Its job is **not to answer the customer**.

Its only job:

```text
Understand:
"What does the customer want right now?"
```

Input:

```python
class RoutingRequest(BaseModel):
    utterance: str

    conversation_id: str

    active_scenario_id: str | None

    conversation_summary: str | None

    recent_messages: list["Message"]

    scenario_candidates: list["ScenarioDefinition"]
```

Output:

```python
class ScenarioAlternative(BaseModel):
    scenario_id: str
    confidence: float
    reason: str


class RoutingDecision(BaseModel):
    primary_scenario_id: str | None

    confidence: float

    alternatives: list[ScenarioAlternative]

    rationale: str

    language: Literal["ru", "kk", "mixed", "other"]

    topic_changed: bool

    continuation_of_current_topic: bool

    needs_clarification: bool

    extracted_parameters: dict[str, Any]
```

Example:

```json
{
  "primary_scenario_id": "policy_not_received",
  "confidence": 0.94,
  "alternatives": [
    {
      "scenario_id": "payment_status",
      "confidence": 0.04,
      "reason": "Payment status is mentioned."
    },
    {
      "scenario_id": "policy_activation",
      "confidence": 0.02,
      "reason": "The policy may not have been activated."
    }
  ],
  "rationale": "The payment was completed but the expected policy was not received.",
  "language": "mixed",
  "topic_changed": false,
  "continuation_of_current_topic": true,
  "needs_clarification": false,
  "extracted_parameters": {
    "payment_time_reference": "yesterday"
  }
}
```

OpenAI Agents can use a Pydantic type as `output_type`, which gives us structured output instead of parsing free-form text ourselves. ([OpenAI][3])

---

# 5. Why I would not use the Realtime model as the primary router

This is an important design decision.

Realtime should handle:

```text
audio
conversation
interruptions
speech
```

Router should handle:

```text
strict classification/routing
structured result
benchmarking
confidence
alternatives
```

The current `gpt-realtime-2.1` supports audio and function calling, but its model page does **not** list Structured Outputs support. GPT-5.6 Sol does support Structured Outputs. ([OpenAI Developers][4])

Therefore:

```text
Realtime model
      ↓
transcript
      ↓
GPT routing model
      ↓
typed RoutingDecision
```

is much easier to evaluate.

---

# 6. Fast Router + Deep Router

This is probably the strongest technical addition for your hackathon.

The specification explicitly mentions hybrid architecture as an optional enhancement.

Architecture:

```text
            Utterance
                │
                ▼
        FAST ROUTER AGENT
                │
                ▼
         confidence >= T
           /          \
         YES           NO
          │             │
          ▼             ▼
       ACCEPT       DEEP ROUTER
                         │
                         ▼
                  final decision
```

Possible configuration:

```yaml
routing:
  fast:
    model: gpt-5.6-luna
    reasoning_effort: none

  deep:
    model: gpt-5.6-sol
    reasoning_effort: low

  accept_threshold: 0.90

  clarify_threshold: 0.65
```

Current Agents SDK documentation specifically recommends low/no reasoning effort when latency matters, and GPT-5.6 Luna is positioned for high-volume efficient workflows. ([OpenAI][5])

But these thresholds **must not be guessed permanently**.

We tune them with `dev_utterances.json`.

---

# 7. Start without Candidate Retrieval

This is another important decision.

There are only:

```text
40 scenarios
```

So version 1 should simply give Router all 40.

```text
40 scenarios
      ↓
LLM Router
      ↓
decision
```

Advantages:

```text
no retrieval recall failure
simple architecture
easy evaluation
easy debugging
```

Then benchmark.

If latency is too high:

```text
40 scenarios
      ↓
Candidate Selector
      ↓
top 5–8
      ↓
LLM Router
```

CandidateSelector could use embeddings / lexical similarity / scenario metadata, but it **must only retrieve candidates**.

It must never make the final decision.

Why?

Because the hackathon specifically prohibits replacing the LLM routing layer with a conventional intent classifier.

So:

```text
retrieval = optimization

LLM = decision maker
```

---

# 8. Scenario Registry

When you receive `scenarios.json`, load it into a proper domain model.

```python
class ScenarioDefinition(BaseModel):
    id: str

    title: str

    purpose: str

    boundaries: list[str]

    parameters: list["ScenarioParameter"]

    actions: list[str]

    examples_ru: list[str]

    examples_kk: list[str]
```

At startup:

```text
scenarios.json
      ↓
ScenarioLoader
      ↓
validation
      ↓
ScenarioRegistry
      ↓
memory
```

Agents never open JSON files themselves.

They call:

```python
scenario_registry.get(...)
scenario_registry.list(...)
```

Clean and testable.

---

# 9. Clarification Agent

The Router must be allowed to say:

```text
"I don't know yet."
```

This is critical.

Suppose:

> “Something is wrong with my policy.”

Potential scenarios:

```text
payment issue
policy activation
policy delivery
policy information
```

Router:

```json
{
  "primary_scenario_id": null,
  "confidence": 0.48,
  "needs_clarification": true
}
```

Clarification Agent sees the top alternatives and generates **one short discriminating question**:

> “Could you clarify whether the problem is with payment or with receiving the policy?”

Not:

> “Please explain your issue.”

The clarification should reduce uncertainty between the candidate scenarios.

---

# 10. Scenario Executor Agent

Once routing is done, Router exits.

```text
Router
  ↓
scenario selected
  ↓
Executor
```

Executor receives:

```python
ExecutionRequest(
    scenario=...,
    collected_parameters=...,
    customer_context=...,
)
```

It decides:

```text
Which parameters are missing?
Which tool is required?
Do we need confirmation?
Can we complete the scenario?
```

Example:

```text
selected scenario:
policy_status

required:
customer_id

known:
customer_id = 82818

Executor
    ↓
get_policy_status(customer_id)
```

This keeps routing evaluation completely independent from backend behavior.

---

# 11. Tools instead of more agents

For deterministic operations:

```python
@function_tool
async def get_customer(...)

@function_tool
async def get_policy_status(...)

@function_tool
async def get_payment_status(...)

@function_tool
async def search_knowledge_base(...)

@function_tool
async def update_customer_data(...)
```

The SDK supports schema-backed function tools with Pydantic validation, which is exactly what we want for clean tool boundaries. ([OpenAI][6])

So don't create:

```text
CustomerAgent
PaymentAgent
PolicyDatabaseAgent
```

when a function is enough.

---

# 12. Confirmation before sensitive actions

The specification says irreversible actions must not happen without customer confirmation.

Pipeline:

```text
Executor
   ↓
change requested
   ↓
ActionPolicy
   ↓
requires confirmation?
       │
      YES
       ↓
"Confirm changing address to ...?"
       ↓
User confirms
       ↓
tool execution
```

Never rely only on:

```text
prompt:
"please remember to ask confirmation"
```

Make it code-level policy.

```python
class ActionDefinition:
    requires_confirmation: bool
```

---

# 13. Conversation State Manager

This is critical for the hidden topic-switch tests.

Example:

```text
User:
Where is my payment?

Bot:
I can check that. What is the order number?

User:
12345.

Bot:
The payment is processed.

User:
And I need to change my address.

Bot:
Sure...
```

State:

```python
class ConversationState(BaseModel):
    conversation_id: str

    active_scenario_id: str | None

    suspended_scenarios: list[str]

    messages: list[Message]

    parameters_by_scenario: dict[str, dict]

    current_language: str

    last_routing_decision: RoutingDecision | None
```

When topic changes:

```text
payment_status
      ↓
suspend
      ↓
change_address
```

And later:

> “Okay, what about the payment?”

System can restore:

```text
payment_status
```

without restarting.

This directly attacks one of the optional requirements: retaining context and returning to an interrupted topic.

---

# 14. Multi-intent utterances

This will probably matter.

Example:

> “I paid yesterday but didn't receive the policy, and I also need to change the address.”

Don't force everything into:

```text
one scenario
```

The router schema can support:

```python
primary_scenario_id

secondary_intents: list[str]
```

Then:

```text
policy_not_received
        ↓
resolve / gather data
        ↓
change_address
```

Or:

```text
primary active scenario
secondary scenario → suspended queue
```

Supervisor sees both.

---

# 15. Language handling

Do **not** put a separate translation service before routing.

Bad:

```text
Kazakh
 ↓
translate to Russian
 ↓
Router
```

That introduces:

```text
latency
translation errors
loss of mixed-language meaning
```

Instead:

```text
RU
KZ
RU+KZ
   │
   ▼
same Router
```

Router outputs:

```json
"language": "mixed"
```

Response Agent uses that as a signal for how to answer.

The case explicitly includes Russian, Kazakh, and one mixed-language utterance in evaluation.

---

# 16. Response Agent

Response Agent does not choose scenarios.

It gets facts:

```text
Scenario
Tool results
Conversation language
Customer question
Required next action
```

Then generates natural speech.

For voice:

```text
short sentences
minimal verbosity
no markdown
one question at a time
no long explanations
```

Example Executor result:

```json
{
  "status": "need_parameter",
  "parameter": "order_id"
}
```

Response Agent:

> “Please tell me your order number.”

---

# 17. Escalation should be mostly deterministic

I actually wouldn't make Escalation a full LLM agent initially.

Policy:

```python
if routing_confidence < threshold:
    clarify()

if clarification_attempts >= 2:
    escalate()

if no_supported_scenario:
    escalate()

if critical_tool_failed:
    escalate()
```

When escalating:

```python
OperatorHandoff(
    conversation_summary=...,
    transcript=...,
    active_scenario=...,
    alternatives=...,
    parameters=...
)
```

That means the human operator receives context.

The specification explicitly says the bot should hand over when it cannot cope.

---

# 18. Orchestrator

This should be the center of `multi_agent`.

Conceptually:

```python
class VoiceRouterOrchestrator:

    async def process_turn(
        self,
        turn: ConversationTurn,
    ) -> TurnResult:

        state = await self.state_store.get(
            turn.conversation_id
        )

        routing = await self.routing_service.route(
            turn=turn,
            state=state,
        )

        await self.trace.record_routing(routing)

        if routing.needs_clarification:
            return await self.clarifier.run(
                routing,
                state,
            )

        if routing.should_escalate:
            return await self.escalation.create_handoff(
                routing,
                state,
            )

        execution = await self.executor.run(
            routing,
            state,
        )

        response = await self.response_agent.run(
            execution,
            state,
        )

        await self.state_store.update(
            state
        )

        return TurnResult(
            routing=routing,
            execution=execution,
            response=response,
        )
```

The orchestrator owns the workflow.

Agents don't randomly call one another.

That keeps the flow debuggable.

---

# 19. Supervisor tracing

This should be built **from day one**, not added at the end.

For each turn:

```json
{
  "trace_id": "tr_123",
  "conversation_id": "call_7392",

  "transcript": "...",

  "language": "mixed",

  "previous_scenario": "payment_status",

  "selected_scenario": "policy_not_received",

  "confidence": 0.94,

  "alternatives": ["...", "..."],

  "rationale": "...",

  "topic_changed": true,

  "timings": {
    "stt_ms": 144,
    "context_ms": 4,
    "routing_ms": 218,
    "tools_ms": 71,
    "response_ms": 122,
    "tts_ms": 95
  }
}
```

Supervisor receives events:

```text
transcription.started
transcription.completed

routing.started
routing.completed

scenario.changed

tool.started
tool.completed

response.started

audio.started

turn.completed
```

The specification explicitly asks the UI to show scenario, rationale, alternatives, and per-stage latency.

OpenAI Agents SDK has built-in tracing for model generations, tools, handoffs, guardrails, and custom spans. We can use that internally while still maintaining our own hackathon-specific trace DTO for the frontend. ([OpenAI][7])

---

# 20. Latency strategy

Your two important targets are:

```text
scenario selection:
500 ms

end of utterance → beginning response:
1.5 sec
```

I would engineer around this budget:

```text
End of speech
     │
     ├─ transcript finalization
     │
     ├─ state assembly      ~ very small
     │
     ├─ FAST ROUTER        ← optimize hardest
     │
     ├─ tool execution
     │
     ├─ response generation
     │
     └─ first audio chunk
```

And record:

```text
p50
p90
p95
max
```

not just average.

---

# 21. Prompt caching

The scenario catalogue is mostly static.

That's ideal for caching.

Prompt:

```text
SYSTEM

ROUTER RULES

40 SCENARIOS
^^^^^^^^^^^^^^
stable prefix

CONVERSATION STATE
USER UTTERANCE
^^^^^^^^^^^^^^
dynamic suffix
```

Current GPT-5.6 APIs support prompt caching and cache prewarming for reusable prompt prefixes. ([OpenAI Developers][8])

So at app startup:

```text
prewarm:
Router instructions
+
scenario catalogue
+
output schema
```

Then requests reuse the same prefix.

This can help both:

```text
latency
cost
```

---

# 22. Project structure

I would make this:

```text
multi_agent/
│
├── __init__.py
├── orchestrator.py
├── config.py
│
├── domain/
│   ├── conversation.py
│   ├── routing.py
│   ├── scenario.py
│   ├── execution.py
│   └── trace.py
│
├── agents/
│   ├── router.py
│   ├── deep_router.py
│   ├── clarifier.py
│   ├── executor.py
│   └── response.py
│
├── routing/
│   ├── service.py
│   ├── policy.py
│   ├── candidate_selector.py
│   └── confidence.py
│
├── scenarios/
│   ├── loader.py
│   └── registry.py
│
├── context/
│   ├── manager.py
│   ├── store.py
│   └── summarizer.py
│
├── tools/
│   ├── knowledge.py
│   ├── customer.py
│   └── actions.py
│
├── providers/
│   └── openai.py
│
├── telephony/
│   ├── base.py
│   └── signalwire.py
│
├── telemetry/
│   ├── trace.py
│   ├── events.py
│   └── latency.py
│
├── guardrails/
│   ├── actions.py
│   └── tools.py
│
└── evals/
    ├── evaluator.py
    ├── metrics.py
    ├── datasets.py
    └── runner.py
```

---

# 23. Evals are mandatory

The starter kit gives you:

```text
dev_utterances.json
evaluate.py
```

and the jury has hidden utterances.

So every routing change should run:

```bash
python -m multi_agent.evals.runner
```

Output:

```text
VOICE ROUTER EVALUATION
──────────────────────────────────

Overall routing accuracy      94.2%

Russian                       96.0%
Kazakh                        92.1%
Mixed language                91.0%

Topic-switch                  93.4%
Boundary scenarios            90.2%

Clarification precision       91.8%
False clarification rate       4.1%

Routing latency:
p50                           218 ms
p90                           391 ms
p95                           468 ms
```

You want a table comparing:

```text
Router v1
Router v2
Fast Router
Fast + Deep
Candidate Retrieval
```

Then your architecture decision is backed by numbers.

---

# 24. Do not put LLM confidence on a pedestal

One subtle point.

A model output like:

```json
"confidence": 0.94
```

doesn't magically mean calibrated 94% probability.

Treat it as a routing signal.

Then calibrate thresholds against `dev_utterances.json`.

For example:

```text
confidence >= 0.90
accuracy = ...

confidence >= 0.80
accuracy = ...
```

Then choose the threshold empirically.

This gives you a much more defensible system.

---

# 25. SignalWire integration

SignalWire becomes an adapter.

```text
SignalWire call
      │
      ▼
SignalWireAdapter
      │
      ▼
VoiceSession
      │
      ▼
Orchestrator
```

Interface:

```python
class VoiceTransport(Protocol):

    async def receive_audio(self): ...

    async def send_audio(self): ...

    async def interrupt(self): ...

    async def close(self): ...
```

Then:

```python
class SignalWireTransport(VoiceTransport):
    ...
```

Browser gets:

```python
class WebRTCTransport(VoiceTransport):
    ...
```

Same brain.

Different transport.

---

# 26. Our final architecture

This is the version I would put directly into README / presentation:

```text
                         ┌─────────────────┐
                         │    CUSTOMER     │
                         └────────┬────────┘
                                  │
                  ┌───────────────┴──────────────┐
                  │                              │
                  ▼                              ▼
            Web Microphone                 Phone Call
              WebRTC                      SignalWire
                  │                              │
                  └───────────────┬──────────────┘
                                  ▼
                         VOICE GATEWAY
                                  │
                         realtime audio
                                  │
                                  ▼
                       SPEECH / VAD / STT
                                  │
                                  ▼
                       CONVERSATION STATE
                                  │
                                  ▼
                    ┌────────────────────────┐
                    │     ROUTING ENGINE     │
                    │                        │
                    │   Fast Router Agent    │
                    │          │             │
                    │     confidence         │
                    │      /       \         │
                    │   high       low       │
                    │    │          │        │
                    │    │     Deep Router   │
                    │    │          │        │
                    │    └────┬─────┘        │
                    └─────────┼──────────────┘
                              ▼
                      ROUTING DECISION
                              │
              ┌───────────────┼─────────────────┐
              │               │                 │
              ▼               ▼                 ▼
          CLARIFY          EXECUTE           ESCALATE
              │               │                 │
              │               ▼                 │
              │        SCENARIO EXECUTOR        │
              │               │                 │
              │        ┌──────┴──────┐          │
              │        ▼             ▼          │
              │    Knowledge      Backend       │
              │      Tools          Tools       │
              │        └──────┬──────┘          │
              │               ▼                 │
              └──────── RESPONSE AGENT ─────────┘
                              │
                              ▼
                        VOICE RESPONSE
                              │
                              ▼
                           CUSTOMER


EVERY STEP
    │
    └─────────────────────► TRACE BUS
                                 │
                                 ▼
                          SUPERVISOR PANEL
```

---

# 27. Implementation order

I would build it in exactly this order:

```text
Phase 1
Domain models
Scenario loader
Scenario registry

        ↓

Phase 2
RouterAgent
40 scenarios → structured decision
CLI testing

        ↓

Phase 3
Evaluator
dev_utterances
accuracy metrics

        ↓

Phase 4
Conversation state
topic switching
multi-intent handling

        ↓

Phase 5
Clarifier
confidence policy
escalation policy

        ↓

Phase 6
Executor
typed tools
knowledge base
mock backend

        ↓

Phase 7
Response Agent

        ↓

Phase 8
Trace collector
latency metrics
supervisor WebSocket

        ↓

Phase 9
Fast Router + Deep Router
benchmark

        ↓

Phase 10
Browser voice

        ↓

Phase 11
SignalWire

        ↓

Phase 12
load test
eval regression
README
Docker one-command launch
```

---

## The main philosophy

The project should **look multi-agent**, but more importantly it should be architecturally justified.

Not:

```text
40 scenarios = 40 agents
```

Instead:

```text
Router Agent
    │
    ├── understands customer intention
    │
    ├── understands context
    │
    ├── detects topic changes
    │
    └── selects scenario
              │
              ▼
       Orchestrator
              │
     ┌────────┼─────────┐
     ▼        ▼         ▼
 Clarifier Executor  Response
```

That gives you a system which is **fast, measurable, explainable, testable, and actually matches what Halyk asks for** rather than adding agents just to say “we use multi-agent AI.” The judging criteria explicitly value technical architecture, consistency between the actual implementation and stated project logic, reproducibility, scalability, and originality.

And current OpenAI tooling fits this design well: Agents SDK provides manager/handoff orchestration, typed outputs, tools and tracing, while Realtime provides the low-latency voice session. ([OpenAI][9])

**This is the architecture I would implement for your `multi_agent/` branch.**

[1]: https://github.com/openai/openai-agents-python/blob/main/docs/realtime/guide.md?utm_source=chatgpt.com "openai-agents-python/docs/realtime/guide.md at main · openai/openai-agents-python · GitHub"
[2]: https://github.com/openai/openai-agents-python/blob/main/docs/agents.md?utm_source=chatgpt.com "openai-agents-python/docs/agents.md at main · openai/openai-agents-python · GitHub"
[3]: https://openai.github.io/openai-agents-python/agents/?utm_source=chatgpt.com "Agents - OpenAI Agents SDK"
[4]: https://developers.openai.com/api/docs/models/gpt-realtime-2.1?utm_source=chatgpt.com "GPT-Realtime-2.1 Model | OpenAI API"
[5]: https://openai.github.io/openai-agents-python/models/?utm_source=chatgpt.com "Models - OpenAI Agents SDK"
[6]: https://openai.github.io/openai-agents-python/tools/?utm_source=chatgpt.com "Tools - OpenAI Agents SDK"
[7]: https://openai.github.io/openai-agents-python/tracing/?utm_source=chatgpt.com "Tracing - OpenAI Agents SDK"
[8]: https://developers.openai.com/api/docs/guides/prompt-caching?utm_source=chatgpt.com "Prompt caching | OpenAI API"
[9]: https://openai.github.io/openai-agents-python/?utm_source=chatgpt.com "OpenAI Agents SDK"
