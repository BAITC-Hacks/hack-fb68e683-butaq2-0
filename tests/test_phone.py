"""Offline phone integration: real orchestrator and signatures, no paid calls."""

import asyncio
import base64
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4
from xml.etree import ElementTree

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from telephony.agents import AgentRequest
from telephony.delegation import DelegationExecutor, LiveSessionCoordinator
from telephony.domain import (
    CallDirection,
    DelegationTask,
    TranscriptFragment,
    WebhookVerificationError,
)
from telephony.memory import InMemoryLiveStateStore
from telephony.openai_live import OpenAILiveTelephony
from telephony.ports import CallQuery, ProviderEvent
from telephony.router import register_exception_handlers
from telephony.session_graph import LiveSessionGraph
from twilio.request_validator import RequestValidator

from app.api.dependencies import get_database, get_service
from app.main import app
from app.phone.api import router
from app.phone.carrier import SignalWireProvider
from app.phone.runtime import ButaqPhoneRuntime
from app.phone.service import InboundPhoneService, build_phone
from app.phone.settings import PhoneSettings
from app.services.voice_router import RouterService
from telephony import (
    CallContext,
    CallState,
    CommentaryUpdate,
    LiveIncomingCall,
    LiveSessionRunner,
)
from tests.test_router import StubGateway, StubPipeline, catalog, decision


def settings(**changes):
    values = {"_env_file": None, "public_base_url": "https://phone.example.com", "openai_project_id": "proj_test",
              "signalwire_space": "test.signalwire.com", "signalwire_project_id": "project-test",
              "signalwire_api_token": "api-token-test", "signalwire_signing_key": "signing-test",
              "openai_api_key": "sk-test", "openai_webhook_secret": "whsec_" + base64.b64encode(b"test-secret").decode()}
    return PhoneSettings(**(values | changes))


def runtime(outputs, **changes):
    service = RouterService(StubPipeline(), catalog=catalog(), gateway=StubGateway(outputs))
    return ButaqPhoneRuntime(service, settings(**changes))


def request(call_id, identifier="delegation-1", offset=100):
    return AgentRequest(DelegationTask(delegation_id=identifier, call_id=call_id, instructions="not user speech", arguments={"offset_ms": offset}))


def context(call_id, *fragments):
    return CallContext(call_id=call_id, state=CallState.CONNECTED, scenario=None, transcript=list(fragments))


async def updates(runtime, req, ctx):
    return [value async for value in runtime.run(req, ctx)]


def inbound(sid="carrier-1", state=CallState.RINGING):
    return ProviderEvent(source="signalwire", event_id=f"{sid}:{state.value}", kind="incoming", provider_call_id=sid, direction=CallDirection.INBOUND, status=state, from_number="+15550001111", to_number="+15550002222")


def phone_service(rt=None, *, runner=None, **changes):
    rt = rt or runtime([])
    config = settings(**changes)
    provider = SignalWireProvider(config)
    provider.hangup = AsyncMock()
    controller = SimpleNamespace(accept=AsyncMock(), reject=AsyncMock(), hangup=AsyncMock())
    runner = runner or SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), aclose=AsyncMock())
    return InboundPhoneService(config, runtime=rt, provider=provider, live_calls=controller, runner=runner)


def bridge(phone, call, session_id="live-1"):
    xml, _ = phone.provider.answer_response(call)
    uri = ElementTree.fromstring(xml).find("Dial/Sip").text
    token = parse_qs(urlsplit(uri).query)["X-Butaq-Bridge-Token"][0]
    return LiveIncomingCall(event_id="event-1", session_id=session_id, linked_call_id=call.call_id, bridge_token=token)


