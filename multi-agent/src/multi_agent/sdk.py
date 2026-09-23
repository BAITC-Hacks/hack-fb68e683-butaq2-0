"""OpenAI Agents SDK implementation of the two-agent runtime port."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from agents import (
    Agent,
    ModelSettings,
    OpenAIResponsesModel,
    RunConfig,
    Runner,
    function_tool,
)
from agents.exceptions import AgentsException
from openai import AsyncOpenAI, OpenAIError
from openai.types.shared import Reasoning
from pydantic import ValidationError

from .context import routing_catalog, scenario_definition
from .contracts import (
    AgentFailure,
    InvalidDecision,
    ResolutionContext,
    RoutingContext,
    RoutingDecision,
    RuntimeConfig,
    TurnTimeout,
)
from .identifiers import demo_record_resource, explicit_demo_ids, parse_demo_ids
from .prompts import RESOLUTION_INVARIANTS, ROUTER_INVARIANTS
from .tools import DemoRecordLookup, scenario_knowledge


class SdkAgentGateway:
    """Create per-turn agents from a live settings snapshot and bounded tools.

    The orchestrator, rather than an LLM handoff, owns the Router → Resolution
    transition. Runner remains injectable for inexpensive deterministic tests.
    """

    def __init__(
        self, client: AsyncOpenAI | Callable[[], AsyncOpenAI], *, runner: Any = Runner
    ):
        self._client = client
        self._runner = runner

    async def route(
        self, context: RoutingContext, config: RuntimeConfig
    ) -> RoutingDecision:
        agent = Agent(
            name="Router",
            instructions=f"{config.routing_prompt}\n\n{ROUTER_INVARIANTS}",
            model=self._model(config),
            model_settings=self._model_settings(config),
            output_type=RoutingDecision,
        )
        payload = {
            "catalog": routing_catalog(context.catalog),
            "confidence_threshold": config.confidence_threshold,
            **self._conversation(context),
        }
        output = await self._run(agent, payload, context, config, max_turns=1)
        try:
            return RoutingDecision.model_validate(output)
        except ValidationError as exc:
            raise InvalidDecision(
                "Router returned an invalid structured decision"
            ) from exc

    async def resolve(self, context: ResolutionContext, config: RuntimeConfig) -> str:
        decision = context.decision
        if (
            decision.action != "route"
            or decision.scenario_id not in context.catalog.scenarios
        ):
            raise InvalidDecision("Resolution requires a validated route")
        lookup = DemoRecordLookup(context)
        records = lookup.available_records()
        supported_ids = explicit_demo_ids(context)
        for record in records:
            supported_ids.update(parse_demo_ids(json.dumps(record, ensure_ascii=False)))
        tools = []
        if lookup.enabled:

            @function_tool
            def lookup_demo_record(record_id: str) -> str:
                """Read one allowed synthetic record using an ID explicitly supplied by the user."""
                result = lookup.lookup(record_id)
                if result.get("status") == "found":
                    supported_ids.update(
                        parse_demo_ids(json.dumps(result, ensure_ascii=False))
                    )
                return json.dumps(result, ensure_ascii=False)

            tools.append(lookup_demo_record)
        agent = Agent(
            name="Resolution",
            instructions=f"{config.answer_prompt}\n\n{RESOLUTION_INVARIANTS}",
            model=self._model(config),
            model_settings=self._model_settings(config),
            tools=tools,
        )
        payload = {
            "selected_scenario": scenario_definition(
                context.catalog.scenarios[decision.scenario_id]
            ),
            "knowledge": scenario_knowledge(context),
            **self._conversation(context),
            "routing_decision": decision.model_dump(),
            "verified_records": records,
            "available_tools": [tool.name for tool in tools],
        }
        output = await self._run(
            agent, payload, context, config, max_turns=config.resolution_max_turns
        )
        if not isinstance(output, str) or not output.strip():
            raise AgentFailure("Resolution returned an empty or invalid answer")
        if parse_demo_ids(output) - supported_ids:
            # Reject fabricated example IDs without another model round trip.
            if decision.language == "kk":
                return "Бұл мәліметтер расталмады. Полис немесе өтініш нөмірін нақтылаңызшы."
            return "Не удалось подтвердить эти сведения. Уточните, пожалуйста, номер полиса или обращения."
        return output.strip()

    @staticmethod
    def _model_settings(config: RuntimeConfig) -> ModelSettings:
        # Preserve the low reasoning budget across the voice model migration;
        # legacy models may not accept reasoning parameters at all.
        if any(
            config.model == name or config.model.startswith(name + "-")
            for name in ("gpt-6-luna", "gpt-5.6-terra")
        ):
            return ModelSettings(reasoning=Reasoning(effort="low"))
        return ModelSettings()

    def _model(self, config: RuntimeConfig) -> OpenAIResponsesModel:
        client = self._client() if callable(self._client) else self._client
        return OpenAIResponsesModel(config.model, client)

    @staticmethod
    def _conversation(context: RoutingContext) -> dict[str, Any]:
        return {
            "utterance": context.text,
            "language_context": {
                "current_utterance": context.text,
                "prior_user_utterances": [
                    entry["content"]
                    for entry in context.history
                    if entry.get("role") == "user" and entry.get("content", "").strip()
                ],
            },
            "explicit_demo_ids": sorted(explicit_demo_ids(context)),
            "current_record_references": [
                {"id": identifier, "resource": demo_record_resource(identifier)}
                for identifier in sorted(parse_demo_ids(context.text))
            ],
            "recent_dialog": context.history,
            "active_scenario": context.active_scenario,
            "pending_scenarios": context.pending_scenarios,
            "parameters": [parameter.model_dump() for parameter in context.parameters],
        }

    async def _run(
        self,
        agent: Agent,
        payload: dict[str, Any],
        context: RoutingContext,
        config: RuntimeConfig,
        *,
        max_turns: int,
    ) -> Any:
        run_config = RunConfig(
            tracing_disabled=not config.tracing_enabled,
            trace_include_sensitive_data=False,
            workflow_name=f"VoiceRouter.{agent.name}",
            group_id=context.session_id,
            trace_metadata={"turn_trace_id": context.trace_id},
        )
        try:
            result = await asyncio.wait_for(
                self._runner.run(
                    agent,
                    input=json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    ),
                    max_turns=max_turns,
                    run_config=run_config,
                ),
                timeout=config.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise TurnTimeout("Agent exceeded its time budget") from exc
        except (AgentsException, OpenAIError) as exc:
            raise AgentFailure(
                f"{agent.name} could not produce a usable answer"
            ) from exc
        return result.final_output
