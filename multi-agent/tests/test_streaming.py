"""Streaming lifecycle checks with deterministic models and controllable audio."""

import asyncio
import base64
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from multi_agent.contracts import Catalog, RoutingDecision
from multi_agent.streaming import StreamingConversation

from app.services.voice_router import RouterService


class Gateway:
    def __init__(self):
        self.calls = []
        self.contexts = []
        self.block = None

    async def route(self, context, config):
        self.calls.append(context.text)
        self.contexts.append(context)
        if self.block is not None:
            await self.block.wait()
        return RoutingDecision(
            action="route",
            scenario_id="policy",
            confidence=0.9,
            reason="Policy request",
            customer_message="Срок полиса?",
            language="ru",
        )

    async def resolve(self, context, config):
        return f"Ответ: {context.text}"


class Pipeline:
    settings = SimpleNamespace(tts_format="mp3")

    def __init__(self):
        self.release = asyncio.Event()
        self.release.set()
        self.closed = asyncio.Event()

    async def speak(self, reply, *, audio_format):
        assert audio_format == "pcm"
        try:
            yield b"\x01\x02\x03"  # A network chunk may end between samples.
            await self.release.wait()
            yield b"\x04\x05\x06"
        finally:
            self.closed.set()


@asynccontextmanager
async def setup_stream(*, transcriber=None, pipeline=None):
    gateway = Gateway()
    pipeline = pipeline or Pipeline()
    service = RouterService(
        pipeline,
        catalog=Catalog.from_payload([{"id": "policy", "title": "Policy"}]),
        gateway=gateway,
    )
    events = []
    arrived = asyncio.Event()

    async def send(event):
        events.append(event)
        arrived.set()

    conversation = StreamingConversation(
        session_id="caller",
        service=service,
        speak=pipeline.speak,
        send=send,
        confirm_delivery=service.orchestrator.confirm_delivery,
        interrupt_delivery=service.orchestrator.interrupt_delivery,
        transcriber=transcriber,
    )

    async def wait_for(kind, turn_id=None):
        async def wait():
            while True:
                for event in events:
                    if event["type"] == kind and (
                        turn_id is None or event.get("turn_id") == turn_id
                    ):
                        return event
                arrived.clear()
                await arrived.wait()

        return await asyncio.wait_for(wait(), 1)

    try:
        yield conversation, service, gateway, pipeline, events, wait_for
    finally:
        await conversation.close()