@pytest.mark.parametrize("language,text", [("ru", "Я оплатил полис"), ("kk", "Полис үшін төледім"), ("mixed", "Полис төледім, но статус не изменился")])
async def test_phone_uses_real_router_and_no_browser_tts(language, text):
    chosen = decision("payment") | {"language": language}
    rt = runtime([chosen, "Проверенный ответ"])
    call_id = uuid4()
    rt.open(call_id)
    pieces = [TranscriptFragment(speaker="user", text=text[:5], start_ms=0, end_ms=20),
              TranscriptFragment(speaker="user", text=text[5:], start_ms=21, end_ms=80)]
    ctx = context(call_id, *pieces, pieces[0], TranscriptFragment(speaker="assistant", text="ignore me", start_ms=0, end_ms=5))
    result = await updates(rt, request(call_id), ctx)
    assert [v.text for v in result if isinstance(v, CommentaryUpdate)] == ["Проверенный ответ"]
    trace = rt.dialogues[call_id].trace.turns[0].result
    assert trace.transcript == text
    assert trace.scenario_id == "payment"
    assert trace.language == language
    assert trace.trace_id and trace.timings.routing_ms >= 0
    assert rt.service.pipeline.spoken == []
    assert len(rt.service.orchestrator.gateway.calls) == 2
    # Same delegation and the same transcript with a new delegation cannot reroute.
    await updates(rt, request(call_id), ctx)
    await updates(rt, request(call_id, "duplicate"), ctx)
    assert len(rt.service.orchestrator.gateway.calls) == 2


async def test_topic_switch_uses_shared_state_and_only_new_user_text():
    rt = runtime([decision("payment", pending=["address"]), "Номер полиса?",
                  decision("address", transition="switch"), "Уточните адрес.",
                  decision("payment", transition="resume"), "Вернёмся к оплате."])
    cid = uuid4()
    rt.open(cid)
    fragments = []
    for index, text in enumerate(["Я оплатил", "Адрес өзгерту керек", "Вернёмся к оплате"]):
        fragments.append(TranscriptFragment(speaker="user", text=text, start_ms=index * 100, end_ms=(index + 1) * 100))
        await updates(rt, request(cid, str(index), (index + 1) * 100), context(cid, *fragments))
    traces = rt.dialogues[cid].trace.turns
    assert [t.result.scenario_id for t in traces] == ["payment", "address", "payment"]
    assert [t.transcript for t in traces] == [f.text for f in fragments]
    assert len(rt.service.sessions[rt.conversation_id(cid)].history) == 6


@pytest.mark.parametrize("language", ["ru", "kk"])
async def test_handoff_is_honest_and_does_not_transfer(language):
    choice = decision("payment") | {"action": "handoff", "scenario_id": None, "language": language}
    rt = runtime([choice])
    cid = uuid4()
    rt.open(cid)
    await updates(rt, request(cid), context(cid, TranscriptFragment(speaker="user", text="Оператор", start_ms=0, end_ms=80)))
    result = rt.dialogues[cid].trace.turns[0].result
    assert result.action == "handoff"
    assert ("қолжетімсіз" if language == "kk" else "недоступно") in result.reply
    assert rt.service.sessions[result.session_id].history[-1]["content"] == result.reply
    assert len(rt.service.orchestrator.gateway.calls) == 1


async def test_failure_does_not_leak_error_or_commit_turn():
    rt = runtime([RuntimeError("SECRET_PROVIDER_ERROR")])
    cid = uuid4()
    rt.open(cid)
    values = await updates(rt, request(cid), context(cid, TranscriptFragment(speaker="user", text="Оплата", start_ms=0, end_ms=80)))
    assert "SECRET" not in str(values) + rt.dialogues[cid].trace.model_dump_json()
    assert rt.dialogues[cid].trace.turns[0].status == "failed"
    assert not rt.service.sessions[rt.conversation_id(cid)].history


async def test_timeout_and_cancellation_do_not_commit_or_send_stale_reply():
    rt = runtime([], delegation_timeout_seconds=0.01)
    rt.service.orchestrator.gateway.route = AsyncMock(side_effect=lambda *args: None)
    entered = asyncio.Event()

    async def slow(*args):
        entered.set()
        await asyncio.Event().wait()

    rt.service.orchestrator.gateway.route = slow
    cid = uuid4()
    rt.open(cid)
    ctx = context(cid, TranscriptFragment(speaker="user", text="Оплата", start_ms=0, end_ms=80))
    await updates(rt, request(cid), ctx)
    assert rt.dialogues[cid].trace.turns[0].status == "failed"
    assert not rt.service.sessions[rt.conversation_id(cid)].history
    rt.settings.delegation_timeout_seconds = 5
    entered.clear()
    ctx.transcript.append(TranscriptFragment(speaker="user", text="Повторяю", start_ms=100, end_ms=180))
    task = asyncio.create_task(updates(rt, request(cid, "second", 200), ctx))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(rt.dialogues[cid].trace.turns) == 1
    assert not rt.service.sessions[rt.conversation_id(cid)].history


