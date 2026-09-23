from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from telephony import CallState, create_telephony_router, register_exception_handlers
from telephony.ports import RoutingDecision

from .conftest import BASE_URL, sign

VOICE_URL = f"{BASE_URL}/telephony/webhooks/twilio/voice"
STATUS_URL = f"{BASE_URL}/telephony/webhooks/twilio/status"


def start_call(client, key="key-1", **overrides):
    body = {"to": "+1 555 000 2222", "scenario": "support", **overrides}
    return client.post("/telephony/calls", json=body, headers={"Idempotency-Key": key})


# --- outbound --------------------------------------------------------------


def test_create_call_returns_202_with_masked_numbers(client, provider):
    response = start_call(client)
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == CallState.DIALING.value
    assert data["to"] == "+1555***2222"
    assert "5550002222" not in response.text  # never hand back the full number
    assert provider.started[0].to_number == "+15550002222"


def test_create_call_is_idempotent(client, provider):
    first = start_call(client, key="same").json()
    second = start_call(client, key="same").json()
    assert first["call_id"] == second["call_id"]
    assert len(provider.started) == 1  # the carrier was dialled once


def test_idempotency_key_is_required(client):
    assert client.post("/telephony/calls", json={"to": "+15550002222"}).status_code == 422


def test_invalid_number_is_422(client):
    assert start_call(client, to="12").status_code == 422


def test_concurrency_limit_is_enforced(client, settings):
    for i in range(settings.max_concurrent_calls):
        assert start_call(client, key=f"k{i}").status_code == 202
    assert start_call(client, key="over").status_code == 403


# --- read ------------------------------------------------------------------


def test_get_and_list_calls(client):
    call_id = start_call(client).json()["call_id"]
    assert client.get(f"/telephony/calls/{call_id}").json()["call_id"] == call_id

    listing = client.get("/telephony/calls", params={"status": "dialing"}).json()
    assert [c["call_id"] for c in listing["items"]] == [call_id]
    assert client.get("/telephony/calls", params={"status": "completed"}).json()["items"] == []


def test_unknown_call_is_404(client):
    assert client.get("/telephony/calls/00000000-0000-0000-0000-000000000000").status_code == 404


