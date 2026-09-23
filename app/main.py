"""FastAPI entry point for the voice-to-voice service."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from v2v import register_exception_handlers

from .api.routes import router
from .api.admin_auth import auth_router

app = FastAPI(title="Butaq V2V")
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
register_exception_handlers(app)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
