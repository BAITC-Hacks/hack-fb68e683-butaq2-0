from __future__ import annotations

import asyncio
from contextlib import suppress
from uuid import uuid4

from telephony import (
    CallContext,
    CallState,
    CommentaryUpdate,
    DelegationExecutor,
    LiveSessionCoordinator,
    LiveSessionGraph,
    TaskCancelled,
    TaskCompleted,
    ThinkingUpdate,
    TranscriptFragment,
)


class RecordingConnection:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send(self, event):
        self.sent.append(dict(event))

    async def events(self):
        if False:
            yield {}


class RecordingRuntime:
    def __init__(self) -> None:
        self.requests = []
        self.contexts = []

    async def run(self, request, context):
        self.requests.append(request)
        self.contexts.append(context)
        yield ThinkingUpdate("verified internally")
        yield CommentaryUpdate("The verified result")
        yield TaskCompleted()


def context() -> CallContext:
    return CallContext(
        call_id=uuid4(),
        scenario="support",
        state=CallState.CONNECTED,
    )


async def test_coordinator_runs_client_delegation_and_preserves_id():
    runtime = RecordingRuntime()
    connection = RecordingConnection()
    executor = DelegationExecutor(runtime)
    coordinator = LiveSessionCoordinator(
        graph=LiveSessionGraph(),
        delegations=executor,
        context=context(),
        connection=connection,
    )
    await coordinator.process(
        {
            "type": "session.input_transcript.delta",
            "delta": "Where is my order?",
            "start_ms": 10,
            "end_ms": 100,
        }
    )
    await coordinator.process(
        {
            "type": "session.delegation.created",
            "offset_ms": 100,
            "delegation": {"id": "delegation-1", "target": "client"},
        }
    )
    assert isinstance(await executor.wait("delegation-1"), TaskCompleted)

    assert [event["type"] for event in connection.sent] == [
        "session.thinking.append",
        "session.commentary.append",
    ]
    assert {event["delegation_id"] for event in connection.sent} == {"delegation-1"}
    assert connection.sent[1]["content"] == "The verified result"
    assert runtime.requests[0].task.arguments == {"offset_ms": 100}
    assert [part.text for part in runtime.contexts[0].transcript] == [
        "Where is my order?"
    ]


async def test_context_excludes_transcript_after_delegation_offset():
    runtime = RecordingRuntime()
    connection = RecordingConnection()
    graph = LiveSessionGraph()
    graph.state.transcripts.extend(
        [
            TranscriptFragment("user", "before", 0, 20),
            TranscriptFragment("assistant", "after", 200, 250),
        ]
    )
    executor = DelegationExecutor(runtime)
    coordinator = LiveSessionCoordinator(
        graph=graph,
        delegations=executor,
        context=context(),
        connection=connection,
    )
    await coordinator.process(
        {
            "type": "session.delegation.created",
            "offset_ms": 100,
            "delegation": {"id": "delegation-1", "target": "client"},
        }
    )
    await executor.wait("delegation-1")
    assert [part.text for part in runtime.contexts[0].transcript] == ["before"]


async def test_cancel_suppresses_late_agent_updates():
    release = asyncio.Event()

    class StubbornRuntime:
        async def run(self, request, context):
            with suppress(asyncio.CancelledError):
                await release.wait()
            yield CommentaryUpdate("too late")

    connection = RecordingConnection()
    executor = DelegationExecutor(StubbornRuntime())
    delegation = LiveSessionGraph().route(
        {
            "type": "session.delegation.created",
            "offset_ms": 0,
            "delegation": {"id": "delegation-1", "target": "client"},
        }
    ).delegation
    assert delegation is not None
    task = await executor.start(delegation, context(), connection)
    await asyncio.sleep(0)
    assert await executor.cancel("delegation-1") is True
    assert isinstance(await task, TaskCancelled)
    assert connection.sent == []


async def test_session_close_cancels_active_delegations():
    started = asyncio.Event()

    class SlowRuntime:
        async def run(self, request, context):
            started.set()
            await asyncio.Event().wait()
            yield CommentaryUpdate("never")

    connection = RecordingConnection()
    executor = DelegationExecutor(SlowRuntime())
    coordinator = LiveSessionCoordinator(
        graph=LiveSessionGraph(),
        delegations=executor,
        context=context(),
        connection=connection,
    )
    await coordinator.process(
        {
            "type": "session.delegation.created",
            "offset_ms": 0,
            "delegation": {"id": "delegation-1", "target": "client"},
        }
    )
    await started.wait()
    await coordinator.process(
        {
            "type": "session.closed",
            "reason": "client_request",
            "usage": {"seconds": 1.0},
        }
    )
    assert executor.active_delegation_ids == frozenset()
    assert connection.sent == []
