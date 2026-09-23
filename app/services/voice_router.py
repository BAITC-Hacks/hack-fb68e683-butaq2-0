"""LLM-driven scenario decisions for the Voice Router case.

The catalog is data, never an intent-classifier training set. Every turn asks an
LLM to choose from the *current* catalog with the conversation as context.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from app.core.prompts import ANSWER_PROMPT, ROUTING_PROMPT
from app.domain import Catalog, Decision, Timings, TurnResult
from v2v import VoicePipeline
from v2v.audio import CONTENT_TYPE_BY_FORMAT


@dataclass
class Conversation:
    history: list[dict[str, str]] = field(default_factory=list)
    active_scenario: str | None = None
    pending_scenarios: list[str] = field(default_factory=list)
    uncertain_turns: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RouterService:
    def __init__(
        self,
        pipeline: VoicePipeline,
        *,
        catalog: Catalog | None = None,
        database: Any = None,
        model: str | None = None,
        threshold: float | None = None,
    ):
        self.catalog = catalog
        self.database = database
        self.pipeline = pipeline
        self.model = model or os.getenv("ROUTER_MODEL", "gpt-4o-mini")
        self.threshold = (
            threshold
            if threshold is not None
            else float(os.getenv("ROUTER_CONFIDENCE_THRESHOLD", "0.65"))
        )
        if not 0 <= self.threshold <= 1:
            raise ValueError("ROUTER_CONFIDENCE_THRESHOLD must be between 0 and 1")
        self.sessions: dict[str, Conversation] = {}

    async def _route(
        self, text: str, state: Conversation, catalog: Catalog, config: dict[str, str]
    ) -> Decision:
        context = {
            "response_format": "json",
            "response_schema": Decision.model_json_schema(),
            "catalog": catalog.prompt_data(),
            "active_scenario": state.active_scenario,
            "pending_scenarios": state.pending_scenarios,
            "recent_dialog": state.history[-20:],
            "utterance": text,
        }
        response = await self.pipeline.client.responses.create(
            model=config["model"],
            instructions=config["routing_prompt"],
            input=json.dumps(context, ensure_ascii=False),
            text={"format": {"type": "json_object"}},
        )
        try:
            decision = Decision.model_validate_json(response.output_text)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(502, "Routing model returned invalid JSON") from exc
        ids = catalog.scenarios
        referenced = [
            decision.scenario_id,
            *[a.scenario_id for a in decision.alternatives],
            *decision.pending_scenario_ids,
        ]
        if any(sid is not None and sid not in ids for sid in referenced):
            raise HTTPException(502, "Routing model returned an unknown scenario ID")
        if decision.action == "route" and decision.scenario_id is None:
            raise HTTPException(502, "Routing model omitted the selected scenario")
        if decision.action != "route" and decision.scenario_id is not None:
            raise HTTPException(
                502, "Routing model selected a scenario without routing"
            )
        return decision

    async def _answer(
        self,
        text: str,
        state: Conversation,
        decision: Decision,
        catalog: Catalog,
        config: dict[str, str],
    ) -> str:
        scenario = catalog.scenarios[decision.scenario_id]  # type: ignore[index]
        facts = {
            "scenario": scenario.details,
            "knowledge_base": catalog.knowledge,
            "mock_backend": catalog.backend,
            "dialog": state.history[-20:],
            "utterance": text,
            "pending_scenario_ids": state.pending_scenarios,
        }
        response = await self.pipeline.client.responses.create(
            model=config["model"],
            instructions=config["answer_prompt"],
            input=json.dumps(facts, ensure_ascii=False),
        )
        answer = (response.output_text or "").strip()
        if not answer:
            raise HTTPException(502, "Response model returned an empty answer")
        return answer

    async def turn(
        self,
        *,
        session_id: str,
        text: str,
        stt_ms: float = 0,
        synthesize: bool = False,
    ) -> TurnResult:
        text = text.strip()
        if not text:
            raise HTTPException(422, "Empty utterance")
        state = self.sessions.setdefault(session_id, Conversation())
        started = perf_counter()
        async with state.lock:
            try:
                catalog = self.database.catalog() if self.database else self.catalog
            except ValueError as exc:
                raise HTTPException(503, str(exc)) from exc
            if catalog is None:
                raise HTTPException(503, "Scenario catalog not configured")
            config = (
                self.database.settings()
                if self.database
                else {
                    "model": self.model,
                    "routing_prompt": ROUTING_PROMPT,
                    "answer_prompt": ANSWER_PROMPT,
                    "confidence_threshold": str(self.threshold),
                }
            )
            state.pending_scenarios = [
                sid for sid in state.pending_scenarios if sid in catalog.scenarios
            ]
            if state.active_scenario not in catalog.scenarios:
                state.active_scenario = None
            routed_at = perf_counter()
            decision = await self._route(text, state, catalog, config)
            routing_ms = (perf_counter() - routed_at) * 1000

            if decision.action == "route" and decision.confidence < float(
                config["confidence_threshold"]
            ):
                decision.action = "clarify"
                decision.scenario_id = None
                decision.reason += " (below confidence threshold)"
            if decision.action == "clarify":
                state.uncertain_turns += 1
                if state.uncertain_turns >= 2:
                    decision.action = "handoff"
                    decision.reason += (
                        " (repeated uncertainty; transferring to an operator)"
                    )
            else:
                state.uncertain_turns = 0

            response_at = perf_counter()
            if decision.action == "route":
                previous = state.active_scenario
                pending = [*decision.pending_scenario_ids, *state.pending_scenarios]
                if previous and previous != decision.scenario_id:
                    pending.append(previous)
                pending = [
                    sid
                    for sid in dict.fromkeys(pending)
                    if sid in catalog.scenarios and sid != decision.scenario_id
                ][:5]
                reply = await self._answer(text, state, decision, catalog, config)
                state.active_scenario = decision.scenario_id
                state.pending_scenarios = pending
            elif decision.action == "clarify":
                reply = decision.customer_message
            else:
                reply = decision.customer_message
            response_ms = (perf_counter() - response_at) * 1000
            state.history.extend(
                [
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": reply},
                ]
            )
            state.history = state.history[-20:]
            result = TurnResult(
                session_id=session_id,
                transcript=text,
                reply=reply,
                action=decision.action,
                scenario_id=decision.scenario_id,
                scenario_title=catalog.scenarios[decision.scenario_id].title
                if decision.scenario_id
                else None,
                confidence=decision.confidence,
                reason=decision.reason,
                alternatives=decision.alternatives[:3],
                pending_scenario_ids=state.pending_scenarios,
                timings=Timings(
                    stt_ms=round(stt_ms, 1),
                    routing_ms=round(routing_ms, 1),
                    response_ms=round(response_ms, 1),
                ),
            )

        if synthesize:
            tts_at = perf_counter()
            audio = await self.pipeline.speak_bytes(result.reply)
            result.audio_base64 = base64.b64encode(audio).decode("ascii")
            result.audio_content_type = CONTENT_TYPE_BY_FORMAT[
                self.pipeline.settings.tts_format
            ]
            result.timings.tts_ms = round((perf_counter() - tts_at) * 1000, 1)
        result.timings.total_ms = round(stt_ms + (perf_counter() - started) * 1000, 1)
        return result
