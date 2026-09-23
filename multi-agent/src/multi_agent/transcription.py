"""Bounded PCM transcription stream, with provider item IDs bound to client turns."""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import os
from collections.abc import AsyncIterator
from typing import Any

from typing_extensions import Self

SAMPLE_RATE = 24000
MAX_CHUNK_BYTES = 65536
MAX_TURN_BYTES = SAMPLE_RATE * 2 * 30
MIN_TURN_BYTES = SAMPLE_RATE * 2 // 10
MAX_TURNS = 256


class RealtimeTranscriber:
    """One transcription-only provider connection per browser connection.

    Commit waits for the provider item ID before accepting another input buffer.
    This allows deltas before commit, while out-of-order finals remain attached
    to the correct turn. The caller decides whether a cancelled turn is obsolete.
    """

    def __init__(self, client: Any, *, model: str | None = None):
        self._client = client
        self.model = model or os.getenv(
            "MULTI_AGENT_TRANSCRIBE_MODEL", "gpt-live-transcribe"
        )
        self._manager: Any = None
        self._connection: Any = None
        self._pump_task: asyncio.Task | None = None
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=128)
        self._current: str | None = None
        self._bytes = 0
        self._used: set[str] = set()
        self._items: dict[str, str] = {}
        self._quarantined_items: set[str] = set()
        self._finished: set[str] = set()
        self._committing: asyncio.Future | None = None
        self._failure: Exception | None = None
        self._accept_early_deltas = True

    async def __aenter__(self) -> Self:
        self._manager = self._client.realtime.connect(
            extra_query={"intent": "transcription"}
        )
        try:
            self._connection = await asyncio.wait_for(self._manager.__aenter__(), 10)
            await self._connection.send(
                {
                    "type": "session.update",
                    "session": {
                        "type": "transcription",
                        "audio": {
                            "input": {
                                "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                                "transcription": {
                                    "model": self.model,
                                    "languages": ["ru", "kk"],
                                    "prompt": "Insurance support in Russian and Kazakh, including mixed speech. Preserve spoken policy and refund identifiers such as DEMO-P-1001 and DEMO-R-4001 without guessing digits.",
                                    "delay": "low",
                                },
                                "turn_detection": None,
                            }
                        },
                    },
                }
            )
            await asyncio.wait_for(self._ready(), 10)
            self._pump_task = asyncio.create_task(self._pump())
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def _ready(self) -> None:
        while True:
            event = self._dict(await self._connection.recv())
            if event.get("type") in {
                "session.updated",
                "transcription_session.updated",
            }:
                return
            if event.get("type") == "error":
                raise RuntimeError("Streaming transcription configuration was rejected")

    async def __aexit__(self, *_: object) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump_task
        if self._committing is not None and not self._committing.done():
            self._committing.cancel()
        if self._connection is not None:
            await self._manager.__aexit__(None, None, None)
            self._connection = None

    def _check_connection(self) -> None:
        if self._failure:
            raise RuntimeError(
                "Streaming transcription connection failed"
            ) from self._failure
        if self._connection is None:
            raise RuntimeError("Streaming transcription is not connected")

    async def append(self, turn_id: str, audio: str) -> None:
        self._check_connection()
        if not isinstance(turn_id, str) or not turn_id.strip() or len(turn_id) > 128:
            raise ValueError("Invalid input turn ID")
        if not isinstance(audio, str) or len(audio) > MAX_CHUNK_BYTES * 4 // 3 + 4:
            raise ValueError("Audio chunk is too large")
        try:
            data = base64.b64decode(audio, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Audio must be base64 PCM16") from exc
        if not data or len(data) % 2 or len(data) > MAX_CHUNK_BYTES:
            raise ValueError("Audio must contain complete PCM16 samples")
        if self._current is None:
            if turn_id in self._used:
                raise ValueError("Input turn ID has already been used")
            if len(self._used) >= MAX_TURNS:
                raise ValueError("Start a new voice session after 256 turns")
            self._current = turn_id
            self._used.add(turn_id)
        if turn_id != self._current or self._committing is not None:
            raise ValueError("Commit or clear the previous input first")
        if self._bytes + len(data) > MAX_TURN_BYTES:
            raise ValueError("A voice utterance cannot exceed 30 seconds")
        self._bytes += len(data)
        await self._connection.send(
            {"type": "input_audio_buffer.append", "audio": audio}
        )

    async def commit(self, turn_id: str) -> None:
        self._check_connection()
        if turn_id != self._current:
            if turn_id in self._used:
                return  # An already committed input is never transcribed twice.
            raise ValueError("No audio for this input turn")
        if self._bytes < MIN_TURN_BYTES:
            raise ValueError("At least 100 ms of audio is required")
        self._committing = asyncio.get_running_loop().create_future()
        try:
            await self._connection.send({"type": "input_audio_buffer.commit"})
            await asyncio.wait_for(self._committing, 10)
        except BaseException:
            # The item association is ambiguous after a failed commit. Require a
            # new connection rather than accidentally assigning a late final.
            self._failure = RuntimeError("Audio commit was not acknowledged")
            raise
        finally:
            self._committing = None
            self._current = None
            self._bytes = 0

    async def clear(self) -> None:
        self._check_connection()
        if self._current is not None:
            self._finished.add(self._current)
            # After clearing an uncommitted input, unknown late item IDs cannot
            # safely be assigned until the next commit acknowledgement.
            self._accept_early_deltas = False
            await self._connection.send({"type": "input_audio_buffer.clear"})
            self._current = None
            self._bytes = 0

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    @staticmethod
    def _dict(event: Any) -> dict[str, Any]:
        return event if isinstance(event, dict) else event.model_dump()

    async def _pump(self) -> None:
        try:
            async for raw in self._connection:
                event = self._dict(raw)
                kind = event.get("type", "")
                item_id = event.get("item_id")
                if kind == "error":
                    raise RuntimeError(
                        "Speech recognition provider rejected the stream"
                    )
                if kind == "input_audio_buffer.committed":
                    if not self._current or not item_id or self._committing is None:
                        raise RuntimeError("Unexpected audio commit acknowledgement")
                    self._items[item_id] = self._current
                    # The acknowledgement identifies the new buffer reliably,
                    # even if its early deltas were quarantined after a clear.
                    self._quarantined_items.discard(item_id)
                    self._accept_early_deltas = True
                    if not self._committing.done():
                        self._committing.set_result(item_id)
                    continue
                prefix = "conversation.item.input_audio_transcription."
                if not kind.startswith(prefix) or not item_id:
                    continue
                if item_id in self._quarantined_items:
                    continue
                if item_id not in self._items:
                    if not self._accept_early_deltas:
                        # Keep late events from an abandoned input out of later
                        # turns after early deltas resume at the next commit.
                        self._quarantined_items.add(item_id)
                        continue
                    if self._current is None:
                        continue
                    self._items[item_id] = self._current
                turn_id = self._items[item_id]
                if turn_id in self._finished:
                    continue
                if kind.endswith(".delta"):
                    await self._queue.put(
                        {
                            "type": "transcript.delta",
                            "turn_id": turn_id,
                            "delta": event.get("delta", ""),
                        }
                    )
                elif kind.endswith(".completed"):
                    self._finished.add(turn_id)
                    await self._queue.put(
                        {
                            "type": "transcript.final",
                            "turn_id": turn_id,
                            "text": event.get("transcript", "").strip(),
                        }
                    )
                elif kind.endswith(".failed"):
                    self._finished.add(turn_id)
                    await self._queue.put(
                        {
                            "type": "error",
                            "turn_id": turn_id,
                            "message": "Не удалось распознать речь. Повторите фразу.",
                        }
                    )
            raise RuntimeError("Streaming transcription disconnected")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — contain provider failures at the stream boundary
            self._failure = exc
            if self._committing is not None and not self._committing.done():
                self._committing.set_exception(RuntimeError("Audio commit failed"))
            await self._queue.put(
                {
                    "type": "error",
                    "message": "Поток распознавания прерван. Начните голосовой сеанс заново.",
                    "fatal": True,
                }
            )
            await self._queue.put(None)
