"""In-memory adapters: the default profile for tests and single-process demos.

Production wiring uses Redis + Postgres implementations of the same ports.
"""

from __future__ import annotations

import asyncio
import time
from uuid import UUID, uuid4

from .domain import Call, CallNotFound, CallState, CallTransition, TranscriptFragment, now_iso
from .ports import (
    CallContext,
    CallQuery,
    OutboundCallCommand,
    Page,
    ProviderCall,
    ProviderEvent,
    RejectReason,
)


class InMemoryCallRepository:
    def __init__(self) -> None:
        self._calls: dict[UUID, Call] = {}
        self._lock = asyncio.Lock()

    async def create(self, call: Call) -> Call:
        async with self._lock:
            self._calls[call.call_id] = call
            return call

    async def get(self, call_id: UUID) -> Call | None:
        return self._calls.get(call_id)

    async def find_by_provider_id(self, provider_call_id: str) -> Call | None:
        return next(
            (c for c in self._calls.values() if c.provider_call_id == provider_call_id), None
        )

    async def find_by_idempotency_key(self, key: str) -> Call | None:
        return next((c for c in self._calls.values() if c.idempotency_key == key), None)

    async def save(self, call: Call) -> Call:
        async with self._lock:
            call.updated_at = now_iso()
            self._calls[call.call_id] = call
            return call

    async def transition(self, call_id: UUID, transition: CallTransition) -> Call:
        async with self._lock:
            call = self._calls.get(call_id)
            if call is None:
                raise CallNotFound(str(call_id))
            call.apply(transition)
            return call

    async def list(self, query: CallQuery) -> Page:
        items = sorted(self._calls.values(), key=lambda c: c.created_at, reverse=True)
        if query.state:
            items = [c for c in items if c.state is query.state]
        if query.direction:
            items = [c for c in items if c.direction is query.direction]
        if query.scenario:
            items = [c for c in items if c.scenario == query.scenario]
        start = 0
        if query.cursor:
            ids = [str(c.call_id) for c in items]
            start = ids.index(query.cursor) + 1 if query.cursor in ids else 0
        window = items[start : start + query.limit]
        more = items[start + query.limit :]
        return Page(items=window, next_cursor=str(window[-1].call_id) if more and window else None)

    async def count_active(self) -> int:
        return sum(1 for c in self._calls.values() if not c.is_terminal)


class InMemoryEventInbox:
    """Dedup by ``(source, event_id)`` with a TTL, like the Redis version."""

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], float] = {}
        self._lock = asyncio.Lock()

    async def claim(self, source: str, event_id: str, ttl_seconds: int) -> bool:
        key = (source, event_id)
        now = time.monotonic()
        async with self._lock:
            for stale, expiry in [(k, v) for k, v in self._seen.items() if v <= now]:
                del self._seen[stale]
                del expiry
            if key in self._seen:
                return False
            self._seen[key] = now + ttl_seconds
            return True

    async def release(self, source: str, event_id: str) -> None:
        async with self._lock:
            self._seen.pop((source, event_id), None)


class InMemoryLiveStateStore:
    def __init__(self) -> None:
        self._contexts: dict[UUID, CallContext] = {}

    async def append_transcript(self, call_id: UUID, fragment: TranscriptFragment) -> None:
        context = self._contexts.get(call_id)
        if context is None:
            return
        context.transcript.append(fragment)
        # Order by media time -- deltas arrive out of order on a live socket.
        context.transcript.sort(key=lambda f: (f.start_ms, f.end_ms))

    async def load_context(self, call_id: UUID) -> CallContext | None:
        return self._contexts.get(call_id)

    async def save_context(self, context: CallContext) -> None:
        self._contexts[context.call_id] = context

    async def delete(self, call_id: UUID) -> None:
        self._contexts.pop(call_id, None)


class FakeProvider:
    """A provider that records calls instead of dialling. Demos and tests only."""

    def __init__(self) -> None:
        self.started: list[OutboundCallCommand] = []
        self.hungup: list[str] = []
        self.transferred: list[tuple[str, str]] = []
        self.rejected: list[tuple[str, RejectReason]] = []

    async def start_call(self, command: OutboundCallCommand) -> ProviderCall:
        self.started.append(command)
        return ProviderCall(provider_call_id=f"FAKE{uuid4().hex[:24]}", status=CallState.DIALING)

    async def reject(self, provider_call_id: str, reason: RejectReason) -> None:
        self.rejected.append((provider_call_id, reason))

    async def transfer(self, provider_call_id: str, target: str) -> None:
        self.transferred.append((provider_call_id, target))

    async def hangup(self, provider_call_id: str) -> None:
        self.hungup.append(provider_call_id)

    def verify_webhook(self, raw_body: bytes, headers, url: str) -> ProviderEvent:
        raise NotImplementedError("FakeProvider does not receive webhooks")

    def answer_response(self, call: Call) -> tuple[str, str]:
        return ("<Response/>", "application/xml")
