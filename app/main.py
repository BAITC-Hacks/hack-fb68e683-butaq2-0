"""FastAPI entry point for the voice-to-voice service."""

from fastapi import FastAPI

from v2v import create_v2v_router, register_exception_handlers

app = FastAPI(title="Butaq V2V")
app.include_router(create_v2v_router())
register_exception_handlers(app)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