async def test_bridge_requires_token_and_replays_accept_only_once():
    phone = phone_service()
    call, _ = await phone.handle_inbound_call(inbound())
    good = bridge(phone, call)
    bad = LiveIncomingCall(event_id="forged", session_id="bad", linked_call_id=call.call_id, bridge_token="forged")
    assert await phone.handle_live_incoming_call(bad) is None
    phone.live_calls.reject.assert_awaited_once_with("bad", status_code=603)
    first, second = await asyncio.gather(phone.handle_live_incoming_call(good), phone.handle_live_incoming_call(good))
    assert first.call_id == second.call_id == call.call_id
    assert call.provider_call_id == "carrier-1" and call.session_id == "live-1"
    phone.live_calls.accept.assert_awaited_once()
    phone.runner.start.assert_awaited_once()
    assert len((await phone.list_calls(CallQuery())).items) == 1


async def test_duplicate_carrier_callbacks_and_call_limit():
    phone = phone_service(max_concurrent_calls=1)
    a, b = await asyncio.gather(phone.handle_inbound_call(inbound()), phone.handle_inbound_call(inbound()))
    assert a[0].call_id == b[0].call_id
    from telephony import PolicyDenied
    with pytest.raises(PolicyDenied):
        await phone.handle_inbound_call(inbound("another"))
    await phone.process_provider_event(inbound(state=CallState.COMPLETED))
    phone.runner.stop.assert_awaited_once_with(a[0].call_id)
    await phone.process_provider_event(inbound(state=CallState.COMPLETED))
    assert phone.runner.stop.await_count == 1
    assert phone.runtime.dialogues[a[0].call_id].closed


async def test_accept_failure_hangup_and_retention():
    phone = phone_service(retention_seconds=0)
    call, _ = await phone.handle_inbound_call(inbound())
    phone.live_calls.accept.side_effect = RuntimeError("provider")
    await phone.handle_live_incoming_call(bridge(phone, call))
    assert call.state is CallState.FAILED
    phone.provider.hangup.assert_awaited_once_with("carrier-1")
    await phone.prune()
    assert await phone.calls.get(call.call_id) is None
    assert not phone.runtime.dialogues and not phone.provider._tokens


async def test_hangup_clears_agent_state_and_is_idempotent():
    rt = runtime([decision("payment"), "Ответ"])
    phone = phone_service(rt)
    call, _ = await phone.handle_inbound_call(inbound())
    await updates(rt, request(call.call_id), context(call.call_id, TranscriptFragment(speaker="user", text="Оплата", start_ms=0, end_ms=80)))
    await phone.hangup_call(call.call_id)
    await phone.hangup_call(call.call_id)
    phone.runner.stop.assert_awaited_once()
    phone.provider.hangup.assert_awaited_once()
    assert rt.conversation_id(call.call_id) not in rt.service.sessions
    assert call.state is CallState.COMPLETED


def signed(config, path, params):
    return {"Content-Type": "application/x-www-form-urlencoded", "X-SignalWire-Signature": RequestValidator(config.signalwire_signing_key).compute_signature(config.public_base_url + path, params)}


def api(phone):
    api = FastAPI()
    api.state.phone = phone
    api.include_router(router)
    api.dependency_overrides[get_database] = lambda: None
    register_exception_handlers(api)
    return api


