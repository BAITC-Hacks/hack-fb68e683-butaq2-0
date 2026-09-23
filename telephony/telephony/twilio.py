"""Twilio implementation of :class:`~telephony.ports.TelephonyProvider`.

Needs the ``twilio`` extra: ``pip install "telephony[twilio]"``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from urllib.parse import parse_qsl
from xml.sax.saxutils import escape, quoteattr

from .config import TelephonySettings, get_settings
from .domain import Call, CallDirection, CallState, WebhookVerificationError
from .openai_live import LINKED_CALL_HEADER
from .ports import OutboundCallCommand, ProviderCall, ProviderEvent, RejectReason

SIGNATURE_HEADER = "x-twilio-signature"

# Twilio CallStatus -> our domain state.
STATUS_MAP: dict[str, CallState] = {
    "queued": CallState.DIALING,
    "initiated": CallState.DIALING,
    "ringing": CallState.RINGING,
    "in-progress": CallState.CONNECTED,
    "answered": CallState.CONNECTED,
    "completed": CallState.COMPLETED,
    "busy": CallState.FAILED,
    "failed": CallState.FAILED,
    "no-answer": CallState.FAILED,
    "canceled": CallState.CANCELED,
}


def _twiml_reject_reason(code: str) -> str:
    """TwiML <Reject> only accepts "rejected" or "busy"."""

    return "busy" if code == "busy" else "rejected"


def _dial_verb(target: str) -> str:
    return f"<Response>{_dial_noun(target)}</Response>"


def _dial_noun(target: str) -> str:
    """Build a ``<Dial>`` noun for a phone number or SIP URI."""

    inner = f"<Sip>{escape(target)}</Sip>" if target.lower().startswith("sip") else escape(target)
    return f"<Dial>{inner}</Dial>"


class TwilioProvider:
    def __init__(
        self,
        settings: TelephonySettings | None = None,
        *,
        client: object | None = None,
        twiml_builder=None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._twiml_builder = twiml_builder or self._default_twiml

    # -- lazy SDK -----------------------------------------------------------

    @property
    def client(self):
        if self._client is None:
            from twilio.rest import Client  # imported lazily: optional extra

            self._client = Client(
                self.settings.twilio_account_sid, self.settings.twilio_auth_token
            )
        return self._client

    def _validator(self):
        from twilio.request_validator import RequestValidator

        token = self.settings.twilio_auth_token
        if not token:
            raise WebhookVerificationError("TELEPHONY_TWILIO_AUTH_TOKEN is not configured")
        return RequestValidator(token)

    # -- outbound -----------------------------------------------------------

    async def start_call(self, command: OutboundCallCommand) -> ProviderCall:
        base = self.settings.public_base_url.rstrip("/") + self.settings.api_prefix
        created = await asyncio.to_thread(
            self.client.calls.create,
            to=command.to_number,
            from_=command.from_number,
            url=f"{base}/webhooks/twilio/voice?call_id={command.call_id}",
            status_callback=f"{base}/webhooks/twilio/status",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            status_callback_method="POST",
            time_limit=self.settings.max_call_seconds,
        )
        return ProviderCall(
            provider_call_id=created.sid,
            status=STATUS_MAP.get(getattr(created, "status", "") or "", CallState.DIALING),
        )

    async def hangup(self, provider_call_id: str) -> None:
        await asyncio.to_thread(self.client.calls(provider_call_id).update, status="completed")

    async def reject(self, provider_call_id: str, reason: RejectReason) -> None:
        twiml, _ = self.reject_response(reason)
        await asyncio.to_thread(self.client.calls(provider_call_id).update, twiml=twiml)

    async def transfer(self, provider_call_id: str, target: str) -> None:
        await asyncio.to_thread(
            self.client.calls(provider_call_id).update, twiml=_dial_verb(target)
        )

    # -- inbound ------------------------------------------------------------

    def verify_webhook(
        self, raw_body: bytes, headers: Mapping[str, str], url: str
    ) -> ProviderEvent:
        """Validate the signature over the *raw* body, then parse it.

        ``url`` must be the public URL Twilio actually requested, which behind a
        proxy is not what ASGI reports -- pass it explicitly.
        """

        lower = {k.lower(): v for k, v in headers.items()}
        signature = lower.get(SIGNATURE_HEADER)
        if not signature:
            raise WebhookVerificationError("Missing X-Twilio-Signature")
        params = dict(parse_qsl(raw_body.decode("utf-8", "replace"), keep_blank_values=True))
        if not self._validator().validate(url, params, signature):
            raise WebhookVerificationError("Twilio signature mismatch")
        return self.to_event(params)

    @staticmethod
    def to_event(params: Mapping[str, str]) -> ProviderEvent:
        sid = params.get("CallSid", "")
        status = params.get("CallStatus", "")
        direction = params.get("Direction", "")
        kind = "incoming" if status == "ringing" and direction.startswith("inbound") else "status"
        return ProviderEvent(
            source="twilio",
            # Twilio has no event id: (call, status) is the event identity and
            # is what a retried delivery repeats.
            event_id=f"{sid}:{status}",
            kind=kind,  # type: ignore[arg-type]
            provider_call_id=sid or None,
            status=STATUS_MAP.get(status),
            direction=(
                CallDirection.INBOUND
                if direction.startswith("inbound")
                else CallDirection.OUTBOUND
            ),
            from_number=params.get("From"),
            to_number=params.get("To"),
            reason=params.get("SipResponseCode") or (status if status in STATUS_MAP else None),
            payload={},  # raw provider payload is not persisted by default
        )

    # -- TwiML --------------------------------------------------------------

    def answer_response(self, call: Call) -> tuple[str, str]:
        return (self._twiml_builder(call, self.settings), "application/xml")

    @staticmethod
    def _default_twiml(call: Call, settings: TelephonySettings) -> str:
        """Connect the Twilio call directly to the project's OpenAI SIP trunk."""

        sip_uri = settings.openai_sips_dial_uri
        if sip_uri is None:
            raise RuntimeError(
                "TELEPHONY_OPENAI_PROJECT_ID is required for the default Twilio SIP bridge"
            )
        # Carry our call id across the bridge so the Live session that comes
        # back is recognised as this call, not a separate inbound one.
        sip_uri = f"{sip_uri}?{LINKED_CALL_HEADER}={call.call_id}"
        say = (
            f"<Say>{escape(settings.disclosure)}</Say>" if settings.disclosure else ""
        )
        return f"<Response>{say}{_dial_noun(sip_uri)}</Response>"

    @staticmethod
    def reject_response(reason: RejectReason) -> tuple[str, str]:
        code = quoteattr(_twiml_reject_reason(reason.code))
        return (f"<Response><Reject reason={code}/></Response>", "application/xml")
