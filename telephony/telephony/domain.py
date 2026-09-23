"""Domain model for telephony calls.

Knows nothing about FastAPI, Twilio, OpenAI, Redis or SQLAlchemy -- just the
call, its legal state changes and the value objects the rest of the package
passes around.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- errors ----------------------------------------------------------------


class TelephonyError(Exception):
    """Base class for every domain error."""


class InvalidTransition(TelephonyError):
    """A state change the machine does not allow."""


class CallNotFound(TelephonyError):
    pass


class PolicyDenied(TelephonyError):
    """Authorization / consent / allowlist said no."""


class InvalidNumber(TelephonyError, ValueError):
    pass


class WebhookVerificationError(TelephonyError):
    """Signature missing, malformed or wrong."""


# --- enums -----------------------------------------------------------------


class CallDirection(str, Enum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class CallState(str, Enum):
    # outbound
    REQUESTED = "requested"
    DIALING = "dialing"
    # inbound
    INCOMING = "incoming"
    ACCEPTED = "accepted"
    # shared
    RINGING = "ringing"
    CONNECTED = "connected"
    ENDING = "ending"
    # terminal
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    REJECTED = "rejected"


TERMINAL_STATES = frozenset(
    {CallState.COMPLETED, CallState.CANCELED, CallState.FAILED, CallState.REJECTED}
)

ALLOWED_TRANSITIONS: dict[CallState, frozenset[CallState]] = {
    CallState.REQUESTED: frozenset(
        {CallState.DIALING, CallState.RINGING, CallState.CANCELED, CallState.FAILED}
    ),
    CallState.DIALING: frozenset(
        {
            CallState.RINGING,
            CallState.CONNECTED,
            CallState.CANCELED,
            CallState.FAILED,
            CallState.COMPLETED,
        }
    ),
    CallState.INCOMING: frozenset(
        {CallState.ACCEPTED, CallState.REJECTED, CallState.FAILED, CallState.CANCELED}
    ),
    CallState.ACCEPTED: frozenset(
        {CallState.RINGING, CallState.CONNECTED, CallState.FAILED, CallState.COMPLETED}
    ),
    CallState.RINGING: frozenset(
        {
            CallState.CONNECTED,
            CallState.ENDING,
            CallState.CANCELED,
            CallState.FAILED,
            CallState.COMPLETED,
        }
    ),
    CallState.CONNECTED: frozenset({CallState.ENDING, CallState.COMPLETED, CallState.FAILED}),
    CallState.ENDING: frozenset({CallState.COMPLETED, CallState.FAILED}),
    CallState.COMPLETED: frozenset(),
    CallState.CANCELED: frozenset(),
    CallState.FAILED: frozenset(),
    CallState.REJECTED: frozenset(),
}

# Progress order, used to recognise a late webhook (target is *behind* where the
# call already is) and drop it instead of rolling the call backwards.
_PROGRESS = [
    CallState.REQUESTED,
    CallState.INCOMING,
    CallState.DIALING,
    CallState.ACCEPTED,
    CallState.RINGING,
    CallState.CONNECTED,
    CallState.ENDING,
    CallState.COMPLETED,
]
_RANK = {state: i for i, state in enumerate(_PROGRESS)}


# Ending and the terminal states are reachable from any live state: either side
# can hang up, and the carrier can fail the call, at any point.
_ALWAYS_REACHABLE = TERMINAL_STATES | {CallState.ENDING}


def next_state(current: CallState, target: CallState) -> CallState | None:
    """Resolve a requested state change.

    Returns the new state, or ``None`` when the change is an ignorable
    duplicate/late provider event. Raises :class:`InvalidTransition` when the
    change is genuinely illegal (a caller bug, not a network reorder).
    """

    if current is target or current in TERMINAL_STATES:
        return None
    if target in ALLOWED_TRANSITIONS[current] or target in _ALWAYS_REACHABLE:
        return target
    if _RANK.get(target, 99) < _RANK.get(current, 99):
        return None  # out-of-order delivery: we are already past it
    raise InvalidTransition(f"{current.value} -> {target.value} is not allowed")


# --- E.164 and masking -----------------------------------------------------

_E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_SIP = re.compile(r"^sips?:[^@\s]+@([A-Za-z0-9.\-]+)$")


def normalize_e164(value: str) -> str:
    """Accept the usual human spellings, return strict E.164 or raise."""

    cleaned = re.sub(r"[\s()\-.]", "", value or "")
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    if not _E164.match(cleaned):
        raise InvalidNumber(f"Not a valid E.164 number: {value!r}")
    return cleaned


def mask_number(value: str | None) -> str | None:
    """``+79991234567`` -> ``+7999***4567``. Never log the full number."""

    if not value:
        return value
    if len(value) <= 8:
        return value[:2] + "*" * (len(value) - 2)
    return f"{value[:5]}***{value[-4:]}"


def check_transfer_target(
    target: str, *, allowed_prefixes: list[str], allowed_sip_domains: list[str]
) -> str:
    """Validate a ``tel:``/``sip:`` transfer target against the allowlist."""

    raw = target.strip()
    sip = _SIP.match(raw)
    if sip:
        domain = sip.group(1).lower()
        if domain not in {d.lower() for d in allowed_sip_domains}:
            raise PolicyDenied(f"SIP domain not allowed: {domain}")
        return raw
    number = normalize_e164(raw[4:] if raw.lower().startswith("tel:") else raw)
    if not any(number.startswith(p) for p in allowed_prefixes):
        raise PolicyDenied(f"Transfer target not allowed: {mask_number(number)}")
    return number


# --- value objects ---------------------------------------------------------


@dataclass(slots=True)
class Usage:
    voice_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    final: bool = False


@dataclass(slots=True)
class CallTransition:
    to_state: CallState
    at: str = field(default_factory=now_iso)
    source: str = "application"  # "provider" | "openai" | "application"
    event_id: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class TranscriptFragment:
    """One piece of speech, ordered by media time -- not by arrival time."""

    speaker: Literal["user", "assistant"]
    text: str
    start_ms: int
    end_ms: int


@dataclass(slots=True)
class DelegationTask:
    delegation_id: str
    call_id: UUID
    instructions: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Call:
    call_id: UUID
    direction: CallDirection
    state: CallState
    to_number: str
    from_number: str
    scenario: str | None = None
    provider_call_id: str | None = None
    session_id: str | None = None
    idempotency_key: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    transitions: list[CallTransition] = field(default_factory=list)
    terminal_reason: str | None = None
    finalization_status: Literal["pending", "confirmed", "unconfirmed"] = "pending"
    version: int = 0
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    connected_at: str | None = None
    ended_at: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def apply(self, transition: CallTransition) -> bool:
        """Apply a state change in place. False = ignored duplicate/late event."""

        resolved = next_state(self.state, transition.to_state)
        if resolved is None:
            return False
        self.state = resolved
        self.updated_at = transition.at
        self.version += 1
        self.transitions.append(transition)
        if resolved is CallState.CONNECTED and self.connected_at is None:
            self.connected_at = transition.at
        if resolved in TERMINAL_STATES:
            self.ended_at = transition.at
            self.terminal_reason = transition.reason or self.terminal_reason
        return True


def new_call(
    *,
    direction: CallDirection,
    to_number: str,
    from_number: str,
    scenario: str | None = None,
    metadata: dict[str, str] | None = None,
    idempotency_key: str | None = None,
    call_id: UUID | None = None,
) -> Call:
    state = CallState.REQUESTED if direction is CallDirection.OUTBOUND else CallState.INCOMING
    return Call(
        call_id=call_id or uuid4(),
        direction=direction,
        state=state,
        to_number=to_number,
        from_number=from_number,
        scenario=scenario,
        metadata=metadata or {},
        idempotency_key=idempotency_key,
    )