def test_http_signatures_auth_disabled_operations_and_traces(monkeypatch):
    monkeypatch.setenv("ROUTER_ADMIN_TOKEN", "test-admin")
    phone = phone_service()
    test_app = api(phone)
    params = {"AccountSid": "project-test", "CallSid": "carrier-1", "CallStatus": "ringing", "Direction": "inbound", "From": "+15550001111", "To": "+15550002222"}
    path = "/telephony/webhooks/signalwire/voice"
    with TestClient(test_app) as client:
        assert client.post(path, data=params).status_code == 403
        response = client.post(path, data=params, headers=signed(phone.settings, path, params))
        assert response.status_code == 200 and "<Sip>" in response.text
        assert client.get("/telephony/calls").status_code in (403, 503)
        client.headers["X-Admin-Token"] = "test-admin"
        calls = client.get("/telephony/calls").json()["items"]
        assert len(calls) == 1 and calls[0]["from"] != params["From"]
        cid = calls[0]["call_id"]
        assert client.get(f"/telephony/calls/{cid}/trace").json()["state"] == "accepted"
        assert client.get(f"/telephony/calls/{cid}/trace").headers["cache-control"] == "no-store"
        assert client.post("/telephony/calls", json={"to": "+15550003333"}).status_code == 405
        assert client.post(f"/telephony/calls/{cid}/transfer", json={"target": "+15550003333"}).status_code == 404
        assert client.post(f"/telephony/calls/{cid}/hangup").json()["status"] == "completed"


def test_signature_uses_signing_key_and_exact_public_url():
    config = settings()
    provider = SignalWireProvider(config)
    params = {"AccountSid": "project-test", "CallSid": "carrier-1", "Direction": "inbound"}
    path = "/telephony/webhooks/signalwire/voice?test=1"
    headers = signed(config, path, params)
    body = urlencode(params).encode()
    assert provider.verify_webhook(body, headers, config.public_base_url + path).source == "signalwire"
    for url, payload in [(config.public_base_url + path[:-1] + "2", body), (config.public_base_url + path, body + b"&CallSid=other")]:
        with pytest.raises(WebhookVerificationError):
            provider.verify_webhook(payload, headers, url)
    headers["X-SignalWire-Signature"] = RequestValidator(config.signalwire_api_token).compute_signature(config.public_base_url + path, params)
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook(body, headers, config.public_base_url + path)


