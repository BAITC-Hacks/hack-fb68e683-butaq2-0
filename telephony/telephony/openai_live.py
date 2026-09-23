"""OpenAI GPT-Live session configuration and sideband transport.

The public adapters keep OpenAI SDK details behind the provider-neutral ports.
The SDK itself is imported only when the default client is needed, so users who
do not install the ``openai`` extra can still use the rest of the package.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, Protocol
from uuid import UUID

from .config import TelephonySettings, get_settings
from .domain import WebhookVerificationError
from .ports import LiveIncomingCall

_LIVE_INCOMING_EVENTS = frozenset({"live.transport.incoming", "live.call.incoming"})
# Custom SIP header carrying our call id across a carrier SIP bridge, so an
# outbound call and the Live session it reaches stay one record.
LINKED_CALL_HEADER = "X-Telephony-Call-Id"

_log = logging.getLogger(__name__)


class _SidebandConnection(Protocol):
    def __aiter__(self) -> AsyncIterator[Any]: ...

    async def send(self, event: Mapping[str, Any]) -> None: ...


def _create_async_client(settings: TelephonySettings) -> Any:
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover - depends on installation profile
        raise RuntimeError(
            'OpenAI support is not installed; install telephony with the "openai" extra'
        ) from exc

    options: dict[str, str] = {}
    if settings.openai_api_key:
        options["api_key"] = settings.openai_api_key
    if settings.openai_base_url:
        options["base_url"] = settings.openai_base_url
    if settings.openai_webhook_secret:
        options["webhook_secret"] = settings.openai_webhook_secret
    return AsyncOpenAI(**options)


def _as_mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        result = model_dump(mode="json", by_alias=True, exclude_none=True)
        if isinstance(result, Mapping):
            return result
    raise WebhookVerificationError(f"OpenAI webhook {name} must be an object")


def _linked_call_id(value: str | None) -> UUID | None:
    """Read back our own call id from the bridge we dialled.

    A caller can forge this header, so it is a *lookup key only*: the call it
    points at must already exist and already be outbound and un-linked. It
    never grants anything by itself.
    """

    if not value:
        return None
    try:
        return UUID(value.strip())
    except ValueError:
        return None


def _number_from_sip_header(value: str | None) -> str | None:
    """Extract only a phone-like user part; never retain the raw SIP header."""

    if not value:
        return None
    candidate = value.strip().strip("<>")
    if ":" in candidate:
        candidate = candidate.split(":", 1)[1]
    candidate = candidate.split("@", 1)[0].split(";", 1)[0]
    return candidate if candidate.startswith("+") and candidate[1:].isdigit() else None


class OpenAILiveConnection:
    """Provider-neutral view of an OpenAI SDK sideband connection."""

    def __init__(self, connection: _SidebandConnection) -> None:
        self._connection = connection

    async def events(self) -> AsyncIterator[Mapping[str, Any]]:
        """Yield SDK events as ordinary mappings for the application layer."""

        async for event in self._connection:
            yield self._to_mapping(event)

    async def send(self, event: Mapping[str, Any]) -> None:
        """Send one Live client event without exposing SDK-specific models."""

        await self._connection.send(dict(event))

    @staticmethod
    def _to_mapping(event: Any) -> Mapping[str, Any]:
        if isinstance(event, Mapping):
            return dict(event)

        model_dump = getattr(event, "model_dump", None)
        if callable(model_dump):
            result = model_dump(mode="json", by_alias=True, exclude_none=True)
            if isinstance(result, Mapping):
                return dict(result)

        raise TypeError(f"unsupported OpenAI Live event: {type(event).__name__}")


class OpenAILiveGateway:
    """Attach to existing GPT-Live sessions through a sideband WebSocket."""

    def __init__(
        self,
        settings: TelephonySettings | None = None,
        *,
        client: Any | None = None,
        graceful_close: bool = True,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._graceful_close = graceful_close

    @asynccontextmanager
    async def attach(self, session_id: str) -> AsyncIterator[OpenAILiveConnection]:
        """Attach without restarting the already-running Live session."""

        session_id = self._require_session_id(session_id)
        manager = self._get_client().live.sideband.connect(
            session_id=session_id,
            graceful_close=self._graceful_close,
        )
        async with manager as connection:
            yield OpenAILiveConnection(connection)

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _create_client(self) -> Any:
        return _create_async_client(self._settings)

    @staticmethod
    def _require_session_id(session_id: str) -> str:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        return session_id


class OpenAILiveSessionBuilder:
    """Build startup configuration for a client-delegated Live session."""

    def __init__(self, settings: TelephonySettings | None = None) -> None:
        self._settings = settings or get_settings()

    def build(
        self,
        *,
        instructions: str | None = None,
        voice: str | None = None,
    ) -> dict[str, Any]:
        """Return a fresh Live session payload with per-call overrides.

        ``None`` means "use the configured default". Empty overrides are
        rejected because silently sending an empty prompt or voice name makes
        call-specific configuration errors difficult to diagnose.
        """

        resolved_instructions = self._resolve(
            "instructions", instructions, self._settings.instructions
        )
        resolved_voice = self._resolve("voice", voice, self._settings.voice)
        model = self._require_text("live_model", self._settings.live_model)

        return {
            "type": "live",
            "model": model,
            "instructions": resolved_instructions,
            "audio": {"output": {"voice": resolved_voice}},
            "delegation": {"type": "client"},
            # Storage is opt-in. The base telephony profile keeps no recording
            # or transcript after a call.
            "store": False,
        }

    @classmethod
    def _resolve(cls, name: str, override: str | None, default: str) -> str:
        return cls._require_text(name, default if override is None else override)

    @staticmethod
    def _require_text(name: str, value: str) -> str:
        if not value.strip():
            raise ValueError(f"{name} must not be empty")
        return value


class OpenAILiveTelephony:
    """Verify Live SIP webhooks and make the one accept/reject decision."""

    def __init__(
        self,
        settings: TelephonySettings | None = None,
        *,
        client: Any | None = None,
        sessions: OpenAILiveSessionBuilder | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._sessions = sessions or OpenAILiveSessionBuilder(self._settings)

    def verify_webhook(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> LiveIncomingCall | None:
        secret = self._settings.openai_webhook_secret
        if not secret:
            raise WebhookVerificationError("TELEPHONY_OPENAI_WEBHOOK_SECRET is not configured")

        try:
            event = self._get_client().webhooks.unwrap(raw_body, headers, secret=secret)
        except Exception as exc:
            raise WebhookVerificationError("OpenAI webhook verification failed") from exc

        payload = _as_mapping(event, name="event")
        event_type = payload.get("type")
        if event_type not in _LIVE_INCOMING_EVENTS:
            # Silence here looks exactly like a broken tunnel from the outside.
            # ``realtime.call.incoming`` is the usual culprit: it carries a
            # Realtime call_id, not the session_id the Live API accepts with.
            _log.info("ignoring OpenAI webhook of type %r", event_type)
            return None

        data = _as_mapping(payload.get("data"), name="data")
        if event_type == "live.transport.incoming" and data.get("type") != "sip":
            return None

        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise WebhookVerificationError("OpenAI Live webhook has no session_id")

        normalized_headers = {str(key).lower(): str(value) for key, value in headers.items()}
        event_id = normalized_headers.get("webhook-id") or payload.get("id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise WebhookVerificationError("OpenAI Live webhook has no event id")

        sip_headers = data.get("sip_headers", [])
        by_name: dict[str, str] = {}
        if isinstance(sip_headers, list):
            for item in sip_headers:
                if isinstance(item, Mapping):
                    name, value = item.get("name"), item.get("value")
                    if isinstance(name, str) and isinstance(value, str):
                        by_name[name.lower()] = value

        return LiveIncomingCall(
            event_id=event_id,
            session_id=session_id,
            from_number=_number_from_sip_header(by_name.get("from")),
            to_number=_number_from_sip_header(by_name.get("to")),
            linked_call_id=_linked_call_id(by_name.get(LINKED_CALL_HEADER.lower())),
            bridge_token=by_name.get("x-butaq-bridge-token"),
        )

    async def accept(
        self,
        session_id: str,
        *,
        instructions: str | None = None,
        voice: str | None = None,
    ) -> None:
        session = self._sessions.build(instructions=instructions, voice=voice)
        await self._get_client().live.sessions.accept(session_id, session=session)

    async def reject(self, session_id: str, *, status_code: int = 603) -> None:
        if not 300 <= status_code <= 699:
            raise ValueError("status_code must be between 300 and 699")
        await self._get_client().live.sessions.reject(session_id, status_code=status_code)

    async def hangup(self, session_id: str) -> None:
        """End an accepted SIP call when no active sideband runner owns it."""

        await self._get_client().live.sessions.hangup(session_id)

    async def transfer(self, session_id: str, target: str) -> None:
        """Hand the caller over with a SIP REFER.

        A direct Live SIP call has no carrier leg to redirect, so the transfer
        has to come from the session itself.
        """

        target_uri = target if ":" in target else f"tel:{target}"
        await self._get_client().live.sessions.refer(session_id, target_uri=target_uri)

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = _create_async_client(self._settings)
        return self._client
