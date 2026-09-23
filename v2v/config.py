"""Configuration for the v2v (voice-to-voice) library.

Two transports are covered, both talking to OpenAI:

* **realtime** -- one speech-to-speech model over one socket
  (``gpt-realtime-2.1``), lowest latency, barge-in handled by the model;
* **pipeline** -- transcribe -> reason -> speak (``gpt-transcribe`` ->
  a text model -> ``gpt-4o-mini-tts``), for request/response style turns.

Override anything with ``V2V_``-prefixed environment variables.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

AudioCodec = Literal["audio/pcm", "audio/pcmu", "audio/pcma"]
TurnDetection = Literal["semantic_vad", "server_vad", "none"]
SpeechFormat = Literal["mp3", "opus", "aac", "flac", "wav", "pcm"]


class V2VSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="V2V_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- OpenAI access ----------------------------------------------------
    api_key: str | None = None
    base_url: str | None = None
    request_timeout: float = 120.0
    max_retries: int = 2

    # --- Realtime transport ------------------------------------------------
    realtime_model: str = "gpt-realtime-2.1"
    voice: str = "marin"
    instructions: str = (
        "You are a helpful voice assistant. Keep answers short and conversational, "
        "one or two sentences unless asked for detail. Speak the user's language."
    )
    input_codec: AudioCodec = "audio/pcm"
    input_sample_rate: int = 24000
    output_codec: AudioCodec = "audio/pcm"
    output_sample_rate: int = 24000
    turn_detection: TurnDetection = "semantic_vad"
    # server_vad only:
    vad_silence_ms: int = 500
    vad_threshold: float = 0.5
    vad_prefix_padding_ms: int = 300
    input_transcription_model: str = "gpt-transcribe"
    output_modalities: list[Literal["audio", "text"]] = ["audio"]
    # Safety valve: how many tool calls the model may chain without new user
    # input before the proxy stops auto-continuing the response. Protects against
    # a runaway tool loop burning tokens (and money) on a live socket.
    max_tool_rounds: int = 8
    # Let browser clients push their own session.update through the proxy.
    # Off by default: the server owns instructions, tools and voice.
    allow_client_session_update: bool = False
    # Lifetime of the ephemeral token handed to browsers (WebRTC/WebSocket).
    client_secret_ttl_seconds: int = 600

    # --- Pipeline transport ------------------------------------------------
    transcribe_model: str = "gpt-transcribe"
    text_model: str = "gpt-5.6-terra"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "marin"
    tts_format: SpeechFormat = "mp3"
    tts_instructions: str | None = None
    transcribe_language: str | None = None
    max_history_turns: int = 20
    max_upload_bytes: int = 25 * 1024 * 1024  # OpenAI transcription file limit

    # --- HTTP ---------------------------------------------------------------
    api_prefix: str = "/v2v"
    api_tag: str = "v2v"


@lru_cache(maxsize=1)
def get_settings() -> V2VSettings:
    return V2VSettings()
