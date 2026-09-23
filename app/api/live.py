"""Browser signaling and UI events for the shared GPT-Live adapter."""
import asyncio
import logging
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from multi_agent.live import LiveConversation
from app.api.dependencies import get_service
from app.api.streaming import _allowed_origin
from app.services.voice_router import RouterService

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/live")
async def live_conversation(socket: WebSocket, service: Annotated[RouterService, Depends(get_service)]) -> None:
    if not _allowed_origin(socket.headers.get("origin")):
        await socket.close(code=1008)
        return
    await socket.accept()
    conversation = None
    session_id = None
    tasks: list[asyncio.Task] = []
    try:
        start = await asyncio.wait_for(socket.receive_json(), 10)
        if not isinstance(start, dict) or start.get("type") != "start":
            raise ValueError("First message must be start")
        identity, sdp = start.get("session_id"), start.get("sdp")
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 128 or identity.startswith("phone:"):
            raise ValueError("Invalid session_id")
        if not isinstance(sdp, str) or not sdp.startswith("v=0") or len(sdp) > 64000:
            raise ValueError("Invalid WebRTC offer")
        if identity in service.stream_sessions:
            raise ValueError("This session already has an active voice connection")
        if service.database is not None and service.database.count_scenarios() == 0:
            raise ValueError("No scenarios configured; import a catalog first")
        service.stream_sessions.add(identity)
        session_id = identity
        conversation = LiveConversation(client=service.pipeline.client, service=service,
            session_id=identity, send=socket.send_json)
        answer = await conversation.open(sdp)
        await conversation.emit({"type": "ready", "sdp": answer})

        async def controls() -> None:
            while True:
                message = await socket.receive_json()
                if message == {"type": "stop"}:
                    return
                if message == {"type": "interrupt"}:
                    await conversation.interrupt()
                else:
                    raise ValueError("Unknown Live control")

        tasks = [asyncio.create_task(conversation.run()), asyncio.create_task(controls())]
        done, _ = await asyncio.wait(tasks, timeout=1200, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except ValueError as exc:
        with suppress(Exception):
            await socket.send_json({"type": "error", "message": str(exc)})
    except Exception as exc:
        logger.warning("Live connection failed", extra={"exception_type": type(exc).__name__})
        with suppress(Exception):
            await socket.send_json({"type": "error", "message": "Не удалось подключить GPT-Live. Начните сеанс заново или напишите сообщение."})
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if conversation is not None:
            await conversation.close()
        if session_id is not None:
            service.stream_sessions.discard(session_id)
        with suppress(Exception):
            await socket.close()
