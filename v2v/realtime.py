"""Realtime speech-to-speech: session config, ephemeral keys and a WS proxy.

Three ways to use the Realtime API, all supported here:

1. **Browser talks to OpenAI directly** (lowest latency, WebRTC). Your server
   only mints an ephemeral key -- :meth:`RealtimeBridge.create_client_secret`.
2. **Browser talks to your server, your server talks to OpenAI** -- the proxy in
   :meth:`RealtimeBridge.proxy`. Your API key never leaves the backend and every
   event passes through your code (logging, tools, guardrails).
3. **Your server drives a session alone** (outbound bot, telephony) --
   :meth:`RealtimeBridge.connect`.

Model: ``gpt-realtime-2.1``. Audio rides inside JSON events as base64:
``input_audio_buffer.append`` up, ``response.output_audio.delta`` down.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from openai import AsyncOpenAI

from .client import ClientProvider
from .config import V2VSettings, get_settings

logger = logging.getLogger("v2v.realtime")

ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]
EventHook = Callable[[dict[str, Any]], Awaitable[None]]

#: Client events a browser is allowed to push through the proxy. Everything that
#: configures the session (instructions, tools, voice) stays server-side.
DEFAULT_CLIENT_EVENT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "input_audio_buffer.append",
        "input_audio_buffer.commit",
        "input_audio_buffer.clear",
        "conversation.item.create",
        "conversation.item.retrieve",
        "conversation.item.truncate",
        "conversation.item.delete",
        "response.create",
        "response.cancel",
        "output_audio_buffer.clear",
    }
)


def build_session(
    settings: V2VSettings,
    *,
    instructions: str | None = None,
    voice: str | None = None,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    input_codec: str | None = None,
    output_codec: str | None = None,
    include_model: bool = True,
) -> dict[str, Any]:
    """Build a GA Realtime session object.

    The same shape is accepted by ``POST /v1/realtime/client_secrets``, by the
    ``session.update`` event, and by ``POST /v1/realtime/calls/{id}/accept``
    (SIP) -- which is why telephony can reuse it verbatim.
    """

    in_codec = input_codec or settings.input_codec
    out_codec = output_codec or settings.output_codec

    input_format: dict[str, Any] = {"type": in_codec}
    if in_codec == "audio/pcm":
        input_format["rate"] = settings.input_sample_rate
    output_format: dict[str, Any] = {"type": out_codec}
    if out_codec == "audio/pcm":
        output_format["rate"] = settings.output_sample_rate

    if settings.turn_detection == "none":
        turn_detection: dict[str, Any] | None = None
    elif settings.turn_detection == "server_vad":
        turn_detection = {
            "type": "server_vad",
            "threshold": settings.vad_threshold,
            "prefix_padding_ms": settings.vad_prefix_padding_ms,
            "silence_duration_ms": settings.vad_silence_ms,
        }
    else:
        turn_detection = {"type": "semantic_vad"}

    session: dict[str, Any] = {
        "type": "realtime",
        "instructions": instructions or settings.instructions,
        "output_modalities": list(settings.output_modalities),
        "audio": {
            "input": {
                "format": input_format,
                "turn_detection": turn_detection,
                "transcription": {"model": settings.input_transcription_model},
            },
            "output": {
                "format": output_format,
                "voice": voice or settings.voice,
            },
        },
    }
    if include_model:
        session["model"] = model or settings.realtime_model
    if tools:
        session["tools"] = tools
    return session


class RealtimeBridge:
    """Owns the OpenAI side of a realtime voice session."""

    def __init__(
        self,
        settings: V2VSettings | None = None,
        *,
        client: AsyncOpenAI | None = None,
        tools: dict[str, ToolHandler] | None = None,
        tool_schemas: list[dict[str, Any]] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._clients = ClientProvider(self.settings, client)
        self.tools: dict[str, ToolHandler] = dict(tools or {})
        self.tool_schemas: list[dict[str, Any]] = list(tool_schemas or [])

    @property
    def client(self) -> AsyncOpenAI:
        return self._clients.get()

    def register_tool(
        self,
        name: str,
        handler: ToolHandler,
        *,
        description: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> None:
        """Expose a python coroutine to the voice model as a callable tool."""

        self.tools[name] = handler
        self.tool_schemas = [s for s in self.tool_schemas if s.get("name") != name]
        self.tool_schemas.append(
            {
                "type": "function",
                "name": name,
                "description": description,
                "parameters": parameters or {"type": "object", "properties": {}},
            }
        )

    def session_payload(self, **overrides: Any) -> dict[str, Any]:
        overrides.setdefault("tools", self.tool_schemas or None)
        return build_session(self.settings, **overrides)

    # -- 1. ephemeral key for browsers ---------------------------------------
    async def create_client_secret(
        self,
        *,
        instructions: str | None = None,
        voice: str | None = None,
        model: str | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        session = self.session_payload(instructions=instructions, voice=voice, model=model)
        secret = await self.client.realtime.client_secrets.create(
            expires_after={
                "anchor": "created_at",
                "seconds": ttl_seconds or self.settings.client_secret_ttl_seconds,
            },
            session=session,  # type: ignore[arg-type]
        )
        return {
            "value": secret.value,
            "expires_at": getattr(secret, "expires_at", None),
            "model": session.get("model", self.settings.realtime_model),
            "session": session,
        }

    # -- 2. server-side session ----------------------------------------------
    def connect(self, *, model: str | None = None, call_id: str | None = None):
        """Async context manager yielding an ``AsyncRealtimeConnection``.

        Pass ``call_id`` to attach to a SIP call accepted earlier.
        """

        if call_id:
            return self.client.realtime.connect(call_id=call_id)
        return self.client.realtime.connect(model=model or self.settings.realtime_model)

    async def handle_tool_call(
        self, conn, item: dict[str, Any], *, request_response: bool = True
    ) -> bool:
        """Run a ``function_call`` item and feed the result back to the model.

        With ``request_response=False`` the output is delivered but no new
        response is requested -- used to break a runaway tool loop.
        """

        name = item.get("name")
        handler = self.tools.get(name or "")
        if handler is None:
            return False
        try:
            args = json.loads(item.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        try:
            result = await handler(args)
            output = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        except Exception as exc:  # surface the failure to the model, not the caller
            logger.exception("Realtime tool %s failed", name)
            output = json.dumps({"error": str(exc)})
        await conn.send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": item.get("call_id"),
                    "output": output,
                },
            }
        )
        if request_response:
            await conn.send({"type": "response.create"})
        return True

    # -- 3. browser <-> server <-> OpenAI proxy --------------------------------
    async def proxy(
        self,
        websocket,
        *,
        instructions: str | None = None,
        voice: str | None = None,
        model: str | None = None,
        on_event: EventHook | None = None,
        allowlist: frozenset[str] | None = None,
    ) -> None:
        """Pump events between an accepted client WebSocket and OpenAI.

        ``websocket`` is a Starlette/FastAPI ``WebSocket`` that you have already
        ``accept()``-ed. Returns when either side disconnects.
        """

        from starlette.websockets import WebSocketDisconnect

        allowed = allowlist or DEFAULT_CLIENT_EVENT_ALLOWLIST
        tool_rounds = 0  # consecutive tool calls since the last user input
        session = self.session_payload(instructions=instructions, voice=voice, model=model)
        connect_model = session.pop("model", model or self.settings.realtime_model)

        async with self.connect(model=connect_model) as conn:
            await conn.send({"type": "session.update", "session": session})

            async def client_to_openai() -> None:
                nonlocal tool_rounds
                while True:
                    try:
                        raw = await websocket.receive_text()
                    except WebSocketDisconnect:
                        return
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        await websocket.send_text(
                            json.dumps({"type": "error", "error": {"message": "invalid JSON"}})
                        )
                        continue
                    kind = event.get("type", "")
                    if kind in ("input_audio_buffer.append", "conversation.item.create"):
                        tool_rounds = 0  # the user spoke again; the budget resets
                    if kind == "session.update" and not self.settings.allow_client_session_update:
                        logger.debug("Dropped client session.update")
                        continue
                    if kind not in allowed and kind != "session.update":
                        logger.debug("Dropped client event %s", kind)
                        continue
                    await conn.send_raw(raw)

            async def openai_to_client() -> None:
                nonlocal tool_rounds
                while True:
                    event = await conn.recv()
                    payload = event.to_dict() if hasattr(event, "to_dict") else dict(event)
                    if on_event is not None:
                        await on_event(payload)
                    if payload.get("type") == "response.output_item.done":
                        item = payload.get("item") or {}
                        if item.get("type") == "function_call":
                            tool_rounds += 1
                            within_budget = tool_rounds <= self.settings.max_tool_rounds
                            if not within_budget:
                                logger.warning(
                                    "Tool-call budget of %s exhausted; not continuing the response",
                                    self.settings.max_tool_rounds,
                                )
                            await self.handle_tool_call(
                                conn, item, request_response=within_budget
                            )
                    await websocket.send_text(json.dumps(payload))

            up = asyncio.create_task(client_to_openai())
            down = asyncio.create_task(openai_to_client())
            done, pending = await asyncio.wait(
                {up, down}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    raise exc
