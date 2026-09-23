# SignalWire → GPT-Live → Butaq multi-agent

Incoming calls use the same `RouterService`, active database catalogue, Router,
Resolution and read-only tools as browser conversations. Phone speech uses
GPT-Live (`gpt-live-1`); business reasoning keeps the configured Terra model.

```text
SignalWire number → signed cXML voice webhook → SIP/TLS → GPT-Live
                                                           ↕ sideband
                                                    ButaqPhoneRuntime
                                                           ↓
                                                 shared RouterService
                                                 Router → Resolution
                                                           ↓
                                                verified commentary → speech
```

The application accepts inbound calls only. Operator transfer and outbound calls
are not implemented. Asking for an operator remains a handoff decision, with an
honest spoken explanation; it does not dial anybody. Caller ID does not authorize
record access. The current tools use synthetic insurance data.

## Configure the existing number

Public origin for this deployment: `https://owlpeer.com`.

- SignalWire Voice POST: `https://owlpeer.com/telephony/webhooks/signalwire/voice`
- SignalWire Status POST: `https://owlpeer.com/telephony/webhooks/signalwire/status`
- OpenAI webhook: `https://owlpeer.com/telephony/webhooks/openai`
- OpenAI event: `live.transport.incoming`

These URLs require the updated app and Caddy config on the server serving the
public domain. An HTML response from `/telephony/health` means the old frontend
fallback still handles the path; a local restart alone does not update that server.

1. Set the backend environment values below, using the same public HTTPS origin
   for signatures and callbacks. An ngrok tunnel must forward to this backend
   (`localhost:8000`) or to its Caddy proxy (`localhost:3000`). Keep that tunnel
   running; if its hostname changes, update both `.env` and provider callbacks.
2. In SignalWire, select **Compatibility/cXML** handling for the number:
   - Voice URL: `PUBLIC_BASE_URL/telephony/webhooks/signalwire/voice`, **POST**.
   - Status callback: `PUBLIC_BASE_URL/telephony/webhooks/signalwire/status`, **POST**.
   The existing hack-tools `/telephony/webhooks/twilio/voice` and `/status` paths
   are supported aliases. They use the same SignalWire signing-key verification.
   An already configured voice URL on that path does not need to change.
3. In the OpenAI project owning the SIP destination, configure:
   - Webhook URL: `PUBLIC_BASE_URL/telephony/webhooks/openai`.
   - Event: **`live.transport.incoming`**.
   - Copy that webhook's signing secret into `TELEPHONY_OPENAI_WEBHOOK_SECRET`.
   A `realtime.call.incoming` subscription is not the Live callback contract.
4. Set `TELEPHONY_ENABLED=true`, then recreate the backend. Caddy proxies nested
   `/telephony/*` API endpoints, but serves `/telephony` and `/telephony/` from
   the static frontend. Rebuild frontend if upgrading an older image:
   `docker compose up -d --build --no-deps frontend`.
5. Check `GET PUBLIC_BASE_URL/telephony/health`: it must return
   `{"enabled":true}` as JSON, not frontend HTML. This proves initialization,
   not carrier/SIP/audio readiness. Call the existing number to verify audio.

The voice webhook returns the SIP destination automatically; do not replace it
with a direct, uncorrelated SIP route. SignalWire uses
`sip:PROJECT_ID@sip.api.openai.com;transport=tls`, plus per-call linkage headers.
The Twilio-specific `sips:` convention caused incompatible SignalWire parsing.
OpenAI Live requires SRTP media as well as TLS signaling; actual negotiation and
Live SIP availability for the project must be confirmed with a carrier call.

## Backend environment

| Variable | Value/source |
| --- | --- |
| `TELEPHONY_ENABLED` | `true` to initialize the phone adapter |
| `TELEPHONY_PUBLIC_BASE_URL` | Exact public HTTPS origin, without a path |
| `TELEPHONY_SIGNALWIRE_SPACE` | `your-space.signalwire.com` |
| `TELEPHONY_SIGNALWIRE_PROJECT_ID` | SignalWire project/account ID |
| `TELEPHONY_SIGNALWIRE_API_TOKEN` | API token with Voice permissions |
| `TELEPHONY_SIGNALWIRE_SIGNING_KEY` | Separate project callback Signing Key |
| `TELEPHONY_OPENAI_PROJECT_ID` | OpenAI project targeted by SIP |
| `TELEPHONY_OPENAI_WEBHOOK_SECRET` | Secret for the OpenAI webhook above |
| `TELEPHONY_OPENAI_API_KEY` | Optional phone project key; defaults to `V2V_API_KEY` |
| `TELEPHONY_LIVE_MODEL` / `TELEPHONY_VOICE` | `gpt-live-1` / `marin` |

