"""WebSocket adapter tests; no provider or network connections are opened."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from multi_agent.contracts import Catalog, RoutingDecision
from starlette.websockets import WebSocketDisconnect

from app.api.dependencies import get_service
from app.main import app
from app.services.voice_router import RouterService


@pytest.fixture
def service():
    class Gateway:
        async def route(self, context, config):
            return RoutingDecision(
                action="clarify",
                confidence=0.5,
                reason="Ambiguous",
                customer_message="Какой полис?",
                language="ru",
            )

        async def resolve(self, context, config):
            raise AssertionError("Clarification does not run Resolution")

    class Pipeline:
        settings = SimpleNamespace(tts_format="mp3")

        @property
        def client(self):
            raise AssertionError("Text sockets must never open ASR")

        async def speak(self, text, *, audio_format):
            assert audio_format == "pcm"
            yield b"\x01\x02"
            yield b"\x03\x04"

    instance = RouterService(
        Pipeline(),
        catalog=Catalog.from_payload([{"id": "policy", "title": "Policy"}]),
        gateway=Gateway(),
    )
    app.dependency_overrides[get_service] = lambda: instance
    try:
        yield instance
    finally:
        app.dependency_overrides.pop(get_service, None)


def start(socket, session_id="caller", mode="text"):
    socket.send_json({"type": "start", "session_id": session_id, "mode": mode})
    assert socket.receive_json()["type"] == "ready"


def test_websocket_streams_audio_and_completes_delivery(service):
    with (
        TestClient(app) as client,
        client.websocket_connect("/router/stream") as socket,
    ):
        start(socket)
        socket.send_json({"type": "text", "turn_id": "first", "text": "Полис"})
        events = []
        while True:
            event = socket.receive_json()
            events.append(event)
            if event["type"] == "audio.delta":
                socket.send_json({"type": "playback.started", "turn_id": "first"})
            if event["type"] == "turn.completed":
                break
        socket.send_json({"type": "playback.completed", "turn_id": "first"})
        # The next request is a deterministic synchronization point for the ack.
        socket.send_json({"type": "unknown", "turn_id": "first"})
        assert socket.receive_json()["type"] == "error"
        assert service.sessions["caller"].history[-1] == {
            "role": "assistant",
            "content": "Какой полис?",
        }
        assert [event["type"] for event in events] == [
            "turn.started",
            "transcript.final",
            "route",
            "reply",
            "audio.delta",
            "audio.delta",
            "turn.completed",
        ]
    assert not service.stream_sessions


def test_invalid_start_closes_without_claiming_session(service):
    with (
        TestClient(app) as client,
        client.websocket_connect("/router/stream") as socket,
    ):
        socket.send_json({"type": "text", "text": "Полис"})
        assert socket.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as exc:
            socket.receive_json()
        assert exc.value.code == 1008
    assert not service.stream_sessions


def test_duplicate_socket_and_http_session_are_rejected_until_disconnect(service):
    with TestClient(app) as client:
        with client.websocket_connect("/router/stream") as first:
            start(first)
            with client.websocket_connect("/router/stream") as duplicate:
                duplicate.send_json(
                    {"type": "start", "session_id": "caller", "mode": "text"}
                )
                assert "active voice" in duplicate.receive_json()["message"]
            assert "caller" in service.stream_sessions
            assert (
                client.post(
                    "/router/text", json={"session_id": "caller", "text": "Полис"}
                ).status_code
                == 409
            )
        with client.websocket_connect("/router/stream") as next_socket:
            start(next_socket)
    assert not service.stream_sessions


def test_cross_origin_socket_is_rejected_before_ready(service):
    with TestClient(app) as client:
        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect(
                "/router/stream", headers={"origin": "https://unapproved.example"}
            ),
        ):
            pass
        assert exc.value.code == 1008


def test_fatal_transcription_event_closes_connection(service, monkeypatch):
    class Transcriber:
        def __init__(self, client):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def events(self):
            yield {"type": "error", "fatal": True, "message": "secret-provider-error"}

    class VoicePipeline:
        client = object()

        async def speak(self, *args, **kwargs):
            yield b"\x00\x00"

    monkeypatch.setattr("multi_agent.transcription.RealtimeTranscriber", Transcriber)
    service.pipeline = VoicePipeline()
    with (
        TestClient(app) as client,
        client.websocket_connect("/router/stream") as socket,
    ):
        start(socket, mode="voice")
        error = socket.receive_json()
        assert error["type"] == "error" and "secret" not in error["message"]
        with pytest.raises(WebSocketDisconnect) as exc:
            socket.receive_json()
        assert exc.value.code == 1011
    assert not service.stream_sessions
