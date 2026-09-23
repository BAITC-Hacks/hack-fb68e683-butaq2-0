from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace

import pytest

from telephony import (
    CallDirection,
    CallQuery,
    CallState,
    LiveIncomingCall,
    OpenAILiveTelephony,
    RoutingDecision,
    TelephonySettings,
    WebhookVerificationError,
)
from telephony.ports import RejectReason


class FakeWebhooks:
    def __init__(self, event: Mapping[str, object] | Exception) -> None:
        self.event = event
        self.calls: list[tuple[bytes, Mapping[str, str], str]] = []

    def unwrap(self, body: bytes, headers: Mapping[str, str], *, secret: str):
        self.calls.append((body, headers, secret))
        if isinstance(self.event, Exception):
            raise self.event
        return self.event


class FakeSessions:
    def __init__(self) -> None:
        self.accepted: list[tuple[str, dict]] = []
        self.rejected: list[tuple[str, int]] = []

    async def accept(self, session_id: str, *, session: dict) -> None:
        self.accepted.append((session_id, session))

    async def reject(self, session_id: str, *, status_code: int) -> None:
        self.rejected.append((session_id, status_code))


class FakeClient:
    def __init__(self, event: Mapping[str, object] | Exception) -> None:
        self.webhooks = FakeWebhooks(event)
        self.sessions = FakeSessions()
        self.live = SimpleNamespace(sessions=self.sessions)


def live_settings(**overrides: object) -> TelephonySettings:
    return TelephonySettings(
        _env_file=None,
        openai_webhook_secret="whsec_test",
        live_model="gpt-live-1",
        instructions="Answer support calls.",
        voice="marin",
        **overrides,
    )


def incoming_event(event_type: str = "live.transport.incoming") -> dict[str, object]:
    data: dict[str, object] = {
        "session_id": "live_session_123",
        "sip_headers": [
            {"name": "From", "value": "sip:+15550001111@sip.example.com"},
            {"name": "To", "value": "<sip:+15550002222@sip.example.com>"},
            {"name": "Authorization", "value": "must-not-escape"},
        ],
    }
    if event_type == "live.transport.incoming":
        data["type"] = "sip"
    return {"id": "evt_body", "type": event_type, "data": data}


def test_verifies_and_sanitizes_live_sip_webhook() -> None:
    client = FakeClient(incoming_event())
    adapter = OpenAILiveTelephony(live_settings(), client=client)

    incoming = adapter.verify_webhook(b"{}", {"Webhook-Id": "wh_delivery_1"})

    assert incoming == LiveIncomingCall(
        event_id="wh_delivery_1",
        session_id="live_session_123",
        from_number="+15550001111",
        to_number="+15550002222",
    )
    assert client.webhooks.calls[0][2] == "whsec_test"
    assert not hasattr(incoming, "sip_headers")


def test_supports_legacy_live_incoming_event_during_migration() -> None:
    adapter = OpenAILiveTelephony(
        live_settings(), client=FakeClient(incoming_event("live.call.incoming"))
    )

    assert adapter.verify_webhook(b"{}", {"webhook-id": "wh_legacy"}) is not None


def test_ignores_non_sip_and_unrelated_webhooks() -> None:
    non_sip = incoming_event()
    non_sip["data"]["type"] = "webrtc"  # type: ignore[index]
    assert (
        OpenAILiveTelephony(live_settings(), client=FakeClient(non_sip)).verify_webhook(
            b"{}", {"webhook-id": "wh_1"}
        )
        is None
    )
    assert (
        OpenAILiveTelephony(
            live_settings(),
            client=FakeClient({"id": "evt_1", "type": "response.completed", "data": {}}),
        ).verify_webhook(b"{}", {"webhook-id": "wh_2"})
        is None
    )


def test_signature_failure_is_a_domain_error() -> None:
    adapter = OpenAILiveTelephony(live_settings(), client=FakeClient(ValueError("bad signature")))

    with pytest.raises(WebhookVerificationError, match="verification failed"):
        adapter.verify_webhook(b"{}", {"webhook-id": "wh_1"})


async def test_accept_uses_live_session_builder_and_reject_validates_status() -> None:
    client = FakeClient(incoming_event())
    adapter = OpenAILiveTelephony(live_settings(), client=client)

    await adapter.accept("live_session_123", voice="quartz")
    await adapter.reject("live_session_456", status_code=486)

    session_id, session = client.sessions.accepted[0]
    assert session_id == "live_session_123"
    assert session == {
        "type": "live",
        "model": "gpt-live-1",
        "instructions": "Answer support calls.",
        "audio": {"output": {"voice": "quartz"}},
        "delegation": {"type": "client"},
        "store": False,
    }
    assert client.sessions.rejected == [("live_session_456", 486)]
    with pytest.raises(ValueError, match="between 300 and 699"):
        await adapter.reject("live_session_789", status_code=200)


class RecordingLiveCalls:
    def __init__(self, incoming: LiveIncomingCall) -> None:
        self.incoming = incoming
        self.accepted: list[str] = []
        self.rejected: list[tuple[str, int]] = []

    def verify_webhook(self, raw_body: bytes, headers: Mapping[str, str]):
        return self.incoming

    async def accept(self, session_id: str, **_: object) -> None:
        self.accepted.append(session_id)

    async def reject(self, session_id: str, *, status_code: int) -> None:
        self.rejected.append((session_id, status_code))


async def test_live_incoming_accepts_once_and_persists_session(telephony) -> None:
    live_calls = RecordingLiveCalls(
        LiveIncomingCall("wh_live_1", "live_session_1", "+15550001111", "+15550002222")
    )
    telephony.live_calls = live_calls

    first = await telephony.handle_live_incoming_call(live_calls.incoming)
    duplicate = await telephony.handle_live_incoming_call(live_calls.incoming)

    assert first is duplicate
    assert live_calls.accepted == ["live_session_1"]
    calls = await telephony.list_calls(CallQuery(direction=CallDirection.INBOUND))
    assert len(calls.items) == 1
    assert calls.items[0].state is CallState.ACCEPTED


async def test_live_incoming_rejects_with_sip_busy(telephony) -> None:
    class RejectBusy:
        async def route(self, event):
            return RoutingDecision(accept=False, reason=RejectReason(code="busy"))

    live_calls = RecordingLiveCalls(LiveIncomingCall("wh_live_2", "live_session_2"))
    telephony.live_calls = live_calls
    telephony.routing = RejectBusy()

    call = await telephony.handle_live_incoming_call(live_calls.incoming)

    assert call is not None and call.state is CallState.REJECTED
    assert live_calls.accepted == []
    assert live_calls.rejected == [("live_session_2", 486)]


async def test_failed_accept_fails_the_call_without_failing_the_webhook(telephony) -> None:
    """A SIP invite is one-shot: an OpenAI retry would only find it expired."""

    class FailingLiveCalls(RecordingLiveCalls):
        async def accept(self, session_id: str, **_: object) -> None:
            raise RuntimeError("srtp_required")

    incoming = LiveIncomingCall("wh_retry", "live_retry")
    telephony.live_calls = FailingLiveCalls(incoming)

    call = await telephony.handle_live_incoming_call(incoming)

    assert call.state is CallState.FAILED
    assert "live_accept_failed" in (call.terminal_reason or "")
    # the event stays claimed, so a retry is a no-op instead of a second failure
    assert await telephony.inbox.claim("openai", "wh_retry", 60) is False
