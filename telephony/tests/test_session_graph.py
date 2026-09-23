from __future__ import annotations

import pytest

from telephony import InvalidLiveEvent, LiveSessionGraph, LiveSessionNode


def test_graph_routes_both_transcript_speakers_in_delivery_order() -> None:
    graph = LiveSessionGraph()

    user = graph.route(
        {
            "type": "session.input_transcript.delta",
            "delta": "Hello",
            "start_ms": 10,
            "end_ms": 40,
        }
    )
    assistant = graph.route(
        {
            "type": "session.output_transcript.delta",
            "delta": "Hi",
            "start_ms": 45,
            "end_ms": 70,
        }
    )

    assert user.node is LiveSessionNode.TRANSCRIPT
    assert user.transcript is graph.state.transcripts[0]
    assert [item.speaker for item in graph.state.transcripts] == ["user", "assistant"]
    assert assistant.transcript is graph.state.transcripts[1]


def test_graph_replaces_cumulative_usage_instead_of_summing() -> None:
    graph = LiveSessionGraph()

    graph.route({"type": "session.usage.updated", "usage": {"seconds": 2.5}})
    update = graph.route({"type": "session.usage.updated", "usage": {"seconds": 4}})

    assert update.node is LiveSessionNode.USAGE
    assert graph.state.usage.voice_seconds == 4.0
    assert graph.state.usage.final is False


def test_graph_routes_delegation_metadata_without_inventing_task_text() -> None:
    graph = LiveSessionGraph()

    update = graph.route(
        {
            "type": "session.delegation.created",
            "event_id": "evt_1",
            "offset_ms": 1250,
            "delegation": {
                "type": "delegation",
                "id": "dlg_1",
                "target": "client",
            },
        }
    )

    assert update.node is LiveSessionNode.DELEGATION
    assert update.delegation is not None
    assert update.delegation.delegation_id == "dlg_1"
    assert update.delegation.offset_ms == 1250
    assert graph.state.closed is False


def test_error_is_recorded_without_closing_the_graph() -> None:
    graph = LiveSessionGraph()

    update = graph.route(
        {
            "type": "error",
            "event_id": "evt_error",
            "error": {
                "type": "invalid_request_error",
                "code": "unknown_parameter",
                "message": "Unknown parameter.",
                "client_event_id": "cmd_1",
            },
        }
    )

    assert update.node is LiveSessionNode.ERROR
    assert update.error is not None
    assert update.error.client_event_id == "cmd_1"
    assert graph.state.closed is False


def test_close_sets_final_usage_and_makes_graph_terminal() -> None:
    graph = LiveSessionGraph()

    closed = graph.route(
        {
            "type": "session.closed",
            "event_id": "evt_close",
            "reason": "remote_hangup",
            "session": {"id": "live_1"},
            "usage": {"seconds": 8.25},
        }
    )
    late = graph.route(
        {
            "type": "session.input_transcript.delta",
            "delta": "late",
            "start_ms": 9,
            "end_ms": 10,
        }
    )

    assert closed.node is LiveSessionNode.CLOSE
    assert closed.close_reason == "remote_hangup"
    assert graph.state.usage.voice_seconds == 8.25
    assert graph.state.usage.final is True
    assert graph.state.closed is True
    assert late.node is LiveSessionNode.IGNORED
    assert graph.state.transcripts == []


@pytest.mark.parametrize(
    "event",
    [
        {"type": "session.input_transcript.delta", "delta": "x", "start_ms": 2},
        {"type": "session.usage.updated", "usage": {"seconds": -1}},
        {
            "type": "session.delegation.created",
            "offset_ms": 0,
            "delegation": {"id": "dlg_1", "target": "elsewhere"},
        },
        {"type": "error", "event_id": "evt_1", "error": {"code": "bad"}},
        {"type": "session.closed", "reason": "expired"},
    ],
)
def test_graph_rejects_malformed_known_events(event: dict[str, object]) -> None:
    with pytest.raises(InvalidLiveEvent):
        LiveSessionGraph().route(event)


@pytest.mark.parametrize("event", [{}, {"type": "session.started"}, {"type": "future.event"}])
def test_graph_tolerates_unknown_events(event: dict[str, object]) -> None:
    update = LiveSessionGraph().route(event)

    assert update.node is LiveSessionNode.IGNORED
