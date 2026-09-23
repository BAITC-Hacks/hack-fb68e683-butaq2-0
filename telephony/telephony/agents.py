"""Backend agent contract.

GPT-Live runs the conversation; the backend agent does the thinking. The
delegation loop hands it a :class:`~telephony.ports.CallContext` and streams
back :class:`AgentUpdate` values, which map one-to-one onto the GPT-Live
``session.thinking.append`` / ``session.commentary.append`` /
``session.instructions.append`` events.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .domain import DelegationTask
from .ports import CallContext


@dataclass(slots=True)
class AgentRequest:
    task: DelegationTask
    instructions: str | None = None


@dataclass(slots=True)
class ThinkingUpdate:
    """Facts for further reasoning. The voice model must not read these out."""

    text: str


@dataclass(slots=True)
class CommentaryUpdate:
    """A verified result GPT-Live should say to the caller."""

    text: str


@dataclass(slots=True)
class InstructionUpdate:
    """System-level intervention: disclosure, stop, hand-off wording."""

    text: str


@dataclass(slots=True)
class TaskCompleted:
    result: Any = None


@dataclass(slots=True)
class TaskFailed:
    error: str


@dataclass(slots=True)
class TaskCancelled:
    reason: str | None = None


AgentUpdate = (
    ThinkingUpdate
    | CommentaryUpdate
    | InstructionUpdate
    | TaskCompleted
    | TaskFailed
    | TaskCancelled
)


class InvalidAgentUpdate(ValueError):
    """An agent emitted a value that cannot be sent to GPT-Live safely."""


_EVENT_BY_TYPE = {
    ThinkingUpdate: "session.thinking.append",
    CommentaryUpdate: "session.commentary.append",
    InstructionUpdate: "session.instructions.append",
}
_AGENT_UPDATE_TYPES = (
    ThinkingUpdate,
    CommentaryUpdate,
    InstructionUpdate,
    TaskCompleted,
    TaskFailed,
    TaskCancelled,
)


def _truncate_tokens(text: str, max_tokens: int) -> str:
    """Cut to the GPT-Live per-append budget.

    Trade-off: 4 chars/token heuristic, no tokenizer dependency. Swap in
    tiktoken if a scenario ever needs the exact boundary.
    """

    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    limit = max_tokens * 4
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def to_live_event(
    update: AgentUpdate, delegation_id: str, *, max_tokens: int = 500
) -> dict[str, Any] | None:
    """Render an update as a GPT-Live sideband event, or None if it carries no text."""

    if type(update) not in _AGENT_UPDATE_TYPES:
        raise InvalidAgentUpdate(f"unsupported agent update: {type(update).__name__}")
    if not isinstance(delegation_id, str) or not delegation_id.strip():
        raise ValueError("delegation_id must not be empty")

    event_type = _EVENT_BY_TYPE.get(type(update))
    if event_type is None:
        if isinstance(update, TaskFailed) and (
            not isinstance(update.error, str) or not update.error.strip()
        ):
            raise InvalidAgentUpdate("TaskFailed.error must not be empty")
        if (
            isinstance(update, TaskCancelled)
            and update.reason is not None
            and (not isinstance(update.reason, str) or not update.reason.strip())
        ):
            raise InvalidAgentUpdate(
                "TaskCancelled.reason must be non-empty when provided"
            )
        return None

    assert isinstance(update, (ThinkingUpdate, CommentaryUpdate, InstructionUpdate))
    text = update.text
    if not isinstance(text, str) or not text.strip():
        raise InvalidAgentUpdate(f"{type(update).__name__}.text must not be empty")
    return {
        "type": event_type,
        "delegation_id": delegation_id,
        "content": _truncate_tokens(text, max_tokens),
    }


@runtime_checkable
class AgentRuntime(Protocol):
    """The backend brain behind a delegation."""

    def run(self, request: AgentRequest, context: CallContext) -> AsyncIterator[AgentUpdate]: ...


class CallableAgentRuntime:
    """Wrap a plain function as an agent runtime -- no framework required.

    The function takes ``(request, context)`` and may return a string, an
    ``AgentUpdate``, a list of updates, or async-yield them one by one.
    """

    def __init__(self, fn: Callable[[AgentRequest, CallContext], Any]) -> None:
        self._fn = fn

    async def run(
        self, request: AgentRequest, context: CallContext
    ) -> AsyncIterator[AgentUpdate]:
        result = self._fn(request, context)
        if inspect.isawaitable(result):
            result = await result
        if inspect.isasyncgen(result):
            async for item in result:
                yield _coerce(item)
            yield TaskCompleted()
            return
        if isinstance(result, (list, tuple)):
            for item in result:
                yield _coerce(item)
        elif result is not None:
            yield _coerce(result)
        yield TaskCompleted(result if isinstance(result, (str, dict)) else None)


def _coerce(value: Any) -> AgentUpdate:
    update = CommentaryUpdate(value) if isinstance(value, str) else value
    if type(update) not in _AGENT_UPDATE_TYPES:
        raise InvalidAgentUpdate(f"unsupported agent update: {type(update).__name__}")
    return update
