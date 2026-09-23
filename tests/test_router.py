"""Exercise the real orchestration and voice adapter with deterministic agents."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from multi_agent.contracts import AgentFailure

from app.api.routes import get_service
from app.domain import Catalog, RoutingDecision, Scenario
from app.main import app
from app.services.voice_router import RouterService
from v2v.pipeline import Transcript


class StubGateway:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    async def route(self, context, config):
        self.calls.append(("route", context, config))
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        return RoutingDecision.model_validate(output)

    async def resolve(self, context, config):
        self.calls.append(("resolve", context, config))
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        return output


class StubPipeline:
    def __init__(self):
        self.settings = SimpleNamespace(tts_format="mp3")
        self.spoken = []

    async def transcribe(self, audio: bytes, *, filename: str) -> Transcript:
        assert audio == b"recorded-audio"
        return Transcript(text="Ақша списали, но заказ не подтвердился", model="stub")

    async def speak_bytes(self, reply: str) -> bytes:
        self.spoken.append(reply)
        return b"mp3-audio"


def decision(
    sid: str,
    *,
    pending: list[str] | None = None,
    confidence: float = 0.95,
    transition: str = "continue",
) -> dict:
    return {
        "action": "route",
        "scenario_id": sid,
        "confidence": confidence,
        "reason": "Клиент явно запросил этот сценарий",
        "alternatives": [],
        "pending_scenario_ids": pending or [],
        "customer_message": "Қай мәселені нақтылайсыз?",
        "clarification_question": "Қай мәселені нақтылайсыз?",
        "language": "mixed",
        "topic_transition": transition,
    }


def catalog() -> Catalog:
    return Catalog(
        [
            Scenario(
                id="payment", title="Оплата", details={"purpose": "payment status"}
            ),
            Scenario(
                id="address", title="Адрес", details={"purpose": "change address"}
            ),
        ]
    )


@pytest.mark.asyncio
async def test_mixed_language_switch_and_resume_with_real_context() -> None:
    gateway = StubGateway(
        [
            decision("payment", pending=["address"]),
            "Уточните номер заказа.",
            decision("address", transition="switch"),
            "Адрес өзгерту үшін өтініш нөмірі керек.",
            decision("payment", transition="resume"),
            "Вернёмся к платежу.",
        ]
    )
    service = RouterService(StubPipeline(), catalog=catalog(), gateway=gateway)
    first = await service.turn(
        session_id="one",
        text="Ақша списали, но заказ не подтвердился; адрес надо поменять",
    )
    second = await service.turn(session_id="one", text="Алдымен адрес ауыстырайық")
    third = await service.turn(session_id="one", text="Вернёмся к оплате")
    assert [first.scenario_id, second.scenario_id, third.scenario_id] == [
        "payment",
        "address",
        "payment",
    ]
    assert first.pending_scenario_ids == ["address"]
    assert second.pending_scenario_ids == ["payment"]
    assert third.pending_scenario_ids == ["address"]
    context = gateway.calls[2][1]
    assert context.active_scenario == "payment"
    assert context.pending_scenarios == ["address"]
    assert context.history[0]["content"].startswith("Ақша")
    assert all(turn.timings.routing_ms >= 0 for turn in (first, second, third))
    assert len({turn.trace_id for turn in (first, second, third)}) == 3


@pytest.mark.asyncio
async def test_low_confidence_clarifies_then_hands_off_without_guessing() -> None:
    gateway = StubGateway(
        [decision("payment", confidence=0.3), decision("address", confidence=0.2)]
    )
    service = RouterService(
        StubPipeline(), catalog=catalog(), threshold=0.65, gateway=gateway
    )
    first = await service.turn(session_id="one", text="Не знаю, куда обратиться")
    second = await service.turn(session_id="one", text="Әлі де түсінбедім")
    assert first.action == "clarify"
    assert second.action == "handoff"
    assert first.scenario_id is second.scenario_id is None
    assert first.reply == "Қай мәселені нақтылайсыз?"
    assert second.reply != first.reply
    assert "оператор" in second.reply.lower()
    assert [call[0] for call in gateway.calls] == ["route", "route"]


@pytest.mark.asyncio
async def test_unknown_model_id_is_rejected_without_mutating_history() -> None:
    service = RouterService(
        StubPipeline(), catalog=catalog(), gateway=StubGateway([decision("unknown")])
    )
    with pytest.raises(HTTPException) as exc:
        await service.turn(session_id="one", text="Где оплата?")
    assert exc.value.status_code == 502
    assert service.sessions["one"].history == []


@pytest.mark.asyncio
async def test_resolution_failure_does_not_commit_turn_or_synthesize() -> None:
    pipeline = StubPipeline()
    service = RouterService(
        pipeline,
        catalog=catalog(),
        gateway=StubGateway([decision("payment"), AgentFailure("Resolution failed")]),
    )
    with pytest.raises(HTTPException) as exc:
        await service.turn(session_id="one", text="Где оплата?", synthesize=True)
    assert exc.value.status_code == 502
    assert service.sessions["one"].history == []
    assert service.sessions["one"].active_scenario is None
    assert pipeline.spoken == []


@pytest.mark.asyncio
async def test_timeout_returns_504_without_committing_turn(monkeypatch) -> None:
    class SlowGateway(StubGateway):
        async def route(self, context, config):
            await asyncio.sleep(1)
            return await super().route(context, config)

    monkeypatch.setenv("MULTI_AGENT_TIMEOUT_SECONDS", "0.01")
    service = RouterService(
        StubPipeline(), catalog=catalog(), gateway=SlowGateway([decision("payment")])
    )
    with pytest.raises(HTTPException) as exc:
        await service.turn(session_id="one", text="Где оплата?")
    assert exc.value.status_code == 504
    assert service.sessions["one"].history == []


@pytest.mark.asyncio
async def test_live_settings_are_snapshotted_for_every_turn() -> None:
    settings = {"model": "first", "routing_prompt": "First prompt"}
    database = SimpleNamespace(catalog=catalog, settings=lambda: settings.copy())
    gateway = StubGateway(
        [decision("payment"), "Номер заказа?", decision("payment"), "Спасибо."]
    )
    service = RouterService(StubPipeline(), database=database, gateway=gateway)
    await service.turn(session_id="one", text="Где оплата?")
    settings.update(
        model="second", routing_prompt="Updated prompt", confidence_threshold="0.8"
    )
    await service.turn(session_id="one", text="Заказ 42")
    assert gateway.calls[0][2].model == "first"
    assert gateway.calls[2][2].model == "second"
    assert gateway.calls[2][2].routing_prompt == "Updated prompt"
    assert gateway.calls[2][2].confidence_threshold == 0.8
    assert gateway.calls[2][2].tracing_enabled is False


def test_voice_endpoint_returns_audio_and_full_trace() -> None:
    pipeline = StubPipeline()
    gateway = StubGateway([decision("payment"), "Пожалуйста, назовите номер заказа."])
    service = RouterService(pipeline, catalog=catalog(), gateway=gateway)
    app.dependency_overrides[get_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.post(
                "/router/voice",
                data={"session_id": "voice-session"},
                files={"audio": ("recording.webm", b"recorded-audio", "audio/webm")},
            )
            state = client.get("/router/sessions/voice-session")
        assert response.status_code == 200
        body = response.json()
        assert body["transcript"].startswith("Ақша")
        assert body["audio_base64"] == "bXAzLWF1ZGlv"
        assert body["audio_content_type"] == "audio/mpeg"
        assert body["trace_id"]
        assert body["language"] == "mixed"
        assert pipeline.spoken == [body["reply"]]
        assert state.json()["active_scenario"] == "payment"
        assert set(body["timings"]) == {
            "stt_ms",
            "routing_ms",
            "response_ms",
            "tts_ms",
            "total_ms",
        }
    finally:
        app.dependency_overrides.pop(get_service, None)


def test_service_construction_does_not_initialize_the_api_client() -> None:
    class LazyPipeline(StubPipeline):
        @property
        def client(self):
            raise AssertionError(
                "API client must be resolved lazily during an agent call"
            )

    service = RouterService(LazyPipeline(), catalog=catalog())
    assert service.sessions == {}