@pytest.mark.asyncio
async def test_first_audio_arrives_before_synthesis_completes_and_ack_commits_history():
    async with setup_stream() as (stream, service, _gateway, pipeline, events, wait):
        pipeline.release.clear()
        await stream.receive({"type": "text", "turn_id": "a", "text": "Полис"})
        chunk = await wait("audio.delta")
        assert base64.b64decode(chunk["audio"]) == b"\x01\x02"
        assert not any(event["type"] == "turn.completed" for event in events)
        assert service.sessions["caller"].history == [
            {"role": "user", "content": "Полис"}
        ]
        await stream.receive({"type": "playback.started", "turn_id": "a"})
        # A premature completion cannot mark ungenerated audio as heard.
        await stream.receive({"type": "playback.completed", "turn_id": "a"})
        assert len(service.sessions["caller"].history) == 1
        pipeline.release.set()
        completed = await wait("turn.completed")
        assert (
            completed["metrics"]["first_audio_ms"] <= completed["metrics"]["total_ms"]
        )
        chunks = [
            base64.b64decode(event["audio"])
            for event in events
            if event["type"] == "audio.delta"
        ]
        assert b"".join(chunks) == b"\x01\x02\x03\x04\x05\x06"
        await stream.receive({"type": "playback.completed", "turn_id": "a"})
        assert service.sessions["caller"].history[-1] == {
            "role": "assistant",
            "content": "Ответ: Полис",
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
        assert [event["seq"] for event in events] == list(range(1, len(events) + 1))


@pytest.mark.asyncio
async def test_interruption_before_resolution_rolls_back_and_next_turn_proceeds():
    async with setup_stream() as (stream, service, gateway, _pipeline, events, wait):
        gateway.block = asyncio.Event()
        await stream.receive({"type": "text", "turn_id": "a", "text": "Полис"})
        await wait("turn.started")
        await asyncio.sleep(0)
        await stream.receive({"type": "interrupt", "turn_id": "a"})
        state = service.sessions.get("caller")
        assert state is None or state.history == []
        gateway.block = None
        await stream.receive({"type": "text", "turn_id": "b", "text": "Возврат"})
        await wait("turn.completed", "b")
        assert not any(
            event["type"] == "reply" and event["turn_id"] == "a" for event in events
        )
        assert service.sessions["caller"].history == [
            {"role": "user", "content": "Возврат"}
        ]


@pytest.mark.asyncio
async def test_interrupt_cancels_provider_and_unplayed_answer_is_not_next_context():
    async with setup_stream() as (stream, service, gateway, pipeline, events, wait):
        pipeline.release.clear()
        await stream.receive({"type": "text", "turn_id": "a", "text": "Первый"})
        await wait("audio.delta", "a")
        await stream.receive({"type": "interrupt", "turn_id": "a"})
        assert pipeline.closed.is_set()
        assert service.sessions["caller"].pending_delivery is None
        pipeline.release.set()
        await stream.receive({"type": "text", "turn_id": "b", "text": "Второй"})
        await wait("turn.completed", "b")
        assert gateway.contexts[-1].history == [{"role": "user", "content": "Первый"}]
        await stream.receive({"type": "playback.started", "turn_id": "a"})
        await stream.receive({"type": "playback.completed", "turn_id": "a"})
        assert not any(
            entry["role"] == "assistant" for entry in service.sessions["caller"].history
        )
        cancelled_index = next(
            i for i, event in enumerate(events) if event["type"] == "turn.cancelled"
        )
        assert all(
            event.get("turn_id") != "a" for event in events[cancelled_index + 1 :]
        )


@pytest.mark.asyncio
async def test_duplicate_turn_and_invalid_messages_cannot_start_extra_agent_calls():
    async with setup_stream() as (stream, _service, gateway, _pipeline, events, wait):
        for message in [[], {}, {"type": "text", "turn_id": "empty", "text": " "}]:
            await stream.receive(message)
        await stream.receive({"type": "text", "turn_id": "a", "text": "Полис"})
        await wait("turn.completed")
        await stream.receive({"type": "text", "turn_id": "a", "text": "Повтор"})
        assert gateway.calls == ["Полис"]
        assert len([event for event in events if event["type"] == "error"]) == 3


class Transcriber:
    def __init__(self):
        self.chunks = []
        self.commits = []
        self.committing = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def clear(self):
        pass

    async def append(self, turn_id, audio):
        self.chunks.append((turn_id, audio))

    async def commit(self, turn_id):
        self.commits.append(turn_id)
        self.committing.set()
        await self.release.wait()


def audio(turn_id):
    return {
        "type": "audio.append",
        "turn_id": turn_id,
        "audio": base64.b64encode(b"\x00\x00" * 2400).decode(),
    }


@pytest.mark.asyncio
async def test_commit_dedup_and_stale_asr_cannot_route_cancelled_microphone_turn():
    asr = Transcriber()
    async with setup_stream(transcriber=asr) as (
        stream,
        _service,
        gateway,
        _pipeline,
        _events,
        wait,
    ):
        await stream.receive(audio("a"))
        await stream.receive({"type": "audio.commit", "turn_id": "a"})
        await stream.receive({"type": "audio.commit", "turn_id": "a"})
        await asyncio.wait_for(stream._audio_queue.join(), 1)
        assert asr.commits == ["a"]
        await stream.receive({"type": "interrupt", "turn_id": "a"})
        await stream.transcript(
            {"type": "transcript.final", "turn_id": "a", "text": "Старый"}
        )
        assert gateway.calls == []
        await stream.receive(audio("b"))
        await stream.receive({"type": "audio.commit", "turn_id": "b"})
        await stream.transcript(
            {"type": "transcript.final", "turn_id": "b", "text": "Новый"}
        )
        await stream.transcript(
            {"type": "transcript.final", "turn_id": "b", "text": "Повтор"}
        )
        await wait("turn.completed", "b")
        assert gateway.calls == ["Новый"]


@pytest.mark.asyncio
async def test_interrupt_remains_responsive_while_provider_commit_waits():
    asr = Transcriber()
    asr.release.clear()
    async with setup_stream(transcriber=asr) as (
        stream,
        _service,
        _gateway,
        _pipeline,
        _events,
        _wait,
    ):
        await stream.receive(audio("a"))
        await stream.receive({"type": "audio.commit", "turn_id": "a"})
        await asyncio.wait_for(asr.committing.wait(), 1)
        await asyncio.wait_for(
            stream.receive({"type": "interrupt", "turn_id": "a"}), 0.1
        )
        await stream.receive(audio("b"))
        assert stream.audio_turn_id == "b"
        asr.release.set()
        await asyncio.wait_for(stream._audio_queue.join(), 1)
        assert [turn_id for turn_id, _ in asr.chunks] == ["a", "b"]


@pytest.mark.asyncio
async def test_tts_failure_does_not_expose_secrets_or_commit_unplayed_answer():
    class FailingPipeline(Pipeline):
        async def speak(self, reply, *, audio_format):
            yield b"\x00\x00"
            raise RuntimeError("secret-provider-token")

    async with setup_stream(pipeline=FailingPipeline()) as (
        stream,
        service,
        _gateway,
        _pipeline,
        events,
        wait,
    ):
        await stream.receive({"type": "text", "turn_id": "a", "text": "Полис"})
        error = await wait("error")
        assert "secret" not in str(error)
        assert service.sessions["caller"].pending_delivery is None
        assert not any(event["type"] == "turn.completed" for event in events)


@pytest.mark.asyncio
async def test_disconnect_discards_undelivered_history_even_after_server_completed():
    async with setup_stream() as (stream, service, _gateway, _pipeline, _events, wait):
        await stream.receive({"type": "text", "turn_id": "a", "text": "Полис"})
        await wait("turn.completed")
        await stream.close()
        assert service.sessions["caller"].pending_delivery is None
        assert service.sessions["caller"].history == [
            {"role": "user", "content": "Полис"}
        ]


@pytest.mark.asyncio
async def test_provider_chunk_after_cancellation_cannot_escape_old_generation():
    class LatePipeline(Pipeline):
        async def speak(self, reply, *, audio_format):
            yield b"\x01\x02"
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                yield b"\x03\x04"  # Simulate a provider yielding a buffered late chunk.

    async with setup_stream(pipeline=LatePipeline()) as (
        stream,
        service,
        _gateway,
        _pipeline,
        events,
        wait,
    ):
        await stream.receive({"type": "text", "turn_id": "a", "text": "Полис"})
        await wait("audio.delta")
        await stream.receive({"type": "interrupt", "turn_id": "a"})
        chunks = [
            base64.b64decode(event["audio"])
            for event in events
            if event["type"] == "audio.delta"
        ]
        assert chunks == [b"\x01\x02"]
        assert service.sessions["caller"].pending_delivery is None
        assert events[-1]["type"] == "turn.cancelled"
