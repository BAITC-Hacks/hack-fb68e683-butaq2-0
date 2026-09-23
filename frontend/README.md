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

The voice workspace is at `/voice/`, linked as **Voice** in the navbar. The homepage keeps the original video landing layout. Voice checks the scenario catalogue before opening the microphone. If empty, an administrator can expand **Set up scenarios**, select the starter-kit JSON files and authorize import with `ROUTER_ADMIN_TOKEN`. This is a separate server-side admin credential, not `V2V_API_KEY`; neither value is bundled into the site. After import the page rechecks readiness and enables the conversation. The repository does not include the starter-kit catalogue.

Click **Talk to Butaq**, allow microphone access, speak and pause. After 850 ms of silence the browser posts a complete MediaRecorder recording to `/router/voice`, plays the returned audio, then listens for the next turn. Silent recordings are discarded. A turn is capped at 20 seconds. This is turn-based voice interaction; the microphone is not recorded during the reply, and interrupting the robot mid-reply is not supported. **End conversation** stops tracks, playback and any pending request; **New conversation** also resets the session ID and trace. The orb moves only on detected microphone speech and respects reduced-motion preferences.

Text fallback calls `/router/text` with `synthesize: true`. Both paths use the existing backend LLM routing and v2v pipeline; no browser intent classifier or fabricated timings are used. The frontend displays the server transcript, answer, scenario, rationale, alternatives and timings. A `handoff` decision ends microphone capture but does not connect to a real operator.

For local development, run the backend at `http://localhost:8000`; its CORS defaults allow the Next dev origin. Production uses relative URLs through Caddy. Optional `NEXT_PUBLIC_API_BASE_URL` overrides the API origin at build time. Backend credentials and a loaded scenario catalogue are required; backend failures are shown in the interface. Microphone access requires HTTPS or localhost.

## UI structure

Tailwind CSS and TypeScript are already installed. `components.json` configures shadcn aliases. Reusable components live in `components/ui` (the supplied OGL orb and shadcn Button); call UI lives in `components/voice`, lifecycle code in `hooks/use-voice-session.ts`, styles in `app/globals.css`. Keeping primitives in `components/ui` lets shadcn registry imports resolve consistently. No extra provider is needed. The orb receives the call's shared audio-level ref so only one microphone stream is opened.

The original videos load from CloudFront and Mux; Google fonts are downloaded by Next.js. These assets require network access.

See ATTRIBUTION.md for original template attribution.