For the existing hack-tools SignalWire setup, map `TELEPHONY_TWILIO_ACCOUNT_SID`,
`TELEPHONY_TWILIO_AUTH_TOKEN` and `TELEPHONY_TWILIO_SIGNING_KEY` to the three
SignalWire settings above; the host in `TELEPHONY_TWILIO_API_BASE_URL` supplies
`TELEPHONY_SIGNALWIRE_SPACE`. Do not copy the generic phone backend prompt or
backend-model setting: Butaq already supplies its own multi-agent runtime.
Credentials stay in the ignored backend `.env`; they are never browser settings.

```bash
docker compose up -d --build backend frontend
curl http://localhost:8000/telephony/health
```

## What to check in a call

Say each utterance separately and wait for the voice reply:

1. «Какие виды страхования у вас есть?» — catalogue routing and native speech.
2. «Когда заканчивается полис демо П один ноль ноль один?» — demo policy lookup.
3. «А он сейчас активен?» — previous policy context without repeating its ID.
4. «Осы полис туралы қазақша айтып берші.» — language change with context.
5. Interrupt a long answer with «Стоп, теперь проверь возврат DEMO-R-4001».
6. Hang up. The call must become terminal and release the active agent work.

Administrator endpoints require the existing `X-Admin-Token` or admin cookie:

- `GET /telephony/calls`
- `GET /telephony/calls/{call_id}`
- `GET /telephony/calls/{call_id}/trace`
- `POST /telephony/calls/{call_id}/hangup`

Trace includes scenario, language, backend reply, routing/resolution timings and
errors. Generated replies are not proof of what the caller heard. Native Live
has no per-answer playback-completed event; STT/TTS timing fields are zero for
unused separate stages, and `speech_end_to_audio_ms` remains unmeasured.

If the call fails, inspect `docker compose logs --tail=100 backend` and provider
webhook delivery logs. A 403 indicates rejected signature/account verification;
503 means the phone adapter is disabled. A SIP start failure records
`live_start_failed` and logs the exception class without provider secrets.
Check the exact public URL, webhook event, correct project/key, then SIP media
negotiation. Never disable signature checks to make a callback pass.

## Runtime boundaries and verification

- Phone and browser share decision logic; phone sessions use reserved `phone:` IDs.
- A 300 ms cancellable grace lets late transcript fragments arrive before taking
  the delegation context snapshot. It is a heuristic, not an utterance-final API.
- Observed voice captions supply context to both agents; generated replies are
  not marked as heard. New delegations cancel unfinished old work.
- Signed callbacks and a random per-call SIP capability prevent arbitrary session
  linkage. Duplicate incoming callbacks cannot accept/start the same call twice.
- Defaults: 5 simultaneous calls, 600 seconds, 50 backend turns per call.
- One backend process; active calls are in memory. Restart drops call state.
- No audio recording. Terminal traces remain in memory for 15 minutes, up to
  100 completed calls. Restart clears them. Browser public APIs cannot read them.

Offline verification: **32 app phone tests and 90 library tests passed**, including
signed carrier/OpenAI HTTP callbacks → SIP correlation → shared Router → verified
commentary → hangup cleanup. Those tests use fake carrier/media/model providers;
they do not prove a real telephone call or SRTP audio works.

```bash
TELEPHONY_ENABLED=false python -m pytest tests/test_phone.py -q
# Run separately: the library has its own tests package.
(cd telephony && TELEPHONY_ENABLED=false PYTHONPATH=. python -m pytest -q)
```

Provider contracts:
[OpenAI Live SIP](https://developers.openai.com/api/docs/guides/voice-sip?api=live),
[Live delegation](https://developers.openai.com/api/docs/guides/live-delegation),
[SignalWire SIP cXML](https://signalwire.com/docs/compatibility-api/cxml/reference/voice/sip),
[SignalWire webhook verification](https://signalwire.com/docs/compatibility-api/guides/webhook-security).
