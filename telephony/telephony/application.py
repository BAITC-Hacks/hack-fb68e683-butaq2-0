"""Use cases and the DI container.

``Telephony`` is both: one object holding the ports, with one method per use
case. Build it once in the FastAPI lifespan and hand it to the router.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from .config import TelephonySettings, get_settings
from .delegation import LiveFinalizationResult
from .domain import (
    Call,
    CallDirection,
    CallNotFound,
    CallState,
    CallTransition,
    PolicyDenied,
    check_transfer_target,
    new_call,
    normalize_e164,
)
from .memory import InMemoryCallRepository, InMemoryEventInbox, InMemoryLiveStateStore
from .ports import (
    CallQuery,
    LiveIncomingCall,
    OutboundCallCommand,
    Page,
    ProviderEvent,
    RejectReason,
    RoutingDecision,
)

_log = logging.getLogger(__name__)


class Telephony:
    """Application service. Every dependency is replaceable at construction."""

    def __init__(
        self,
        settings: TelephonySettings | None = None,
        *,
        provider: Any = None,
        live: Any = None,
        live_calls: Any = None,
        agent: Any = None,
        runner: Any = None,
        calls: Any = None,
        inbox: Any = None,
        live_state: Any = None,
        routing: Any = None,
        authorization: Any = None,
        events: Any = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.provider = provider
        self.live = live
        self.live_calls = live_calls
        self.agent = agent
        self.runner = runner
        self.calls = calls or InMemoryCallRepository()
        self.inbox = inbox or InMemoryEventInbox()
        self.live_state = live_state or InMemoryLiveStateStore()
        self.routing = routing
        self.authorization = authorization
        self.events = events

    # -- helpers ------------------------------------------------------------

    def _require_provider(self) -> Any:
        if self.provider is None:
            raise RuntimeError("No TelephonyProvider configured (pass provider= to Telephony)")
        return self.provider

    async def _emit(self, call: Call, event: str, detail: Mapping[str, Any] | None = None) -> None:
        if self.events is not None:
            await self.events.emit(call, event, detail or {})

    async def _apply(
        self,
        call: Call,
        to_state: CallState,
        *,
        source: str = "application",
        event_id: str | None = None,
        reason: str | None = None,
    ) -> bool:
        changed = call.apply(
            CallTransition(to_state=to_state, source=source, event_id=event_id, reason=reason)
        )
        if changed:
            await self.calls.save(call)
            await self._emit(call, f"call.{to_state.value}", {"reason": reason})
            if call.is_terminal:
                await self.live_state.delete(call.call_id)
        return changed

    # -- use cases ----------------------------------------------------------

    async def start_outbound_call(
        self,
        *,
        to: str,
        from_number: str | None = None,
        idempotency_key: str,
        scenario: str | None = None,
        instructions: str | None = None,
        voice: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Call:
        existing = await self.calls.find_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing

        to_e164 = normalize_e164(to)
        from_e164 = normalize_e164(from_number or self.settings.twilio_from_number or "")
        if await self.calls.count_active() >= self.settings.max_concurrent_calls:
            raise PolicyDenied("Concurrent call limit reached")

        call = new_call(
            direction=CallDirection.OUTBOUND,
            to_number=to_e164,
            from_number=from_e164,
            scenario=scenario,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )
        command = OutboundCallCommand(
            to_number=to_e164,
            from_number=from_e164,
            call_id=call.call_id,
            scenario=scenario,
            instructions=instructions,
            voice=voice or self.settings.voice,
            metadata=call.metadata,
        )
        if self.authorization is not None:
            await self.authorization.allow_outbound(command)

        # Persist *before* dialling: a provider callback can beat the response.
        await self.calls.create(call)
        try:
            result = await self._require_provider().start_call(command)
        except Exception as exc:
            reason = f"provider_error: {type(exc).__name__}"
            await self._apply(call, CallState.FAILED, reason=reason)
            raise
        call.provider_call_id = result.provider_call_id
        await self.calls.save(call)
        await self._apply(call, result.status or CallState.DIALING)
        return call

    async def handle_inbound_call(self, event: ProviderEvent) -> tuple[Call, RoutingDecision]:
        """Create (or find) the inbound call and decide whether to take it."""

        existing = (
            await self.calls.find_by_provider_id(event.provider_call_id)
            if event.provider_call_id
            else None
        )
        decision = RoutingDecision()
        if self.routing is not None:
            decision = await self.routing.route(event)

        if existing is not None:
            return existing, decision

        call = new_call(
            direction=CallDirection.INBOUND,
            to_number=event.to_number or "",
            from_number=event.from_number or "",
            scenario=decision.scenario,
        )
        call.provider_call_id = event.provider_call_id
        await self.calls.create(call)
        await self._apply(
            call,
            CallState.ACCEPTED if decision.accept else CallState.REJECTED,
            source=event.source,
            event_id=event.event_id,
            reason=None if decision.accept else (decision.reason or RejectReason()).code,
        )
        return call, decision

    async def process_provider_event(self, event: ProviderEvent) -> Call | None:
        """Idempotent status handling. Returns None for replays and unknown calls."""

        if not await self.inbox.claim(
            event.source, event.event_id, self.settings.event_dedup_ttl_seconds
        ):
            return None
        call = (
            await self.calls.find_by_provider_id(event.provider_call_id)
            if event.provider_call_id
            else None
        )
        if call is None:
            return None
        if event.session_id and call.session_id != event.session_id:
            call.session_id = event.session_id
            await self.calls.save(call)
        if event.status is not None:
            await self._apply(
                call,
                event.status,
                source=event.source,
                event_id=event.event_id,
                reason=event.reason,
            )
        return call

    async def handle_live_incoming_call(self, incoming: LiveIncomingCall) -> Call | None:
        """Make one idempotent decision for a verified OpenAI Live SIP invite."""

        if self.live_calls is None:
            raise RuntimeError("No LiveCallController configured (pass live_calls= to Telephony)")
        source = "openai"
        if not await self.inbox.claim(
            source, incoming.event_id, self.settings.event_dedup_ttl_seconds
        ):
            return await self.calls.find_by_provider_id(incoming.session_id)

        event = ProviderEvent(
            source=source,
            event_id=incoming.event_id,
            kind="incoming",
            provider_call_id=incoming.session_id,
            direction=CallDirection.INBOUND,
            from_number=incoming.from_number,
            to_number=incoming.to_number,
            session_id=incoming.session_id,
        )
        try:
            decision = (
                await self.routing.route(event) if self.routing is not None else RoutingDecision()
            )
            call = await self.calls.find_by_provider_id(incoming.session_id)
            bridged = call is None and await self._bridged_outbound_call(incoming)
            if bridged:
                call = bridged
                call.session_id = incoming.session_id
                await self.calls.save(call)
            elif call is None:
                call = new_call(
                    direction=CallDirection.INBOUND,
                    to_number=incoming.to_number or "",
                    from_number=incoming.from_number or "",
                    scenario=decision.scenario,
                )
                call.provider_call_id = incoming.session_id
                call.session_id = incoming.session_id
                await self.calls.create(call)

            if decision.accept:
                try:
                    await self.live_calls.accept(incoming.session_id)
                except Exception as exc:
                    # A SIP invite is one-shot. Letting this escape would make
                    # the webhook a 500, and every OpenAI retry would then find
                    # an expired session. Record it and answer 2xx instead.
                    _log.warning("Live accept failed for %s: %s", incoming.session_id, exc)
                    await self._apply(
                        call,
                        CallState.FAILED,
                        source=source,
                        event_id=incoming.event_id,
                        reason=f"live_accept_failed: {type(exc).__name__}",
                    )
                    return call
                await self._apply(
                    # An outbound call reaching us over its own SIP bridge is
                    # already dialling; "accepted" only exists for inbound.
                    call,
                    CallState.CONNECTED if bridged else CallState.ACCEPTED,
                    source=source,
                    event_id=incoming.event_id,
                )
                if self.runner is not None:
                    await self.runner.start(call, on_finalized=self.finalize_live_session)
            else:
                reason = decision.reason or RejectReason()
                status_code = 486 if reason.code == "busy" else 603
                await self.live_calls.reject(incoming.session_id, status_code=status_code)
                await self._apply(
                    call,
                    CallState.REJECTED,
                    source=source,
                    event_id=incoming.event_id,
                    reason=reason.code,
                )
            return call
        except BaseException:
            await self.inbox.release(source, incoming.event_id)
            raise

    async def _bridged_outbound_call(self, incoming: LiveIncomingCall) -> Call | None:
        """Find the outbound call whose SIP bridge produced this Live session.

        The header is caller-controlled, so it is only ever used to look a call
        up: anything that is not our own still-dialling, not-yet-linked
        outbound call is treated as an ordinary inbound invite instead.
        """

        if incoming.linked_call_id is None:
            return None
        call = await self.calls.get(incoming.linked_call_id)
        if call is None or call.direction is not CallDirection.OUTBOUND:
            return None
        if call.is_terminal or call.session_id is not None:
            return None
        return call

    async def get_call(self, call_id: UUID) -> Call:
        call = await self.calls.get(call_id)
        if call is None:
            raise CallNotFound(str(call_id))
        return call

    async def list_calls(self, query: CallQuery) -> Page:
        return await self.calls.list(query)

    async def transfer_call(self, call_id: UUID, target: str) -> Call:
        call = await self.get_call(call_id)
        resolved = check_transfer_target(
            target,
            allowed_prefixes=self.settings.allowed_transfer_prefixes,
            allowed_sip_domains=self.settings.allowed_sip_domains,
        )
        if call.is_terminal or call.provider_call_id is None:
            raise PolicyDenied(f"Call is not transferable in state {call.state.value}")
        if self.authorization is not None:
            await self.authorization.allow_transfer(call, resolved)
        # A direct Live SIP call has no carrier leg to redirect: the session
        # itself has to REFER. A bridged call still goes through the provider.
        if self._is_direct_live_sip(call) and self.live_calls is not None:
            await self.live_calls.transfer(call.session_id, resolved)
        else:
            await self._require_provider().transfer(call.provider_call_id, resolved)
        await self._emit(call, "call.transferred", {"target": resolved})
        return call

    @staticmethod
    def _is_direct_live_sip(call: Call) -> bool:
        """True when OpenAI holds the only leg, with no carrier call behind it."""

        return call.session_id is not None and call.provider_call_id == call.session_id

    async def hangup_call(self, call_id: UUID) -> Call:
        """Idempotent: hanging up a finished call is a no-op, not an error."""

        call = await self.get_call(call_id)
        if call.is_terminal or call.state is CallState.ENDING:
            return call
        closed_by_runner = (
            await self.runner.request_close(call_id) if self.runner is not None else False
        )
        if not closed_by_runner and self._is_direct_live_sip(call) and self.live_calls is not None:
            await self.live_calls.hangup(call.session_id)
        elif not closed_by_runner and call.provider_call_id:
            await self._require_provider().hangup(call.provider_call_id)
        await self._apply(call, CallState.ENDING, reason="application_hangup")
        return call

    async def finalize_live_session(
        self, call_id: UUID, result: LiveFinalizationResult
    ) -> None:
        """Persist the authoritative outcome reported by a sideband runner."""

        call = await self.get_call(call_id)
        call.usage = result.usage
        call.finalization_status = result.status
        await self.calls.save(call)
        if call.is_terminal:
            return

        
        if result.transport_error:
            target = CallState.FAILED
            reason = result.reason or "live_session_error"
        else:
            target = CallState.COMPLETED
            reason = result.reason or (None if result.confirmed else "live_session_unconfirmed")
        await self._apply(call, target, source="openai", reason=reason)

    async def cancel_call(self, call_id: UUID) -> Call:
        call = await self.get_call(call_id)
        if call.is_terminal:
            return call
        if call.state is CallState.CONNECTED:
            raise PolicyDenied("Call is already connected; use hangup")
        if call.provider_call_id:
            await self._require_provider().hangup(call.provider_call_id)
        await self._apply(call, CallState.CANCELED, reason="application_cancel")
        return call

    async def aclose(self) -> None:
        for dep in (
            self.runner,
            self.provider,
            self.live,
            self.live_calls,
            self.calls,
            self.live_state,
            self.inbox,
        ):
            closer = getattr(dep, "aclose", None)
            if closer is not None:
                await closer()
