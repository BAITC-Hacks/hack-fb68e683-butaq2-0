"""Provider protocol tests without microphone, credentials or network."""

import asyncio
import base64
from types import SimpleNamespace

import pytest
from multi_agent.transcription import RealtimeTranscriber

AUDIO = base64.b64encode(b"\0\0" * 2400).decode()


class Connection:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def send(self, event):
        self.sent.append(event)
        if event["type"] == "session.update":
            await self.incoming.put({"type": "session.updated"})

    async def recv(self):
        return await self.incoming.get()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.recv()


class Manager:
    def __init__(self):
        self.conn = Connection()

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_):
        self.conn.closed = True

    def client(self):
        return SimpleNamespace(realtime=SimpleNamespace(connect=self.connect))

    def connect(self, **kwargs):
        assert kwargs == {"extra_query": {"intent": "transcription"}}
        return self


async def commit(stt, conn, turn_id, item_id):
    task = asyncio.create_task(stt.commit(turn_id))
    await asyncio.sleep(0)
    await conn.incoming.put(
        {"type": "input_audio_buffer.committed", "item_id": item_id}
    )
    await task


@pytest.mark.asyncio
async def test_pcm_streams_before_commit_and_final_ids_survive_reordering():
    manager = Manager()
    async with RealtimeTranscriber(manager.client()) as stt:
        events = stt.events()
        config = manager.conn.sent[0]["session"]["audio"]["input"]
        assert config["format"] == {"type": "audio/pcm", "rate": 24000}
        assert config["turn_detection"] is None
        assert config["transcription"]["languages"] == ["ru", "kk"]
        await stt.append("one", AUDIO)
        assert manager.conn.sent[-1]["type"] == "input_audio_buffer.append"
        await manager.conn.incoming.put(
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "item_id": "i1",
                "delta": "Когда",
            }
        )
        assert await anext(events) == {
            "type": "transcript.delta",
            "turn_id": "one",
            "delta": "Когда",
        }
        await commit(stt, manager.conn, "one", "i1")
        await stt.append("two", AUDIO)
        await commit(stt, manager.conn, "two", "i2")
        for item in ("i2", "i1", "i1"):
            await manager.conn.incoming.put(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": item,
                    "transcript": item,
                }
            )
        assert (await anext(events))["turn_id"] == "two"
        assert (await anext(events))["turn_id"] == "one"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(events), 0.01)
    assert manager.conn.closed


@pytest.mark.asyncio
async def test_duplicate_commit_does_not_create_second_provider_turn():
    manager = Manager()
    async with RealtimeTranscriber(manager.client()) as stt:
        await stt.append("one", AUDIO)
        await commit(stt, manager.conn, "one", "i1")
        await stt.commit("one")
        assert (
            sum(e["type"] == "input_audio_buffer.commit" for e in manager.conn.sent)
            == 1
        )
        with pytest.raises(ValueError, match="already been used"):
            await stt.append("one", AUDIO)


@pytest.mark.asyncio
@pytest.mark.parametrize("audio", ["!", "", "YQ==", "x" * 100000])
async def test_invalid_audio_is_rejected_before_provider_send(audio):
    manager = Manager()
    async with RealtimeTranscriber(manager.client()) as stt:
        with pytest.raises(ValueError):
            await stt.append("one", audio)
        assert len(manager.conn.sent) == 1


@pytest.mark.asyncio
async def test_short_audio_rejected_and_clear_allows_new_turn():
    manager = Manager()
    async with RealtimeTranscriber(manager.client()) as stt:
        await stt.append("one", "AAA=")
        with pytest.raises(ValueError, match="100 ms"):
            await stt.commit("one")
        await stt.clear()
        await stt.append("two", AUDIO)
        await commit(stt, manager.conn, "two", "i2")


@pytest.mark.asyncio
async def test_provider_failure_is_redacted_and_connection_is_not_reused():
    manager = Manager()
    async with RealtimeTranscriber(manager.client()) as stt:
        await manager.conn.incoming.put(
            {"type": "error", "error": {"message": "secret-provider-details"}}
        )
        event = await anext(stt.events())
        assert event["type"] == "error" and event["fatal"] is True
        assert "secret-provider-details" not in str(event)
        with pytest.raises(RuntimeError, match="failed"):
            await stt.append("one", AUDIO)


@pytest.mark.asyncio
async def test_clear_quarantines_late_items_then_restores_future_partial_transcripts():
    manager = Manager()
    async with RealtimeTranscriber(manager.client()) as stt:
        events = stt.events()
        await stt.append("cancelled", AUDIO)
        await stt.clear()
        await stt.append("two", AUDIO)
        # Neither unknown item is safe to attribute until the new commit is
        # acknowledged. The second ID really belongs to the new input.
        for item_id in ("abandoned-item", "i2"):
            await manager.conn.incoming.put(
                {
                    "type": "conversation.item.input_audio_transcription.delta",
                    "item_id": item_id,
                    "delta": "Неоднозначный фрагмент",
                }
            )
        await commit(stt, manager.conn, "two", "i2")
        await manager.conn.incoming.put(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "i2",
                "transcript": "Вторая фраза",
            }
        )
        assert await anext(events) == {
            "type": "transcript.final",
            "turn_id": "two",
            "text": "Вторая фраза",
        }

        await stt.append("three", AUDIO)
        # Late fragments and a final from the cleared input must not become
        # speech or a final for the third turn after partials resume.
        for suffix in ("delta", "completed"):
            await manager.conn.incoming.put(
                {
                    "type": "conversation.item.input_audio_transcription." + suffix,
                    "item_id": "abandoned-item",
                    "delta": "Старая фраза",
                    "transcript": "Старая фраза",
                }
            )
        await manager.conn.incoming.put(
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "item_id": "i3",
                "delta": "Третья",
            }
        )
        assert await asyncio.wait_for(anext(events), 1) == {
            "type": "transcript.delta",
            "turn_id": "three",
            "delta": "Третья",
        }
        await commit(stt, manager.conn, "three", "i3")
        await manager.conn.incoming.put(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "i3",
                "transcript": "Третья фраза",
            }
        )
        assert await anext(events) == {
            "type": "transcript.final",
            "turn_id": "three",
            "text": "Третья фраза",
        }
    assert manager.conn.closed
