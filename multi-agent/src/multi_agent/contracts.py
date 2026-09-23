"""Catalog, decision and trace contracts shared by API, database and service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class Scenario(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    details: dict[str, Any]


class Catalog:
    def __init__(
        self, scenarios: list[Scenario], *, knowledge: Any = None, backend: Any = None
    ):
        if not scenarios or len({s.id for s in scenarios}) != len(scenarios):
            raise ValueError("Scenario catalog must have unique, non-empty IDs")
        self.scenarios = {s.id: s for s in scenarios}
        self.knowledge = knowledge
        self.backend = backend

    @classmethod
    def from_payload(
        cls, raw: Any, *, knowledge: Any = None, backend: Any = None
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
        return cls(scenarios, knowledge=knowledge, backend=backend)

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


@dataclass(frozen=True)
class ResolutionContext(RoutingContext):
    decision: RoutingDecision


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