def test_list_pagination_uses_a_cursor(client):
    for i in range(3):
        start_call(client, key=f"p{i}")
    first = client.get("/telephony/calls", params={"limit": 2}).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    rest = client.get(
        "/telephony/calls", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert len(rest["items"]) == 1 and rest["next_cursor"] is None


# --- control ---------------------------------------------------------------


def test_transfer_checks_the_allowlist(client, provider):
    call_id = start_call(client).json()["call_id"]
    ok = client.post(f"/telephony/calls/{call_id}/transfer", json={"target": "tel:+15550003333"})
    assert ok.status_code == 200
    assert provider.transferred[0][1] == "+15550003333"

    denied = client.post(
        f"/telephony/calls/{call_id}/transfer", json={"target": "tel:+445550003333"}
    )
    assert denied.status_code == 403


def test_hangup_is_idempotent(client, provider):
    call_id = start_call(client).json()["call_id"]
    assert client.post(f"/telephony/calls/{call_id}/hangup").json()["status"] == "ending"
    again = client.post(f"/telephony/calls/{call_id}/hangup")
    assert again.status_code == 200
    assert len(provider.hungup) == 1


def test_cancel_before_connect(client):
    call_id = start_call(client).json()["call_id"]
    assert client.post(f"/telephony/calls/{call_id}/cancel").json()["status"] == "canceled"


def test_cancel_after_connect_is_rejected(client, telephony):
    call_id = start_call(client).json()["call_id"]
    params = {"CallSid": _sid(telephony, call_id), "CallStatus": "in-progress"}
    client.post(STATUS_URL, data=params, headers=sign(STATUS_URL, params))
    assert client.post(f"/telephony/calls/{call_id}/cancel").status_code == 403


# --- webhooks --------------------------------------------------------------


def _sid(telephony, call_id) -> str:
    from uuid import UUID

    return telephony.calls._calls[UUID(call_id)].provider_call_id


def test_inbound_call_is_answered_with_twiml(client):
    params = {
        "CallSid": "CAinbound1",
        "CallStatus": "ringing",
        "Direction": "inbound",
        "From": "+15550009999",
        "To": "+15550001111",
    }
    response = client.post(VOICE_URL, data=params, headers=sign(VOICE_URL, params))
    assert response.status_code == 200
    # sips:, not sip: -- OpenAI rejects a Live SIP call whose media is not SRTP.
    assert "<Dial><Sip>sips:proj_test@sip.api.openai.com?" in response.text
    assert client.get("/telephony/calls", params={"direction": "inbound"}).json()["items"]


def test_outbound_answer_webhook_reuses_the_existing_call(client, telephony):
    call_id = start_call(client).json()["call_id"]
    sid = _sid(telephony, call_id)
    url = f"{VOICE_URL}?call_id={call_id}"
    params = {"CallSid": sid, "CallStatus": "in-progress", "Direction": "outbound-api"}

    response = client.post(url, data=params, headers=sign(url, params))

    assert response.status_code == 200
    # the SIP bridge carries our call id back, so S7 can link the session
    assert f"X-Telephony-Call-Id={call_id}</Sip>" in response.text
    assert client.get(f"/telephony/calls/{call_id}").json()["status"] == "connected"
    assert len(client.get("/telephony/calls").json()["items"]) == 1


def test_inbound_call_can_be_rejected_by_policy(settings, provider, telephony):
    class RejectAll:
        async def route(self, event):
            return RoutingDecision(accept=False)

    telephony.routing = RejectAll()
    app = FastAPI()
    app.include_router(create_telephony_router(telephony=telephony))
    register_exception_handlers(app)
    from fastapi.testclient import TestClient

    params = {"CallSid": "CAinbound2", "CallStatus": "ringing", "Direction": "inbound"}
    with TestClient(app) as local:
        response = local.post(VOICE_URL, data=params, headers=sign(VOICE_URL, params))
    assert "<Reject" in response.text


def test_status_webhook_advances_the_call(client, telephony):
    call_id = start_call(client).json()["call_id"]
    sid = _sid(telephony, call_id)
    for status_value, expected in (("ringing", "ringing"), ("in-progress", "connected")):
        params = {"CallSid": sid, "CallStatus": status_value, "Direction": "outbound-api"}
        posted = client.post(STATUS_URL, data=params, headers=sign(STATUS_URL, params))
        assert posted.status_code == 204
        assert client.get(f"/telephony/calls/{call_id}").json()["status"] == expected


def test_replayed_webhook_has_no_second_effect(client, telephony):
    call_id = start_call(client).json()["call_id"]
    sid = _sid(telephony, call_id)
    params = {"CallSid": sid, "CallStatus": "completed", "Direction": "outbound-api"}
    headers = sign(STATUS_URL, params)
    client.post(STATUS_URL, data=params, headers=headers)
    before = client.get(f"/telephony/calls/{call_id}").json()
    client.post(STATUS_URL, data=params, headers=headers)  # Twilio retry
    assert client.get(f"/telephony/calls/{call_id}").json() == before


def test_out_of_order_webhook_does_not_roll_back(client, telephony):
    call_id = start_call(client).json()["call_id"]
    sid = _sid(telephony, call_id)
    for status_value in ("in-progress", "ringing"):
        params = {"CallSid": sid, "CallStatus": status_value, "Direction": "outbound-api"}
        client.post(STATUS_URL, data=params, headers=sign(STATUS_URL, params))
    assert client.get(f"/telephony/calls/{call_id}").json()["status"] == "connected"


def test_bad_signature_is_rejected(client):
    params = {"CallSid": "CAx", "CallStatus": "ringing", "Direction": "inbound"}
    bad = {"X-Twilio-Signature": "nope", "Content-Type": "application/x-www-form-urlencoded"}
    assert client.post(VOICE_URL, data=params, headers=bad).status_code == 403


def test_missing_signature_is_rejected(client):
    params = {"CallSid": "CAx", "CallStatus": "ringing", "Direction": "inbound"}
    assert client.post(VOICE_URL, data=params).status_code == 403


# --- auth ------------------------------------------------------------------


def test_management_requires_host_auth_but_webhooks_do_not(telephony):
    def require_key(x_api_key: str | None = None):
        if x_api_key != "secret":
            raise HTTPException(401, "unauthorized")

    app = FastAPI()
    app.include_router(
        create_telephony_router(telephony=telephony, dependencies=[Depends(require_key)])
    )
    register_exception_handlers(app)
    from fastapi.testclient import TestClient

    with TestClient(app) as local:
        assert local.get("/telephony/calls").status_code == 401
        params = {"CallSid": "CAauth", "CallStatus": "ringing", "Direction": "inbound"}
        hook = local.post(VOICE_URL, data=params, headers=sign(VOICE_URL, params))
        assert hook.status_code == 200


# --- health ----------------------------------------------------------------


def test_health(client):
    assert client.get("/telephony/health/live").json()["status"] == "ok"
    ready = client.get("/telephony/health/ready").json()
    assert ready["checks"]["provider"] is True
    assert ready["status"] == "degraded"  # no OpenAI key in the test settings
