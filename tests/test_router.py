"""Exercise real routing state transitions with a deterministic model stub."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.routes import get_service
from app.domain import Catalog, Scenario
from app.main import app
from app.services.voice_router import RouterService
from v2v.pipeline import Transcript


class StubResponses:
    def __init__(self, outputs: list[str | dict]):
        self.outputs = iter(outputs)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        output = next(self.outputs)
        return SimpleNamespace(
            output_text=json.dumps(output, ensure_ascii=False)
            if isinstance(output, dict)
            else output
        )


class StubPipeline:
    def __init__(self, outputs: list[str | dict]):
        self.responses = StubResponses(outputs)
        self.client = SimpleNamespace(responses=self.responses)
        self.settings = SimpleNamespace(tts_format="mp3")

    async def transcribe(self, audio: bytes, *, filename: str) -> Transcript:
        assert audio == b"recorded-audio"
        return Transcript(text="Ақша списали, но заказ не подтвердился", model="stub")

    async def speak_bytes(self, reply: str) -> bytes:
        return b"mp3-audio"


def decision(
    sid: str, *, pending: list[str] | None = None, confidence: float = 0.95
) -> dict:
    return {
        "action": "route",
        "scenario_id": sid,
        "confidence": confidence,
        "reason": "Клиент явно запросил этот сценарий",
        "alternatives": [],
        "pending_scenario_ids": pending or [],
        "customer_message": "Қай мәселені нақтылайсыз?",
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
    pipeline = StubPipeline(
        [
            decision("payment", pending=["address"]),
            "Уточните номер заказа.",
            decision("address"),
            "Адрес өзгерту үшін өтініш нөмірі керек.",
            decision("payment"),
            "Вернёмся к платежу.",
        ]
    )
    service = RouterService(pipeline, catalog=catalog())
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
    context = json.loads(pipeline.responses.calls[2]["input"])
    assert context["response_format"] == "json"
    assert context["response_schema"]["properties"]["customer_message"]["minLength"] == 1
    assert context["active_scenario"] == "payment"
    assert context["pending_scenarios"] == ["address"]
    assert context["recent_dialog"][0]["content"].startswith("Ақша")
    assert all(turn.timings.routing_ms >= 0 for turn in (first, second, third))


@pytest.mark.asyncio
async def test_low_confidence_clarifies_then_hands_off_without_guessing() -> None:
    pipeline = StubPipeline(
        [decision("payment", confidence=0.3), decision("address", confidence=0.2)]
    )
    service = RouterService(pipeline, catalog=catalog(), threshold=0.65)
    first = await service.turn(session_id="one", text="Не знаю, куда обратиться")
    second = await service.turn(session_id="one", text="Әлі де түсінбедім")
    assert first.action == "clarify"
    assert second.action == "handoff"
    assert first.scenario_id is second.scenario_id is None
    assert first.reply == second.reply == "Қай мәселені нақтылайсыз?"
    assert len(pipeline.responses.calls) == 2  # no unsupported answer generation


@pytest.mark.asyncio
async def test_unknown_model_id_is_rejected_without_mutating_history() -> None:
    pipeline = StubPipeline([decision("unknown")])
    service = RouterService(pipeline, catalog=catalog())
    with pytest.raises(HTTPException) as exc:
        await service.turn(session_id="one", text="Где оплата?")
    assert exc.value.status_code == 502
    assert service.sessions["one"].history == []


def test_voice_endpoint_returns_audio_and_full_trace() -> None:
    pipeline = StubPipeline([decision("payment"), "Пожалуйста, назовите номер заказа."])
    service = RouterService(pipeline, catalog=catalog())
    app.dependency_overrides[get_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.post(
                "/router/voice",
                data={"session_id": "voice-session"},
                files={"audio": ("recording.webm", b"recorded-audio", "audio/webm")},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["transcript"].startswith("Ақша")
        assert body["audio_base64"] == "bXAzLWF1ZGlv"
        assert body["audio_content_type"] == "audio/mpeg"
        assert set(body["timings"]) == {
            "stt_ms",
            "routing_ms",
            "response_ms",
            "tts_ms",
            "total_ms",
        }
    finally:
        app.dependency_overrides.clear()
