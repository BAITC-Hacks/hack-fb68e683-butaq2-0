"""Conversation memory for pipeline turns.

Kept deliberately small: a session is an ordered list of ``{role, content}``
messages plus free-form metadata. Implement :class:`SessionStore` over Redis or
Postgres for multi-worker deployments.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class Message:
    role: str  # "user" | "assistant" | "system"
    content: str
    created_at: str = field(default_factory=_now)


@dataclass(slots=True)
class Session:
    session_id: str
    messages: list[Message] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    last_response_id: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def as_input(self, limit: int) -> list[dict[str, str]]:
        """Render history in the shape the Responses API takes as ``input``."""

        return [{"role": m.role, "content": m.content} for m in self.messages[-limit:]]


@runtime_checkable
class SessionStore(Protocol):
    async def get_or_create(self, session_id: str) -> Session: ...
    async def append(self, session_id: str, *messages: Message) -> Session: ...
    async def get(self, session_id: str) -> Session | None: ...
    async def delete(self, session_id: str) -> bool: ...
    async def set_response_id(self, session_id: str, response_id: str | None) -> None: ...


class InMemorySessionStore:
    def __init__(self, max_messages: int = 200) -> None:
        self._data: dict[str, Session] = {}
        self._max = max_messages
        self._lock = asyncio.Lock()

    async def get_or_create(self, session_id: str) -> Session:
        async with self._lock:
            session = self._data.get(session_id)
            if session is None:
                session = Session(session_id=session_id)
                self._data[session_id] = session
            return session

    async def append(self, session_id: str, *messages: Message) -> Session:
        session = await self.get_or_create(session_id)
        async with self._lock:
            session.messages.extend(messages)
            if len(session.messages) > self._max:
                del session.messages[: len(session.messages) - self._max]
            session.updated_at = _now()
            return session

    async def get(self, session_id: str) -> Session | None:
        return self._data.get(session_id)

    async def delete(self, session_id: str) -> bool:
        async with self._lock:
            return self._data.pop(session_id, None) is not None

    async def set_response_id(self, session_id: str, response_id: str | None) -> None:
        session = await self.get_or_create(session_id)
        session.last_response_id = response_id
        session.updated_at = _now()
