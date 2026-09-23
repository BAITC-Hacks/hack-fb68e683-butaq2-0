# Butaq inbound telephony — prepared, not deployed

The backend now connects the vendored `telephony` package to the **same Router →
Resolution agents and active catalogue** used by `/router/text` and `/router/voice`.
No number has been purchased; no live carrier/audio call has been verified.
The official insurance dataset is not imported by this change.

```text
US +1 number (TODO) → SignalWire cXML → OpenAI SIP / GPT-Live
                                            ↓ client delegation
                                  ButaqPhoneRuntime
                                            ↓
                                  RouterService.turn
                                            ↓
                                Router → Resolution
                                            ↓
                              verified reply + trace
```

GPT-Live supplies phone transcription/speech. The backend does not run browser
STT/TTS again. The voice prompt requires delegation for every business request;
greetings can be answered directly. Live delegation timing is model-controlled,
not an exact end-of-utterance signal: actual coverage must be checked on real calls.
The adapter uses new, deduplicated user transcript fragments ending at or before
the delegation timestamp. A newer delegation cancels unfinished older work and
reuses its uncommitted speech. Completed turns remain in the shared dialogue state.

## Current scope and controls

- Inbound only. **No outbound, transfer, cancellation API, or operator queue.**
- `handoff` remains visible in the trace, but the spoken reply honestly states
  that specialist connection is unavailable. It does not dial or notify anyone.
- All business tools remain the existing read-only tools. Caller ID is not identity verification.
- cXML callbacks require the SignalWire **signing key**, not the API token.
  OpenAI callbacks require its separate webhook secret. Signature validation uses
  the configured public HTTPS origin, never caller-supplied forwarded headers.
- The SIP bridge includes a per-call random capability. A call ID by itself cannot
  link a session. Direct, uncorrelated OpenAI SIP invitations are rejected.
- Call IDs, carrier IDs and Live session IDs remain correlated. Duplicate callback
  delivery cannot start another agent runner. API responses mask phone numbers.
- Hangup cancels agent work and removes its dialogue state. A carrier termination
  failure is marked unconfirmed/failed, not a successful hangup.
- One API process only. Restart loses call state; no Redis/Postgres phone persistence.
- No audio recording. Completed turn traces are held **in memory** for 15 minutes,
  at most 100 completed calls (periodic cleanup every 10 seconds). No durable transcript storage.
- Defaults: 5 concurrent calls, 600 seconds per call, 50 routing turns per call,
  20-second phone delegation timeout. The runner and cleanup loop enforce duration limits.

## Configuration — leave disabled until number setup

`TELEPHONY_ENABLED=false` is the default. No carrier credentials are required while
disabled. `/telephony/health` reports `{"enabled": false}`; webhooks return 503.
This does not disable browser voice or text.

For a later deployment, supply these variables **on the backend only**:

| Variable | Purpose |
| --- | --- |
| `TELEPHONY_ENABLED` | Set `true` only after configuration |
| `TELEPHONY_PUBLIC_BASE_URL` | Exact public HTTPS origin, e.g. `https://owlpeer.com` |
| `TELEPHONY_SIGNALWIRE_SPACE` | Hostname such as `your-space.signalwire.com` |
| `TELEPHONY_SIGNALWIRE_PROJECT_ID` | SignalWire project/account ID |
| `TELEPHONY_SIGNALWIRE_API_TOKEN` | API token with Voice permissions |
| `TELEPHONY_SIGNALWIRE_SIGNING_KEY` | Separate callback signing key |
| `TELEPHONY_OPENAI_PROJECT_ID` | OpenAI SIP destination project |
| `TELEPHONY_OPENAI_WEBHOOK_SECRET` | Secret from the OpenAI webhook configuration |
| `TELEPHONY_OPENAI_API_KEY` | Optional override; otherwise use existing `V2V_API_KEY` |
| `TELEPHONY_LIVE_MODEL` | Defaults to the library's `gpt-live-1`; project access unverified |
| `ROUTER_ADMIN_TOKEN` | Existing administrator authentication |

