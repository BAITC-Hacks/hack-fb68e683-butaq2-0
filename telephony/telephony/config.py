"""Configuration for the telephony library.

Override anything with ``TELEPHONY_``-prefixed environment variables, e.g.
``TELEPHONY_TWILIO_AUTH_TOKEN``, ``TELEPHONY_MAX_CONCURRENT_CALLS``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class TelephonySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TELEPHONY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- OpenAI -------------------------------------------------------------
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_webhook_secret: str | None = None
    # Project > General in the OpenAI dashboard. It identifies the SIP
    # destination and is not an API credential.
    openai_project_id: str | None = None
    live_model: str = "gpt-live-1"
    voice: str = "marin"
    # Frontend prompt: how to *talk*. Business rules belong in backend_instructions.
    instructions: str = (
        "You are a phone agent. Speak naturally, keep turns short, and let the "
        "caller interrupt you. When a request needs a lookup or an action, "
        "delegate it to the backend and say what you are doing meanwhile."
    )
    backend_model: str = "gpt-5.6-luna"
    backend_instructions: str = (
        "You are the backend of a phone agent. Use the available tools, verify "
        "facts before reporting them, and never invent account data."
    )
    # Selects the backend adapter created by ``build_runtime``. Framework
    # objects remain application-owned and are passed to that factory.
    agent_runtime: Literal["responses", "langchain", "langgraph", "callable"] = "responses"
    delegation_timeout_seconds: float = 20.0

    # --- Twilio -------------------------------------------------------------
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    # Public HTTPS base that Twilio calls back on; used for signature checking.
    public_base_url: str = "http://localhost:8000"

    # --- infrastructure -----------------------------------------------------
    redis_url: str | None = None
    database_url: str | None = None
    queue_name: str = "telephony:jobs"
    lease_ttl_seconds: int = 30
    lease_heartbeat_seconds: int = 10
    event_dedup_ttl_seconds: int = 24 * 3600

    # --- limits -------------------------------------------------------------
    max_call_seconds: int = 1800
    max_concurrent_calls: int = 50
    max_tool_concurrency: int = 4
    max_tool_calls_per_call: int = 32
    max_webhook_bytes: int = 256 * 1024
    max_metadata_bytes: int = 4096
    # One delegation append is capped by the GPT-Live contract at 500 tokens.
    max_append_tokens: int = 500

    # --- privacy ------------------------------------------------------------
    # Nothing is stored: the Live session is built with store=False and the
    # in-memory context is dropped when the call ends. Recording and transcript
    # persistence are not implemented, so there are no flags for them.
    # Spoken on the Twilio leg only; direct SIP callers hear whatever the
    # voice prompt in ``instructions`` says.
    disclosure: str | None = "This call is handled by an AI assistant."

    # --- transfer allowlist -------------------------------------------------
    allowed_transfer_prefixes: list[str] = []
    allowed_sip_domains: list[str] = []

    # --- HTTP ---------------------------------------------------------------
    api_prefix: str = "/telephony"
    api_tag: str = "telephony"

    @property
    def openai_sip_origination_uri(self) -> str | None:
        """TLS SIP destination for a carrier trunk, or ``None`` until configured."""

        if not self.openai_project_id:
            return None
        return f"sip:{self.openai_project_id}@sip.api.openai.com;transport=tls"

    @property
    def openai_sips_dial_uri(self) -> str | None:
        """Same destination for a TwiML ``<Dial><Sip>``, or ``None``.

        The scheme has to be ``sips:``: OpenAI rejects a Live SIP call without
        SRTP, and for Twilio ``sip:`` with ``;transport=tls`` secures only the
        signalling. ``sips:`` is what turns on encrypted media, and it implies
        TLS, so the transport parameter must not be repeated here.
        """

        if not self.openai_project_id:
            return None
        return f"sips:{self.openai_project_id}@sip.api.openai.com"


@lru_cache(maxsize=1)
def get_settings() -> TelephonySettings:
    return TelephonySettings()
