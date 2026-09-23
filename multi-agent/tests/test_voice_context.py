"""Native voice captions provide context without claiming playback or authorizing IDs."""

from types import SimpleNamespace

import pytest
from multi_agent.contracts import Catalog, RoutingDecision
from multi_agent.sdk import SdkAgentGateway

from app.services.voice_router import RouterService


@pytest.mark.asyncio
async def test_observed_voice_context_reaches_both_agents_without_becoming_delivered_history():
    observed = [
        {"role": "assistant", "content": "Хотите узнать срок действия полиса?"},
        {"role": "user", "content": "да"},
    ]
    contexts = []

    class Gateway:
        async def route(self, context, config):
            contexts.append(context)
            return RoutingDecision(action="route", scenario_id="policy", confidence=0.95,
                                   reason="User confirms policy expiry question",
                                   customer_message="Уточните полис", language="ru")

        async def resolve(self, context, config):
            contexts.append(context)
            return "Назовите номер полиса."

    service = RouterService(SimpleNamespace(client=object()), gateway=Gateway(),
                            catalog=Catalog.from_payload([{"id": "policy", "title": "Policy"}]))
    await service.turn(session_id="native", text="да", voice_context=observed,
                       delivery_id="delegation-1")
    assert len(contexts) == 2
    for context in contexts:
        assert context.text == "да"
        assert context.voice_context == observed
        assert not any(entry["role"] == "assistant" for entry in context.history)
        payload = SdkAgentGateway._conversation(context)
        assert payload["observed_native_voice_context"] == observed
    # Neither the native caption nor the generated backend draft is known to have played.
    assert service.sessions["native"].history == [{"role": "user", "content": "да"}]
    await service.orchestrator.interrupt_delivery("native", "delegation-1")
    assert service.sessions["native"].pending_delivery is None
    assert service.sessions["native"].history == [{"role": "user", "content": "да"}]
    observed[0]["content"] = "Caller-owned list changed later"
    assert contexts[0].voice_context[0]["content"] == "Хотите узнать срок действия полиса?"


def test_native_captions_do_not_authorize_demo_records_or_set_user_language():
    from multi_agent.contracts import RoutingContext

    context = RoutingContext(
        catalog=Catalog.from_payload([{"id": "policy", "title": "Policy"}]),
        text="да", history=[], active_scenario=None, pending_scenarios=[],
        parameters=[], trace_id="trace", session_id="native",
        voice_context=[{"role": "assistant", "content": "DEMO-P-1001 полисін тексерейін бе?"}],
    )
    payload = SdkAgentGateway._conversation(context)
    assert payload["explicit_demo_ids"] == []
    assert payload["current_record_references"] == []
    assert payload["language_context"] == {"current_utterance": "да", "prior_user_utterances": []}
