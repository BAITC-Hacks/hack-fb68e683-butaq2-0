"""FastAPI router for v2v.

    from v2v import create_v2v_router
    app.include_router(create_v2v_router())
"""

import base64
import contextlib
from typing import Annotated
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    status,
)
from fastapi.responses import StreamingResponse
from openai import OpenAIError

from .audio import CONTENT_TYPE_BY_FORMAT, AudioError
from .config import V2VSettings, get_settings
from .pipeline import VoicePipeline
from .realtime import RealtimeBridge
from .schemas import (
    ChatTurnRequest,
    ChatTurnResponse,
    ClientSecretRequest,
    ClientSecretResponse,
    DeleteResponse,
    HealthResponse,
    SessionMessage,
    SessionResponse,
    SpeakRequest,
    TranscriptionResponse,
    TurnResponse,
)
from .sessions import SessionStore

_default_pipeline: VoicePipeline | None = None
_default_bridge: RealtimeBridge | None = None


def get_pipeline() -> VoicePipeline:
    """Default pipeline dependency; override via ``app.dependency_overrides``."""

    global _default_pipeline
    if _default_pipeline is None:
        _default_pipeline = VoicePipeline()
    return _default_pipeline


def get_bridge() -> RealtimeBridge:
    """Default realtime bridge dependency."""

    global _default_bridge
    if _default_bridge is None:
        _default_bridge = RealtimeBridge()
    return _default_bridge


def set_pipeline(pipeline: VoicePipeline | None) -> None:
    global _default_pipeline
    _default_pipeline = pipeline


def set_bridge(bridge: RealtimeBridge | None) -> None:
    global _default_bridge
    _default_bridge = bridge


PipelineDep = Annotated[VoicePipeline, Depends(get_pipeline)]
BridgeDep = Annotated[RealtimeBridge, Depends(get_bridge)]


def _header_safe(value: str, limit: int = 1024) -> str:
    """HTTP headers are latin-1; percent-encode so any language survives."""

    return quote(value[:limit], safe="")


