# telephony

Drop-in phone calls for FastAPI: direct OpenAI SIP handles inbound calls,
Twilio provides the carrier abstraction for provider-owned calls, and
`gpt-live-1` holds the conversation while your application keeps reasoning,
tools and policy through client delegation.

```python
from fastapi import Depends, FastAPI
from telephony import (
    OpenAILiveTelephony,
    Telephony,
    create_telephony_router,
    register_exception_handlers,
)
from telephony.twilio import TwilioProvider

telephony = Telephony(
    provider=TwilioProvider(),
    live_calls=OpenAILiveTelephony(),
)
app = FastAPI()
app.include_router(
    create_telephony_router(telephony=telephony, dependencies=[Depends(authenticate_api_user)])
)
register_exception_handlers(app)
```

## Install

```bash
pip install -e ".[twilio,dev]"
pytest
```

## HTTP API

Prefix defaults to `/telephony`.

| Endpoint | What it does |
| --- | --- |
| `POST /calls` | Start an outbound call. `Idempotency-Key` header required; a repeat returns the original call. `202`. |
| `GET /calls/{id}` | State, direction, masked numbers, timestamps, usage, terminal reason. |
| `GET /calls` | Cursor pagination, filters on `status`, `direction`, `scenario`. |
| `POST /calls/{id}/transfer` | Transfer to an allowlisted `tel:`/`sip:` target. |
| `POST /calls/{id}/hangup` | Idempotent hangup. |
| `POST /calls/{id}/cancel` | Cancel a call that has not connected. |
| `GET /health/live`, `/health/ready` | Liveness, and configuration/dependency readiness. |
| `POST /webhooks/twilio/voice` | Inbound call: routing decision, then TwiML. |
| `POST /webhooks/twilio/status` | ringing/answered/completed/busy/failed/no-answer. |
| `POST /webhooks/openai` | Verified `live.transport.incoming` SIP decision (accept/reject). |

Management endpoints take the host app's auth dependencies. Webhook endpoints
deliberately do **not**. Twilio signatures are checked over the raw body and
the public URL in `TELEPHONY_PUBLIC_BASE_URL`; OpenAI signatures are checked
over the raw body with `TELEPHONY_OPENAI_WEBHOOK_SECRET`. Verification happens
before application processing. Twilio events are deduplicated by
`(CallSid, CallStatus)` and OpenAI events by `webhook-id`.

Responses carry masked numbers (`+1555***2222`), never credentials, SIP headers
or raw provider payloads.

## Architecture

```
domain.py       Call, CallState, legal transitions, E.164, masking, allowlists
ports.py        Protocol interfaces: provider, live, repository, inbox, policies
application.py  Telephony -- use cases + the DI container
agents.py       AgentRuntime, update validation, CallableAgentRuntime
delegation.py   Graph-driven delegation execution and cancellation
openai_live.py  GPT-Live session builder, SIP call control, sideband adapter
session_graph.py Typed routing/state graph for Live server events
memory.py       In-memory adapters (default profile) and FakeProvider
twilio.py       TwilioProvider: REST, TwiML, signature verification
router.py       FastAPI router, webhook controllers, exception handlers
schemas.py      Pydantic DTOs
```

The domain imports nothing from FastAPI, Twilio, OpenAI, Redis or SQLAlchemy.
Every dependency is a constructor argument:

```python
telephony = Telephony(
    settings=settings,
    provider=TwilioProvider(settings),
    live_calls=OpenAILiveTelephony(settings),
    agent=CallableAgentRuntime(my_tool),
    calls=my_repository,      # any CallRepository
    inbox=my_event_inbox,     # any EventInbox
    routing=my_routing_policy,
)
```

## Call lifecycle

```
requested ─> dialing ─> ringing ─> connected ─> ending ─> completed
          └> canceled / failed

incoming  ─> accepted ─> ringing/connected ...
          └> rejected
```

`ending` and the terminal states are reachable from any live state — either
side can hang up at any moment. A late or duplicated webhook never rolls the
call backwards, and a terminal state is immutable.

## Backend runtimes

`TELEPHONY_AGENT_RUNTIME` picks what does the thinking behind GPT-Live:
`responses` (default), `langchain`, `langgraph` or `callable`. All three model
runtimes share one `ToolRegistry`, so the scenario allowlist, the per-call
quota and the timeouts apply identically.

`responses.py` talks to `/v1/responses` directly. The LangChain and LangGraph
adapters take *your* runnable, and that runnable must reach the same endpoint:

```python
ChatOpenAI(model=settings.backend_model, use_responses_api=True)
```

Without `use_responses_api=True` a reasoning model such as `gpt-5.6-luna`
rejects the request — function tools combined with `reasoning_effort` are not
supported on the legacy `/v1/chat/completions` endpoint.

One LangGraph thread is one delegation (`thread_id = "{call_id}/{delegation_id}"`).
`checkpoint_ns` is reserved by LangGraph for subgraph nesting and does not fork
a thread, so it cannot be used to isolate delegations; call-long context is
rebuilt from the transcript on every run instead.

## Privacy defaults

No transcript and no audio are persisted: the Live session is created with
`store=False` and the in-memory context is dropped as soon as the call reaches
a terminal state. Recording is not implemented.

`TELEPHONY_DISCLOSURE` is spoken before the Twilio SIP bridge. Calls arriving
straight over OpenAI SIP have no Twilio leg, so put their disclosure in
`TELEPHONY_INSTRUCTIONS` instead.

## Local setup

1. Buy a Twilio number and set `TELEPHONY_TWILIO_ACCOUNT_SID`, `_AUTH_TOKEN`,
   `_FROM_NUMBER` (see `.env.example`).
2. Expose the app over HTTPS: `ngrok http 8000` (or `cloudflared tunnel`), and
   set `TELEPHONY_PUBLIC_BASE_URL` to that hostname. The Twilio signature is
   computed over the exact URL Twilio requests, so this must match.
3. Point the number's Voice webhook at
   `https://<tunnel>/telephony/webhooks/twilio/voice` (HTTP POST). Outbound
   calls register their status callback automatically.
4. `uvicorn example_app:app --reload`.

### Direct OpenAI SIP inbound calls

1. Configure `/telephony/webhooks/openai` in the OpenAI project webhooks and
   subscribe to `live.transport.incoming`. Set its signing secret as
   `TELEPHONY_OPENAI_WEBHOOK_SECRET`.
2. Route the SIP trunk to
   `sip:$PROJECT_ID@sip.api.openai.com;transport=tls` (or the documented EU
   endpoint when applicable). The provider must support TLS signaling and
   SRTP media.
3. Wire `OpenAILiveTelephony(settings)` as `live_calls`. The handler verifies
   the raw webhook, deduplicates `webhook-id`, applies the inbound routing
   policy, and calls the Live accept or reject endpoint exactly once.

SIP headers are caller-controlled and never used for authorization. Only
phone-like From/To values are retained; the original header collection is
discarded. The Redis worker that attaches the sideband and runs delegated
backend tasks is the next sprint.

## Implementation progress

Sprints 4A-4F provide the GPT-Live client-delegation session builder, sideband
transport adapter, a framework-independent session graph, and supervised agent
execution with validated append events and cancellation. The coordinator now
requests graceful close and reports final usage as confirmed only after the
terminal `session.closed` event; earlier transport termination is unconfirmed.
The direct SIP ingress verifies OpenAI webhooks and performs idempotent Live
accept/reject decisions while retaining the session ID for worker attachment.
Phases 5-10 follow in `PLAN.md`.
