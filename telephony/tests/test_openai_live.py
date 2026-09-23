from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest

from telephony import OpenAILiveGateway, OpenAILiveSessionBuilder, TelephonySettings


def settings(**overrides: object) -> TelephonySettings:
    values = {
        "live_model": "gpt-live-1",
        "instructions": "Keep answers concise.",
        "voice": "marin",
        **overrides,
    }
    return TelephonySettings(_env_file=None, **values)


def test_builder_creates_client_delegated_session() -> None:
    session = OpenAILiveSessionBuilder(settings()).build()

    assert session == {
        "type": "live",
        "model": "gpt-live-1",
        "instructions": "Keep answers concise.",
        "audio": {"output": {"voice": "marin"}},
        "delegation": {"type": "client"},
        "store": False,
    }


def test_builder_applies_per_call_prompt_and_voice() -> None:
    session = OpenAILiveSessionBuilder(settings()).build(
        instructions="Speak Kazakh.",
        voice="quartz",
    )

    assert session["instructions"] == "Speak Kazakh."
    assert session["audio"]["output"]["voice"] == "quartz"


def test_builder_returns_a_fresh_payload() -> None:
    builder = OpenAILiveSessionBuilder(settings())
    first = builder.build()
    first["audio"]["output"]["voice"] = "changed"

    assert builder.build()["audio"]["output"]["voice"] == "marin"


@pytest.mark.parametrize(
    "overrides",
    [
        {"live_model": " "},
        {"instructions": ""},
        {"voice": "\t"},
    ],
)
def test_builder_rejects_blank_config(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        OpenAILiveSessionBuilder(settings(**overrides)).build()


@pytest.mark.parametrize("field", ["instructions", "voice"])
def test_builder_rejects_blank_call_overrides(field: str) -> None:
    with pytest.raises(ValueError):
        OpenAILiveSessionBuilder(settings()).build(**{field: " "})


class FakeSDKEvent:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = payload

    def model_dump(self, **options: Any) -> Mapping[str, Any]:
        assert options == {"mode": "json", "by_alias": True, "exclude_none": True}
        return self.payload


class FakeSDKConnection:
    def __init__(self, events: list[Any] | None = None) -> None:
        self.incoming = events or []
        self.sent: list[dict[str, Any]] = []

    async def __aiter__(self) -> AsyncIterator[Any]:
        for event in self.incoming:
            yield event

    async def send(self, event: Mapping[str, Any]) -> None:
        self.sent.append(dict(event))


class FakeConnectionManager:
    def __init__(self, connection: FakeSDKConnection) -> None:
        self.connection = connection
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> FakeSDKConnection:
        self.entered = True
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


class FakeSideband:
    def __init__(self, manager: FakeConnectionManager) -> None:
        self.manager = manager
        self.connect_calls: list[dict[str, Any]] = []

    def connect(self, **options: Any) -> FakeConnectionManager:
        self.connect_calls.append(options)
        return self.manager


class FakeLive:
    def __init__(self, sideband: FakeSideband) -> None:
        self.sideband = sideband


class FakeOpenAIClient:
    def __init__(self, manager: FakeConnectionManager) -> None:
        self.live = FakeLive(FakeSideband(manager))


def fake_transport(
    events: list[Any] | None = None,
) -> tuple[FakeOpenAIClient, FakeConnectionManager, FakeSDKConnection]:
    connection = FakeSDKConnection(events)
    manager = FakeConnectionManager(connection)
    return FakeOpenAIClient(manager), manager, connection


async def test_gateway_attaches_with_graceful_close_and_manages_lifecycle() -> None:
    client, manager, raw_connection = fake_transport()
    gateway = OpenAILiveGateway(settings(), client=client)

    async with gateway.attach("live_123") as connection:
        assert manager.entered is True
        await connection.send({"type": "session.close", "event_id": "event_1"})

    assert client.live.sideband.connect_calls == [
        {"session_id": "live_123", "graceful_close": True}
    ]
    assert raw_connection.sent == [{"type": "session.close", "event_id": "event_1"}]
    assert manager.exited is True


async def test_gateway_can_disable_graceful_close() -> None:
    client, _, _ = fake_transport()
    gateway = OpenAILiveGateway(settings(), client=client, graceful_close=False)

    async with gateway.attach("live_123"):
        pass

    assert client.live.sideband.connect_calls[0]["graceful_close"] is False


async def test_connection_normalizes_mapping_and_sdk_events() -> None:
    client, _, _ = fake_transport(
        [
            {"type": "session.started", "event_id": "event_1"},
            FakeSDKEvent({"type": "session.closed", "event_id": "event_2"}),
        ]
    )
    gateway = OpenAILiveGateway(settings(), client=client)

    async with gateway.attach("live_123") as connection:
        events = [event async for event in connection.events()]

    assert events == [
        {"type": "session.started", "event_id": "event_1"},
        {"type": "session.closed", "event_id": "event_2"},
    ]


async def test_connection_rejects_unknown_sdk_event_shape() -> None:
    client, _, _ = fake_transport([object()])
    gateway = OpenAILiveGateway(settings(), client=client)

    async with gateway.attach("live_123") as connection:
        with pytest.raises(TypeError, match="unsupported OpenAI Live event"):
            await anext(connection.events())


async def test_gateway_rejects_blank_session_id_before_connecting() -> None:
    client, manager, _ = fake_transport()
    gateway = OpenAILiveGateway(settings(), client=client)

    with pytest.raises(ValueError, match="session_id"):
        async with gateway.attach("  "):
            pass

    assert manager.entered is False
    assert client.live.sideband.connect_calls == []
