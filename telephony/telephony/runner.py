"""Single-process lifecycle manager for OpenAI Live sideband sessions."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, replace
from uuid import UUID

from .agents import AgentRuntime
from .config import TelephonySettings, get_settings
from .delegation import DelegationExecutor, LiveFinalizationResult, LiveSessionCoordinator
from .domain import Call
from .ports import CallContext, LiveCallController, LiveFinalizer, LiveGateway, LiveStateStore
from .session_graph import LiveSessionGraph
from .tools import ToolRegistry

# Grace between asking GPT-Live to close and dropping the leg ourselves.
_CLOSE_GRACE_SECONDS = 10.0


@dataclass(slots=True)
class _RunningSession:
    task: asyncio.Task[None] | None = None
    coordinator: LiveSessionCoordinator | None = None
    close_requested: bool = False


class LiveSessionRunner:
    """Run one supervised sideband task per call inside the API process."""

    def __init__(
        self,
        *,
        gateway: LiveGateway,
        runtime: AgentRuntime,
        live_state: LiveStateStore,
        registry: ToolRegistry | None = None,
        live_calls: LiveCallController | None = None,
        settings: TelephonySettings | None = None,
        replace_delegations: bool = False,
    ) -> None:
        self._gateway = gateway
        self._runtime = runtime
        self._live_state = live_state
        self._registry = registry
        self._live_calls = live_calls
        self._settings = settings or get_settings()
        self._sessions: dict[UUID, _RunningSession] = {}
        self._lock = asyncio.Lock()
        self._closing = False
        self._replace_delegations = replace_delegations

    async def stop(self, call_id: UUID) -> None:
        """Cancel local work immediately after a carrier terminal event or forced hangup."""
        running = self._sessions.get(call_id)
        if running and running.task and running.task is not asyncio.current_task():
            running.task.cancel("call ended")
            await asyncio.gather(running.task, return_exceptions=True)

    async def start(self, call: Call, *, on_finalized: LiveFinalizer) -> bool:
        """Schedule a call once. Return False when it is already running."""

        if not call.session_id or not call.session_id.strip():
            raise ValueError("call.session_id must not be empty")

        async with self._lock:
            if self._closing:
                raise RuntimeError("LiveSessionRunner is closing")
            current = self._sessions.get(call.call_id)
            if current is not None and current.task is not None and not current.task.done():
                return False

            running = _RunningSession()
            self._sessions[call.call_id] = running
            running.task = asyncio.create_task(
                self._run(call, running, on_finalized),
                name=f"live-session:{call.call_id}",
            )
            running.task.add_done_callback(self._consume_task_result)
            return True

    async def request_close(self, call_id: UUID) -> bool:
        """Close now, or remember the request until sideband is attached."""

        async with self._lock:
            running = self._sessions.get(call_id)
            if running is None or running.task is None or running.task.done():
                return False
            coordinator = running.coordinator
            if coordinator is None:
                if running.close_requested:
                    return False
                running.close_requested = True
                return True

        return await coordinator.request_close()

    async def aclose(self, *, drain_timeout: float = 30.0) -> None:
        """Request graceful closure, then cancel only sessions that miss the deadline."""

        if drain_timeout < 0:
            raise ValueError("drain_timeout must be non-negative")
        async with self._lock:
            self._closing = True
            call_ids = tuple(self._sessions)

        for call_id in call_ids:
            with suppress(Exception):
                await self.request_close(call_id)

        async with self._lock:
            tasks = tuple(
                running.task
                for running in self._sessions.values()
                if running.task is not None and not running.task.done()
            )
        if not tasks:
            return

        _, pending = await asyncio.wait(tasks, timeout=drain_timeout)
        for task in pending:
            task.cancel("LiveSessionRunner shutdown")
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _run(
        self,
        call: Call,
        running: _RunningSession,
        on_finalized: LiveFinalizer,
    ) -> None:
        try:
            result: LiveFinalizationResult
            try:
                context = await self._context_for(call)
                async with self._gateway.attach(call.session_id or "") as connection:
                    coordinator = LiveSessionCoordinator(
                        graph=LiveSessionGraph(),
                        delegations=DelegationExecutor(
                            self._runtime,
                            max_append_tokens=self._settings.max_append_tokens,
                        ),
                        context=context,
                        connection=connection,
                        replace_delegations=self._replace_delegations,
                    )
                    async with self._lock:
                        if self._sessions.get(call.call_id) is not running:
                            return
                        running.coordinator = coordinator
                        close_requested = running.close_requested
                    if close_requested:
                        await coordinator.request_close()
                    result = await self._run_until_deadline(coordinator, call)
            except asyncio.CancelledError:
                await self._force_hangup(call)
                raise
            except Exception as exc:
                result = LiveFinalizationResult(
                    status="unconfirmed",
                    usage=replace(call.usage),
                    reason="sideband_error",
                    transport_error=f"{type(exc).__name__}: {exc}",
                )
            if not result.confirmed:
                await self._force_hangup(call)
            await on_finalized(call.call_id, result)
        finally:
            if self._registry is not None:
                self._registry.forget_call(call.call_id)
            await self._live_state.delete(call.call_id)
            async with self._lock:
                if self._sessions.get(call.call_id) is running:
                    self._sessions.pop(call.call_id, None)

    async def _run_until_deadline(
        self, coordinator: LiveSessionCoordinator, call: Call
    ) -> LiveFinalizationResult:
        """Consume the session, but never past ``max_call_seconds``.

        Nothing else bounds a direct Live SIP call: the carrier time limit only
        applies to provider-owned legs, so a wedged session would bill until
        someone noticed. Ask GPT-Live to close first; drop the leg only if it
        will not.
        """

        run = asyncio.create_task(coordinator.run(), name="live-session-run")
        try:
            done, _ = await asyncio.wait({run}, timeout=self._settings.max_call_seconds)
            if done:
                return run.result()
            await coordinator.request_close()
            done, _ = await asyncio.wait({run}, timeout=_CLOSE_GRACE_SECONDS)
            if done:
                return run.result()
            return LiveFinalizationResult(
                status="unconfirmed", usage=replace(coordinator.graph.state.usage),
                reason="max_call_seconds", transport_error="call exceeded max_call_seconds",
            )
        finally:
            if not run.done():
                run.cancel("session owner stopped")
            await asyncio.gather(run, return_exceptions=True)

    async def _force_hangup(self, call: Call) -> None:
        """Drop the SIP leg itself; closing the sideband does not end the call."""

        if self._live_calls is None or not call.session_id:
            return
        with suppress(Exception):
            await self._live_calls.hangup(call.session_id)

    async def _context_for(self, call: Call) -> CallContext:
        existing = await self._live_state.load_context(call.call_id)
        if existing is None:
            context = CallContext(
                call_id=call.call_id,
                scenario=call.scenario,
                state=call.state,
                metadata=dict(call.metadata),
            )
        else:
            context = replace(
                existing,
                scenario=call.scenario,
                state=call.state,
                metadata={**existing.metadata, **call.metadata},
                application_state=dict(existing.application_state),
                transcript=list(existing.transcript),
            )
        await self._live_state.save_context(context)
        return context

    @staticmethod
    def _consume_task_result(task: asyncio.Task[None]) -> None:
        """Retrieve background exceptions so asyncio never reports an orphaned task."""

        if not task.cancelled():
            task.exception()
