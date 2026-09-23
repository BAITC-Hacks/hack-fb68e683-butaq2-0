"""Transaction, isolation and policy tests without model or network calls."""

import asyncio

import pytest
from multi_agent.contracts import (
    AgentFailure,
    Catalog,
    ExtractedParameter,
    InvalidDecision,
    RoutingDecision,
    RuntimeConfig,
    TurnTimeout,
)
from multi_agent.orchestrator import Conversation, VoiceRouterOrchestrator


@pytest.fixture
def catalog():
    return Catalog.from_payload(
        [
            {
                "id": "payment",
                "title": "Payment",
                "parameters": [{"name": "policy_id"}],
            },
            {
                "id": "address",
                "title": "Address",
                "parameters": [{"name": "policy_id"}],
            },
        ]
    )


@pytest.fixture
def config():
    return RuntimeConfig(
        model="test-model", routing_prompt="Route", answer_prompt="Answer"
    )


def decision(sid="payment", **updates):
    payload = {
        "action": "route",
        "scenario_id": sid,
        "confidence": 0.95,
        "reason": "Identified intent",
        "customer_message": "Уточните вопрос",
        "language": "ru",
    }
    payload.update(updates)
    return RoutingDecision(**payload)


class Gateway:
    def __init__(self, *decisions):
        self.decisions = iter(decisions)
        self.routing = []
        self.resolution = []

    async def route(self, context, config):
        self.routing.append(context)
        return next(self.decisions)

    async def resolve(self, context, config):
        self.resolution.append(context)
        return "Ответ по выбранному сценарию."


async def turn(core, catalog, config, text="Помогите", session_id="caller"):
    return await core.turn(
        session_id=session_id, text=text, catalog=catalog, config=config
    )


@pytest.mark.asyncio
async def test_switch_resume_keeps_parameter_ownership(catalog, config):
    gateway = Gateway(
        decision(
            extracted_parameters=[
                ExtractedParameter(scenario_id="payment", name="policy_id", value="P1")
            ]
        ),
        decision(
            "address",
            topic_transition="switch",
            extracted_parameters=[
                ExtractedParameter(scenario_id="address", name="policy_id", value="P2")
            ],
        ),
        decision(topic_transition="resume"),
    )
    core = VoiceRouterOrchestrator(gateway)
    first = await turn(core, catalog, config)
    second = await turn(core, catalog, config)
    third = await turn(core, catalog, config)
    assert first.pending_scenario_ids == []
    assert second.pending_scenario_ids == ["payment"]
    assert third.pending_scenario_ids == ["address"]
    assert [
        [item.value for item in context.parameters] for context in gateway.resolution
    ] == [["P1"], ["P2"], ["P1"]]
    assert {item.scenario_id for item in gateway.routing[-1].parameters} == {
        "payment",
        "address",
    }
    assert len({first.trace_id, second.trace_id, third.trace_id}) == 3
    assert first.timings.routing_ms >= 0 and first.timings.response_ms >= 0


@pytest.mark.asyncio
async def test_low_confidence_then_handoff_never_runs_resolution(catalog, config):
    gateway = Gateway(
        decision(confidence=0.2, language="kk"), decision(confidence=0.2, language="kk")
    )
    core = VoiceRouterOrchestrator(gateway)
    first = await turn(core, catalog, config)
    second = await turn(core, catalog, config)
    assert (first.action, second.action) == ("clarify", "handoff")
    assert first.reply != second.reply
    assert "операторына хабарласыңыз" in second.reply
    assert first.scenario_id is None and second.scenario_id is None
    assert not gateway.resolution
    assert core.sessions["caller"].active_scenario is None


@pytest.mark.asyncio
async def test_explicit_clarify_and_handoff_never_switch_topics(catalog, config):
    gateway = Gateway(
        decision(
            None,
            action="clarify",
            topic_transition="switch",
            clarification_question="Қандай полис?",
        ),
        decision(
            None,
            action="handoff",
            topic_transition="resume",
            customer_message="Already transferred",
        ),
    )
    core = VoiceRouterOrchestrator(gateway)
    first = await turn(core, catalog, config)
    second = await turn(core, catalog, config)
    assert first.reply == "Қандай полис?"
    assert second.reply != "Already transferred"
    assert first.topic_transition == second.topic_transition == "continue"
    assert not gateway.resolution


