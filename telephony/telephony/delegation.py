"""Execute client delegations produced by the Live session graph."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import Any, Literal

from .agents import (
    AgentRequest,
    AgentRuntime,
    AgentUpdate,
    TaskCancelled,
    TaskCompleted,
    TaskFailed,
    to_live_event,
)
from .domain import DelegationTask, Usage
from .ports import CallContext, LiveConnection
from .session_graph import LiveDelegation, LiveSessionGraph, LiveSessionNode, LiveSessionUpdate


@dataclass(frozen=True, slots=True)
class LiveFinalizationResult:
    """Authoritative outcome of consuming one Live session stream."""

    status: Literal["confirmed", "unconfirmed"]
    usage: Usage
    reason: str | None = None
    transport_error: str | None = None

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"


class DelegationExecutor:
    """Supervise backend agent tasks and return validated updates to GPT-Live."""

    def __init__(self, runtime: AgentRuntime, *, max_append_tokens: int = 500, context_grace_seconds: float = 0) -> None:
        if max_append_tokens < 1 or max_append_tokens > 500:
            raise ValueError("max_append_tokens must be between 1 and 500")
        if not 0 <= context_grace_seconds <= 1:
            raise ValueError("context_grace_seconds must be between 0 and 1")
        self._context_grace_seconds = context_grace_seconds
        self._runtime = runtime
        self._max_append_tokens = max_append_tokens
        self._tasks: dict[str, asyncio.Task[AgentUpdate]] = {}

    @property
    def active_delegation_ids(self) -> frozenset[str]:
        return frozenset(self._tasks)

    async def start(
        self,
        delegation: LiveDelegation,
        context: CallContext | Callable[[], CallContext],
        connection: LiveConnection,
    ) -> asyncio.Task[AgentUpdate]:
        """Start or replace one delegation task."""

        if delegation.target != "client":
            raise ValueError("only client delegations can run through AgentRuntime")
        await self.cancel(delegation.delegation_id, reason="replaced")
        task = asyncio.create_task(
            self._execute(delegation, context, connection),
            name=f"live-delegation:{delegation.delegation_id}",
        )
        self._tasks[delegation.delegation_id] = task
        task.add_done_callback(
            lambda completed, delegation_id=delegation.delegation_id: self._forget(
                delegation_id, completed
            )
        )
        return task

    async def cancel(self, delegation_id: str, *, reason: str = "cancelled") -> bool:
        """Cancel a running delegation and suppress every later update from it."""

        task = self._tasks.pop(delegation_id, None)
        if task is None:
            return False
        task.cancel(reason)
        with suppress(asyncio.CancelledError):
            await task
        return True

    async def cancel_all(self, *, reason: str = "session closed") -> None:
        for delegation_id in tuple(self._tasks):
            await self.cancel(delegation_id, reason=reason)

    async def wait(self, delegation_id: str) -> AgentUpdate | None:
        """Wait for an active delegation, or return None when it is unknown."""

        task = self._tasks.get(delegation_id)
        return None if task is None else await task

    def _forget(
        self, delegation_id: str, completed: asyncio.Task[AgentUpdate]
    ) -> None:
        if self._tasks.get(delegation_id) is completed:
            self._tasks.pop(delegation_id, None)

    async def _execute(
        self,
        delegation: LiveDelegation,
        context: CallContext | Callable[[], CallContext],
        connection: LiveConnection,
    ) -> AgentUpdate:
        terminal: AgentUpdate = TaskCompleted()
        try:
            # Wait inside the tracked task so transport ingestion and cancellation
            # remain responsive while delayed transcript deltas arrive.
            if self._context_grace_seconds:
                await asyncio.sleep(self._context_grace_seconds)
            context = context() if callable(context) else context
            request = AgentRequest(
                task=DelegationTask(
                    delegation_id=delegation.delegation_id,
                    call_id=context.call_id,
                    instructions="",
                    arguments={"offset_ms": delegation.offset_ms},
                )
            )
            async for update in self._runtime.run(request, context):
                event = to_live_event(
                    update,
                    delegation.delegation_id,
                    max_tokens=self._max_append_tokens,
                )
                if event is not None:
                    current = asyncio.current_task()
                    if self._tasks.get(delegation.delegation_id) is not current:
                        return TaskCancelled("cancelled")
                    await connection.send(event)
                if isinstance(update, (TaskCompleted, TaskFailed, TaskCancelled)):
                    terminal = update
                    break
            return terminal
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return TaskFailed(str(exc))


class LiveSessionCoordinator:
    """Apply graph transitions and dispatch their side effects."""

    def __init__(
        self,
        *,
        graph: LiveSessionGraph,
        delegations: DelegationExecutor,
        context: CallContext,
        connection: LiveConnection,
        replace_delegations: bool = False,
    ) -> None:
        self.graph = graph
        self._delegations = delegations
        self._context = context
        self._connection = connection
        self._close_requested = False
        self._replace_delegations = replace_delegations
        self._seen_delegations: set[str] = set()

    async def run(self) -> LiveFinalizationResult:
        """Consume events until authoritative close or transport termination.

        Only ``session.closed`` confirms finalization. A clean iterator EOF or
        an exception raised while reading the transport produces an
        unconfirmed result with the latest cumulative usage snapshot.
        """

        transport_error: str | None = None
        events = self._connection.events().__aiter__()
        try:
            while True:
                try:
                    event = await anext(events)
                except StopAsyncIteration:
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    transport_error = str(exc)
                    break

                update = await self.process(event)
                if update.node is LiveSessionNode.CLOSE:
                    return self._finalization_result("confirmed")

            return self._finalization_result(
                "unconfirmed", transport_error=transport_error
            )
        finally:
            reason = "session closed" if self.graph.state.closed else "live transport ended"
            await self._delegations.cancel_all(reason=reason)

    async def request_close(self, *, event_id: str | None = None) -> bool:
        """Request graceful close once; confirmation still comes from ``run``."""

        if event_id is not None and not event_id.strip():
            raise ValueError("event_id must not be empty")
        if self.graph.state.closed or self._close_requested:
            return False

        event = {"type": "session.close"}
        if event_id is not None:
            event["event_id"] = event_id
        self._close_requested = True
        await self._delegations.cancel_all(reason="close requested")
        try:
            await self._connection.send(event)
        except BaseException:
            self._close_requested = False
            raise
        return True

    async def process(self, event: Mapping[str, Any]) -> LiveSessionUpdate:
        update = self.graph.route(event)
        if update.node is LiveSessionNode.DELEGATION and update.delegation is not None:
            identifier = update.delegation.delegation_id
            unseen = identifier not in self._seen_delegations
            if update.delegation.target == "client" and not self._close_requested and unseen:
                self._seen_delegations.add(identifier)
                if self._replace_delegations:
                    await self._delegations.cancel_all(reason="superseded by new request")
                await self._delegations.start(
                    update.delegation,
                    lambda offset=update.delegation.offset_ms: self._context_at(offset),
                    self._connection,
                )
        elif update.node is LiveSessionNode.CLOSE:
            await self._delegations.cancel_all()
        return update

    def _context_at(self, offset_ms: int) -> CallContext:
        """Snapshot only transcript fragments known by the delegation offset."""

        transcript = []
        seen = set()
        for fragment in [*self._context.transcript, *self.graph.state.transcripts]:
            identity = (fragment.speaker, fragment.text, fragment.start_ms, fragment.end_ms)
            if fragment.start_ms <= offset_ms and identity not in seen:
                transcript.append(fragment)
                seen.add(identity)
        return replace(
            self._context,
            transcript=transcript,
            application_state=dict(self._context.application_state),
            metadata=dict(self._context.metadata),
        )

    def _finalization_result(
        self,
        status: Literal["confirmed", "unconfirmed"],
        *,
        transport_error: str | None = None,
    ) -> LiveFinalizationResult:
        state = self.graph.state
        return LiveFinalizationResult(
            status=status,
            usage=replace(state.usage),
            reason=state.close_reason,
            transport_error=transport_error,
        )