async def test_signalwire_hangup_calls_compatibility_api_only():
    received = []
    async def handler(req):
        received.append(req)
        return httpx.Response(200, json={"status": "completed"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = SignalWireProvider(settings(), client=client)
    await provider.hangup("carrier-1")
    assert str(received[0].url) == "https://test.signalwire.com/api/laml/2010-04-01/Accounts/project-test/Calls/carrier-1.json"
    assert received[0].content == b"Status=completed"
    await provider.aclose()


def test_real_openai_webhook_signature_and_bridge_token():
    config = settings()
    sdk = AsyncOpenAI(api_key="sk-test")
    controller = OpenAILiveTelephony(config, client=sdk)
    cid = uuid4()
    body = json.dumps({"id": "evt_1", "type": "live.transport.incoming", "created_at": 1, "data": {"type": "sip", "session_id": "live-1", "sip_headers": [
        {"name": "X-Telephony-Call-Id", "value": str(cid)}, {"name": "X-Butaq-Bridge-Token", "value": "bridge-secret"}]}}).encode()
    timestamp = str(int(time.time()))
    message = b"evt_1." + timestamp.encode() + b"." + body
    signature = base64.b64encode(hmac.new(b"test-secret", message, hashlib.sha256).digest()).decode()
    headers = {"webhook-id": "evt_1", "webhook-timestamp": timestamp, "webhook-signature": "v1," + signature}
    incoming = controller.verify_webhook(body, headers)
    assert incoming.linked_call_id == cid and incoming.bridge_token == "bridge-secret"
    assert "bridge-secret" not in repr(incoming)
    with pytest.raises(WebhookVerificationError):
        controller.verify_webhook(body + b" ", headers)


def test_default_disabled_has_no_network_or_credentials(monkeypatch):
    monkeypatch.setenv("TELEPHONY_ENABLED", "false")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/telephony/health").json() == {"enabled": False}
        assert client.post("/telephony/webhooks/openai", json={}).status_code == 503
    config = settings(enabled=True, signalwire_signing_key=None)
    with pytest.raises(ValueError, match="SIGNING_KEY"):
        config.validate_enabled()


async def test_sdk_compatibility_build_is_offline_and_closes_client():
    config = settings(enabled=True)
    service = runtime([]).service
    service.pipeline.settings = SimpleNamespace(api_key="sk-test")
    phone = build_phone(config, service)
    assert phone.live.live.sideband and phone.live.live.sessions
    await phone.aclose()
    assert phone.live.is_closed()


class QueueConnection:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.sent = []
        self.spoken = asyncio.Event()
        self.exited = asyncio.Event()

    async def events(self):
        try:
            while True:
                yield await self.queue.get()
        finally:
            self.exited.set()

    async def send(self, event):
        self.sent.append(event)
        if event["type"] == "session.commentary.append":
            self.spoken.set()


def transcript(text, start=0, end=80):
    return {"type": "session.input_transcript.delta", "delta": text, "start_ms": start, "end_ms": end}


def delegation(identifier="d1", offset=100):
    return {"type": "session.delegation.created", "offset_ms": offset, "delegation": {"id": identifier, "target": "client"}}


async def test_real_runner_routes_stream_and_cleans_up_on_carrier_hangup():
    rt = runtime([decision("payment"), "Проверенный ответ"])
    connection = QueueConnection()

    @asynccontextmanager
    async def attach(session_id):
        assert session_id == "live-1"
        yield connection

    state = InMemoryLiveStateStore()
    controller = SimpleNamespace(accept=AsyncMock(), reject=AsyncMock(), hangup=AsyncMock())
    runner = LiveSessionRunner(gateway=SimpleNamespace(attach=attach), runtime=rt, live_state=state, live_calls=controller, settings=settings(), replace_delegations=True)
    phone = phone_service(rt, runner=runner)
    phone.live_calls = controller
    call, _ = await phone.handle_inbound_call(inbound())
    await phone.handle_live_incoming_call(bridge(phone, call))
    await connection.queue.put(transcript("Я оплатил полис"))
    await connection.queue.put(delegation())
    await asyncio.wait_for(connection.spoken.wait(), timeout=2)
    assert connection.sent[0]["content"] == "Проверенный ответ"
    assert connection.sent[0]["delegation_id"] == "d1"
    await phone.process_provider_event(inbound(state=CallState.COMPLETED))
    await asyncio.wait_for(connection.exited.wait(), timeout=2)
    assert not runner._sessions
    assert await state.load_context(call.call_id) is None
    assert rt.conversation_id(call.call_id) not in rt.service.sessions
    assert rt.dialogues[call.call_id].trace.turns[0].result.scenario_id == "payment"
    assert call.state is CallState.COMPLETED
    await phone.aclose()


async def test_new_delegation_cancels_old_work_and_close_blocks_more_work():
    rt = runtime([decision("address"), "Новый ответ"])
    original = rt.service.orchestrator.gateway.route
    started = asyncio.Event()
    attempts = []

    async def route(ctx, config):
        attempts.append(ctx.text)
        if len(attempts) == 1:
            started.set()
            await asyncio.Event().wait()
        return await original(ctx, config)

    rt.service.orchestrator.gateway.route = route
    cid = uuid4()
    rt.open(cid)
    connection = QueueConnection()
    executor = DelegationExecutor(rt)
    coordinator = LiveSessionCoordinator(graph=LiveSessionGraph(), delegations=executor, context=context(cid), connection=connection, replace_delegations=True)
    await coordinator.process(transcript("Оплата. "))
    await coordinator.process(delegation())
    await asyncio.wait_for(started.wait(), timeout=2)
    await coordinator.process(delegation())  # replay cannot cancel the valid running request
    assert len(attempts) == 1
    await coordinator.process(transcript("Нет, сначала адрес", start=100, end=180))
    await coordinator.process(delegation("d2", 200))
    await executor.wait("d2")
    assert attempts == ["Оплата.", "Оплата. Нет, сначала адрес"]
    assert [e["delegation_id"] for e in connection.sent] == ["d2"]
    assert len(rt.service.sessions[rt.conversation_id(cid)].history) == 2
    await coordinator.request_close()
    await coordinator.process(transcript("Ещё вопрос", start=200, end=280))
    await coordinator.process(delegation("d3", 300))
    assert not executor.active_delegation_ids


async def test_two_phone_calls_keep_context_separate():
    rt = runtime([decision("payment"), decision("payment"), "Первый ответ", "Второй ответ"])
    # Use independent deterministic outputs, since scheduling is not part of the contract.
    from multi_agent.contracts import RoutingDecision
    rt.service.orchestrator.gateway.route = AsyncMock(return_value=RoutingDecision(**decision("payment")))
    rt.service.orchestrator.gateway.resolve = AsyncMock(return_value="Ответ")
    first, second = uuid4(), uuid4()
    for cid in (first, second):
        rt.open(cid)
    await asyncio.gather(*[updates(rt, request(cid), context(cid, TranscriptFragment(speaker="user", text=text, start_ms=0, end_ms=80))) for cid, text in [(first, "Первый"), (second, "Екінші")]])
    assert rt.service.sessions[rt.conversation_id(first)].history[0]["content"] == "Первый"
    assert rt.service.sessions[rt.conversation_id(second)].history[0]["content"] == "Екінші"


def test_public_browser_endpoints_cannot_read_or_overwrite_phone_session():
    app.dependency_overrides[get_service] = lambda: SimpleNamespace()
    try:
        with TestClient(app) as client:
            assert client.get("/router/sessions/phone:example").status_code == 404
            assert client.post("/router/text", json={"session_id": "phone:example", "text": "inject"}).status_code == 422
            assert client.post("/router/voice", data={"session_id": "phone:example"}, files={"audio": ("a.wav", b"unused")}).status_code == 422
    finally:
        app.dependency_overrides.pop(get_service, None)


def test_webhook_body_limit_and_no_json_carrier_callbacks():
    phone = phone_service(max_webhook_bytes=10)
    with TestClient(api(phone)) as client:
        assert client.post("/telephony/webhooks/openai", content=b"x" * 11).status_code == 413
        assert client.post("/telephony/webhooks/signalwire/voice", json={}).status_code == 415


async def test_fragment_after_offset_is_not_used_until_next_request():
    rt = runtime([decision("payment"), "Ответ"])
    cid = uuid4()
    rt.open(cid)
    ctx = context(cid, TranscriptFragment(speaker="user", text="Төлем", start_ms=80, end_ms=150))
    await updates(rt, request(cid, "early", 100), ctx)
    assert not rt.service.orchestrator.gateway.calls
    await updates(rt, request(cid, "ready", 200), ctx)
    assert rt.dialogues[cid].trace.turns[0].result.transcript == "Төлем"


async def test_duration_and_retained_call_count_are_bounded():
    phone = phone_service(max_call_seconds=1, max_retained_calls=1)
    first, _ = await phone.handle_inbound_call(inbound("first"))
    phone._created[first.call_id] -= 2
    await phone.prune()
    assert first.state is CallState.COMPLETED
    second, _ = await phone.handle_inbound_call(inbound("second"))
    await phone.hangup_call(second.call_id)
    await phone.prune()
    assert await phone.calls.get(first.call_id) is None
    assert await phone.calls.get(second.call_id) is not None


async def test_no_real_operator_or_outbound_even_when_calling_service_directly():
    from telephony import PolicyDenied
    phone = phone_service()
    with pytest.raises(PolicyDenied, match="Outbound"):
        await phone.start_outbound_call(to="+15550003333", idempotency_key="no")
    with pytest.raises(PolicyDenied, match="transfer"):
        await phone.transfer_call(uuid4(), "+15550003333")


async def test_hangup_failure_is_visible_and_agent_work_stays_stopped():
    phone = phone_service()
    call, _ = await phone.handle_inbound_call(inbound())
    phone.provider.hangup.side_effect = TimeoutError()
    await phone.hangup_call(call.call_id)
    assert call.state is CallState.FAILED
    assert call.terminal_reason == "carrier_hangup_unconfirmed"
    assert phone.runtime.dialogues[call.call_id].closed


def test_graph_rejects_unbounded_transcript():
    from telephony.session_graph import InvalidLiveEvent
    with pytest.raises(InvalidLiveEvent, match="size limit"):
        LiveSessionGraph().route(transcript("x" * 64001))
