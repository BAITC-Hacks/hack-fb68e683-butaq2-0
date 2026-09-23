"""HTTP/audio adapter for the shared multi-agent decision service."""

from __future__ import annotations

import base64
import os
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

from fastapi import HTTPException
from multi_agent.contracts import (
    DEFAULT_MODEL,
    AgentFailure,
    AgentGateway,
    Catalog,
    InvalidDecision,
    RoutingDecision,
    RuntimeConfig,
    TurnResult,
    TurnTimeout,
)
from multi_agent.orchestrator import Conversation, VoiceRouterOrchestrator
from multi_agent.prompts import ANSWER_PROMPT, ROUTING_PROMPT
from multi_agent.sdk import SdkAgentGateway
from pydantic import ValidationError

from v2v import VoicePipeline
from v2v.audio import CONTENT_TYPE_BY_FORMAT


class RouterService:
    def __init__(
        self,
        pipeline: VoicePipeline,
        *,
        catalog: Catalog | None = None,
        database: Any = None,
        model: str | None = None,
        threshold: float | None = None,
        gateway: AgentGateway | None = None,
    ):
        self.catalog = catalog
        self.database = database
        self.pipeline = pipeline
        self.stream_sessions: set[str] = set()
        self.config = RuntimeConfig(
            model=model or os.getenv("ROUTER_MODEL", DEFAULT_MODEL),
            routing_prompt=ROUTING_PROMPT,
            answer_prompt=ANSWER_PROMPT,
            confidence_threshold=(
                threshold
                if threshold is not None
                else os.getenv("ROUTER_CONFIDENCE_THRESHOLD", "0.65")
            ),
            timeout_seconds=os.getenv("MULTI_AGENT_TIMEOUT_SECONDS", "20"),
            resolution_max_turns=os.getenv("MULTI_AGENT_RESOLUTION_MAX_TURNS", "3"),
            tracing_enabled=os.getenv("MULTI_AGENT_TRACING", "false"),
        )
        self.orchestrator = VoiceRouterOrchestrator(
            gateway if gateway is not None else SdkAgentGateway(lambda: pipeline.client)
        )

    @property
    def sessions(self) -> dict[str, Conversation]:
        return self.orchestrator.sessions

    async def turn(
        self,
        *,
        session_id: str,
        text: str,
        stt_ms: float = 0,
        synthesize: bool = False,
        on_route: Callable[[RoutingDecision], Awaitable[None]] | None = None,
        delivery_id: str | None = None,
        voice_context: list[dict[str, str]] | None = None,
    ) -> TurnResult:
        if delivery_id is None and session_id in self.stream_sessions:
            raise HTTPException(
                409, "This session already has an active voice connection"
            )
        text = text.strip()
        if not text:
            raise HTTPException(422, "Empty utterance")
        started = perf_counter()
        try:
            catalog = self.database.catalog() if self.database else self.catalog
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        if catalog is None:
            raise HTTPException(503, "Scenario catalog not configured")
        try:
            config = (
                RuntimeConfig(
                    **{**self.config.model_dump(), **self.database.settings()}
                )
                if self.database
                else self.config
            )
        except ValidationError as exc:
            raise HTTPException(503, "Invalid multi-agent runtime settings") from exc

        try:
            result = await self.orchestrator.turn(
                session_id=session_id,
                text=text,
                catalog=catalog,
                config=config,
                on_route=on_route,
                delivery_id=delivery_id,
                voice_context=voice_context,
            )
        except (InvalidDecision, AgentFailure) as exc:
            raise HTTPException(502, str(exc)) from exc
        except TurnTimeout as exc:
            raise HTTPException(
                504, "Multi-agent turn exceeded its time budget"
            ) from exc
        result.timings.stt_ms = round(stt_ms, 1)

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
