"""Shared, transport-independent routing transaction for one conversation turn."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import perf_counter
from uuid import uuid4

from .contracts import (
    AgentFailure,
    AgentGateway,
    Catalog,
    ExtractedParameter,
    InvalidDecision,
    ResolutionContext,
    RoutingContext,
    RoutingDecision,
    RuntimeConfig,
    Timings,
    TurnResult,
    TurnTimeout,
)
from .workflow import (
    ScenarioProgress,
    WorkflowOutcome,
    advance_workflow,
    confirmation_intent,
    is_executable_scenario,
)


@dataclass(frozen=True)
class PendingDelivery:
    turn_id: str
    reply: str


@dataclass
class Conversation:
    history: list[dict[str, str]] = field(default_factory=list)
    active_scenario: str | None = None
    pending_scenarios: list[str] = field(default_factory=list)
    uncertain_turns: int = 0
    parameters: list[ExtractedParameter] = field(default_factory=list)
    workflows: dict[str, ScenarioProgress] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    pending_delivery: PendingDelivery | None = None

    def snapshot(self, catalog: Catalog) -> Conversation:
        """Prune removed scenarios in a copy so a failed turn changes no state."""
        valid = catalog.scenarios
        return Conversation(
            history=[entry.copy() for entry in self.history],
            active_scenario=self.active_scenario
            if self.active_scenario in valid
            else None,
            pending_scenarios=[sid for sid in self.pending_scenarios if sid in valid],
            uncertain_turns=self.uncertain_turns,
            parameters=[
                item.model_copy(deep=True)
                for item in self.parameters
                if item.scenario_id in valid
            ],
            workflows={
                sid: progress.copy()
                for sid, progress in self.workflows.items()
                if sid in valid
            },
        )

    def commit(self, updated: Conversation) -> None:
        self.history = updated.history
        self.active_scenario = updated.active_scenario
        self.pending_scenarios = updated.pending_scenarios
        self.uncertain_turns = updated.uncertain_turns
        self.parameters = updated.parameters
        self.workflows = updated.workflows
        self.pending_delivery = updated.pending_delivery


def _validate(decision: RoutingDecision, context: RoutingContext) -> None:
    catalog = context.catalog
    references = (
        ([decision.scenario_id] if decision.scenario_id is not None else [])
        + [item.scenario_id for item in decision.alternatives]
        + decision.pending_scenario_ids
        + decision.secondary_intents
        + [item.scenario_id for item in decision.extracted_parameters]
    )
    if any(sid not in catalog.scenarios for sid in references):
        raise InvalidDecision("Router referenced a scenario outside the catalog")
    if (decision.action == "route") != (decision.scenario_id is not None):
        raise InvalidDecision("Only a route decision must specify a primary scenario")
    for item in decision.extracted_parameters:
        details = catalog.scenarios[item.scenario_id].details
        declared = details.get("parameters")
        if declared is None and isinstance(details.get("slots"), dict):
            declared = list(details["slots"].get("required", [])) + list(
                details["slots"].get("optional", [])
            )
        if declared is not None:
            if isinstance(declared, dict):
                entries = list(declared)
            elif isinstance(declared, list):
                entries = declared
            else:
                raise InvalidDecision(
                    "Scenario parameter declarations must be a list or mapping"
                )
            names = set()
            for entry in entries:
                name = entry.get("name") if isinstance(entry, dict) else entry
                if not isinstance(name, str) or not name.strip():
                    raise InvalidDecision(
                        "Scenario parameters must declare non-empty string names"
                    )
                names.add(name)
            if item.name not in names:
                raise InvalidDecision(
                    "Router extracted an undeclared scenario parameter"
                )


def _normalize_transition(
    decision: RoutingDecision, context: RoutingContext
) -> RoutingDecision:
    """Derive the transition from a validated scenario choice and prior state."""
    transition = "continue"
    if decision.action == "route" and decision.scenario_id != context.active_scenario:
        if decision.scenario_id in context.pending_scenarios:
            transition = "resume"
        elif context.active_scenario is not None:
            transition = "switch"
    return decision.model_copy(update={"topic_transition": transition})


def _operator_message(language: str) -> str:
    if language == "kk":
        return "Операторға арналған контекст дайындалды; демо-нұсқада нақты қосылу орындалмайды."
    return (
        "Контекст для оператора подготовлен; фактического соединения в демо-версии нет."
    )


def _clarification_message(language: str) -> str:
    if language == "kk":
        return "Қай мәселе бойынша көмек қажет екенін нақтылай аласыз ба?"
    return "Уточните, пожалуйста, по какому вопросу вам нужна помощь?"


class VoiceRouterOrchestrator:
    """Run Router once and Resolution only for an accepted route.

    Conversation locks serialize turns from the same caller. Model failure,
    cancellation and timeout leave the previous conversation intact.
    """

    def __init__(self, gateway: AgentGateway):
        self.gateway = gateway
        self.sessions: dict[str, Conversation] = {}

    async def turn(
        self,
        *,
        session_id: str,
        text: str,
        catalog: Catalog,
        config: RuntimeConfig,
        on_route: Callable[[RoutingDecision], Awaitable[None]] | None = None,
        delivery_id: str | None = None,
        voice_context: list[dict[str, str]] | None = None,
    ) -> TurnResult:
        text = text.strip()
        if not text:
            raise ValueError("A turn requires a non-empty utterance")
        started = perf_counter()
        state = self.sessions.setdefault(session_id, Conversation())
        async with state.lock:
            candidate = state.snapshot(catalog)
            try:
                result = await asyncio.wait_for(
                    self._execute(
                        session_id,
                        text,
                        catalog,
                        config,
                        candidate,
                        on_route,
                        voice_context=voice_context,
                    ),
                    timeout=config.timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                raise TurnTimeout("Agent turn exceeded its time budget") from exc
            if delivery_id is not None:
                # The browser confirms delivery only after the final sample plays.
                # An interrupted answer must never become assumed conversation context.
                candidate.history.pop()
                candidate.pending_delivery = PendingDelivery(delivery_id, result.reply)
            state.commit(candidate)
        result.timings.total_ms = (perf_counter() - started) * 1000
        return result

    async def confirm_delivery(self, session_id: str, turn_id: str) -> None:
        state = self.sessions.get(session_id)
        if state is None:
            return
        async with state.lock:
            pending = state.pending_delivery
            if pending is not None and pending.turn_id == turn_id:
                state.history = (
                    state.history + [{"role": "assistant", "content": pending.reply}]
                )[-20:]
                state.pending_delivery = None

    async def interrupt_delivery(self, session_id: str, turn_id: str) -> None:
        state = self.sessions.get(session_id)
        if state is None:
            return
        async with state.lock:
            if (
                state.pending_delivery is not None
                and state.pending_delivery.turn_id == turn_id
            ):
                state.pending_delivery = None

    async def _execute(
        self,
        session_id: str,
        text: str,
        catalog: Catalog,
        config: RuntimeConfig,
        state: Conversation,
        on_route: Callable[[RoutingDecision], Awaitable[None]] | None = None,
        *,
        voice_context: list[dict[str, str]] | None = None,
    ) -> TurnResult:
        context = RoutingContext(
            catalog=catalog,
            text=text,
            history=state.history,
            active_scenario=state.active_scenario,
            pending_scenarios=state.pending_scenarios,
            parameters=state.parameters,
            trace_id="trace_" + uuid4().hex,
            session_id=session_id,
            voice_context=voice_context or [],
        )
        timings = Timings()
        started = perf_counter()
        active_progress = state.workflows.get(state.active_scenario or "")
        confirmation = confirmation_intent(text)
        if (
            state.active_scenario in catalog.scenarios
            and active_progress is not None
            and active_progress.awaiting_confirmation
            and confirmation != "unknown"
        ):
            language = (
                "kk"
                if confirmation_intent(text) != "unknown"
                and any(char in text.casefold() for char in "әіңғүұқөһ")
                else "ru"
            )
            decision = RoutingDecision(
                action="route",
                scenario_id=state.active_scenario,
                confidence=1,
                reason="Explicit response to the pending confirmation",
                customer_message="Continue the active scenario",
                language=language,
            )
        else:
            decision = await self.gateway.route(context, config)
        timings.routing_ms = (perf_counter() - started) * 1000
        _validate(decision, context)
        if (
            decision.action == "route"
            and decision.confidence < config.confidence_threshold
        ):
            decision = decision.model_copy(
                update={
                    "action": "clarify",
                    "scenario_id": None,
                    "extracted_parameters": [],
                    "clarification_question": (
                        decision.clarification_question
                        or _clarification_message(decision.language)
                    ),
                    "reason": decision.reason + "; confidence below routing threshold",
                }
            )
        state.uncertain_turns = (
            state.uncertain_turns + 1 if decision.action == "clarify" else 0
        )
        if (
            decision.action == "clarify"
            and state.uncertain_turns >= config.max_uncertain_turns
        ):
            decision = decision.model_copy(
                update={
                    "action": "handoff",
                    "clarification_question": None,
                    "reason": decision.reason
                    + "; repeated uncertainty requires operator assistance",
                }
            )
        decision = _normalize_transition(decision, context)
        if on_route is not None:
            await on_route(decision)

        parameters = {(item.scenario_id, item.name): item for item in state.parameters}
        parameters.update(
            {
                (item.scenario_id, item.name): item
                for item in decision.extracted_parameters
            }
        )
        state.parameters = list(parameters.values())
        workflow = WorkflowOutcome(
            status="idle",
            slots={},
            missing=[],
            confirmation_required=False,
            traces=[],
        )
        if decision.action == "route":
            pending = (
                state.pending_scenarios
                + decision.pending_scenario_ids
                + decision.secondary_intents
            )
            if state.active_scenario and state.active_scenario != decision.scenario_id:
                pending.append(state.active_scenario)
            state.pending_scenarios = list(
                dict.fromkeys(sid for sid in pending if sid != decision.scenario_id)
            )[:5]
            scenario = catalog.scenarios[decision.scenario_id]
            if config.workflow_enabled and is_executable_scenario(catalog, scenario):
                progress = state.workflows.setdefault(
                    decision.scenario_id, ScenarioProgress()
                )
                workflow = advance_workflow(
                    catalog=catalog,
                    scenario=scenario,
                    progress=progress,
                    parameters=decision.extracted_parameters,
                    text=text,
                    language=decision.language,
                    session_id=session_id,
                )
            if workflow.deterministic_reply is not None:
                reply = workflow.deterministic_reply
            else:
                resolution = ResolutionContext(
                    catalog=catalog,
                    text=text,
                    history=state.history,
                    active_scenario=state.active_scenario,
                    pending_scenarios=state.pending_scenarios,
                    parameters=[
                        item
                        for item in state.parameters
                        if item.scenario_id == decision.scenario_id
                    ],
                    trace_id=context.trace_id,
                    session_id=session_id,
                    decision=decision,
                    workflow_status=workflow.status,
                    collected_slots=workflow.slots,
                    missing_slots=workflow.missing,
                    confirmation_required=workflow.confirmation_required,
                    action_trace=workflow.traces,
                    voice_context=context.voice_context,
                )
                started = perf_counter()
                reply = await self.gateway.resolve(resolution, config)
                timings.response_ms = (perf_counter() - started) * 1000
            state.active_scenario = decision.scenario_id
        elif decision.action == "handoff":
            reply = _operator_message(decision.language)
        else:
            reply = decision.clarification_question or decision.customer_message
        if not isinstance(reply, str) or not reply.strip():
            raise AgentFailure("Agent returned an empty reply")
        reply = reply.strip()
        state.history = (
            state.history
            + [
                {"role": "user", "content": text},
                {"role": "assistant", "content": reply},
            ]
        )[-20:]
        return TurnResult(
            session_id=session_id,
            transcript=text,
            reply=reply,
            action=decision.action,
            scenario_id=decision.scenario_id,
            scenario_title=(
                catalog.scenarios[decision.scenario_id].title
                if decision.scenario_id
                else None
            ),
            confidence=decision.confidence,
            reason=decision.reason,
            alternatives=decision.alternatives[:3],
            pending_scenario_ids=state.pending_scenarios,
            timings=timings,
            trace_id=context.trace_id,
            language=decision.language,
            topic_transition=decision.topic_transition,
            extracted_parameters=decision.extracted_parameters,
            workflow_status=workflow.status,
            collected_slots=workflow.slots,
            missing_slots=workflow.missing,
            confirmation_required=workflow.confirmation_required,
            action_trace=workflow.traces,
            completed=workflow.completed,
        )
