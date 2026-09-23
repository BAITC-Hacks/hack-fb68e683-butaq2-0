from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from uuid import uuid4

import pytest

from telephony import (
    CallContext,
    CallState,
    CommentaryUpdate,
    DelegationExecutor,
    LiveSessionCoordinator,
    LiveSessionGraph,
)


class StreamConnection:
    def __init__(
        self,
        events: list[Mapping[str, object]] | None = None,
        *,
        failure: Exception | None = None,
    ) -> None:
        self._events = events or []
        self._failure = failure
        self.sent: list[dict[str, object]] = []

    async def events(self) -> AsyncIterator[Mapping[str, object]]:
        for event in self._events:
            yield event
        if self._failure is not None:
            raise self._failure

    async def send(self, event: Mapping[str, object]) -> None:
        self.sent.append(dict(event))


class IdleRuntime:
    async def run(self, request, context):
        if False:
            yield CommentaryUpdate("unused")


def coordinator(connection: StreamConnection) -> LiveSessionCoordinator:
    return LiveSessionCoordinator(
        graph=LiveSessionGraph(),
        delegations=DelegationExecutor(IdleRuntime()),
        context=CallContext(
            call_id=uuid4(),
            scenario="support",
            state=CallState.CONNECTED,
        ),
        connection=connection,
    )


async def test_closed_event_confirms_final_usage_and_reason() -> None:
    session = coordinator(
        StreamConnection(
            [
                {"type": "session.usage.updated", "usage": {"seconds": 2.5}},
                {
                    "type": "session.closed",
                    "reason": "client_request",
                    "usage": {"seconds": 3.75},
                },
            ]
        )
    )

    result = await session.run()

    assert result.confirmed is True
    assert result.status == "confirmed"
    assert result.usage.voice_seconds == 3.75
    assert result.usage.final is True
    assert result.reason == "client_request"
    assert result.transport_error is None


async def test_stream_eof_before_closed_is_unconfirmed() -> None:
    session = coordinator(
        StreamConnection(
            [{"type": "session.usage.updated", "usage": {"seconds": 2.5}}]
        )
    )

    result = await session.run()

    assert result.confirmed is False
    assert result.status == "unconfirmed"
    assert result.usage.voice_seconds == 2.5
    assert result.usage.final is False
    assert result.reason is None
    assert result.transport_error is None


async def test_transport_read_failure_is_an_unconfirmed_result() -> None:
    result = await coordinator(
        StreamConnection(failure=ConnectionError("sideband disconnected"))
    ).run()

    assert result.status == "unconfirmed"
    assert result.transport_error == "sideband disconnected"


async def test_request_close_sends_exact_event_once() -> None:
    connection = StreamConnection()
    session = coordinator(connection)

    assert await session.request_close(event_id="close-1") is True
    assert await session.request_close(event_id="close-2") is False
    assert connection.sent == [{"type": "session.close", "event_id": "close-1"}]

    with pytest.raises(ValueError, match="event_id must not be empty"):
        await coordinator(StreamConnection()).request_close(event_id=" ")


async def test_unconfirmed_end_cancels_active_delegations() -> None:
    class SlowRuntime:
        async def run(self, request, context):
            await asyncio.Event().wait()
            yield CommentaryUpdate("never")

    connection = StreamConnection(
        [
            {
                "type": "session.delegation.created",
                "offset_ms": 0,
                "delegation": {"id": "delegation-1", "target": "client"},
            }
        ]
    )
    executor = DelegationExecutor(SlowRuntime())
    session = LiveSessionCoordinator(
        graph=LiveSessionGraph(),
        delegations=executor,
        context=CallContext(
            call_id=uuid4(),
            scenario=None,
            state=CallState.CONNECTED,
        ),
        connection=connection,
    )

    result = await session.run()

    assert result.status == "unconfirmed"
    assert executor.active_delegation_ids == frozenset()
