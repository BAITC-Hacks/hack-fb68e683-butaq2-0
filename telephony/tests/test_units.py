from __future__ import annotations

from uuid import uuid4

import pytest

from telephony import (
    CallDirection,
    CallState,
    CallTransition,
    CommentaryUpdate,
    InvalidAgentUpdate,
    InvalidNumber,
    InvalidTransition,
    PolicyDenied,
    ThinkingUpdate,
    check_transfer_target,
    mask_number,
    new_call,
    next_state,
    normalize_e164,
    to_live_event,
)
from telephony.agents import AgentRequest, CallableAgentRuntime, TaskCompleted
from telephony.domain import DelegationTask, TranscriptFragment
from telephony.memory import InMemoryEventInbox, InMemoryLiveStateStore
from telephony.ports import CallContext
from telephony.twilio import STATUS_MAP, TwilioProvider

# --- state machine ---------------------------------------------------------


def test_outbound_happy_path():
    call = new_call(direction=CallDirection.OUTBOUND, to_number="+15550002222", from_number="+1")
    for state in (CallState.DIALING, CallState.RINGING, CallState.CONNECTED, CallState.COMPLETED):
        assert call.apply(CallTransition(to_state=state)) is True
    assert call.is_terminal and call.connected_at and call.ended_at
    assert call.version == 4


def test_late_event_does_not_roll_back():
    call = new_call(direction=CallDirection.OUTBOUND, to_number="+15550002222", from_number="+1")
    call.apply(CallTransition(to_state=CallState.DIALING))
    call.apply(CallTransition(to_state=CallState.CONNECTED))
    # "ringing" arrives after "in-progress": ignore it, do not regress.
    assert call.apply(CallTransition(to_state=CallState.RINGING)) is False
    assert call.state is CallState.CONNECTED


def test_terminal_states_are_immutable():
    call = new_call(direction=CallDirection.OUTBOUND, to_number="+15550002222", from_number="+1")
    call.apply(CallTransition(to_state=CallState.FAILED))
    assert call.apply(CallTransition(to_state=CallState.CONNECTED)) is False
    assert call.state is CallState.FAILED


def test_duplicate_is_ignored_but_illegal_forward_jump_raises():
    assert next_state(CallState.CONNECTED, CallState.CONNECTED) is None
    with pytest.raises(InvalidTransition):
        next_state(CallState.REQUESTED, CallState.CONNECTED)


def test_inbound_reject_is_terminal():
    call = new_call(direction=CallDirection.INBOUND, to_number="+1", from_number="+1")
    assert call.state is CallState.INCOMING
    assert call.apply(CallTransition(to_state=CallState.REJECTED)) is True
    assert call.is_terminal


# --- numbers ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+1 (555) 000-2222", "+15550002222"),
        ("0074951234567", "+74951234567"),
        ("74951234567", "+74951234567"),
    ],
)
def test_normalize_e164(raw, expected):
    assert normalize_e164(raw) == expected


@pytest.mark.parametrize("raw", ["", "+0123456789", "12345", "not-a-number"])
def test_normalize_e164_rejects(raw):
    with pytest.raises(InvalidNumber):
        normalize_e164(raw)


def test_mask_number_hides_the_middle():
    assert mask_number("+15550002222") == "+1555***2222"
    assert "0002" not in mask_number("+15550002222")


def test_transfer_allowlist():
    allowed = dict(allowed_prefixes=["+1555"], allowed_sip_domains=["support.example.com"])
    assert check_transfer_target("tel:+1555 000 3333", **allowed) == "+15550003333"
    assert check_transfer_target("sip:queue@support.example.com", **allowed).startswith("sip:")
    with pytest.raises(PolicyDenied):
        check_transfer_target("+79001234567", **allowed)
    with pytest.raises(PolicyDenied):
        check_transfer_target("sip:queue@evil.example.com", **allowed)


# --- delegation updates ----------------------------------------------------


def test_update_types_map_to_live_events():
    thinking = to_live_event(ThinkingUpdate("x"), "d1")
    commentary = to_live_event(CommentaryUpdate("x"), "d1")
    assert thinking == {
        "type": "session.thinking.append",
        "delegation_id": "d1",
        "content": "x",
    }
    assert commentary == {
        "type": "session.commentary.append",
        "delegation_id": "d1",
        "content": "x",
    }
    assert to_live_event(TaskCompleted(), "d1") is None


def test_append_is_capped_at_the_token_budget():
    event = to_live_event(CommentaryUpdate("word " * 5000), "d1", max_tokens=500)
    assert len(event["content"]) <= 500 * 4
    assert event["delegation_id"] == "d1"


def test_append_rejects_empty_content_and_delegation_id():
    with pytest.raises(InvalidAgentUpdate):
        to_live_event(CommentaryUpdate("  "), "d1")
    with pytest.raises(ValueError):
        to_live_event(CommentaryUpdate("result"), "  ")


async def test_callable_runtime_wraps_a_plain_function():
    runtime = CallableAgentRuntime(lambda request, context: f"balance for {context.scenario}")
    call_id = uuid4()
    request = AgentRequest(
        task=DelegationTask(delegation_id="d1", call_id=call_id, instructions="")
    )
    context = CallContext(call_id=call_id, scenario="billing", state=CallState.CONNECTED)
    updates = [u async for u in runtime.run(request, context)]
    assert isinstance(updates[0], CommentaryUpdate)
    assert updates[0].text == "balance for billing"
    assert isinstance(updates[-1], TaskCompleted)


# --- transcript assembly ---------------------------------------------------


async def test_transcript_is_ordered_by_media_time_not_arrival():
    store = InMemoryLiveStateStore()
    call_id = uuid4()
    context = CallContext(call_id=call_id, scenario=None, state=CallState.CONNECTED)
    await store.save_context(context)
    await store.append_transcript(call_id, TranscriptFragment("assistant", "second", 1000, 2000))
    await store.append_transcript(call_id, TranscriptFragment("user", "first", 0, 900))
    assert [f.text for f in context.transcript] == ["first", "second"]


# --- inbox -----------------------------------------------------------------


async def test_inbox_claims_an_event_once():
    inbox = InMemoryEventInbox()
    assert await inbox.claim("twilio", "CA1:ringing", 60) is True
    assert await inbox.claim("twilio", "CA1:ringing", 60) is False
    assert await inbox.claim("twilio", "CA1:completed", 60) is True


# --- twilio mapping --------------------------------------------------------


def test_twilio_status_mapping_covers_the_terminal_reasons():
    event = TwilioProvider.to_event(
        {"CallSid": "CA1", "CallStatus": "no-answer", "Direction": "outbound-api"}
    )
    assert event.status is CallState.FAILED
    assert event.event_id == "CA1:no-answer"
    assert event.payload == {}  # raw provider payload is never carried along
    assert set(STATUS_MAP.values()) >= {CallState.RINGING, CallState.CONNECTED}
