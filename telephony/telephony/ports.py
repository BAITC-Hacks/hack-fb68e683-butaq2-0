"""Protocol interfaces the application layer depends on.

Every provider SDK lives behind one of these. Swap any of them in the
:class:`telephony.Telephony` constructor -- nothing above this file imports
Twilio, OpenAI, Redis or SQLAlchemy.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from .domain import Call, CallDirection, CallState, CallTransition, TranscriptFragment

if TYPE_CHECKING:
    from .delegation import LiveFinalizationResult

# --- provider-facing value objects -----------------------------------------


@dataclass(slots=True)
class OutboundCallCommand:
    to_number: str
    from_number: str
    call_id: UUID
    scenario: str | None = None
    instructions: str | None = None
    voice: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderCall:
    provider_call_id: str
    status: CallState | None = None


@dataclass(slots=True)
class ProviderEvent:
    """A verified, provider-neutral webhook payload."""

    source: str  # "twilio" | "openai" | ...
    event_id: str
    kind: Literal["incoming", "status", "session", "dtmf"]
    provider_call_id: str | None = None
    status: CallState | None = None
    direction: CallDirection | None = None
    from_number: str | None = None
    to_number: str | None = None
    session_id: str | None = None
    reason: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LiveIncomingCall:
    """Verified OpenAI Live SIP invitation with untrusted headers removed."""

    event_id: str
    session_id: str
    from_number: str | None = None
    to_number: str | None = None
    # Our own call id, echoed back by the SIP bridge we dialled. Only a value
    # we put there ourselves is trusted; any other SIP header stays out.
    linked_call_id: UUID | None = None
    # Application-issued capability; never use the call ID alone to authorize a bridge.
    bridge_token: str | None = field(default=None, repr=False)


@dataclass(slots=True)
class RejectReason:
    code: Literal["busy", "rejected", "policy"] = "rejected"
    detail: str | None = None


@dataclass(slots=True)
class CallQuery:
    state: CallState | None = None
    direction: CallDirection | None = None
    scenario: str | None = None
    cursor: str | None = None
    limit: int = 50


@dataclass(slots=True)
class Page:
    items: list[Call]
    next_cursor: str | None = None


@dataclass(slots=True)
class CallContext:
    """What the backend agent is allowed to see. No raw provider payloads."""

    call_id: UUID
    scenario: str | None
    state: CallState
    transcript: list[TranscriptFragment] = field(default_factory=list)
    application_state: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)


# --- ports -----------------------------------------------------------------


@runtime_checkable
class TelephonyProvider(Protocol):
    """Carrier side: Twilio today, anything else tomorrow."""

    async def start_call(self, command: OutboundCallCommand) -> ProviderCall: ...
    async def reject(self, provider_call_id: str, reason: RejectReason) -> None: ...
    async def transfer(self, provider_call_id: str, target: str) -> None: ...
    async def hangup(self, provider_call_id: str) -> None: ...
    def verify_webhook(
        self, raw_body: bytes, headers: Mapping[str, str], url: str
    ) -> ProviderEvent: ...
    def answer_response(self, call: Call) -> tuple[str, str]:
        """``(body, content_type)`` returned to the provider on an inbound call."""
        ...


@runtime_checkable
class LiveConnection(Protocol):
    async def events(self) -> AsyncIterator[Mapping[str, Any]]: ...
    async def send(self, event: Mapping[str, Any]) -> None: ...


@runtime_checkable
class LiveGateway(Protocol):
    def attach(self, session_id: str) -> Any:
        """Async context manager yielding a :class:`LiveConnection`."""
        ...


@runtime_checkable
class LiveCallController(Protocol):
    """OpenAI-owned SIP call decisions, separate from sideband transport."""

    def verify_webhook(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> LiveIncomingCall | None: ...

    async def accept(
        self,
        session_id: str,
        *,
        instructions: str | None = None,
        voice: str | None = None,
    ) -> None: ...

    async def reject(self, session_id: str, *, status_code: int) -> None: ...

    async def hangup(self, session_id: str) -> None: ...

    async def transfer(self, session_id: str, target: str) -> None: ...


LiveFinalizer = Callable[[UUID, "LiveFinalizationResult"], Awaitable[None]]


@runtime_checkable
class LiveSessionRunnerPort(Protocol):
    """Own background sideband tasks without owning call persistence."""

    async def start(self, call: Call, *, on_finalized: LiveFinalizer) -> bool: ...
    async def request_close(self, call_id: UUID) -> bool: ...
    async def aclose(self, *, drain_timeout: float = 30.0) -> None: ...


@runtime_checkable
class CallRepository(Protocol):
    async def create(self, call: Call) -> Call: ...
    async def get(self, call_id: UUID) -> Call | None: ...
    async def find_by_provider_id(self, provider_call_id: str) -> Call | None: ...
    async def find_by_idempotency_key(self, key: str) -> Call | None: ...
    async def save(self, call: Call) -> Call: ...
    async def transition(self, call_id: UUID, transition: CallTransition) -> Call: ...
    async def list(self, query: CallQuery) -> Page: ...
    async def count_active(self) -> int: ...


@runtime_checkable
class EventInbox(Protocol):
    async def claim(self, source: str, event_id: str, ttl_seconds: int) -> bool:
        """True the first time this event is seen, False for a replay."""
        ...

    async def release(self, source: str, event_id: str) -> None:
        """Release a failed claim so the provider retry can be processed."""
        ...


@runtime_checkable
class LiveStateStore(Protocol):
    async def append_transcript(self, call_id: UUID, fragment: TranscriptFragment) -> None: ...
    async def load_context(self, call_id: UUID) -> CallContext | None: ...
    async def save_context(self, context: CallContext) -> None: ...
    async def delete(self, call_id: UUID) -> None: ...


@runtime_checkable
class InboundRoutingPolicy(Protocol):
    async def route(self, event: ProviderEvent) -> RoutingDecision: ...


@dataclass(slots=True)
class RoutingDecision:
    accept: bool = True
    scenario: str | None = None
    reason: RejectReason | None = None


@runtime_checkable
class AuthorizationPolicy(Protocol):
    async def allow_outbound(self, command: OutboundCallCommand) -> None: ...
    async def allow_transfer(self, call: Call, target: str) -> None: ...


@runtime_checkable
class CallEventSink(Protocol):
    async def emit(self, call: Call, event: str, detail: Mapping[str, Any]) -> None: ...


@runtime_checkable
class TranscriptSink(Protocol):
    async def write(self, call_id: UUID, fragments: Sequence[TranscriptFragment]) -> None: ...
