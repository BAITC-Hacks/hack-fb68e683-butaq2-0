"""Pydantic DTOs.

Responses carry masked numbers and no provider credentials, SIP headers or
raw payloads.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from .domain import Call, CallDirection, CallState, mask_number


class CreateCallRequest(BaseModel):
    to: str = Field(description="Callee number, normalised to E.164")
    from_number: str | None = Field(default=None, alias="from")
    scenario: str | None = None
    instructions: str | None = Field(default=None, description="Frontend (voice) prompt override")
    voice: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class TransferRequest(BaseModel):
    target: str = Field(description="tel:+123... or sip:user@domain, subject to the allowlist")


class UsageResponse(BaseModel):
    voice_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    final: bool = False


class CallResponse(BaseModel):
    call_id: UUID
    direction: CallDirection
    status: CallState
    to: str | None
    from_number: str | None = Field(default=None, serialization_alias="from")
    scenario: str | None = None
    provider_call_id: str | None = None
    session_id: str | None = None
    terminal_reason: str | None = None
    finalization_status: Literal["pending", "confirmed", "unconfirmed"] = "pending"
    metadata: dict[str, str] = Field(default_factory=dict)
    usage: UsageResponse = Field(default_factory=UsageResponse)
    created_at: str
    updated_at: str
    connected_at: str | None = None
    ended_at: str | None = None

    model_config = {"populate_by_name": True}

    @classmethod
    def of(cls, call: Call) -> CallResponse:
        return cls(
            call_id=call.call_id,
            direction=call.direction,
            status=call.state,
            to=mask_number(call.to_number),
            from_number=mask_number(call.from_number),
            scenario=call.scenario,
            provider_call_id=call.provider_call_id,
            session_id=call.session_id,
            terminal_reason=call.terminal_reason,
            finalization_status=call.finalization_status,
            metadata=call.metadata,
            usage=UsageResponse(
                voice_seconds=call.usage.voice_seconds,
                input_tokens=call.usage.input_tokens,
                output_tokens=call.usage.output_tokens,
                final=call.usage.final,
            ),
            created_at=call.created_at,
            updated_at=call.updated_at,
            connected_at=call.connected_at,
            ended_at=call.ended_at,
        )


class CallListResponse(BaseModel):
    items: list[CallResponse]
    next_cursor: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, bool] = Field(default_factory=dict)
    live_model: str | None = None
    provider: str | None = None
