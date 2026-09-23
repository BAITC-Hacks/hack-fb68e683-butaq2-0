"""Exercise the real SDK against an offline Responses transport."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from multi_agent.contracts import (
    AgentFailure,
    Catalog,
    InvalidDecision,
    ResolutionContext,
    RoutingContext,
    RoutingDecision,
    RuntimeConfig,
    Scenario,
    TurnTimeout,
)
from multi_agent.prompts import ANSWER_PROMPT, ROUTING_PROMPT
from multi_agent.sdk import SdkAgentGateway
from multi_agent.tools import DemoRecordLookup, scenario_knowledge
from openai import AsyncOpenAI


def catalog():
    return Catalog(
        [
            Scenario(
                id="policy",
                title="Policy status",
                details={
                    "parameters": [{"name": "policy_id"}],
                    "knowledge_refs": ["policies"],
                    "actions": {"type": "explain_or_lookup"},
                },
            )
        ],
        knowledge={
            "policies": {"terms": "Demo only"},
            "unrelated": "PRIVATE-KNOWLEDGE",
        },
        backend={
            "synthetic": True,
            "read_only": True,
            "policies": [
                {"id": "DEMO-P-1001", "status": "active"},
                {"id": "DEMO-P-9999", "status": "PRIVATE-RECORD"},
            ],
            "customers": [{"id": "DEMO-U-001", "name": "PRIVATE-CUSTOMER"}],
        },
    )


def decision():
    return RoutingDecision(
        action="route",
        scenario_id="policy",
        confidence=0.95,
        reason="Asked for policy status",
        customer_message="Which policy?",
        language="ru",
    )


def context():
    return RoutingContext(
        catalog=catalog(),
        text="Статус DEMO-P-1001?",
        history=[],
        active_scenario=None,
        pending_scenarios=[],
        parameters=[],
        trace_id="trace_" + "a" * 32,
        session_id="offline-test",
    )


def resolution_context(**changes):
    routing = context()
    return ResolutionContext(**{**vars(routing), **changes}, decision=decision())


def config(**changes):
    return RuntimeConfig(
        model="test-model",
        routing_prompt=ROUTING_PROMPT,
        answer_prompt=ANSWER_PROMPT,
        **changes,
    )


def message(text):
    return {
        "type": "message",
        "id": "msg_test",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


class ResponsesTransport:
    def __init__(self, outputs, *, status="completed"):
        self.status = status
        self.outputs = iter(outputs)
        self.requests = []

    def __call__(self, request):
        self.requests.append(json.loads(request.content))
        output = next(self.outputs)
        return httpx.Response(
            200,
            json={
                "id": f"resp_{len(self.requests)}",
                "object": "response",
                "created_at": 0,
                "status": self.status,
                "model": "test-model",
                "output": output,
                "incomplete_details": {"reason": "max_output_tokens"}
                if self.status == "incomplete"
                else None,
                "parallel_tool_calls": True,
                "tool_choice": "auto",
                "tools": [],
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 5,
                    "total_tokens": 10,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    def client(self):
        return AsyncOpenAI(
            api_key="offline-test",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self)),
        )


@pytest.mark.asyncio
async def test_real_sdk_router_uses_strict_schema_and_scoped_input():
    transport = ResponsesTransport([[message(decision().model_dump_json())]])
    async with transport.client() as client:
        result = await SdkAgentGateway(client).route(context(), config())
    assert result.scenario_id == "policy"
    request = transport.requests[0]
    assert request["model"] == "test-model"
    schema = request["text"]["format"]
    assert schema["type"] == "json_schema"
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert "clarification_question" in schema["schema"]["required"]
    assert "PRIVATE-RECORD" not in json.dumps(request)
    assert "PRIVATE-KNOWLEDGE" not in json.dumps(request)


@pytest.mark.asyncio
async def test_real_sdk_resolution_executes_scoped_function_call():
    transport = ResponsesTransport(
        [
            [
                {
                    "type": "function_call",
                    "id": "fc_test",
                    "call_id": "call_test",
                    "name": "lookup_demo_record",
                    "arguments": '{"record_id":"DEMO-P-1001"}',
                    "status": "completed",
                }
            ],
            [message("Ваш демо-полис активен.")],
        ]
    )
    async with transport.client() as client:
        result = await SdkAgentGateway(client).resolve(resolution_context(), config())
    assert result == "Ваш демо-полис активен."
    assert len(transport.requests) == 2
    first = transport.requests[0]
    assert [tool["name"] for tool in first["tools"]] == ["lookup_demo_record"]
    serialized = json.dumps(transport.requests)
    assert "PRIVATE-RECORD" not in serialized
    assert "PRIVATE-CUSTOMER" not in serialized
    assert "PRIVATE-KNOWLEDGE" not in serialized
    outputs = [
        item
        for item in transport.requests[1]["input"]
        if item.get("type") == "function_call_output"
    ]
    assert json.loads(outputs[0]["output"])["record"]["status"] == "active"


@pytest.mark.parametrize("record_id", ["DEMO-P-9999", "DEMO-U-001", "not-an-id"])
def test_lookup_denies_unsupplied_or_other_resource_ids(record_id):
    current = resolution_context(history=[{"role": "assistant", "content": record_id}])
    assert DemoRecordLookup(current).lookup(record_id)["status"] == "denied"


def test_explicit_user_id_does_not_grant_other_resource_access():
    current = resolution_context(text="DEMO-U-001 DEMO-P-1001")
    assert DemoRecordLookup(current).lookup("DEMO-U-001")["status"] == "denied"
    assert DemoRecordLookup(current).lookup("DEMO-P-1001")["status"] == "found"


@pytest.mark.parametrize(
    "changes",
    [
        {"actions": {"type": "mutation"}},
        {"allowed_tools": []},
        {"allowed_tools": ["delete_policy"]},
        {"parameters": []},
    ],
)
def test_unsupported_tool_policy_fails_closed(changes):
    current = resolution_context()
    current.catalog.scenarios["policy"].details.update(changes)
    assert not DemoRecordLookup(current).enabled
    assert DemoRecordLookup(current).lookup("DEMO-P-1001")["status"] == "denied"


def test_real_backend_is_never_available_to_demo_tool():
    current = resolution_context()
    current.catalog.backend["synthetic"] = False
    assert not DemoRecordLookup(current).enabled


def test_knowledge_is_limited_to_declared_references():
    assert scenario_knowledge(resolution_context()) == {
        "policies": {"terms": "Demo only"}
    }


@pytest.mark.asyncio
async def test_runner_bounds_and_trace_privacy():
    calls = []

    class FakeRunner:
        @staticmethod
        async def run(agent, **kwargs):
            calls.append((agent, kwargs))
            return SimpleNamespace(
                final_output=decision() if agent.name == "Router" else "Done"
            )

    transport = ResponsesTransport([])
    async with transport.client() as client:
        gateway = SdkAgentGateway(client, runner=FakeRunner)
        await gateway.route(context(), config())
        await gateway.resolve(resolution_context(), config(resolution_max_turns=2))
    assert [kwargs["max_turns"] for _, kwargs in calls] == [1, 2]
    for _, kwargs in calls:
        assert kwargs["run_config"].tracing_disabled is True
        assert kwargs["run_config"].trace_include_sensitive_data is False
        assert (
            kwargs["run_config"].trace_metadata["turn_trace_id"] == context().trace_id
        )


@pytest.mark.asyncio
async def test_timeout_cancels_sdk_run():
    cancelled = asyncio.Event()

    class HangingRunner:
        @staticmethod
        async def run(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    transport = ResponsesTransport([])
    async with transport.client() as client:
        with pytest.raises(TurnTimeout):
            await SdkAgentGateway(client, runner=HangingRunner).route(
                context(), config(timeout_seconds=0.01)
            )
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_empty_resolution_answer_fails():
    class EmptyRunner:
        @staticmethod
        async def run(*args, **kwargs):
            return SimpleNamespace(final_output="  ")

    transport = ResponsesTransport([])
    async with transport.client() as client:
        with pytest.raises(AgentFailure):
            await SdkAgentGateway(client, runner=EmptyRunner).resolve(
                resolution_context(), config()
            )


@pytest.mark.asyncio
async def test_client_factory_is_lazy_and_resolved_for_each_agent():
    clients = []

    def client_factory():
        client = object()
        clients.append(client)
        return client

    class FakeRunner:
        @staticmethod
        async def run(agent, **kwargs):
            assert agent.model._client is clients[-1]
            return SimpleNamespace(
                final_output=decision() if agent.name == "Router" else "Done"
            )

    gateway = SdkAgentGateway(client_factory, runner=FakeRunner)
    assert not clients
    await gateway.route(context(), config())
    await gateway.resolve(resolution_context(), config())
    assert len(clients) == 2
    assert clients[0] is not clients[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output,status",
    [
        ([message('{"action":"route"}')], "completed"),
        ([message('{"action":')], "incomplete"),
        (
            [
                {
                    "type": "message",
                    "id": "msg_refusal",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "refusal", "refusal": "Cannot route this request."}
                    ],
                }
            ],
            "completed",
        ),
    ],
)
async def test_real_sdk_refusal_or_malformed_decision_fails(output, status):
    transport = ResponsesTransport([output], status=status)
    async with transport.client() as client:
        with pytest.raises((AgentFailure, InvalidDecision)):
            await SdkAgentGateway(client).route(context(), config())
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_real_sdk_stops_tool_loop_at_max_turns():
    transport = ResponsesTransport(
        [
            [
                {
                    "type": "function_call",
                    "id": "fc_loop",
                    "call_id": "call_loop",
                    "name": "lookup_demo_record",
                    "arguments": '{"record_id":"DEMO-P-1001"}',
                    "status": "completed",
                }
            ],
        ]
    )
    async with transport.client() as client:
        with pytest.raises(AgentFailure):
            await SdkAgentGateway(client).resolve(
                resolution_context(), config(resolution_max_turns=1)
            )
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_scenario_examples_never_become_customer_facts():
    current = resolution_context()
    current.catalog.scenarios["policy"].details["examples"] = [
        {
            "request": "When is DEMO-C-5003 paid?",
            "response": "180000 EXAMPLE-ONLY-FACT",
        }
    ]
    transport = ResponsesTransport(
        [
            [message(decision().model_dump_json())],
            [message("Ваш демо-полис активен.")],
        ]
    )
    async with transport.client() as client:
        gateway = SdkAgentGateway(client)
        await gateway.route(current, config())
        await gateway.resolve(current, config())
    serialized = json.dumps(transport.requests)
    assert "EXAMPLE-ONLY-FACT" not in serialized
    assert "DEMO-C-5003" not in serialized
    assert current.catalog.scenarios["policy"].details["examples"]


@pytest.mark.asyncio
async def test_spoken_identifier_prefetches_record_in_one_resolution_call():
    current = resolution_context(text="Проверь полис демо П-1001")
    transport = ResponsesTransport([[message("Полис DEMO-P-1001 активен.")]])
    async with transport.client() as client:
        result = await SdkAgentGateway(client).resolve(current, config())
    assert result == "Полис DEMO-P-1001 активен."
    assert len(transport.requests) == 1
    payload = json.loads(transport.requests[0]["input"][0]["content"])
    assert payload["utterance"] == current.text
    assert payload["explicit_demo_ids"] == ["DEMO-P-1001"]
    assert payload["verified_records"][0]["record"]["status"] == "active"
    assert "PRIVATE-RECORD" not in json.dumps(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["ru", "kk"])
async def test_fabricated_record_id_is_not_spoken_even_from_previous_assistant(
    language,
):
    current = resolution_context(
        text="Когда придут деньги?",
        history=[{"role": "assistant", "content": "По DEMO-C-5003 одобрено 180000"}],
    )
    current.decision.language = language
    transport = ResponsesTransport([[message("По DEMO-C-5003 вам одобрено 180000.")]])
    async with transport.client() as client:
        answer = await SdkAgentGateway(client).resolve(current, config())
    assert "DEMO-C-5003" not in answer
    assert "180000" not in answer
    assert "нөмірін" in answer if language == "kk" else "номер" in answer
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_routing_prefix_is_stable_across_user_turns():
    current = context()
    transport = ResponsesTransport([[message(decision().model_dump_json())]] * 2)
    async with transport.client() as client:
        gateway = SdkAgentGateway(client)
        await gateway.route(current, config())
        await gateway.route(resolution_context(text="Другой вопрос"), config())
    inputs = [request["input"][0]["content"] for request in transport.requests]
    assert inputs[0].startswith('{"catalog":')
    assert inputs[0].split(',"utterance":')[0] == inputs[1].split(',"utterance":')[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    [
        "gpt-6-luna",
        "gpt-6-luna-2026-09-01",
        "gpt-5.6-terra",
        "gpt-5.6-terra-test-snapshot",
        "gpt-4o-mini",
    ],
)
async def test_model_reasoning_settings_reach_both_agents(model):
    transport = ResponsesTransport([
        [message(decision().model_dump_json())],
        [message("Ваш полис активен.")],
    ])
    settings = config().model_copy(update={"model": model})
    async with transport.client() as client:
        gateway = SdkAgentGateway(client)
        await gateway.route(context(), settings)
        await gateway.resolve(resolution_context(), settings)
    assert len(transport.requests) == 2
    for request in transport.requests:
        assert request["model"] == model
        if model.startswith(("gpt-6-luna", "gpt-5.6-terra")):
            assert request["reasoning"]["effort"] == "low"
        else:
            assert request.get("reasoning") is None


@pytest.mark.asyncio
async def test_both_agents_receive_user_only_language_evidence():
    current = resolution_context(
        text="Задам, какие виды страхования у вас есть?",
        history=[
            {"role": "user", "content": "Сәлем, полисім белсенді ме?"},
            {"role": "assistant", "content": "ASSISTANT-LANGUAGE: Қазақша жауап."},
            {"role": "user", "content": "Расскажите по-русски."},
            {"role": "assistant", "content": "ASSISTANT-LANGUAGE: Тағы не керек?"},
            {"role": "user", "content": " "},
        ],
    )
    current.catalog.scenarios["policy"].title = "CATALOG-LANGUAGE: Полис мерзімі"
    transport = ResponsesTransport(
        [
            [message(decision().model_dump_json())],
            [message("Уточните, пожалуйста, номер полиса.")],
        ]
    )
    async with transport.client() as client:
        gateway = SdkAgentGateway(client)
        await gateway.route(current, config())
        await gateway.resolve(current, config())
    assert len(transport.requests) == 2
    for request in transport.requests:
        payload = json.loads(request["input"][0]["content"])
        assert payload["language_context"] == {
            "current_utterance": current.text,
            "prior_user_utterances": [
                "Сәлем, полисім белсенді ме?",
                "Расскажите по-русски.",
            ],
        }
        # Assistant context remains available for intent, but not as user speech.
        assert payload["recent_dialog"] == current.history
        assert "ASSISTANT-LANGUAGE" not in json.dumps(payload["language_context"])
        assert "CATALOG-LANGUAGE" not in json.dumps(payload["language_context"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance,language",
    [
        ("Задам, какие виды страхования у вас есть?", "ru"),
        ("Кашан ол аякталады?", "kk"),
        ("Когда он оканчается, кашан ол аякталады?", "mixed"),
        ("Я про возврат за двойную оплату на Demo R 4001.", "ru"),
        ("DEMO-P-1001", "unknown"),
    ],
)
async def test_language_input_keeps_noisy_and_mixed_speech_without_alphabet_rules(
    utterance, language
):
    current = resolution_context(text=utterance)
    output = decision().model_copy(update={"language": language})
    transport = ResponsesTransport([[message(output.model_dump_json())]])
    async with transport.client() as client:
        result = await SdkAgentGateway(client).route(current, config())
    payload = json.loads(transport.requests[0]["input"][0]["content"])
    assert payload["utterance"] == utterance
    assert payload["language_context"] == {
        "current_utterance": utterance,
        "prior_user_utterances": [],
    }
    # Semantic language remains the model's decision. These offline fixtures test
    # lossless context and no alphabet-based override, not language accuracy.
    assert result.language == language
    assert len(transport.requests) == 1
