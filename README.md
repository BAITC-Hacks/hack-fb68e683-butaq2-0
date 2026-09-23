# Butaq V2V

FastAPI service for voice-to-voice conversations. The `v2v/` package is copied
from `hack/v2v/v2v` and installed with this project; `app/main.py` imports it
as a regular Python package.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env  # set V2V_API_KEY in .env before making voice requests
uvicorn app.main:app --reload
```

## Run with Docker Compose

```bash
cp .env.example .env  # set V2V_API_KEY in .env before making voice requests
docker compose up --build
```

Compose builds `dockerfiles/backend.Dockerfile`, forwards port 8000, and loads
the optional `.env` file into the container. Stop with `docker compose down`.

Open http://localhost:8000/docs for the API documentation. `/health` checks
the service; `/v2v/health` reports configured voice models without contacting
OpenAI. The voice endpoints use `V2V_API_KEY` from `.env` (or an exported
`OPENAI_API_KEY`).

```bash
curl -F audio=@question.wav -F session_id=demo \
  http://localhost:8000/v2v/turn -o answer.mp3
```

The included router also exposes transcription, speech synthesis, text chat,
session history, and realtime WebSocket/WebRTC endpoints under `/v2v`.
Environment variables prefixed with `V2V_` customize its models, voice, and
API prefix.

## Test

```bash
pip install -e '.[dev]'
python -m pytest
```
