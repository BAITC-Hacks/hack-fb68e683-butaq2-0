"""Transport-independent streaming turns, delivery acknowledgements and interruption."""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, suppress
from time import perf_counter
from typing import Any, Protocol

from .contracts import RoutingDecision, TurnResult

logger = logging.getLogger(__name__)
SAMPLE_RATE = 24000
MAX_TURNS = 256


class Transcriber(Protocol):
    async def append(self, turn_id: str, audio: str) -> None: ...
    async def commit(self, turn_id: str) -> None: ...
    async def clear(self) -> None: ...


class TurnService(Protocol):
    async def turn(self, **kwargs: Any) -> TurnResult: ...


class StreamingConversation:
    """One socket's turn lifecycle; recognition can run while output is playing.

    Browser acknowledgements distinguish generated audio from delivered audio.
    Every output task has a generation token, so late provider data cannot escape
    after interruption. Complete, validated replies alone reach synthesis.
    """

    def __init__(
        self,
        *,
        session_id: str,
        service: TurnService,
        speak: Callable[..., AsyncIterator[bytes]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
        confirm_delivery: Callable[[str, str], Awaitable[None]],
        interrupt_delivery: Callable[[str, str], Awaitable[None]],
        transcriber: Transcriber | None = None,
        tts_timeout: float = 30,
    ):
        self.session_id = session_id
        self.service = service
        self.speak = speak
        self.send = send
        self.confirm_delivery = confirm_delivery
        self.interrupt_delivery = interrupt_delivery
        self.transcriber = transcriber
        self.tts_timeout = tts_timeout
        self.active_turn_id: str | None = None
        self.audio_turn_id: str | None = None
        self._audio_committed_at: float | None = None
        self._task: asyncio.Task[None] | None = None
        self._generation = 0
        self._seen: set[str] = set()
        self._sequence = 0
        self._send_lock = asyncio.Lock()
        self._input_lock = asyncio.Lock()
        self._completed = False
        self._playback_started = False
        self._closed = False
        self._audio_bytes = 0
        self._audio_queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=128)
        self._audio_task = (
            asyncio.create_task(self._forward_audio())
            if transcriber is not None
            else None
        )

    async def emit(
        self, event: dict[str, Any], *, generation: int | None = None
    ) -> None:
        async with self._send_lock:
            if self._closed or (
                generation is not None and generation != self._generation
            ):
                return
            self._sequence += 1
            await self.send({**event, "seq": self._sequence})

    async def error(self, message: str, turn_id: str | None = None) -> None:
        await self.emit({"type": "error", "turn_id": turn_id, "message": message})

    @staticmethod
    def _turn_id(message: dict[str, Any]) -> str:
        value = message.get("turn_id")
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise ValueError(
                "turn_id must be a non-empty string of at most 128 characters"
            )
        return value

    def _claim(self, turn_id: str) -> bool:
        if turn_id in self._seen:
            return False
        if len(self._seen) >= MAX_TURNS:
            raise ValueError("Conversation limit reached; reconnect with a new session")
        self._seen.add(turn_id)
        return True

    async def receive(self, message: Any) -> None:
        """Process one JSON message. Invalid messages never start model work."""
        async with self._input_lock:
            if self._closed:
                return
            try:
                if not isinstance(message, dict):
                    raise TypeError("Expected a JSON object")
                event_type = message.get("type")
                if event_type == "interrupt":
                    requested = message.get("turn_id")
                    if requested is not None:
                        requested = self._turn_id(message)
                    # A late interrupt for a previous generation must not stop a new one.
                    if requested is None or requested in {
                        self.active_turn_id,
                        self.audio_turn_id,
                    }:
                        await self._interrupt()
                        await self._discard_audio()
                    return
                turn_id = self._turn_id(message)
                if event_type == "text":
                    text = message.get("text")
                    if (
                        not isinstance(text, str)
                        or not text.strip()
                        or len(text) > 8000
                    ):
                        raise ValueError(
                            "text must contain between 1 and 8000 characters"
                        )
                    if not self._claim(turn_id):
                        return
                    await self._interrupt()
                    await self._discard_audio()
                    await self._start(turn_id, text.strip())
                elif event_type == "audio.append":
                    if self.transcriber is None:
                        raise ValueError(
                            "This connection was not started in voice mode"
                        )
                    audio = message.get("audio")
                    if not isinstance(audio, str) or not audio or len(audio) > 87384:
                        raise ValueError(
                            "audio must contain a base64 PCM chunk of at most 64 KiB"
                        )
                    try:
                        raw = base64.b64decode(audio, validate=True)
                    except (ValueError, TypeError) as exc:
                        raise ValueError("audio must be valid base64 PCM16") from exc
                    if not raw or len(raw) > 65536 or len(raw) % 2:
                        raise ValueError("audio must contain complete PCM16 samples")
                    if turn_id != self.audio_turn_id:
                        if not self._claim(turn_id):
                            return
                        await self._interrupt()
                        await self._discard_audio()
                        self.audio_turn_id = turn_id
                    if self._audio_committed_at is not None:
                        return
                    if self._audio_bytes + len(raw) > SAMPLE_RATE * 2 * 30:
                        raise ValueError("A voice utterance cannot exceed 30 seconds")
                    self._queue_audio(
                        {"type": "append", "turn_id": turn_id, "audio": audio}
                    )
                    self._audio_bytes += len(raw)
                elif event_type == "audio.commit":
                    if self.transcriber is None:
                        raise ValueError(
                            "This connection was not started in voice mode"
                        )
                    if turn_id != self.audio_turn_id:
                        if turn_id in self._seen:
                            return
                        raise ValueError("No audio has been appended for this turn")
                    if self._audio_committed_at is not None:
                        return
                    if self._audio_bytes < SAMPLE_RATE * 2 // 10:
                        raise ValueError("At least 100 ms of audio is required")
                    self._queue_audio({"type": "commit", "turn_id": turn_id})
                    self._audio_committed_at = perf_counter()
                    await self.emit({"type": "turn.started", "turn_id": turn_id})
                elif event_type == "playback.started":
                    if turn_id == self.active_turn_id:
                        self._playback_started = True
                elif event_type == "playback.completed":
                    if (
                        turn_id == self.active_turn_id
                        and self._completed
                        and self._playback_started
                    ):
                        await self.confirm_delivery(self.session_id, turn_id)
                        self.active_turn_id = None
                else:
                    raise ValueError("Unknown streaming message type")
            except (ValueError, TypeError) as exc:
                turn_id = message.get("turn_id") if isinstance(message, dict) else None
                if (
                    isinstance(message, dict)
                    and message.get("type") in {"audio.append", "audio.commit"}
                    and turn_id == self.audio_turn_id
                ):
                    await self._discard_audio()
                await self.error(str(exc), turn_id)

    def _queue_audio(self, message: dict[str, str]) -> None:
        try:
            self._audio_queue.put_nowait(message)
        except asyncio.QueueFull as exc:
            raise ValueError(
                "Speech connection is too slow; reconnect and try again"
            ) from exc

    async def _forward_audio(self) -> None:
        """Serialize provider buffers without blocking interruption or socket reads."""
        provider_turn: str | None = None
        assert self.transcriber is not None
        while True:
            message = await self._audio_queue.get()
            turn_id = message["turn_id"]
            try:
                if message["type"] == "clear":
                    if provider_turn == turn_id:
                        await self.transcriber.clear()
                        provider_turn = None
                    continue
                if turn_id != self.audio_turn_id:
                    continue
                if message["type"] == "append":
                    if provider_turn != turn_id:
                        await self.transcriber.clear()
                        provider_turn = turn_id
                    await self.transcriber.append(turn_id, message["audio"])
                else:
                    await self.transcriber.commit(turn_id)
                    provider_turn = None
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — contain provider failures at the transport boundary
                async with self._input_lock:
                    if turn_id == self.audio_turn_id:
                        await self.error(
                            "Speech recognition failed; reconnect to continue", turn_id
                        )
                        await self._discard_audio()
            finally:
                self._audio_queue.task_done()

    async def transcript(self, event: dict[str, Any]) -> None:
        """Handle ASR events; only the currently accepted utterance may route."""
        async with self._input_lock:
            turn_id = event.get("turn_id")
            if turn_id is None or turn_id != self.audio_turn_id:
                return
            event_type = event.get("type")
            if event_type == "transcript.delta":
                await self.emit(event)
            elif event_type == "transcript.final":
                if self._audio_committed_at is None:
                    return
                text = event.get("text", "")
                started = self._audio_committed_at
                self.audio_turn_id = None
                self._audio_committed_at = None
                if not isinstance(text, str) or not text.strip():
                    await self.error("No speech detected", turn_id)
                    return
                if len(text) > 8000:
                    await self.error("Transcript exceeds 8000 characters", turn_id)
                    return
                await self._start(
                    turn_id, text.strip(), started=started, already_started=True
                )
            elif event_type == "error":
                await self.error("Speech recognition failed; please try again", turn_id)
                await self._discard_audio()

    async def _start(
        self,
        turn_id: str,
        text: str,
        *,
        started: float | None = None,
        already_started: bool = False,
    ) -> None:
        self.active_turn_id = turn_id
        self._completed = False
        self._playback_started = False
        if not already_started:
            await self.emit({"type": "turn.started", "turn_id": turn_id})
        await self.emit({"type": "transcript.final", "turn_id": turn_id, "text": text})
        stt_ms = (perf_counter() - started) * 1000 if started is not None else 0
        self._task = asyncio.create_task(
            self._run(
                turn_id, text, self._generation, started or perf_counter(), stt_ms
            )
        )

    async def _run(
        self, turn_id: str, text: str, generation: int, started: float, stt_ms: float
    ) -> None:
        def current() -> bool:
            return generation == self._generation and not self._closed

        async def emit(event: dict[str, Any]) -> None:
            if current():
                await self.emit({**event, "turn_id": turn_id}, generation=generation)

        async def on_route(decision: RoutingDecision) -> None:
            await emit({"type": "route", "decision": decision.model_dump()})

        try:
            result = await self.service.turn(
                session_id=self.session_id,
                text=text,
                stt_ms=stt_ms,
                on_route=on_route,
                delivery_id=turn_id,
            )
            if not current():
                return
            await emit({"type": "reply", "result": result.model_dump()})
            tts_started = perf_counter()
            first_audio_ms: float | None = None

            async def synthesize() -> None:
                nonlocal first_audio_ms
                # HTTP chunk boundaries need not coincide with PCM16 sample boundaries.
                remainder = b""
                async with aclosing(
                    self.speak(result.reply, audio_format="pcm")
                ) as stream:
                    async for chunk in stream:
                        if not current():
                            return
                        payload = remainder + chunk
                        boundary = len(payload) - len(payload) % 2
                        remainder = payload[boundary:]
                        payload = payload[:boundary]
                        if not payload:
                            continue
                        if first_audio_ms is None:
                            first_audio_ms = round((perf_counter() - started) * 1000, 1)
                        await emit(
                            {
                                "type": "audio.delta",
                                "audio": base64.b64encode(payload).decode("ascii"),
                                "sample_rate": SAMPLE_RATE,
                            }
                        )
                if remainder:
                    raise ValueError("Incomplete PCM sample from synthesis provider")
                if first_audio_ms is None:
                    raise ValueError("Empty synthesis output")

            await asyncio.wait_for(synthesize(), timeout=self.tts_timeout)
            if not current():
                return
            result.timings.tts_ms = round((perf_counter() - tts_started) * 1000, 1)
            result.timings.total_ms = round((perf_counter() - started) * 1000, 1)
            self._completed = True
            await emit(
                {
                    "type": "turn.completed",
                    "result": result.model_dump(),
                    "metrics": {
                        "first_audio_ms": first_audio_ms,
                        "tts_ms": result.timings.tts_ms,
                        "total_ms": result.timings.total_ms,
                    },
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — contain provider failures at the transport boundary
            # Upstream exception strings may contain credentials, request bodies or URLs.
            logger.warning("Streaming turn failed", extra={"turn_id": turn_id})
            if current():
                await self.interrupt_delivery(self.session_id, turn_id)
                self.active_turn_id = None
                await emit(
                    {"type": "error", "message": "Voice turn failed; please try again"}
                )

    async def _discard_audio(self) -> None:
        pending = self.audio_turn_id
        self.audio_turn_id = None
        self._audio_committed_at = None
        self._audio_bytes = 0
        if pending is not None and self.transcriber is not None:
            with suppress(asyncio.QueueFull):
                self._audio_queue.put_nowait({"type": "clear", "turn_id": pending})
            await self.emit({"type": "turn.cancelled", "turn_id": pending})

    async def _interrupt(self) -> None:
        turn_id = self.active_turn_id
        self.active_turn_id = None
        self._generation += 1
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if turn_id is not None:
            await self.interrupt_delivery(self.session_id, turn_id)
            await self.emit({"type": "turn.cancelled", "turn_id": turn_id})

    async def close(self) -> None:
        self._closed = True
        await self._interrupt()
        await self._discard_audio()
        if self._audio_task is not None:
            self._audio_task.cancel()
            await asyncio.gather(self._audio_task, return_exceptions=True)
