# Butaq — Voice Router frontend

Original React template running in Next.js with Tailwind CSS, including the video hero, Inter / Instrument Serif fonts and glass buttons.

## Run

```bash
npm install
npm run dev
```

Open http://localhost:3000. The homepage renders `components/templates/velorah/velorah.tsx`.

Production: run `docker compose up --build` from the repository root. Next.js exports to `out/`, which Caddy serves along with a same-origin `/router/*` proxy to the backend. Type check: `npm run typecheck`.

## Voice conversation

The voice workspace is at `/voice/`, linked as **Voice** in the navbar. The homepage keeps the original video landing layout. The backend automatically seeds an empty database with 40 synthetic insurance scenarios, company facts and linked mock records. Voice checks readiness before opening the microphone; there is no upload form or admin token field. Existing imported catalogues are preserved. The bundled catalogue contains synthetic demo data and is not the official starter kit.

Click **Talk to Butaq**, allow microphone access, speak and pause. AudioWorklet
captures mono PCM and resamples it to 24 kHz. The browser streams 20 ms frames over
`/router/stream`, retaining 200 ms of pre-roll. After 500 ms of silence it commits
the utterance; sustained speech is capped at 20 seconds. Partial transcription
appears while speaking. Only final transcripts enter Router → Resolution.

The microphone stays active during a reply. Sustained new speech or **Interrupt**
stops queued playback immediately and invalidates the old server turn. **Send now**
commits an in-progress utterance. **End conversation** closes capture, playback and
the socket; **New conversation** also resets the session. Browser echo cancellation
is requested, but microphone/speaker feedback still needs testing on real devices.

The server sends a validated routing decision, then a complete validated reply,
then PCM audio chunks as synthesis produces them. The browser schedules each chunk
without waiting for the final audio file. It acknowledges delivery only after the
output timeline drains; cancelled replies never receive a completion acknowledgement.
First text, first server audio and playback onset are separate metrics; browsers
without output timestamps explicitly label playback time as estimated.

Typed messages use the same streaming path. After a streaming failure the user can
explicitly send a typed message through the existing buffered HTTP fallback. No
failed request is automatically resent. A `handoff` ends capture but does not connect
a real operator. No intent classifier runs in the browser.

Validation: `npm run typecheck`, `npm run build`, and `npm run test:voice` (Node 22+).
The deterministic tests cover resampling, VAD, chunk playback, interruption and stale
messages; these do not replace microphone/listening checks on target devices.

For local development, run the backend at `http://localhost:8000`; its CORS defaults allow the Next dev origin. Production uses relative URLs through Caddy. Optional `NEXT_PUBLIC_API_BASE_URL` overrides the API origin at build time. The backend `FRONTEND_ORIGINS` must already include the exact browser origin for WebSocket access. Backend credentials and a loaded scenario catalogue are required; backend failures are shown in the interface. Microphone access requires HTTPS or localhost.

## UI structure

Tailwind CSS and TypeScript are already installed. `components.json` configures shadcn aliases. Reusable components live in `components/ui` (the supplied OGL orb and shadcn Button); call UI lives in `components/voice`, lifecycle code in `hooks/use-voice-session.ts`, styles in `app/globals.css`. Keeping primitives in `components/ui` lets shadcn registry imports resolve consistently. No extra provider is needed. The orb receives the call's shared audio-level ref so only one microphone stream is opened.

The original videos load from CloudFront and Mux; Google fonts are downloaded by Next.js. These assets require network access.

See ATTRIBUTION.md for original template attribution.
