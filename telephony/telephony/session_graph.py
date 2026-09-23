"""Small state graph for OpenAI Live server events.

The graph is deliberately framework-independent.  A worker feeds it normalized
event mappings and acts on the typed update it returns.  LangGraph can wrap this
boundary later without coupling the core session lifecycle to that dependency.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from .domain import TranscriptFragment, Usage


class InvalidLiveEvent(ValueError):
    """A recognized Live event does not match its documented shape."""


class LiveSessionNode(str, Enum):
    """Processing nodes in the session event graph."""

    TRANSCRIPT = "transcript"
    USAGE = "usage"
    DELEGATION = "delegation"
    ERROR = "error"
    CLOSE = "close"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class LiveDelegation:
    delegation_id: str
    target: Literal["client", "responses"]
    offset_ms: int


@dataclass(frozen=True, slots=True)
class LiveSessionError:
    code: str
    message: str
    event_id: str
    client_event_id: str | None = None


@dataclass(frozen=True, slots=True)
class LiveSessionUpdate:
    """Typed output of one graph transition."""

    node: LiveSessionNode
    event_type: str
    transcript: TranscriptFragment | None = None
    usage: Usage | None = None
    delegation: LiveDelegation | None = None
    error: LiveSessionError | None = None
    close_reason: str | None = None


@dataclass(slots=True)
class LiveSessionState:
    transcripts: list[TranscriptFragment] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    delegations: list[LiveDelegation] = field(default_factory=list)
    errors: list[LiveSessionError] = field(default_factory=list)
    closed: bool = False
    close_reason: str | None = None


class LiveSessionGraph:
    """Route normalized Live events through deterministic processing nodes."""

    _TRANSCRIPT_SPEAKERS: dict[str, Literal["user", "assistant"]] = {
        "session.input_transcript.delta": "user",
        "session.output_transcript.delta": "assistant",
    }

    def __init__(self, state: LiveSessionState | None = None) -> None:
        self.state = state or LiveSessionState()
        self._transcript_chars = sum(len(fragment.text) for fragment in self.state.transcripts)

    def route(self, event: Mapping[str, Any]) -> LiveSessionUpdate:
        """Apply one server event and return the node output for the worker."""

        event_type = event.get("type")
        if not isinstance(event_type, str):
            return self._ignored("")
        if self.state.closed:
            return self._ignored(event_type)
        event_count = len(self.state.transcripts) + len(self.state.delegations)
        event_count += len(self.state.errors)
        if event_count >= 10000:
            raise InvalidLiveEvent("Live session event limit reached")

        speaker = self._TRANSCRIPT_SPEAKERS.get(event_type)
        if speaker is not None:
            return self._route_transcript(event, event_type, speaker)
        if event_type == "session.usage.updated":
            return self._route_usage(event, final=False)
        if event_type == "session.delegation.created":
            return self._route_delegation(event)
        if event_type == "error":
            return self._route_error(event)
        if event_type == "session.closed":
            return self._route_close(event)
        return self._ignored(event_type)

    def _route_transcript(
        self,
        event: Mapping[str, Any],
        event_type: str,
        speaker: Literal["user", "assistant"],
    ) -> LiveSessionUpdate:
        fragment = TranscriptFragment(
            speaker=speaker,
            text=self._require_str(event, "delta", event_type),
            start_ms=self._require_int(event, "start_ms", event_type),
            end_ms=self._require_int(event, "end_ms", event_type),
        )
        if self._transcript_chars + len(fragment.text) > 64000:
            raise InvalidLiveEvent("Live transcript size limit reached")
        if fragment.end_ms < fragment.start_ms:
            raise InvalidLiveEvent(f"{event_type}.end_ms must be >= start_ms")
        self.state.transcripts.append(fragment)
        self._transcript_chars += len(fragment.text)
        return LiveSessionUpdate(
            node=LiveSessionNode.TRANSCRIPT,
            event_type=event_type,
            transcript=fragment,
        )

    def _route_usage(
        self, event: Mapping[str, Any], *, final: bool
    ) -> LiveSessionUpdate:
        event_type = str(event["type"])
        usage_payload = self._require_mapping(event, "usage", event_type)
        seconds = self._require_number(usage_payload, "seconds", f"{event_type}.usage")
        if seconds < 0:
            raise InvalidLiveEvent(f"{event_type}.usage.seconds must be non-negative")

        # Live usage events are cumulative snapshots, not increments.
        usage = Usage(
            voice_seconds=seconds,
            input_tokens=self.state.usage.input_tokens,
            output_tokens=self.state.usage.output_tokens,
            final=final,
        )
        self.state.usage = usage
        return LiveSessionUpdate(
            node=LiveSessionNode.CLOSE if final else LiveSessionNode.USAGE,
            event_type=event_type,
            usage=usage,
        )

    def _route_delegation(self, event: Mapping[str, Any]) -> LiveSessionUpdate:
        event_type = "session.delegation.created"
        payload = self._require_mapping(event, "delegation", event_type)
        target = self._require_str(payload, "target", f"{event_type}.delegation")
        if target not in {"client", "responses"}:
            raise InvalidLiveEvent(f"{event_type}.delegation.target is invalid")
        resolved_target: Literal["client", "responses"] = (
            "client" if target == "client" else "responses"
        )
        delegation = LiveDelegation(
            delegation_id=self._require_str(payload, "id", f"{event_type}.delegation"),
            target=resolved_target,
            offset_ms=self._require_int(event, "offset_ms", event_type),
        )
        if delegation.offset_ms < 0:
            raise InvalidLiveEvent(f"{event_type}.offset_ms must be non-negative")
        self.state.delegations.append(delegation)
        return LiveSessionUpdate(
            node=LiveSessionNode.DELEGATION,
            event_type=event_type,
            delegation=delegation,
        )

    def _route_error(self, event: Mapping[str, Any]) -> LiveSessionUpdate:
        event_type = "error"
        payload = self._require_mapping(event, "error", event_type)
        client_event_id = payload.get("client_event_id")
        if client_event_id is not None and not isinstance(client_event_id, str):
            raise InvalidLiveEvent("error.error.client_event_id must be a string")
        error = LiveSessionError(
            code=self._require_str(payload, "code", "error.error"),
            message=self._require_str(payload, "message", "error.error"),
            event_id=self._require_str(event, "event_id", event_type),
            client_event_id=client_event_id,
        )
        self.state.errors.append(error)
        return LiveSessionUpdate(
            node=LiveSessionNode.ERROR,
            event_type=event_type,
            error=error,
        )

    def _route_close(self, event: Mapping[str, Any]) -> LiveSessionUpdate:
        event_type = "session.closed"
        reason = self._require_str(event, "reason", event_type)
        update = self._route_usage(event, final=True)
        self.state.closed = True
        self.state.close_reason = reason
        return LiveSessionUpdate(
            node=LiveSessionNode.CLOSE,
            event_type=event_type,
            usage=update.usage,
            close_reason=reason,
        )

    @staticmethod
    def _ignored(event_type: str) -> LiveSessionUpdate:
        return LiveSessionUpdate(node=LiveSessionNode.IGNORED, event_type=event_type)

    @staticmethod
    def _require_mapping(
        payload: Mapping[str, Any], key: str, event_type: str
    ) -> Mapping[str, Any]:
        value = payload.get(key)
        if not isinstance(value, Mapping):
            raise InvalidLiveEvent(f"{event_type}.{key} must be an object")
        return value

    @staticmethod
    def _require_str(payload: Mapping[str, Any], key: str, event_type: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str):
            raise InvalidLiveEvent(f"{event_type}.{key} must be a string")
        return value

    @staticmethod
    def _require_int(payload: Mapping[str, Any], key: str, event_type: str) -> int:
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvalidLiveEvent(f"{event_type}.{key} must be an integer")
        return value

    @staticmethod
    def _require_number(payload: Mapping[str, Any], key: str, event_type: str) -> float:
        value = payload.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise InvalidLiveEvent(f"{event_type}.{key} must be a number")
        return float(value)