def create_v2v_router(
    *,
    pipeline: VoicePipeline | None = None,
    bridge: RealtimeBridge | None = None,
    settings: V2VSettings | None = None,
    store: SessionStore | None = None,
    prefix: str | None = None,
    tags: list[str] | None = None,
    dependencies: list | None = None,
) -> APIRouter:
    """Build the v2v router.

    Bare, it reads settings from the environment and uses the shared
    dependencies. Pass ``pipeline=``/``bridge=``/``settings=``/``store=`` to bind
    isolated instances -- useful when mounting the router more than once or
    inside a host app with its own configuration.
    """

    settings = settings or (pipeline.settings if pipeline else get_settings())
    bound_pipeline = pipeline
    if bound_pipeline is None and (store is not None or settings is not get_settings()):
        bound_pipeline = VoicePipeline(settings, store=store)
    bound_bridge = bridge
    if bound_bridge is None and settings is not get_settings():
        bound_bridge = RealtimeBridge(settings)

    pipeline_dep = (
        Annotated[VoicePipeline, Depends(lambda: bound_pipeline)] if bound_pipeline else PipelineDep
    )
    bridge_dep = (
        Annotated[RealtimeBridge, Depends(lambda: bound_bridge)] if bound_bridge else BridgeDep
    )

    router = APIRouter(
        prefix=prefix if prefix is not None else settings.api_prefix,
        tags=tags if tags is not None else [settings.api_tag],
        dependencies=dependencies or [],
    )

    def resolve_bridge() -> RealtimeBridge:
        return bound_bridge or get_bridge()

    @router.get("/health", response_model=HealthResponse, summary="Configured models")
    async def health(pipe: pipeline_dep) -> HealthResponse:
        s = pipe.settings
        return HealthResponse(
            status="ok",
            realtime_model=s.realtime_model,
            transcribe_model=s.transcribe_model,
            text_model=s.text_model,
            tts_model=s.tts_model,
            voice=s.voice,
        )

    # -- pipeline: speech to text ------------------------------------------
    @router.post(
        "/transcribe",
        response_model=TranscriptionResponse,
        summary="Speech to text",
    )
    async def transcribe(
        pipe: pipeline_dep,
        audio: Annotated[UploadFile, File(description="mp3, wav, webm, m4a, ogg, flac (<=25MB)")],
        language: Annotated[str | None, Form(description="ISO-639-1 hint, e.g. ru")] = None,
        prompt: Annotated[str | None, Form(description="Vocabulary / context hint")] = None,
    ) -> TranscriptionResponse:
        data = await audio.read()
        transcript = await pipe.transcribe(
            data,
            filename=audio.filename or "audio.wav",
            language=language,
            prompt=prompt,
        )
        return TranscriptionResponse(
            text=transcript.text, model=transcript.model, language=transcript.language
        )

    # -- pipeline: text to speech -------------------------------------------
    @router.post(
        "/speak",
        summary="Text to speech (streamed)",
        response_class=StreamingResponse,
        responses={200: {"content": {"audio/mpeg": {}}, "description": "Synthesised audio"}},
    )
    async def speak(pipe: pipeline_dep, body: SpeakRequest) -> StreamingResponse:
        fmt = body.format or pipe.settings.tts_format
        stream = pipe.speak(
            body.text,
            voice=body.voice,
            audio_format=fmt,
            instructions=body.instructions,
            model=body.model,
        )
        return StreamingResponse(
            stream,
            media_type=CONTENT_TYPE_BY_FORMAT.get(fmt, "application/octet-stream"),
            headers={"Cache-Control": "no-store"},
        )

    # -- pipeline: text turn -------------------------------------------------
    @router.post("/chat", response_model=ChatTurnResponse, summary="Text turn with session memory")
    async def chat(pipe: pipeline_dep, body: ChatTurnRequest) -> ChatTurnResponse:
        reply = await pipe.reply(
            body.session_id, body.text, model=body.model, instructions=body.instructions
        )
        return ChatTurnResponse(
            session_id=body.session_id, reply=reply, model=body.model or pipe.settings.text_model
        )

    # -- pipeline: full voice turn -------------------------------------------
    @router.post(
        "/turn",
        summary="Voice in, voice out (streamed audio + transcript headers)",
        response_class=StreamingResponse,
    )
    async def turn(
        pipe: pipeline_dep,
        audio: Annotated[UploadFile, File()],
        session_id: Annotated[str, Form()] = "default",
        voice: Annotated[str | None, Form()] = None,
        audio_format: Annotated[str | None, Form()] = None,
        instructions: Annotated[str | None, Form()] = None,
    ) -> StreamingResponse:
        data = await audio.read()
        result, stream = await pipe.turn(
            data,
            session_id=session_id,
            filename=audio.filename or "audio.wav",
            voice=voice,
            audio_format=audio_format,
            instructions=instructions,
        )
        return StreamingResponse(
            stream,
            media_type=result.content_type,
            headers={
                # Percent-encoded UTF-8; decode with decodeURIComponent().
                "X-V2V-Session-Id": _header_safe(result.session_id),
                "X-V2V-Transcript": _header_safe(result.transcript),
                "X-V2V-Reply": _header_safe(result.reply),
                "Access-Control-Expose-Headers": "X-V2V-Session-Id, X-V2V-Transcript, X-V2V-Reply",
                "Cache-Control": "no-store",
            },
        )

    @router.post(
        "/turn/json",
        response_model=TurnResponse,
        summary="Voice in, voice out (JSON with base64 audio)",
    )
    async def turn_json(
        pipe: pipeline_dep,
        audio: Annotated[UploadFile, File()],
        session_id: Annotated[str, Form()] = "default",
        voice: Annotated[str | None, Form()] = None,
        audio_format: Annotated[str | None, Form()] = None,
        instructions: Annotated[str | None, Form()] = None,
    ) -> TurnResponse:
        data = await audio.read()
        result, stream = await pipe.turn(
            data,
            session_id=session_id,
            filename=audio.filename or "audio.wav",
            voice=voice,
            audio_format=audio_format,
            instructions=instructions,
        )
        buffer = bytearray()
        async for piece in stream:
            buffer.extend(piece)
        return TurnResponse(
            session_id=result.session_id,
            transcript=result.transcript,
            reply=result.reply,
            audio_base64=base64.b64encode(bytes(buffer)).decode("ascii"),
            audio_format=result.audio_format,
            content_type=result.content_type,
        )

    # -- realtime -------------------------------------------------------------
    @router.post(
        "/realtime/client-secret",
        response_model=ClientSecretResponse,
        summary="Mint an ephemeral key for a browser realtime session",
    )
    async def client_secret(
        bridge_: bridge_dep, body: ClientSecretRequest | None = None
    ) -> ClientSecretResponse:
        body = body or ClientSecretRequest()
        data = await bridge_.create_client_secret(
            instructions=body.instructions,
            voice=body.voice,
            model=body.model,
            ttl_seconds=body.ttl_seconds,
        )
        return ClientSecretResponse(**data)

    @router.websocket("/realtime/ws")
    async def realtime_ws(
        websocket: WebSocket,
        instructions: str | None = Query(default=None),
        voice: str | None = Query(default=None),
        model: str | None = Query(default=None),
    ) -> None:
        """Proxy a browser socket to OpenAI Realtime.

        The client speaks the Realtime event protocol as usual (send
        ``input_audio_buffer.append`` with base64 PCM16, read
        ``response.output_audio.delta``) -- but your API key stays server-side and
        every event passes through your process.
        """

        bridge_ = resolve_bridge()
        await websocket.accept()
        try:
            await bridge_.proxy(websocket, instructions=instructions, voice=voice, model=model)
        except OpenAIError as exc:
            await websocket.close(code=1011, reason=str(exc)[:120])
        finally:
            with contextlib.suppress(RuntimeError):
                await websocket.close()

    # -- sessions --------------------------------------------------------------
    @router.get("/sessions/{session_id}", response_model=SessionResponse, summary="Read history")
    async def get_session(pipe: pipeline_dep, session_id: str) -> SessionResponse:
        session = await pipe.store.get(session_id)
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown session: {session_id}")
        return SessionResponse(
            session_id=session.session_id,
            messages=[
                SessionMessage(role=m.role, content=m.content, created_at=m.created_at)
                for m in session.messages
            ],
            metadata=session.metadata,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    @router.delete(
        "/sessions/{session_id}", response_model=DeleteResponse, summary="Forget history"
    )
    async def delete_session(pipe: pipeline_dep, session_id: str) -> DeleteResponse:
        return DeleteResponse(session_id=session_id, deleted=await pipe.store.delete(session_id))

    return router


def register_exception_handlers(app) -> None:
    """Turn OpenAI/domain errors into clean HTTP responses."""

    from fastapi.responses import JSONResponse

    @app.exception_handler(AudioError)
    async def _bad_audio(_, exc: AudioError):
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _bad_value(_, exc: ValueError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": str(exc)}
        )

    @app.exception_handler(OpenAIError)
    async def _openai_error(_, exc: OpenAIError):
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": f"OpenAI request failed: {exc}"},
        )
