# Butaq — Voice Router frontend

Next.js, React, TypeScript and Tailwind CSS. The homepage preserves the Velorah video landing; `/voice/` is the conversation workspace, `/admin/` edits the catalogue and settings.

## Run

```bash
npm ci
npm run dev
```

Open http://localhost:3000/voice/ with the backend at http://localhost:8000.
Production: `make up` from the repository root. Next exports `out/`; Caddy serves it and proxies `/router/*` to the backend. An optional build-time `NEXT_PUBLIC_API_BASE_URL` overrides the API origin. Backend `FRONTEND_ORIGINS` must include the exact browser origin. Microphone access requires HTTPS or localhost.

## Current voice path

- **Поговорить с Butaq** opens OpenAI Live over WebRTC. `/router/live` is the WebSocket for setup, captions, delegated Router → Resolution tasks and traces. The provider handles turn-taking; the old local VAD/“Send now” flow is not the primary voice UI.
- You can interrupt, mute/unmute the microphone without reconnecting, finish the call, or reset the conversation. The orb reads the existing call's audio analyser; it never opens a second microphone.
- Typed messages during a call use Live and wait for a server acceptance acknowledgement. Acceptance means queued, not answered. Timeout/rejection keeps the draft, and no failed request is automatically resent.
- Outside a call, text uses `/router/stream` with streamed TTS. A failed streamed request exposes an explicit HTTP fallback. The PCM/AudioWorklet implementation remains available to legacy API consumers but is not used by the main voice button.
- UI labels are RU/KK. Spoken response language is selected from the user's utterance, not the UI toggle.

The catalogue banner reports the **active backend data**, not provider health. A fresh DB initially seeds the author demo. For jury testing, explicitly import all five official files using `make import-official` (replaces catalogue data) or `/admin/import/`. See the root README.

## Trace and measurements

Select any of the last 10 processed turns to inspect its scenario, short rationale, alternatives, extracted parameters, language, pending topics and measured backend times. Download JSON includes the same turns without audio blobs. Do not use real personal data.

Live captions and the validated backend answer are separate: the provider may paraphrase commentary. Separate Live STT/TTS times and end-of-speech → first-audio latency are currently **unmeasured**, never reported as zero. Backend total is not voice end-to-end latency. Legacy estimated playback times are labelled as estimates.

The system is read-only: `handoff` means human help is needed, not that an operator was connected. No policy changes or payments are executed.

## Validation and structure

```bash
npm run test:voice
npm run typecheck
npm run build
```

Node 22+ tests cover connection lifecycle, typed acknowledgements, cleanup, mute, VAD/resampling, interruption, playback and exported measurements. They do not replace physical microphone/listening or browser layout checks; use [the jury checklist](../docs/jury-checklist.md).

`components.json` configures shadcn aliases. Reusable primitives live in `components/ui`; conversation UI in `components/voice`; lifecycle in `hooks/use-voice-session.ts`; styles in `app/globals.css`. No extra provider is required.

CloudFront/Mux videos and Google fonts require network access. See `ATTRIBUTION.md` for template attribution.
