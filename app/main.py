"""FastAPI entry point for the voice-to-voice service."""

import asyncio
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from telephony.router import register_exception_handlers as register_phone_handlers

from v2v import register_exception_handlers

from .api.admin_auth import auth_router
from .api.routes import router
from .phone.api import router as phone_router
from .phone.settings import PhoneSettings


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .api.dependencies import get_service

    # Production/Docker provides DATABASE_URL. Construct the shared service during
    # startup so an empty database is atomically seeded before the first request.
    # Lightweight offline tests may omit DATABASE_URL and still exercise /health.
    service_factory = app.dependency_overrides.get(get_service)
    service = service_factory() if service_factory else None
    if service is None and os.getenv("DATABASE_URL"):
        service = get_service()
    settings = PhoneSettings()
    app.state.phone = None
    cleanup = None
    if settings.enabled:
        from v2v.config import get_settings

        from .phone.service import build_phone

        settings.validate_enabled(get_settings().api_key)
        if service is None:
            service = get_service()
        app.state.phone = build_phone(settings, service)

        async def prune_calls():
            while True:
                await asyncio.sleep(10)
                await app.state.phone.prune()

        cleanup = asyncio.create_task(prune_calls(), name="phone-cleanup")
    try:
        yield
    finally:
        if cleanup:
            cleanup.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup
        if app.state.phone:
            await app.state.phone.aclose()
            app.state.phone = None


app = FastAPI(title="Butaq V2V", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "FRONTEND_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
        ).split(",")
        if origin.strip()
    ],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Admin-Token"],
    allow_credentials=True,
)
app.include_router(router)
app.include_router(auth_router)
app.include_router(phone_router)
register_exception_handlers(app)
register_phone_handlers(app)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
