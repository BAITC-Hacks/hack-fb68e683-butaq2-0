"""WebSocket adapter for the shared multi-agent streaming conversation."""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack, suppress
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from multi_agent.streaming import SAMPLE_RATE, StreamingConversation

from app.api.dependencies import get_service
from app.services.voice_router import RouterService

router = APIRouter()


def _allowed_origin(origin: str | None) -> bool:
    # Non-browser clients (including future phone adapters) have no Origin header.
    if origin is None:
        return True
    allowed = os.getenv(
        "FRONTEND_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    )
    return origin in {value.strip() for value in allowed.split(",")}


@router.websocket("/stream")
async def stream_conversation(
    socket: WebSocket, service: Annotated[RouterService, Depends(get_service)]
) -> None:
    if not _allowed_origin(socket.headers.get("origin")):
        await socket.close(code=1008)
        return
    await socket.accept()
    conversation: StreamingConversation | None = None
    transcription_task: asyncio.Task[None] | None = None
    owned_session: str | None = None
    try:
        start = await asyncio.wait_for(socket.receive_json(), timeout=10)
        if not isinstance(start, dict) or start.get("type") != "start":
            raise ValueError("First message must be start")
        session_id, mode = start.get("session_id"), start.get("mode", "voice")
        if (
            not isinstance(session_id, str)
            or not session_id.strip()
            or len(session_id) > 128
        ):
            raise ValueError("session_id must contain between 1 and 128 characters")
        if mode not in {"text", "voice"}:
            raise ValueError("mode must be text or voice")
        if service.database is not None and service.database.count_scenarios() == 0:
            raise ValueError("No scenarios configured; import a catalog first")
        if session_id in service.stream_sessions:
            raise ValueError("This session already has an active voice connection")
        service.stream_sessions.add(session_id)
        owned_session = session_id
        async with AsyncExitStack() as stack:
            transcriber = None
            if mode == "voice":
                # Text-only sockets and HTTP callers never open a realtime session.
                from multi_agent.transcription import RealtimeTranscriber

                transcriber = await stack.enter_async_context(
                    RealtimeTranscriber(service.pipeline.client)
                )
            conversation = StreamingConversation(
                session_id=session_id,
                service=service,
                speak=service.pipeline.speak,
                send=socket.send_json,
                confirm_delivery=service.orchestrator.confirm_delivery,
                interrupt_delivery=service.orchestrator.interrupt_delivery,
                transcriber=transcriber,
            )
            await conversation.emit({"type": "ready", "sample_rate": SAMPLE_RATE})
            if transcriber is not None:

                async def forward_transcripts() -> None:
                    try:
                        async for event in transcriber.events():
                            if event.get("fatal"):
                                await conversation.error(
                                    "Speech connection failed; reconnect to continue"
                                )
                                await socket.close(code=1011)
                                return
                            await conversation.transcript(event)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 — contain provider failures at the transport boundary
                        await conversation.error(
                            "Speech connection failed; reconnect to continue"
                        )
                        await socket.close(code=1011)

                transcription_task = asyncio.create_task(forward_transcripts())
            try:
                while True:
                    try:
                        message = await socket.receive_json()
                    except ValueError:
                        await conversation.error("Expected valid JSON")
                        continue
                    await conversation.receive(message)
            finally:
                if transcription_task is not None:
                    transcription_task.cancel()
                    with suppress(
                        asyncio.CancelledError, WebSocketDisconnect, RuntimeError
                    ):
                        await transcription_task
                await conversation.close()
    except WebSocketDisconnect:
        pass
    except ValueError as exc:
        await socket.send_json({"type": "error", "message": str(exc)})
        await socket.close(code=1008)
    except asyncio.TimeoutError:
        await socket.send_json(
            {"type": "error", "message": "Streaming setup timed out"}
        )
        await socket.close(code=1008)
    except Exception:  # noqa: BLE001 — contain provider failures at the transport boundary
        # Never expose provider errors, credentials or internal URLs to the browser.
        with suppress(WebSocketDisconnect, RuntimeError):
            await socket.send_json(
                {
                    "type": "error",
                    "message": "Voice connection failed; reconnect to continue",
                }
            )
            await socket.close(code=1011)

    finally:
        if conversation is not None:
            await conversation.close()
        if owned_session is not None:
            service.stream_sessions.discard(owned_session)
