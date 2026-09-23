"""Catalog, decision and trace contracts shared by API, database and service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class Scenario(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    details: dict[str, Any]


class Catalog:
    def __init__(
        self,
        scenarios: list[Scenario],
        *,
        knowledge: Any = None,
        backend: Any = None,
        slots: Any = None,
        actions: Any = None,
        dev_utterances: Any = None,
        dialogs_sample: Any = None,
    ):
        if not scenarios or len({s.id for s in scenarios}) != len(scenarios):
            raise ValueError("Scenario catalog must have unique, non-empty IDs")
        self.scenarios = {s.id: s for s in scenarios}
        self.knowledge = knowledge
        self.backend = backend
        self.slots = self._definitions(slots, "slots", "name")
        self.actions = self._definitions(actions, "actions", "name")
        self.dev_utterances = dev_utterances
        self.dialogs_sample = dialogs_sample
        self._validate_references()

    @staticmethod
    def _definitions(raw: Any, container: str, key: str) -> dict[str, dict[str, Any]]:
        if raw is None:
            return {}
        entries = raw.get(container, raw) if isinstance(raw, dict) else raw
        if isinstance(entries, dict):
            entries = [dict(value, **{key: name}) for name, value in entries.items()]
        if not isinstance(entries, list) or any(
            not isinstance(item, dict) for item in entries
        ):
            raise TypeError(
                f"{container}.json must contain a list or a '{container}' list"
            )
        definitions: dict[str, dict[str, Any]] = {}
        for item in entries:
            name = item.get(key)
            if not isinstance(name, str) or not name.strip() or name in definitions:
                raise ValueError(
                    f"Every {container} entry must have a unique non-empty {key}"
                )
            definitions[name] = item
        return definitions

    def _validate_references(self) -> None:
        for scenario in self.scenarios.values():
            details = scenario.details
            declared_slots = details.get("slots")
            slot_names: list[str] = []
            if isinstance(declared_slots, dict):
                for group in ("required", "optional"):
                    values = declared_slots.get(group, [])
                    if not isinstance(values, list) or any(
                        not isinstance(v, str) for v in values
                    ):
                        raise ValueError(
                            f"Scenario {scenario.id} has invalid {group} slots"
                        )
                    slot_names.extend(values)
            if self.slots and set(slot_names) - self.slots.keys():
                missing = sorted(set(slot_names) - self.slots.keys())
                raise ValueError(
                    f"Scenario {scenario.id} references unknown slots: {', '.join(missing)}"
                )

            declared_actions = details.get("actions")
            if isinstance(declared_actions, list):
                if any(not isinstance(value, str) for value in declared_actions):
                    raise ValueError(f"Scenario {scenario.id} has invalid actions")
                if self.actions and set(declared_actions) - self.actions.keys():
                    missing = sorted(set(declared_actions) - self.actions.keys())
                    raise ValueError(
                        f"Scenario {scenario.id} references unknown actions: {', '.join(missing)}"
                    )

    @classmethod
    def from_payload(
        cls,
        raw: Any,
        *,
        knowledge: Any = None,
        backend: Any = None,
        slots: Any = None,
        actions: Any = None,
        dev_utterances: Any = None,
        dialogs_sample: Any = None,
    ) -> Catalog:
        entries = raw.get("scenarios", raw) if isinstance(raw, dict) else raw
        if isinstance(entries, dict):
            if any(not isinstance(value, dict) for value in entries.values()):
                raise TypeError("Every scenario must be an object")
            entries = [dict(value, id=key) for key, value in entries.items()]
        if not isinstance(entries, list):
            raise TypeError("scenarios.json must contain a list or a 'scenarios' list")
        scenarios = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise TypeError("Every scenario must be an object")
            identifier = (
                entry.get("id") or entry.get("scenario_id") or entry.get("code")
            )
            if identifier is None:
                raise ValueError("Every scenario must contain id, scenario_id or code")
            title = (
                entry.get("title")
                or entry.get("name")
                or entry.get("purpose")
                or str(identifier)
            )
            scenarios.append(
                Scenario(id=str(identifier), title=str(title), details=entry)
            )
        return cls(
            scenarios,
            knowledge=knowledge,
            backend=backend,
            slots=slots,
            actions=actions,
            dev_utterances=dev_utterances,
            dialogs_sample=dialogs_sample,
        )

    def prompt_data(self) -> list[dict[str, Any]]:
        return [
            {"id": s.id, "title": s.title, "details": s.details}
            for s in self.scenarios.values()
        ]


class Alternative(BaseModel):
    scenario_id: str
    reason: str


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["route", "clarify", "handoff"]
    scenario_id: str | None = None
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)
    alternatives: list[Alternative] = Field(default_factory=list)
    pending_scenario_ids: list[str] = Field(default_factory=list)
    customer_message: str = Field(min_length=1)


class ExtractedParameter(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    scenario_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    value: str = Field(min_length=1)


class RoutingDecision(Decision):
    """Typed Router output; Python policy validates catalog references and transitions."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    language: Literal["ru", "kk", "mixed", "unknown"] = "unknown"
    topic_transition: Literal["continue", "switch", "resume"] = "continue"
    secondary_intents: list[str] = Field(default_factory=list)
    extracted_parameters: list[ExtractedParameter] = Field(default_factory=list)
    clarification_question: str | None = Field(default=None, min_length=1)


