"""Catalog-driven slot collection and confirmation policy."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from .contracts import ActionTrace, Catalog, ExtractedParameter, Scenario
from .simulation import SimulationEngine


WorkflowStatus = Literal[
    "idle", "collecting", "awaiting_confirmation", "completed", "cancelled", "handoff"
]


@dataclass
class ScenarioProgress:
    slots: dict[str, Any] = field(default_factory=dict)
    status: WorkflowStatus = "idle"
    awaiting_confirmation: bool = False
    traces: list[ActionTrace] = field(default_factory=list)
    results: dict[str, Any] = field(default_factory=dict)

    def copy(self) -> "ScenarioProgress":
        return ScenarioProgress(
            slots=json.loads(json.dumps(self.slots, ensure_ascii=False, default=str)),
            status=self.status,
            awaiting_confirmation=self.awaiting_confirmation,
            traces=[item.model_copy(deep=True) for item in self.traces],
            results=json.loads(
                json.dumps(self.results, ensure_ascii=False, default=str)
            ),
        )


@dataclass(frozen=True)
class WorkflowOutcome:
    status: WorkflowStatus
    slots: dict[str, Any]
    missing: list[str]
    confirmation_required: bool
    traces: list[ActionTrace]
    deterministic_reply: str | None = None
    should_resolve: bool = False

    @property
    def completed(self) -> bool:
        return self.status in {"completed", "cancelled", "handoff"}


def confirmation_intent(text: str) -> Literal["approve", "reject", "unknown"]:
    normalized = re.sub(r"[^\wәіңғүұқөһ]+", " ", text.casefold()).strip()
    words = normalized.split()
    if not words or len(words) > 6:
        return "unknown"
    approve = {
        "да",
        "верно",
        "подтверждаю",
        "согласен",
        "согласна",
        "оформляйте",
        "иә",
        "иа",
        "дұрыс",
        "растаймын",
        "келісемін",
        "жасаңыз",
        "орындаңыз",
    }
    reject = {
        "нет",
        "неверно",
        "отмена",
        "отменить",
        "не надо",
        "жоқ",
        "болдырмау",
        "керек емес",
    }
    if normalized in reject or any(phrase in normalized for phrase in reject):
        return "reject"
    if normalized in approve or any(word in approve for word in words):
        return "approve"
    return "unknown"


def is_executable_scenario(catalog: Catalog, scenario: Scenario) -> bool:
    slots = scenario.details.get("slots")
    actions = scenario.details.get("actions")
    return bool(catalog.slots) and isinstance(slots, dict) and isinstance(actions, list)


def required_slots(scenario: Scenario) -> list[str]:
    slots = scenario.details.get("slots", {})
    values = slots.get("required", []) if isinstance(slots, dict) else []
    return [value for value in values if isinstance(value, str)]


def slot_prompt(catalog: Catalog, name: str, language: str) -> str:
    definition = catalog.slots.get(name, {})
    prompts = definition.get("prompt", {})
    if isinstance(prompts, dict):
        prompt = prompts.get("kk" if language == "kk" else "ru")
        if isinstance(prompt, str) and prompt.strip():
            return prompt.strip()
    return (
        f"{name} мәнін нақтылап жіберіңізші."
        if language == "kk"
        else f"Уточните, пожалуйста, значение: {name}."
    )


def normalize_slot(value: Any, definition: dict[str, Any]) -> Any:
    kind = definition.get("type", "string")
    if kind == "integer":
        digits = re.sub(r"[^0-9-]", "", str(value))
        if not digits or digits == "-":
            raise ValueError("integer required")
        normalized: Any = int(digits)
    elif kind == "boolean":
        lowered = str(value).casefold().strip()
        if lowered in {"true", "1", "yes", "да", "иә", "иа", "бар"}:
            normalized = True
        elif lowered in {"false", "0", "no", "нет", "жоқ"}:
            normalized = False
        else:
            raise ValueError("boolean required")
    elif kind == "list":
        if isinstance(value, list):
            normalized = value
        else:
            raw = str(value).strip()
            try:
                parsed = json.loads(raw)
                normalized = parsed if isinstance(parsed, list) else [raw]
            except json.JSONDecodeError:
                normalized = [
                    part.strip() for part in re.split(r"[,;]", raw) if part.strip()
                ]
    elif kind == "date":
        normalized = str(value).strip()
        date.fromisoformat(normalized)
    else:
        normalized = str(value).strip()

    values = definition.get("values")
    if isinstance(values, list):
        match = next(
            (
                item
                for item in values
                if str(item).casefold() == str(normalized).casefold()
            ),
            None,
        )
        if match is None:
            raise ValueError("value outside enum")
        normalized = match
    pattern = definition.get("pattern")
    if isinstance(pattern, str):
        candidates = normalized if isinstance(normalized, list) else [normalized]
        if any(re.fullmatch(pattern, str(item)) is None for item in candidates):
            raise ValueError("value does not match pattern")
    return normalized


def merge_parameters(
    progress: ScenarioProgress,
    catalog: Catalog,
    scenario_id: str,
    parameters: list[ExtractedParameter],
) -> None:
    for parameter in parameters:
        if parameter.scenario_id != scenario_id or parameter.name not in catalog.slots:
            continue
        try:
            progress.slots[parameter.name] = normalize_slot(
                parameter.value, catalog.slots[parameter.name]
            )
        except (TypeError, ValueError):
            progress.slots.pop(parameter.name, None)


def advance_workflow(
    *,
    catalog: Catalog,
    scenario: Scenario,
    progress: ScenarioProgress,
    parameters: list[ExtractedParameter],
    text: str,
    language: str,
    session_id: str,
) -> WorkflowOutcome:
    merge_parameters(progress, catalog, scenario.id, parameters)
    missing = [name for name in required_slots(scenario) if name not in progress.slots]
    if missing:
        progress.status = "collecting"
        progress.awaiting_confirmation = False
        return WorkflowOutcome(
            status=progress.status,
            slots=dict(progress.slots),
            missing=missing,
            confirmation_required=False,
            traces=list(progress.traces),
            deterministic_reply=slot_prompt(catalog, missing[0], language),
        )

    engine = SimulationEngine(catalog, scenario, session_id)
    if progress.awaiting_confirmation:
        intent = confirmation_intent(text)
        if intent == "unknown":
            reply = (
                "Әрекетті растайсыз ба?"
                if language == "kk"
                else "Подтвердите, пожалуйста: выполнить эту демо-операцию?"
            )
            return WorkflowOutcome(
                status="awaiting_confirmation",
                slots=dict(progress.slots),
                missing=[],
                confirmation_required=True,
                traces=list(progress.traces),
                deterministic_reply=reply,
            )
        if intent == "reject":
            progress.status = "cancelled"
            progress.awaiting_confirmation = False
            reply = (
                "Демо-операция отменена."
                if language != "kk"
                else "Демо-операциядан бас тартылды."
            )
            return WorkflowOutcome(
                status=progress.status,
                slots=dict(progress.slots),
                missing=[],
                confirmation_required=False,
                traces=list(progress.traces),
                deterministic_reply=reply,
            )
        traces, results = engine.run(progress.slots, mode="simulate", utterance=text)
        progress.traces = traces
        progress.results = results
        progress.status = (
            "handoff"
            if any(t.mode == "handoff" and t.status == "success" for t in traces)
            else "completed"
        )
        progress.awaiting_confirmation = False
        return WorkflowOutcome(
            status=progress.status,
            slots=dict(progress.slots),
            missing=[],
            confirmation_required=False,
            traces=list(traces),
            should_resolve=True,
        )

    requires_confirmation = bool(scenario.details.get("requires_confirmation"))
    traces, results = engine.run(
        progress.slots,
        mode="preview" if requires_confirmation else "simulate",
        utterance=text,
    )
    progress.traces = traces
    progress.results = results
    if requires_confirmation:
        progress.status = "awaiting_confirmation"
        progress.awaiting_confirmation = True
    else:
        progress.status = (
            "handoff"
            if any(t.mode == "handoff" and t.status == "success" for t in traces)
            else "completed"
        )
    return WorkflowOutcome(
        status=progress.status,
        slots=dict(progress.slots),
        missing=[],
        confirmation_required=requires_confirmation,
        traces=list(traces),
        should_resolve=True,
    )
