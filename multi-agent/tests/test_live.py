"""Native voice delegation and lifecycle regressions, without provider calls."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from multi_agent.contracts import Catalog, RoutingDecision
from multi_agent.live import LiveConversation, commentary_chunks

from app.services.voice_router import RouterService


class Gateway:
    def __init__(self):
        self.texts = []
        self.started = asyncio.Event()
        self.release = None
        self.ignore_cancel = False

    async def route(self, context, config):
        self.texts.append(context.text)
        self.started.set()
        if self.release is not None:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                if not self.ignore_cancel:
                    raise
        return RoutingDecision(action="clarify", confidence=0.5, reason="Need policy",
                               customer_message="Нөмірін айтыңыз, пожалуйста.", language="mixed")

    async def resolve(self, *args):
        raise AssertionError("Clarification does not run Resolution")


@asynccontextmanager
async def live_session():
    connection = SimpleNamespace(send=AsyncMock())
    sideband_closed = []

    @asynccontextmanager
    async def connect(**kwargs):
        assert kwargs == {"session_id": "provider-session", "max_retries": 0}
        try:
            yield connection
        finally:
            sideband_closed.append(True)

    provider = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(
            session=SimpleNamespace(id="provider-session"),
            transport=SimpleNamespace(sdp="v=0\r\nanswer"))),
        sideband=SimpleNamespace(connect=connect),
        sessions=SimpleNamespace(hangup=AsyncMock()),
    )
    pipeline = SimpleNamespace(client=SimpleNamespace(live=provider),
                               speak=AsyncMock(side_effect=AssertionError("Native voice must not call TTS")))
    gateway = Gateway()
    service = RouterService(pipeline, gateway=gateway,
                            catalog=Catalog.from_payload([{"id": "policy", "title": "Policy"}]))
    events = []

    async def send(event):
        events.append(event)

    live = LiveConversation(client=pipeline.client, service=service, session_id="caller",
                            send=send, transcript_grace=0)
    try:
        assert await live.open("v=0\r\noffer") == "v=0\r\nanswer"
        yield live, service, gateway, connection, provider, events, sideband_closed
    finally:
        await live.close()
        pipeline.speak.assert_not_called()


async def fragment(live, text, start=0, end=100, event_id=None):
    await live.handle({"type": "session.input_transcript.delta", "delta": text,
                       "start_ms": start, "end_ms": end, "event_id": event_id})


async def delegate(live, identity="d1", offset=100):
    await live.handle({"type": "session.delegation.created", "offset_ms": offset,
                       "delegation": {"id": identity, "target": "client",
                                      "description": "INVENTED PAYMENT DEMO-C-5003"}})


async def finish(live):
    await asyncio.wait_for(live._task, 1)


@pytest.mark.asyncio
async def test_actual_transcript_preserves_mixed_language_and_deduplicates_events():
    async with live_session() as (live, service, gateway, connection, provider, events, _):
        await fragment(live, "Мой полис", 0, 50, "fragment-1")
        await fragment(live, "Мой полис", 0, 50, "fragment-1")
        await fragment(live, " қашан аяқталады?", 50, 100, "fragment-2")
        await delegate(live)
        await finish(live)
        await delegate(live)
        assert gateway.texts == ["Мой полис қашан аяқталады?"]
        assert len([event for event in events if event["type"] == "result"]) == 1
        sent = connection.send.call_args_list
        assert len(sent) == 1
        assert sent[0].args[0]["content"] == "Нөмірін айтыңыз, пожалуйста."
        assert service.sessions["caller"].history == [
            {"role": "user", "content": "Мой полис қашан аяқталады?"}]
        config = provider.create.call_args.kwargs["session"]
        assert config["delegation"] == {"type": "client"}
        assert config["client"]["data_channel"]["allowed_client_events"] == []
        assert config["client"]["data_channel"]["allowed_server_events"] == [
            {"type": "session.started"}, {"type": "session.closed"}, {"type": "error"}]


@pytest.mark.asyncio
async def test_delegation_without_transcript_never_routes_metadata():
    async with live_session() as (live, _, gateway, connection, _, events, _):
        await delegate(live)
        await finish(live)
        assert not gateway.texts
        assert not any(event["type"] == "result" for event in events)
        assert "расслышать" in connection.send.call_args.args[0]["content"]


@pytest.mark.asyncio
async def test_newer_speech_prevents_obsolete_result_being_spoken():
    async with live_session() as (live, _, gateway, connection, _, events, _):
        gateway.release = asyncio.Event()
        await fragment(live, "Полис", end=100)
        await delegate(live)
        await asyncio.wait_for(gateway.started.wait(), 1)
        await fragment(live, "Нет, возврат", start=200, end=300)
        gateway.release.set()
        await finish(live)
        connection.send.assert_not_called()
        assert not any(event["type"] == "result" for event in events)
        await delegate(live, "d2", offset=300)
        await finish(live)
        assert gateway.texts == ["Полис", "Нет, возврат"]
        assert connection.send.call_args.args[0]["delegation_id"] == "d2"


@pytest.mark.asyncio
async def test_interrupt_suppresses_result_even_if_backend_swallows_cancellation():
    async with live_session() as (live, _, gateway, connection, _, events, _):
        gateway.release = asyncio.Event()
        gateway.ignore_cancel = True
        await fragment(live, "Полис")
        await delegate(live)
        await asyncio.wait_for(gateway.started.wait(), 1)
        await asyncio.wait_for(live.interrupt(), 1)
        assert not any(event["type"] == "result" for event in events)
        assert [call.args[0]["type"] for call in connection.send.call_args_list] == [
            "session.instructions.append"]


@pytest.mark.asyncio
async def test_backend_failure_is_safe_and_close_hangs_up_once():
    async with live_session() as (live, service, _, connection, provider, events, closed):
        service.turn = AsyncMock(side_effect=RuntimeError("secret-provider-key"))
        await fragment(live, "Полис")
        await delegate(live)
        await finish(live)
        assert any(event["type"] == "task.error" for event in events)
        assert "secret-provider-key" not in str(events) + str(connection.send.call_args_list)
        await live.close()
        await live.close()
        assert closed == [True]
        provider.sessions.hangup.assert_awaited_once_with("provider-session", timeout=5)


def test_commentary_chunks_preserve_words_and_bound_cyrillic_bytes():
    text = "Полис мерзімі аяқталады завтра. " * 100
    chunks = commentary_chunks(text)
    assert " ".join(chunks) == text.strip()
    assert all(len(chunk.encode("utf-8")) <= 480 for chunk in chunks)
    with pytest.raises(ValueError, match="oversized"):
        commentary_chunks("я" * 241)


@pytest.mark.asyncio
async def test_native_assistant_question_is_passed_as_observed_context_for_short_confirmation():
    async with live_session() as (live, service, _, _, _, _, _):
        service.turn = AsyncMock(wraps=service.turn)
        await live.handle({"type": "session.output_transcript.delta", "delta": "Хотите проверить полис?",
                           "start_ms": 0, "end_ms": 100, "event_id": "assistant-question"})
        await fragment(live, "да", start=200, end=300)
        await delegate(live, offset=300)
        await finish(live)
        kwargs = service.turn.call_args.kwargs
        assert kwargs["text"] == "да"
        assert {"role": "assistant", "content": "Хотите проверить полис?"} in kwargs["voice_context"]
        assert {"role": "user", "content": "да"} in kwargs["voice_context"]
        assert service.sessions["caller"].history == [{"role": "user", "content": "да"}]


@pytest.mark.asyncio
async def test_late_words_for_running_delegation_restart_with_complete_request_only():
    async with live_session() as (live, service, gateway, connection, _, events, _):
        gateway.release = asyncio.Event()
        original_route = gateway.route

        async def echo_request(context, config):
            decision = await original_route(context, config)
            return decision.model_copy(update={"customer_message": f"Уточняю: {context.text}"})

        gateway.route = echo_request
        await fragment(live, "Проверьте", start=0, end=50, event_id="first-half")
        await delegate(live, offset=100)
        await asyncio.wait_for(gateway.started.wait(), 1)
        obsolete_task = live._task
        await fragment(live, " срок полиса", start=50, end=100, event_id="late-half")
        assert obsolete_task.done()
        gateway.release.set()
        await finish(live)

        assert gateway.texts == ["Проверьте", "Проверьте срок полиса"]
        assert service.sessions["caller"].history == [
            {"role": "user", "content": "Проверьте срок полиса"}]
        assert service.sessions["caller"].pending_delivery is None
        connection.send.assert_awaited_once_with({
            "type": "session.commentary.append", "delegation_id": "d1",
            "content": "Уточняю: Проверьте срок полиса",
        })
        results = [event for event in events if event["type"] == "result"]
        assert len(results) == 1
        assert results[0]["result"]["reply"] == "Уточняю: Проверьте срок полиса"


@pytest.mark.asyncio
async def test_typed_message_during_live_call_is_verified_and_read_aloud():
    async with live_session() as (live, service, gateway, connection, _, events, _):
        await live.say("  Когда заканчивается полис?  ")
        await finish(live)
        assert gateway.texts == ["Когда заканчивается полис?"]
        sent = connection.send.call_args.args[0]
        assert sent["type"] == "session.instructions.append" and sent["delegation_id"] is None
        assert "Нөмірін айтыңыз" in sent["content"]
        assert [event["type"] for event in events if event["type"] in {"working", "result", "working.done"}] == [
            "working", "result", "working.done"]
        assert service.sessions["caller"].history == [
            {"role": "user", "content": "Когда заканчивается полис?"}]
        await live.say("   ")
        assert gateway.texts == ["Когда заканчивается полис?"]
