"""OpenAI Agents SDK implementation of the two-agent runtime port."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from agents import Agent, OpenAIResponsesModel, RunConfig, Runner, function_tool
from agents.exceptions import AgentsException
from openai import AsyncOpenAI, OpenAIError
from pydantic import ValidationError

from .contracts import (
    AgentFailure,
    InvalidDecision,
    ResolutionContext,
    RoutingContext,
    RoutingDecision,
    RuntimeConfig,
    TurnTimeout,
)
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
            output_type=RoutingDecision,
        )
        payload = {
            **self._conversation(context),
            "catalog": context.catalog.prompt_data(),
            "confidence_threshold": config.confidence_threshold,
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
        tools = []
        if lookup.enabled:

            @function_tool
            def lookup_demo_record(record_id: str) -> str:
                """Read one allowed synthetic record using an ID explicitly supplied by the user."""
                return json.dumps(lookup.lookup(record_id), ensure_ascii=False)

            tools.append(lookup_demo_record)
        agent = Agent(
            name="Resolution",
            instructions=f"{config.answer_prompt}\n\n{RESOLUTION_INVARIANTS}",
            model=self._model(config),
            tools=tools,
        )
        payload = {
            **self._conversation(context),
            "selected_scenario": context.catalog.scenarios[
                decision.scenario_id
            ].model_dump(),
            "routing_decision": decision.model_dump(),
            "knowledge": scenario_knowledge(context),
            "available_tools": [tool.name for tool in tools],
        }
        output = await self._run(
            agent, payload, context, config, max_turns=config.resolution_max_turns
        )
        if not isinstance(output, str) or not output.strip():
            raise AgentFailure("Resolution returned an empty or invalid answer")
        return output.strip()

    def _model(self, config: RuntimeConfig) -> OpenAIResponsesModel:
        client = self._client() if callable(self._client) else self._client
        return OpenAIResponsesModel(config.model, client)

    @staticmethod
    def _conversation(context: RoutingContext) -> dict[str, Any]:
        return {
            "utterance": context.text,
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
                    input=json.dumps(payload, ensure_ascii=False),
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