@pytest.mark.parametrize(
    "bad",
    [
        decision("missing"),
        decision(alternatives=[{"scenario_id": "missing", "reason": "another intent"}]),
        decision(pending_scenario_ids=["missing"]),
        decision(secondary_intents=["missing"]),
        decision(
            extracted_parameters=[
                {"scenario_id": "missing", "name": "policy_id", "value": "P"}
            ]
        ),
        decision(
            extracted_parameters=[
                {"scenario_id": "payment", "name": "unapproved", "value": "P"}
            ]
        ),
        decision(None),
        decision(action="clarify"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_decisions_leave_state_untouched(catalog, config, bad):
    gateway = Gateway(bad)
    core = VoiceRouterOrchestrator(gateway)
    core.sessions["caller"] = Conversation(
        history=[{"role": "user", "content": "earlier"}],
        active_scenario="payment",
        pending_scenarios=["deleted"],
        uncertain_turns=1,
    )
    with pytest.raises(InvalidDecision):
        await turn(core, catalog, config)
    state = core.sessions["caller"]
    assert state.history == [{"role": "user", "content": "earlier"}]
    assert state.pending_scenarios == ["deleted"]
    assert state.active_scenario == "payment"
    assert state.uncertain_turns == 1
    assert not gateway.resolution


@pytest.mark.asyncio
async def test_failed_resolution_rolls_back_pruning_and_extracted_facts(
    catalog, config
):
    class FailingGateway(Gateway):
        async def resolve(self, context, config):
            raise AgentFailure("Provider failed")

    gateway = FailingGateway(
        decision(
            extracted_parameters=[
                {"scenario_id": "payment", "name": "policy_id", "value": "new"}
            ]
        )
    )
    core = VoiceRouterOrchestrator(gateway)
    original = Conversation(
        active_scenario="removed", pending_scenarios=["removed"], uncertain_turns=1
    )
    core.sessions["caller"] = original
    with pytest.raises(AgentFailure):
        await turn(core, catalog, config)
    assert original.active_scenario == "removed"
    assert original.pending_scenarios == ["removed"]
    assert original.uncertain_turns == 1
    assert original.parameters == [] and original.history == []


@pytest.mark.asyncio
async def test_empty_resolution_cannot_commit(catalog, config):
    class EmptyGateway(Gateway):
        async def resolve(self, context, config):
            return "  "

    core = VoiceRouterOrchestrator(EmptyGateway(decision()))
    with pytest.raises(AgentFailure):
        await turn(core, catalog, config)
    assert core.sessions["caller"].history == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_or_cancellation_rolls_back_and_unlocks(catalog, config, cancel):
    entered = asyncio.Event()

    class SlowGateway(Gateway):
        async def resolve(self, context, config):
            entered.set()
            await asyncio.Event().wait()

    core = VoiceRouterOrchestrator(SlowGateway(decision()))
    runtime = config.model_copy(update={"timeout_seconds": 10 if cancel else 0.03})
    task = asyncio.create_task(turn(core, catalog, runtime))
    await entered.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TurnTimeout):
        await task
    state = core.sessions["caller"]
    assert state.history == [] and state.active_scenario is None
    assert not state.lock.locked()
    core.gateway = Gateway(decision())
    assert (await turn(core, catalog, config)).action == "route"


@pytest.mark.asyncio
async def test_same_session_serializes_while_other_session_proceeds(catalog, config):
    first_entered, release_first, other_finished = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    histories = {}

    class ConcurrentGateway(Gateway):
        async def route(self, context, config):
            histories[context.text] = [entry.copy() for entry in context.history]
            if context.text == "first":
                first_entered.set()
                await release_first.wait()
            return decision()

        async def resolve(self, context, config):
            if context.session_id == "other":
                other_finished.set()
            return context.text

    core = VoiceRouterOrchestrator(ConcurrentGateway())
    first = asyncio.create_task(turn(core, catalog, config, text="first"))
    await first_entered.wait()
    second = asyncio.create_task(turn(core, catalog, config, text="second"))
    other = asyncio.create_task(
        turn(core, catalog, config, text="independent", session_id="other")
    )
    await asyncio.wait_for(other_finished.wait(), 0.5)
    assert "second" not in histories
    release_first.set()
    await asyncio.gather(first, second, other)
    assert histories["independent"] == []
    assert histories["second"] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first"},
    ]


@pytest.mark.asyncio
async def test_history_is_bounded_and_removed_catalog_entries_are_pruned_on_success(
    catalog, config
):
    core = VoiceRouterOrchestrator(Gateway(*(decision() for _ in range(12))))
    core.sessions["caller"] = Conversation(
        active_scenario="removed",
        pending_scenarios=["removed"],
        parameters=[
            ExtractedParameter(
                scenario_id="removed", name="policy_id", value="obsolete"
            )
        ],
    )
    for index in range(12):
        await turn(core, catalog, config, text=str(index))
    state = core.sessions["caller"]
    assert len(state.history) == 20
    assert state.history[0]["content"] == "2"
    assert state.pending_scenarios == []
    assert state.parameters == []


@pytest.mark.parametrize("declaration", [42, [{"name": ["invalid", "name"]}]])
@pytest.mark.asyncio
async def test_malformed_parameter_declarations_fail_closed(
    catalog, config, declaration
):
    catalog.scenarios["payment"].details["parameters"] = declaration
    gateway = Gateway(
        decision(
            extracted_parameters=[
                {"scenario_id": "payment", "name": "policy_id", "value": "P1"}
            ]
        )
    )
    core = VoiceRouterOrchestrator(gateway)
    with pytest.raises(InvalidDecision, match="Scenario parameter"):
        await turn(core, catalog, config)
    assert core.sessions["caller"].history == []
    assert not gateway.resolution


@pytest.mark.asyncio
async def test_empty_clarification_cannot_commit_even_from_unvalidated_gateway(
    catalog, config
):
    malformed = decision(None, action="clarify").model_copy(
        update={"customer_message": "   ", "clarification_question": "   "}
    )
    core = VoiceRouterOrchestrator(Gateway(malformed))
    with pytest.raises(AgentFailure, match="empty reply"):
        await turn(core, catalog, config)
    assert core.sessions["caller"].history == []
    assert core.sessions["caller"].uncertain_turns == 0


@pytest.mark.asyncio
async def test_trace_exposes_at_most_three_validated_alternatives(catalog, config):
    gateway = Gateway(
        decision(
            alternatives=[
                {"scenario_id": "address", "reason": f"Alternative {index}"}
                for index in range(5)
            ]
        )
    )
    result = await turn(VoiceRouterOrchestrator(gateway), catalog, config)
    assert len(result.alternatives) == 3


@pytest.mark.parametrize("model_transition", ["continue", "switch", "resume"])
@pytest.mark.parametrize(
    "active,pending,target,expected,expected_pending",
    [
        (None, [], "payment", "continue", []),
        ("payment", [], "payment", "continue", []),
        ("payment", [], "address", "switch", ["payment"]),
        ("payment", ["address"], "address", "resume", ["payment"]),
        (None, ["address"], "address", "resume", []),
    ],
)
@pytest.mark.asyncio
async def test_transition_is_derived_from_selected_scenario_and_state(
    catalog,
    config,
    model_transition,
    active,
    pending,
    target,
    expected,
    expected_pending,
):
    proposed = decision(target, topic_transition=model_transition)
    gateway = Gateway(proposed)
    core = VoiceRouterOrchestrator(gateway)
    core.sessions["caller"] = Conversation(
        active_scenario=active, pending_scenarios=pending.copy()
    )
    result = await turn(core, catalog, config)
    assert result.action == "route"
    assert result.scenario_id == target
    assert result.topic_transition == expected
    assert result.pending_scenario_ids == expected_pending
    assert core.sessions["caller"].active_scenario == target
    assert gateway.resolution[0].decision.topic_transition == expected
    assert proposed.topic_transition == model_transition
    assert len(gateway.routing) == len(gateway.resolution) == 1


@pytest.mark.asyncio
async def test_uncertain_topic_change_clarifies_without_switching(catalog, config):
    gateway = Gateway(decision("address", confidence=0.2, topic_transition="continue"))
    core = VoiceRouterOrchestrator(gateway)
    core.sessions["caller"] = Conversation(active_scenario="payment")
    result = await turn(core, catalog, config)
    assert result.action == "clarify"
    assert result.topic_transition == "continue"
    assert core.sessions["caller"].active_scenario == "payment"
    assert not gateway.resolution


@pytest.mark.asyncio
async def test_corrected_transition_does_not_commit_on_resolution_failure(
    catalog, config
):
    class FailingGateway(Gateway):
        async def resolve(self, context, config):
            assert context.decision.topic_transition == "switch"
            raise AgentFailure("Provider failed")

    core = VoiceRouterOrchestrator(FailingGateway(decision("address")))
    core.sessions["caller"] = Conversation(active_scenario="payment")
    with pytest.raises(AgentFailure):
        await turn(core, catalog, config)
    state = core.sessions["caller"]
    assert state.active_scenario == "payment"
    assert state.pending_scenarios == []
    assert state.history == []
