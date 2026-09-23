"""Catalog, decision and trace contracts shared by API, database and service."""

from __future__ import annotations

from typing import Any, Literal

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
