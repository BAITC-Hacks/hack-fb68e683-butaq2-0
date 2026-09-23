"""STT -> LLM -> TTS voice pipeline.

Use this when a turn-based HTTP call fits better than a live socket: the client
posts recorded audio, gets audio back. For barge-in and sub-second latency use
``realtime.py`` instead.

Docs: speech-to-text, text-to-speech and the Responses API on
https://developers.openai.com/api/docs
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from openai import AsyncOpenAI

from .audio import CONTENT_TYPE_BY_FORMAT, upload_name, validate
from .client import ClientProvider
from .config import V2VSettings, get_settings
from .sessions import InMemorySessionStore, Message, SessionStore


@dataclass(slots=True)
class Transcript:
    text: str
    model: str
    language: str | None = None
    duration_ms: int | None = None


@dataclass(slots=True)
class Turn:
    session_id: str
    transcript: str
    reply: str
    audio_format: str
    content_type: str


class VoicePipeline:
    def __init__(
        self,
        settings: V2VSettings | None = None,
        *,
        client: AsyncOpenAI | None = None,
        store: SessionStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store: SessionStore = store or InMemorySessionStore()
        self._clients = ClientProvider(self.settings, client)

    @property
    def client(self) -> AsyncOpenAI:
        return self._clients.get()

    # -- speech to text ----------------------------------------------------
    async def transcribe(
        self,
        audio: bytes,
        *,
        filename: str = "audio.wav",
        model: str | None = None,
        language: str | None = None,
        prompt: str | None = None,
    ) -> Transcript:
        settings = self.settings
        # Sniff before spending a request: a mislabelled blob is renamed after
        # its real container, and a non-audio upload is refused here.
        mime = validate(audio, max_bytes=settings.max_upload_bytes)
        kwargs: dict[str, object] = {}
        lang = language or settings.transcribe_language
        if lang:
            kwargs["language"] = lang
        if prompt:
            kwargs["prompt"] = prompt
        used_model = model or settings.transcribe_model
        result = await self.client.audio.transcriptions.create(
            model=used_model,
            file=(upload_name(filename, mime), audio, mime),
            response_format="json",
            **kwargs,
        )
        return Transcript(text=(result.text or "").strip(), model=used_model, language=lang)

    # -- reasoning ---------------------------------------------------------
    async def reply(
        self,
        session_id: str,
        user_text: str,
        *,
        model: str | None = None,
        instructions: str | None = None,
    ) -> str:
        settings = self.settings
        session = await self.store.get_or_create(session_id)
        history = session.as_input(settings.max_history_turns)
        history.append({"role": "user", "content": user_text})
        used_model = model or settings.text_model
        response = await self.client.responses.create(
            model=used_model,
            instructions=instructions or settings.instructions,
            input=history,
        )
        text = (response.output_text or "").strip()
        await self.store.append(
            session_id,
            Message(role="user", content=user_text),
            Message(role="assistant", content=text),
        )
        await self.store.set_response_id(session_id, response.id)
        return text

    # -- text to speech ----------------------------------------------------
    async def speak(
        self,
        text: str,
        *,
        voice: str | None = None,
        audio_format: str | None = None,
        instructions: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Stream synthesised audio chunks as they are produced."""

        settings = self.settings
        fmt = audio_format or settings.tts_format
        kwargs: dict[str, object] = {}
        directions = instructions or settings.tts_instructions
        if directions:
            kwargs["instructions"] = directions
        async with self.client.audio.speech.with_streaming_response.create(
            model=model or settings.tts_model,
            voice=voice or settings.tts_voice,
            input=text,
            response_format=fmt,
            **kwargs,
        ) as response:
            async for piece in response.iter_bytes():
                yield piece

    async def speak_bytes(self, text: str, **kwargs) -> bytes:
        buffer = bytearray()
        async for piece in self.speak(text, **kwargs):
            buffer.extend(piece)
        return bytes(buffer)

    # -- full voice turn ----------------------------------------------------
    async def turn(
        self,
        audio: bytes,
        *,
        session_id: str = "default",
        filename: str = "audio.wav",
        voice: str | None = None,
        audio_format: str | None = None,
        instructions: str | None = None,
        model: str | None = None,
    ) -> tuple[Turn, AsyncIterator[bytes]]:
        """Audio in, audio out. Returns the turn's text plus an audio stream."""

        transcript = await self.transcribe(audio, filename=filename)
        if not transcript.text:
            raise ValueError("No speech detected in the uploaded audio")
        answer = await self.reply(
            session_id, transcript.text, model=model, instructions=instructions
        )
        fmt = audio_format or self.settings.tts_format
        turn = Turn(
            session_id=session_id,
            transcript=transcript.text,
            reply=answer,
            audio_format=fmt,
            content_type=CONTENT_TYPE_BY_FORMAT.get(fmt, "application/octet-stream"),
        )
        stream = self.speak(answer, voice=voice, audio_format=fmt)
        return turn, stream
