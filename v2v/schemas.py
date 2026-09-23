"""Public request/response models for the v2v HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import SpeechFormat


class _Request(BaseModel):
    """Base for request bodies: an unknown field is a client bug, not a default.

    Without this, ``{"response_format": "wav"}`` instead of ``{"format": "wav"}``
    is silently dropped and the caller gets mp3 with a 200.
    """

    model_config = ConfigDict(extra="forbid")


class TranscriptionResponse(BaseModel):
    text: str
    model: str
    duration_ms: int | None = None
    language: str | None = None


class SpeakRequest(_Request):
    text: str = Field(min_length=1, max_length=8000)
    voice: str | None = Field(default=None, description="alloy, ash, coral, marin, cedar, ...")
    format: SpeechFormat | None = None
    instructions: str | None = Field(
        default=None, description="Delivery directions: tone, pace, accent, emotion"
    )
    model: str | None = None


class TurnResponse(BaseModel):
    """One voice turn, with audio inlined as base64 (see /turn for a stream)."""

    session_id: str
    transcript: str
    reply: str
    audio_base64: str
    audio_format: str
    content_type: str


class ChatTurnRequest(_Request):
    session_id: str = Field(default="default")
    text: str
    instructions: str | None = None
    model: str | None = None


class ChatTurnResponse(BaseModel):
    session_id: str
    reply: str
    model: str


class ClientSecretRequest(_Request):
    """Mint an ephemeral key so a browser can talk to OpenAI directly."""

    model: str | None = None
    voice: str | None = None
    instructions: str | None = None
    ttl_seconds: int | None = Field(default=None, ge=10, le=7200)


class ClientSecretResponse(BaseModel):
    value: str = Field(description="Ephemeral key (ek_...); safe to send to the browser")
    expires_at: int | None = None
    model: str
    session: dict


class SessionMessage(BaseModel):
    role: str
    content: str
    created_at: str


class SessionResponse(BaseModel):
    session_id: str
    messages: list[SessionMessage]
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class DeleteResponse(BaseModel):
    session_id: str
    deleted: bool


class HealthResponse(BaseModel):
    status: Literal["ok"]
    realtime_model: str
    transcribe_model: str
    text_model: str
    tts_model: str
    voice: str
