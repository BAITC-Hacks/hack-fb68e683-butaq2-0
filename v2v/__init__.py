"""v2v -- drop-in voice-to-voice (speech in, speech out) for FastAPI.

Two transports over the same package:

* **Realtime** -- ``gpt-realtime-2.1`` speech-to-speech over one socket, with an
  ephemeral-key endpoint for browsers and a server-side WebSocket proxy.
* **Pipeline** -- ``gpt-transcribe`` -> text model -> ``gpt-4o-mini-tts`` for
  turn-based HTTP calls.

Quick start::

    from fastapi import FastAPI
    from v2v import create_v2v_router, register_exception_handlers

    app = FastAPI()
    app.include_router(create_v2v_router())
    register_exception_handlers(app)
"""

from .audio import AudioError, b64decode, b64encode, chunk, pcm16_to_wav
from .config import V2VSettings, get_settings
from .pipeline import Transcript, Turn, VoicePipeline
from .realtime import (
    DEFAULT_CLIENT_EVENT_ALLOWLIST,
    RealtimeBridge,
    build_session,
)
from .router import (
    create_v2v_router,
    get_bridge,
    get_pipeline,
    register_exception_handlers,
    set_bridge,
    set_pipeline,
)
from .sessions import InMemorySessionStore, Message, Session, SessionStore

__all__ = [
    "AudioError",
    "DEFAULT_CLIENT_EVENT_ALLOWLIST",
    "InMemorySessionStore",
    "Message",
    "RealtimeBridge",
    "Session",
    "SessionStore",
    "Transcript",
    "Turn",
    "V2VSettings",
    "VoicePipeline",
    "b64decode",
    "b64encode",
    "build_session",
    "chunk",
    "create_v2v_router",
    "get_bridge",
    "get_pipeline",
    "get_settings",
    "pcm16_to_wav",
    "register_exception_handlers",
    "set_bridge",
    "set_pipeline",
]

__version__ = "1.0.0"
