"""Single-process, inbound-only call ownership and bounded trace retention."""

import asyncio
from contextlib import suppress
from dataclasses import replace
from time import monotonic
from typing import Any
from uuid import UUID

from telephony.delegation import LiveFinalizationResult
from telephony.domain import Call
from telephony.memory import InMemoryCallRepository, InMemoryLiveStateStore
from telephony.openai_live import OpenAILiveGateway, OpenAILiveTelephony
from telephony.ports import LiveIncomingCall, ProviderEvent, RoutingDecision

from app.services.voice_router import RouterService
from telephony import (
    CallDirection,
    CallState,
    LiveSessionRunner,
    PolicyDenied,
    Telephony,
)

from .carrier import SignalWireProvider
from .runtime import ButaqPhoneRuntime
from .settings import PhoneSettings


class PhoneCallRepository(InMemoryCallRepository):
    async def find_by_provider_id(self, provider_call_id: str) -> Call | None:
        return next((call for call in self._calls.values() if provider_call_id in (call.provider_call_id, call.session_id)), None)

    def forget(self, call_id: UUID) -> None:
        self._calls.pop(call_id, None)


class InboundPhoneService(Telephony):
    def __init__(self, settings: PhoneSettings, *, runtime: ButaqPhoneRuntime, **kwargs: Any) -> None:
        super().__init__(settings, calls=PhoneCallRepository(), agent=runtime, **kwargs)
        self.runtime = runtime
        self._incoming_lock = asyncio.Lock()
        self._created: dict[UUID, float] = {}

    async def handle_inbound_call(self, event: ProviderEvent) -> tuple[Call, RoutingDecision]:
        async with self._incoming_lock:
            if event.direction is not CallDirection.INBOUND or not event.provider_call_id:
                raise PolicyDenied("Only identified inbound calls are supported")
            existing = await self.calls.find_by_provider_id(event.provider_call_id)
            if existing:
                return existing, RoutingDecision(accept=not existing.is_terminal and existing.state is not CallState.ENDING)
            if await self.calls.count_active() >= self.settings.max_concurrent_calls:
                raise PolicyDenied("Concurrent call limit reached")
            call, decision = await super().handle_inbound_call(event)
            self._created[call.call_id] = monotonic()
            self.runtime.open(call.call_id)
            return call, decision

    async def handle_live_incoming_call(self, incoming: LiveIncomingCall) -> Call | None:
        async with self._incoming_lock:
            existing = await self.calls.find_by_provider_id(incoming.session_id)
            if existing:
                return existing  # Re-delivery must never accept or restart a session twice.
            call = await self.calls.get(incoming.linked_call_id) if incoming.linked_call_id else None
            if (call is None or call.is_terminal or call.state is CallState.ENDING or call.session_id
                    or not self.provider.matches_bridge(call.call_id, incoming.bridge_token)):
                await self.live_calls.reject(incoming.session_id, status_code=603)
                return None
            call.session_id = incoming.session_id
            await self.calls.save(call)
            self.provider.forget(call.call_id)  # Consume the capability once.
            try:
                await self.live_calls.accept(incoming.session_id)
                await self._apply(call, CallState.CONNECTED, source="openai")
                await self.runner.start(call, on_finalized=self.finalize_live_session)
            except Exception:  # noqa: BLE001 - record failed SIP acceptance without leaking credentials
                with suppress(Exception):
                    await self.live_calls.hangup(incoming.session_id)
                with suppress(Exception):
                    await self.provider.hangup(call.provider_call_id)
                await self._apply(call, CallState.FAILED, reason="live_start_failed")
                self.runtime.finish(call.call_id, call.state.value)
            return call

    async def process_provider_event(self, event: ProviderEvent) -> Call | None:
        async with self._incoming_lock:
            return await self._process_provider_event(event)

    async def _process_provider_event(self, event: ProviderEvent) -> Call | None:
        call = await super().process_provider_event(event)
        if call and call.is_terminal:
            await self.runner.stop(call.call_id)
            self.provider.forget(call.call_id)
            self.runtime.finish(call.call_id, call.state.value)
        return call

    async def hangup_call(self, call_id: UUID) -> Call:
        async with self._incoming_lock:
            return await self._hangup_call(call_id)

    async def _hangup_call(self, call_id: UUID) -> Call:
        call = await self.get_call(call_id)
        if call.is_terminal:
            return call
        # Stop decisions before making any network request. Keep an honest FAILED
        # state if carrier termination could not be confirmed; do not claim success.
        await self._apply(call, CallState.ENDING, reason="application_hangup")
        await self.runner.stop(call_id)
        self.runtime.finish(call_id, "ending")
        self.provider.forget(call_id)
        try:
            await self.provider.hangup(call.provider_call_id)
        except Exception:  # noqa: BLE001 - caller receives an explicit unconfirmed terminal state
            await self._apply(call, CallState.FAILED, reason="carrier_hangup_unconfirmed")
        else:
            await self._apply(call, CallState.COMPLETED, reason="application_hangup")
        self.runtime.finish(call_id, call.state.value)
        return call

    async def finalize_live_session(self, call_id: UUID, result: LiveFinalizationResult) -> None:
        if not result.confirmed and not result.transport_error:
            result = replace(result, transport_error="live_close_unconfirmed")
        await super().finalize_live_session(call_id, result)
        call = await self.get_call(call_id)
        self.runtime.finish(call_id, call.state.value)
        self.provider.forget(call_id)
        if not result.confirmed:
            with suppress(Exception):
                await self.provider.hangup(call.provider_call_id)

    async def start_outbound_call(self, **kwargs: Any) -> Call:
        raise PolicyDenied("Outbound calls are disabled")

    async def transfer_call(self, call_id: UUID, target: str) -> Call:
        raise PolicyDenied("Human transfer is not implemented")

    async def prune(self) -> None:
        now = monotonic()
        for call_id, created in list(self._created.items()):
            call = await self.calls.get(call_id)
            if call and not call.is_terminal and now - created >= self.settings.max_call_seconds:
                await self.hangup_call(call_id)
        ended = sorted((d.ended_at, cid) for cid, d in self.runtime.dialogues.items() if d.ended_at is not None)
        overflow = max(0, len(ended) - self.settings.max_retained_calls)
        for index, (ended_at, call_id) in enumerate(ended):
            if index < overflow or now - ended_at >= self.settings.retention_seconds:
                self.calls.forget(call_id)
                self.runtime.forget(call_id)
                self._created.pop(call_id, None)

    async def aclose(self) -> None:
        for call_id in list(self._created):
            await self.hangup_call(call_id)
        await super().aclose()
        if self.live is not None:
            await self.live.close()
        for call_id in list(self.runtime.dialogues):
            self.runtime.forget(call_id)


def build_phone(settings: PhoneSettings, service: RouterService) -> InboundPhoneService:
    from openai import AsyncOpenAI

    settings.validate_enabled(service.pipeline.settings.api_key)
    # Validate the SDK surface without making an API request or probing account access.
    try:
        from openai.resources.live.live import AsyncLive
        from twilio.request_validator import RequestValidator
        del AsyncLive, RequestValidator
    except ImportError as exc:
        raise RuntimeError("Install Butaq with the telephony extra to enable phone calls") from exc
    client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url, webhook_secret=settings.openai_webhook_secret, timeout=10, max_retries=0)
    controller = OpenAILiveTelephony(settings, client=client)
    gateway = OpenAILiveGateway(settings, client=client)
    runtime = ButaqPhoneRuntime(service, settings)
    state = InMemoryLiveStateStore()
    runner = LiveSessionRunner(gateway=gateway, runtime=runtime, live_state=state, live_calls=controller, settings=settings, replace_delegations=True)
    phone = InboundPhoneService(settings, runtime=runtime, provider=SignalWireProvider(settings), runner=runner, live_calls=controller, live_state=state)
    phone.live = client  # InboundPhoneService.aclose owns the shared SDK client.
    return phone
