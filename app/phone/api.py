"""Explicit inbound-only API: never mount the library's outbound/transfer routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from telephony.domain import CallState, PolicyDenied
from telephony.ports import CallQuery, RejectReason
from telephony.schemas import CallListResponse, CallResponse

from app.api.admin_auth import require_admin

from .runtime import PhoneTrace
from .service import InboundPhoneService

router = APIRouter(prefix="/telephony", tags=["Telephony"])
managed = APIRouter(dependencies=[Depends(require_admin)])


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


managed.dependencies.append(Depends(no_store))


def get_phone(request: Request) -> InboundPhoneService:
    phone = getattr(request.app.state, "phone", None)
    if phone is None:
        raise HTTPException(503, "Telephony is disabled")
    return phone


Phone = Annotated[InboundPhoneService, Depends(get_phone)]


@router.get("/health")
async def health(request: Request):
    return {"enabled": getattr(request.app.state, "phone", None) is not None}


@managed.get("/calls", response_model=CallListResponse)
async def calls(phone: Phone, limit: Annotated[int, Query(ge=1, le=100)] = 50, cursor: str | None = None):
    page = await phone.list_calls(CallQuery(limit=limit, cursor=cursor))
    return CallListResponse(items=[CallResponse.of(call) for call in page.items], next_cursor=page.next_cursor)


@managed.get("/calls/{call_id}", response_model=CallResponse)
async def call(call_id: UUID, phone: Phone):
    return CallResponse.of(await phone.get_call(call_id))


@managed.get("/calls/{call_id}/trace", response_model=PhoneTrace)
async def trace(call_id: UUID, phone: Phone):
    call = await phone.get_call(call_id)
    dialogue = phone.runtime.dialogues.get(call_id)
    if dialogue is None:
        raise HTTPException(404, "Trace expired")
    return dialogue.trace.model_copy(update={"state": call.state.value})


@managed.post("/calls/{call_id}/hangup", response_model=CallResponse)
async def hangup(call_id: UUID, phone: Phone):
    return CallResponse.of(await phone.hangup_call(call_id))


async def _body(request: Request, phone: InboundPhoneService) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > phone.settings.max_webhook_bytes:
            raise HTTPException(413, "Webhook body too large")
    return bytes(body)


async def _carrier_event(request: Request, phone: InboundPhoneService):
    if request.headers.get("content-type", "").split(";")[0].lower() != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Expected a cXML form callback")
    # Never trust Host/X-Forwarded-* supplied by an arbitrary caller.
    url = phone.settings.public_base_url.rstrip("/") + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    return phone.provider.verify_webhook(await _body(request, phone), request.headers, url)


@router.post("/webhooks/signalwire/voice")
async def incoming(request: Request, phone: Phone):
    event = await _carrier_event(request, phone)
    try:
        call, decision = await phone.handle_inbound_call(event)
    except PolicyDenied:
        body, content_type = phone.provider.reject_response(RejectReason(code="busy"))
    else:
        if decision.accept and call.state not in (CallState.ENDING,):
            body, content_type = phone.provider.answer_response(call)
        else:
            body, content_type = phone.provider.reject_response(RejectReason())
    return Response(content=body, media_type=content_type)


@router.post("/webhooks/signalwire/status", status_code=204)
async def status(request: Request, phone: Phone):
    await phone.process_provider_event(await _carrier_event(request, phone))
    return Response(status_code=204)


@router.post("/webhooks/openai", status_code=204)
async def live_incoming(request: Request, phone: Phone):
    body = await _body(request, phone)
    incoming = phone.live_calls.verify_webhook(body, request.headers)
    if incoming is not None:
        await phone.handle_live_incoming_call(incoming)
    return Response(status_code=204)


router.include_router(managed)