class Timings(BaseModel):
    stt_ms: float = 0
    routing_ms: float = 0
    response_ms: float = 0
    tts_ms: float = 0
    total_ms: float = 0


class ActionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    mode: Literal["read", "preview", "simulate", "handoff", "skipped"]
    status: Literal["success", "error", "skipped"]
    result: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None


class TurnResult(BaseModel):
    session_id: str
    transcript: str
    reply: str
    action: Literal["route", "clarify", "handoff"]
    scenario_id: str | None
    scenario_title: str | None
    confidence: float
    reason: str
    alternatives: list[Alternative]
    pending_scenario_ids: list[str]
    timings: Timings
    audio_base64: str | None = None
    audio_content_type: str | None = None

    trace_id: str = ""
    language: Literal["ru", "kk", "mixed", "unknown"] = "unknown"
    topic_transition: Literal["continue", "switch", "resume"] = "continue"
    extracted_parameters: list[ExtractedParameter] = Field(default_factory=list)
    workflow_status: Literal[
        "idle",
        "collecting",
        "awaiting_confirmation",
        "completed",
        "cancelled",
        "handoff",
    ] = "idle"
    collected_slots: dict[str, Any] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    confirmation_required: bool = False
    action_trace: list[ActionTrace] = Field(default_factory=list)
    completed: bool = False


DEFAULT_MODEL = "gpt-5.6-terra"


class RuntimeConfig(BaseModel):
    """Per-turn snapshot of live application settings."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    model: str = Field(min_length=1)
    routing_prompt: str
    answer_prompt: str
    confidence_threshold: float = Field(default=0.65, ge=0, le=1)
    timeout_seconds: float = Field(default=20, gt=0)
    resolution_max_turns: int = Field(default=3, ge=1, le=8)
    tracing_enabled: bool = False
    workflow_enabled: bool = True
    max_uncertain_turns: int = Field(default=2, ge=1, le=10)
    simulation_mode: Literal["simulate"] = "simulate"


@dataclass(frozen=True)
class RoutingContext:
    catalog: Catalog
    text: str
    history: list[dict[str, str]]
    active_scenario: str | None
    pending_scenarios: list[str]
    parameters: list[ExtractedParameter]
    trace_id: str
    session_id: str
    voice_context: list[dict[str, str]] = field(default_factory=list, kw_only=True)

    def __post_init__(self) -> None:
        # Provider captions help interpret short follow-ups, but are not delivered
        # history or record authorization. Copy and bound this transient snapshot.
        remaining = 8000
        bounded: list[dict[str, str]] = []
        for entry in reversed(self.voice_context[-20:]):
            if entry.get("role") not in {"user", "assistant"}:
                continue
            content = entry.get("content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            content = content[-min(2000, remaining) :]
            bounded.append({"role": entry["role"], "content": content})
            remaining -= len(content)
            if remaining <= 0:
                break
        object.__setattr__(self, "voice_context", list(reversed(bounded)))


@dataclass(frozen=True)
class ResolutionContext(RoutingContext):
    decision: RoutingDecision
    workflow_status: str = "idle"
    collected_slots: dict[str, Any] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    confirmation_required: bool = False
    action_trace: list[ActionTrace] = field(default_factory=list)


class AgentGateway(Protocol):
    async def route(
        self, context: RoutingContext, config: RuntimeConfig
    ) -> RoutingDecision:
        """Run the mandatory Router and return structured output."""
        ...

    async def resolve(self, context: ResolutionContext, config: RuntimeConfig) -> str:
        """Resolve a validated scenario using only allowed facts/tools."""
        ...


class InvalidDecision(ValueError):
    """The model returned a decision that application policy cannot accept."""


class AgentFailure(RuntimeError):
    """The upstream agent could not produce a usable answer."""


class TurnTimeout(TimeoutError):
    """The turn exceeded its configured time budget."""
