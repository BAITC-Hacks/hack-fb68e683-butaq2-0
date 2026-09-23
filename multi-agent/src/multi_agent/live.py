"""GPT-Live voice sessions delegating work to the shared orchestrator."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, suppress
from typing import Any

from .contracts import RoutingDecision

logger = logging.getLogger(__name__)
LIVE_PROMPT = """Ты Butaq, голосовой помощник страховой службы.
Говори кратко и спокойно, на языке текущей реплики: русском, казахском или
смешанном. Сохраняй номера, суммы и даты из проверенных результатов.
Backchannel policy: Коротко подтверждай, что слушаешь, не перебивая пользователя.
Interruption policy: Когда пользователь перебивает, прекрати ответ и слушай.
Delegation policy:
Backend tools:
- Страховой Router: выбор сценария, уточнение запроса, проверка сведений о
  продуктах, полисах, оплатах, возвратах и страховых случаях.
Delegate to the backend when:
- После каждого законченного страхового вопроса, уточнения, номера документа,
  смены темы или просьбы соединить с оператором.
- Пользователь исправляет или дополняет предыдущий запрос.
Do not delegate to the backend when:
- Пользователь только здоровается, благодарит или просит повторить готовый ответ.
Делегируй до содержательного ответа. Пока ждёшь, можно кратко сказать, что
проверяешь. Не придумывай факты. Задавай пользователю уточняющий вопрос backend.
Просьба об операторе не означает, что телефонное соединение уже установлено.
Не упоминай внутренние названия моделей, сценариев и инструментов.
"""


def commentary_chunks(text: str) -> list[str]:
    """UTF-8 bytes bound tokens; keep each Live append below its 500-token limit."""
    chunks: list[str] = []
    current = ""
    for word in text.split():
        if len(word.encode("utf-8")) > 480:
            raise ValueError("Backend reply contains an oversized word")
        candidate = f"{current} {word}".strip()
        if len(candidate.encode("utf-8")) > 480:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


class LiveConversation:
    """Own a media session, trusted sideband and cancellable backend tasks.

    Browser audio travels over WebRTC. Only the sideband may supply context and
    backend results. Live has no response-done event: generated drafts must not
    be recorded as completely played assistant messages in orchestrator history.
    """

    def __init__(self, *, client: Any, service: Any, session_id: str,
                 send: Callable[[dict[str, Any]], Awaitable[None]],
                 transcript_grace: float = 0.3):
        self.client, self.service = client, service
        self.session_id, self.send = session_id, send
        self.transcript_grace = transcript_grace
        self.provider_id: str | None = None
        self.connection: Any = None
        self._stack = AsyncExitStack()
        self._task: asyncio.Task | None = None
        self._generation = 0
        self._closed = False
        self._seen_events: set[str] = set()
        self._seen_delegations: set[str] = set()
        self._fragments: list[tuple[int, int, str]] = []
        self._consumed_ms = -1
        self._active_delegation: tuple[str, int] | None = None
        self._resolving = False
        self._dialogue: list[dict[str, str]] = []
        self.finalized = False
        self._send_lock = asyncio.Lock()

    async def emit(self, event: dict[str, Any]) -> None:
        async with self._send_lock:
            if not self._closed:
                await self.send(event)

    async def open(self, sdp: str) -> str:
        session: dict[str, Any] = {
            "model": os.getenv("MULTI_AGENT_LIVE_MODEL", "gpt-live-1"),
            "instructions": LIVE_PROMPT,
            "audio": {"output": {"voice": os.getenv("MULTI_AGENT_LIVE_VOICE", "marin")}},
            "delegation": {"type": "client"}, "store": False,
            "client": {"data_channel": {
                "allowed_client_events": [],
                "allowed_server_events": [{"type": name} for name in ("session.started", "session.closed", "error")],
            }},
        }
        state = self.service.sessions.get(self.session_id)
        if state is not None:
            # Bound rendered history too; the provider permits 8192 tokens.
            history: list[dict[str, str]] = []
            remaining = 6000
            for entry in reversed(state.history[-10:]):
                size = len(entry["content"].encode("utf-8")) + 64
                if size > remaining:
                    break
                history.insert(0, entry)
                remaining -= size
            session["input"] = [
                {"role": entry["role"], "content": [{
                    "type": "input_text" if entry["role"] == "user" else "output_text",
                    "text": entry["content"],
                }]} for entry in history
            ]
        created = await self.client.live.create(
            session=session, transport={"type": "webrtc", "sdp": sdp}, timeout=20,
        )
        self.provider_id = created.session.id
        self.connection = await asyncio.wait_for(self._stack.enter_async_context(
            self.client.live.sideband.connect(session_id=self.provider_id, max_retries=0)
        ), 10)
        return created.transport.sdp

    async def run(self) -> None:
        async for raw in self.connection:
            event = raw if isinstance(raw, dict) else raw.model_dump()
            await self.handle(event)
            if event.get("type") in {"session.closed", "error"}:
                return
        await self.emit({"type": "error", "message": "Голосовое соединение закрыто. Начните сеанс заново."})

    async def handle(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        event_id = event.get("event_id")
        if event_id:
            if event_id in self._seen_events:
                return
            if len(self._seen_events) >= 20000:
                raise ValueError("Live session event limit reached")
            self._seen_events.add(event_id)
        kind = event.get("type")
        if kind in {"session.input_transcript.delta", "session.output_transcript.delta"}:
            delta = event.get("delta", "")
            if not isinstance(delta, str):
                return
            speaker = "user" if kind == "session.input_transcript.delta" else "assistant"
            if self._dialogue and self._dialogue[-1]["role"] == speaker:
                self._dialogue[-1]["content"] = (self._dialogue[-1]["content"] + delta)[-2000:]
            else:
                self._dialogue.append({"role": speaker, "content": delta[-2000:]})
                self._dialogue = self._dialogue[-12:]
            if kind == "session.input_transcript.delta":
                start, end = event["start_ms"], event["end_ms"]
                if end > self._consumed_ms:
                    self._fragments.append((start, end, delta))
                if sum(len(part[2]) for part in self._fragments) > 16000:
                    raise ValueError("Live input is too long")
                # Late words belonging to the SAME request must not be omitted
                # from an already running backend call. Cancel before restarting.
                if self._resolving and self._active_delegation and start <= self._active_delegation[1]:
                    identity, offset = self._active_delegation
                    await self._cancel()
                    self._task = asyncio.create_task(self._resolve(identity, offset, self._generation))
            await self.emit({"type": "caption", "speaker": "user" if kind == "session.input_transcript.delta" else "assistant",
                             "delta": delta, "start_ms": event["start_ms"], "end_ms": event["end_ms"]})
        elif kind == "session.delegation.created":
            delegation = event["delegation"]
            identity = delegation["id"]
            if delegation["target"] != "client" or identity in self._seen_delegations:
                return
            if len(self._seen_delegations) >= 256:
                raise ValueError("Live session turn limit reached")
            self._seen_delegations.add(identity)
            await self._cancel()
            self._active_delegation = (identity, event["offset_ms"])
            self._task = asyncio.create_task(self._resolve(identity, event["offset_ms"], self._generation))
        elif kind == "error":
            logger.warning("Live provider error", extra={"code": event.get("error", {}).get("code")})
            await self.emit({"type": "error", "message": "Ошибка голосовой модели. Начните сеанс заново."})
        elif kind == "session.closed":
            self.finalized = True
            await self.emit({"type": "closed"})

    async def _resolve(self, identity: str, offset_ms: int, generation: int) -> None:
        def current() -> bool:
            return not self._closed and generation == self._generation

        async def on_route(decision: RoutingDecision) -> None:
            if current():
                await self.emit({"type": "route", "decision": decision.model_dump()})

        try:
            # Delegation may precede delivery of its last transcript fragment.
            await asyncio.sleep(self.transcript_grace)
            parts = [part for part in self._fragments if part[0] <= offset_ms]
            text = "".join(part[2] for part in parts).strip()
            if not text:
                await self.connection.send({"type": "session.commentary.append", "delegation_id": identity,
                    "content": "Не удалось расслышать запрос. Попроси пользователя повторить вопрос."})
                return
            await self.emit({"type": "working", "turn_id": identity})
            self._resolving = True
            result = await self.service.turn(session_id=self.session_id, text=text,
                on_route=on_route, delivery_id=identity,
                voice_context=[entry.copy() for entry in self._dialogue])
            if not current():
                return
            self._consumed_ms = max(part[1] for part in parts)
            self._fragments = [part for part in self._fragments if part[1] > self._consumed_ms]
            if any(part[0] > offset_ms for part in self._fragments):
                await self.emit({"type": "working.done", "turn_id": identity})
                return
            for chunk in commentary_chunks(result.reply):
                await self.connection.send({"type": "session.commentary.append", "delegation_id": identity, "content": chunk})
            await self.emit({"type": "result", "result": {**result.model_dump(), "turn_id": identity}})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Live backend task failed", extra={"exception_type": type(exc).__name__})
            if current():
                await self.emit({"type": "task.error", "turn_id": identity, "message": "Не удалось проверить запрос. Повторите его, пожалуйста."})
                with suppress(Exception):
                    await self.connection.send({"type": "session.commentary.append", "delegation_id": identity,
                        "content": "Проверка запроса не удалась. Попроси пользователя повторить. Не придумывай результат."})
        finally:
            self._resolving = False
            await self.service.orchestrator.interrupt_delivery(self.session_id, identity)
            await self.emit({"type": "working.done", "turn_id": identity})

    async def say(self, text: str) -> None:
        """A typed message during a live call: verify it, then let Live read the reply.

        The sideband has no user-input event, so the verified answer arrives as an
        instruction append instead of a delegation commentary.
        """
        text = text.strip()
        if not text:
            return
        await self._cancel()
        self._task = asyncio.create_task(
            self._say(f"typed:{uuid.uuid4().hex}", text[:2000], self._generation))

    async def _say(self, identity: str, text: str, generation: int) -> None:
        def current() -> bool:
            return not self._closed and generation == self._generation

        async def on_route(decision: RoutingDecision) -> None:
            if current():
                await self.emit({"type": "route", "decision": decision.model_dump()})

        try:
            await self.emit({"type": "working", "turn_id": identity})
            self._dialogue.append({"role": "user", "content": text[-2000:]})
            self._dialogue = self._dialogue[-12:]
            result = await self.service.turn(session_id=self.session_id, text=text,
                on_route=on_route, delivery_id=identity,
                voice_context=[entry.copy() for entry in self._dialogue])
            if not current():
                return
            for chunk in commentary_chunks(
                f"Пользователь написал: {text} "
                f"Прочитай вслух этот проверенный ответ: {result.reply}"
            ):
                await self.connection.send({"type": "session.instructions.append",
                    "delegation_id": None, "content": chunk})
            await self.emit({"type": "result", "result": {**result.model_dump(), "turn_id": identity}})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Live typed task failed", extra={"exception_type": type(exc).__name__})
            if current():
                await self.emit({"type": "task.error", "turn_id": identity,
                    "message": "Не удалось проверить запрос. Повторите его, пожалуйста."})
        finally:
            await self.service.orchestrator.interrupt_delivery(self.session_id, identity)
            await self.emit({"type": "working.done", "turn_id": identity})

    async def interrupt(self) -> None:
        await self._cancel()
        await self.connection.send({"type": "session.instructions.append", "delegation_id": None,
            "content": "Прекрати текущую речь и слушай пользователя. Дождись следующего запроса."})

    async def _cancel(self) -> None:
        self._generation += 1
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._cancel()
        # The API adapter stops its reader before close; only this task drains.
        if self.connection is not None and not self.finalized and hasattr(self.connection, "recv"):
            with suppress(Exception):
                async with asyncio.timeout(3):
                    await self.connection.send({"type": "session.close"})
                    while True:
                        raw = await self.connection.recv()
                        event = raw if isinstance(raw, dict) else raw.model_dump()
                        if event.get("type") == "session.closed":
                            self.finalized = True
                            break
        if self.provider_id and not self.finalized:
            with suppress(Exception):
                await self.client.live.sessions.hangup(self.provider_id, timeout=5)
            logger.info("Live final usage unconfirmed after transport close")
        with suppress(Exception):
            await asyncio.wait_for(self._stack.aclose(), 5)
