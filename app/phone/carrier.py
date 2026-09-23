"""SignalWire cXML adapter. Only inbound bridging and hangup are enabled."""

import hmac
import secrets
from collections.abc import Mapping
from urllib.parse import parse_qsl, quote, urlencode
from uuid import UUID
from xml.sax.saxutils import escape, quoteattr

import httpx
from telephony.domain import Call, PolicyDenied, WebhookVerificationError
from telephony.openai_live import LINKED_CALL_HEADER
from telephony.ports import ProviderEvent, RejectReason
from telephony.twilio import TwilioProvider

from .settings import PhoneSettings

BRIDGE_TOKEN_HEADER = "X-Butaq-Bridge-Token"


class SignalWireProvider:
    def __init__(self, settings: PhoneSettings, *, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client
        self._tokens: dict[UUID, str] = {}

    def verify_webhook(self, raw_body: bytes, headers: Mapping[str, str], url: str) -> ProviderEvent:
        from twilio.request_validator import RequestValidator

        lower = {key.lower(): value for key, value in headers.items()}
        signature = lower.get("x-signalwire-signature") or lower.get("x-twilio-signature")
        key = self.settings.signalwire_signing_key
        if not key or not signature:
            raise WebhookVerificationError("Missing SignalWire webhook signature or signing key")
        try:
            pairs = parse_qsl(raw_body.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
        except (UnicodeError, ValueError) as exc:
            raise WebhookVerificationError("Invalid form body") from exc
        params = dict(pairs)
        if len(params) != len(pairs) or not RequestValidator(key).validate(url, params, signature):
            raise WebhookVerificationError("SignalWire signature mismatch")
        if params.get("AccountSid") != self.settings.signalwire_project_id or not params.get("CallSid"):
            raise WebhookVerificationError("Unexpected SignalWire account or missing call ID")
        event = TwilioProvider.to_event(params)
        event.source = "signalwire"
        return event

    def answer_response(self, call: Call) -> tuple[str, str]:
        token = self._tokens.setdefault(call.call_id, secrets.token_urlsafe(32))
        uri = self.settings.openai_sips_dial_uri
        if not uri:
            raise ValueError("OpenAI SIP project not configured")
        uri += "?" + urlencode({LINKED_CALL_HEADER: str(call.call_id), BRIDGE_TOKEN_HEADER: token})
        # After the SIP child ends, end the original carrier leg too.
        xml = f'<Response><Dial timeLimit={quoteattr(str(self.settings.max_call_seconds))}><Sip>{escape(uri)}</Sip></Dial><Hangup/></Response>'
        return xml, "application/xml"

    def matches_bridge(self, call_id: UUID, token: str | None) -> bool:
        expected = self._tokens.get(call_id)
        return bool(expected and token and hmac.compare_digest(expected, token))

    def forget(self, call_id: UUID) -> None:
        self._tokens.pop(call_id, None)

    reject_response = staticmethod(TwilioProvider.reject_response)

    async def start_call(self, command):
        raise PolicyDenied("Outbound calls are disabled")

    async def transfer(self, provider_call_id: str, target: str) -> None:
        raise PolicyDenied("Human transfer is not implemented")

    async def reject(self, provider_call_id: str, reason: RejectReason) -> None:
        await self.hangup(provider_call_id)

    async def hangup(self, provider_call_id: str) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10, follow_redirects=False)
        project = quote(self.settings.signalwire_project_id or "", safe="")
        call_id = quote(provider_call_id, safe="")
        url = f"https://{self.settings.signalwire_space}/api/laml/2010-04-01/Accounts/{project}/Calls/{call_id}.json"
        response = await self._client.post(url, data={"Status": "completed"}, auth=(self.settings.signalwire_project_id or "", self.settings.signalwire_api_token or ""))
        response.raise_for_status()

    async def aclose(self) -> None:
        self._tokens.clear()
        if self._client is not None:
            await self._client.aclose()