Limits can be overridden using `TELEPHONY_MAX_CONCURRENT_CALLS`,
`TELEPHONY_MAX_CALL_SECONDS`, `TELEPHONY_MAX_TURNS_PER_CALL`,
`TELEPHONY_DELEGATION_TIMEOUT_SECONDS`, `TELEPHONY_RETENTION_SECONDS`, and
`TELEPHONY_MAX_RETAINED_CALLS`. Setting retention to zero removes terminal traces
on the next cleanup pass. No credentials or production configuration were edited.

## HTTP interface

Unauthenticated **but signature-verified** provider callbacks:

- `POST /telephony/webhooks/signalwire/voice` — form-encoded cXML answer callback.
- `POST /telephony/webhooks/signalwire/status` — form-encoded call-status callback.
- `POST /telephony/webhooks/openai` — signed `live.transport.incoming` SIP invitation.

Administrator endpoints use the existing `X-Admin-Token` or admin session cookie
(including existing Origin protection for cookie-authenticated mutations):

- `GET /telephony/calls`
- `GET /telephony/calls/{call_id}`
- `GET /telephony/calls/{call_id}/trace`
- `POST /telephony/calls/{call_id}/hangup`

Trace includes each processed user request, the backend reply, scenario ID,
rationale, alternatives, language, routing/resolution times, and errors. Replies
are backend-approved text, not an exact transcript of GPT-Live's paraphrased audio.
`speech_end_to_audio_ms` is `null`: no first-audio latency has been measured.
`TurnResult` STT/TTS values remain zero because those separate stages did not run;
do not present them as measured phone recognition/synthesis latencies.
There is no new supervisor frontend in this slice; traces are accessible via API/docs.
The public browser endpoints reject the reserved `phone:` conversation namespace.

## Install and run offline tests

Use Python 3.12 and the existing project environment:

```bash
pip install -e '.[dev,telephony]'
python -m pytest -q
```

Run the vendored library's suite separately because it also defines a `tests` package:

```bash
cd telephony
PYTHONPATH=. python -m pytest -q
```

Tests use fake carriers, signed synthetic webhooks and deterministic agent outputs.
They do not buy numbers, dial, or make paid model requests. PostgreSQL integration
tests still need the existing `TEST_DATABASE_URL` test database configuration.
The backend Dockerfile now includes the package and its optional SDK dependencies.

## Deployment TODO — requires separate approval and a purchased number

1. Buy a SignalWire +1 number and select **Compatibility/cXML** handling (not SWML).
2. Configure the voice and status callback URLs above, using POST.
3. Configure OpenAI Live SIP incoming webhooks and verify project/model access.
4. Add `/telephony` and `/telephony/*` to the backend path matcher in Caddy;
   pass the configuration above to the backend container. Production Caddy/Compose
   files were deliberately left unchanged. Without this proxy change, the domain
   will serve frontend HTML for telephone callback paths.
5. Confirm HTTPS reachability, the exact callback origin/signatures, and the generated
   `sips:` bridge's TLS/SRTP compatibility with the purchased number/carrier setup.
6. Enable telephony and place an explicitly authorized inbound test call. Verify RU,
   KK, mixed speech, topic switching, interruption, traces, and actual disconnect.
   Measure routing and first-audio latency independently. Unit tests cannot prove audio works.

Human/operator transfer remains a separate future task.

References used for the adapters:
[OpenAI client delegation](https://developers.openai.com/api/docs/guides/live-delegation),
[OpenAI SIP](https://developers.openai.com/api/docs/guides/voice-sip),
[SignalWire webhook validation](https://signalwire.com/docs/server-sdks/reference/python/core/security/validate-webhook-signature),
[SignalWire Compatibility calls](https://signalwire.com/docs/compatibility-api/rest/calls/list-all-calls).
