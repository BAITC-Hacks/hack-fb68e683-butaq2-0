




"""FastAPI router for telephony.

    from telephony import Telephony, create_telephony_router, register_exception_handlers

    app.include_router(create_telephony_router(telephony=Telephony(...)))
    register_exception_handlers(app)

Management endpoints take the host app's auth dependencies. Webhook endpoints
do not -- they authenticate with the provider signature instead.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status

from .application import Telephony
from .domain import (
    CallDirection,
    CallNotFound,
    CallState,
    InvalidNumber,
    InvalidTransition,
    PolicyDenied,
    WebhookVerificationError,
)
from .ports import CallQuery, RejectReason
from .schemas import (
    CallListResponse,
    CallResponse,
    CreateCallRequest,
    HealthResponse,
    TransferRequest,
)

_default: Telephony | None = None


def get_telephony() -> Telephony:
    """Lazy default for demos; production passes ``telephony=`` explicitly."""

    global _default
    if _default is None:
        _default = Telephony()
    return _default


def set_telephony(telephony: Telephony | None) -> None:
    global _default
    _default = telephony


def create_telephony_router(
    *,
    telephony: Telephony | None = None,
    prefix: str | None = None,
    tags: list[str] | None = None,
    dependencies: list | None = None,
) -> APIRouter:
    settings = telephony.settings if telephony else get_telephony().settings

    def resolve() -> Telephony:
        return telephony or get_telephony()

    Tel = Annotated[Telephony, Depends(resolve)]

    router = APIRouter(
        prefix=prefix if prefix is not None else settings.api_prefix,
        tags=tags if tags is not None else [settings.api_tag],
    )
    managed = APIRouter(dependencies=dependencies or [])
    webhooks = APIRouter(prefix="/webhooks")

    # -- control plane ------------------------------------------------------

    @managed.post(
        "/calls",
        response_model=CallResponse,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Start an outbound call",
    )
    async def create_call(
        tel: Tel,
        body: CreateCallRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> CallResponse:
        call = await tel.start_outbound_call(
            to=body.to,
            from_number=body.from_number,
            idempotency_key=idempotency_key,
            scenario=body.scenario,
            instructions=body.instructions,
            voice=body.voice,
            metadata=body.metadata,
        )
        return CallResponse.of(call)

    @managed.get("/calls", response_model=CallListResponse, summary="List calls")
    async def list_calls(
        tel: Tel,
        call_status: Annotated[CallState | None, Query(alias="status")] = None,
        direction: CallDirection | None = None,
        scenario: str | None = None,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> CallListResponse:
        page = await tel.list_calls(
            CallQuery(
                state=call_status,
                direction=direction,
                scenario=scenario,
                cursor=cursor,
                limit=limit,
            )
        )
        return CallListResponse(
            items=[CallResponse.of(c) for c in page.items], next_cursor=page.next_cursor
        )

    @managed.get("/calls/{call_id}", response_model=CallResponse, summary="Call state")
    async def get_call(tel: Tel, call_id: UUID) -> CallResponse:
        return CallResponse.of(await tel.get_call(call_id))

    @managed.post(
        "/calls/{call_id}/transfer", response_model=CallResponse, summary="Transfer the call"
    )
    async def transfer(tel: Tel, call_id: UUID, body: TransferRequest) -> CallResponse:
        return CallResponse.of(await tel.transfer_call(call_id, body.target))

    @managed.post(
        "/calls/{call_id}/hangup", response_model=CallResponse, summary="Hang up (idempotent)"
    )
    async def hangup(tel: Tel, call_id: UUID) -> CallResponse:
        return CallResponse.of(await tel.hangup_call(call_id))

    @managed.post(
        "/calls/{call_id}/cancel", response_model=CallResponse, summary="Cancel before connect"
    )
    async def cancel(tel: Tel, call_id: UUID) -> CallResponse:
        return CallResponse.of(await tel.cancel_call(call_id))

    # -- health -------------------------------------------------------------

    @router.get("/health/live", response_model=HealthResponse, summary="Process is alive")
    async def health_live() -> HealthResponse:
        return HealthResponse(status="ok")

    @router.get("/health/ready", response_model=HealthResponse, summary="Dependencies are usable")
    async def health_ready(tel: Tel) -> HealthResponse:
        checks = {
            "provider": tel.provider is not None,
            "openai_key": bool(tel.settings.openai_api_key),
            "caller_id": bool(tel.settings.twilio_from_number),
        }
        return HealthResponse(
            status="ok" if all(checks.values()) else "degraded",
            checks=checks,
            live_model=tel.settings.live_model,
            provider=type(tel.provider).__name__ if tel.provider else None,
        )

    # -- webhooks -----------------------------------------------------------

    async def _verified_event(tel: Telephony, request: Request, path: str):
        body = await request.body()
        if len(body) > tel.settings.max_webhook_bytes:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Webhook body too large")
        url = tel.settings.public_base_url.rstrip("/") + router.prefix + path
        if request.url.query:
            url = f"{url}?{request.url.query}"
        return tel.provider.verify_webhook(body, request.headers, url)

    @webhooks.post("/twilio/voice", summary="Inbound call / answer")
    async def twilio_voice(
        tel: Tel,
        request: Request,
        call_id: Annotated[UUID | None, Query()] = None,
    ) -> Response:
        event = await _verified_event(tel, request, "/webhooks/twilio/voice")
        if call_id is not None:
            # ``start_call`` puts our call id in Twilio's signed answer URL.
            # Reuse that call rather than interpreting the answer webhook as a
            # new inbound call. This is the outgoing half of the SIP bridge.
            call = await tel.get_call(call_id)
            if event.direction is not CallDirection.OUTBOUND:
                raise HTTPException(status.HTTP_409_CONFLICT, "Unexpected inbound answer webhook")
            if call.provider_call_id != event.provider_call_id:
                raise HTTPException(status.HTTP_409_CONFLICT, "Twilio call id does not match")
            await tel.process_provider_event(event)
            body, content_type = tel.provider.answer_response(call)
            return Response(content=body, media_type=content_type)

        call, decision = await tel.handle_inbound_call(event)
        if not decision.accept:
            body, content_type = tel.provider.reject_response(decision.reason or RejectReason())
        else:
            body, content_type = tel.provider.answer_response(call)
        return Response(content=body, media_type=content_type)

    @webhooks.post("/twilio/status", summary="Call status callback")
    async def twilio_status(tel: Tel, request: Request) -> Response:
        event = await _verified_event(tel, request, "/webhooks/twilio/status")
        # Verified, deduplicated, persisted -- then answer fast. Long work
        # belongs on the worker, not on the provider's retry clock.
        await tel.process_provider_event(event)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @webhooks.post("/openai", summary="OpenAI Live SIP incoming call")
    async def openai_live(tel: Tel, request: Request) -> Response:
        body = await request.body()
        if len(body) > tel.settings.max_webhook_bytes:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Webhook body too large")
        if tel.live_calls is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "OpenAI Live telephony is not configured"
            )
        incoming = tel.live_calls.verify_webhook(body, request.headers)
        if incoming is not None:
            await tel.handle_live_incoming_call(incoming)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    router.include_router(managed)
    router.include_router(webhooks)
    return router


def register_exception_handlers(app) -> None:
    """Map domain errors onto HTTP responses."""

    from fastapi.responses import JSONResponse

    def _handler(code: int):
        async def handle(_, exc: Exception):
            return JSONResponse(status_code=code, content={"detail": str(exc)})

        return handle

    app.add_exception_handler(CallNotFound, _handler(status.HTTP_404_NOT_FOUND))
    app.add_exception_handler(PolicyDenied, _handler(status.HTTP_403_FORBIDDEN))
    app.add_exception_handler(InvalidNumber, _handler(status.HTTP_422_UNPROCESSABLE_ENTITY))
    app.add_exception_handler(InvalidTransition, _handler(status.HTTP_409_CONFLICT))
    app.add_exception_handler(WebhookVerificationError, _handler(status.HTTP_403_FORBIDDEN))
