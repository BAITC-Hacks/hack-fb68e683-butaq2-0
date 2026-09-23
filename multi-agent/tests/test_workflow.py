"""Data-driven coverage for every configured starter-kit workflow."""

import copy

import pytest
from app.demo_catalog import load_demo_catalog
from multi_agent.contracts import Catalog, ExtractedParameter
from multi_agent.contracts import RoutingDecision, RuntimeConfig
from multi_agent.orchestrator import VoiceRouterOrchestrator
from multi_agent.simulation import SUPPORTED_ACTIONS
from multi_agent.workflow import ScenarioProgress, advance_workflow, normalize_slot


VALUES = {
    "phone": "+77010000001",
    "iin": "850314300121",
    "policy_number": "SQ-OGPO-104501",
    "claim_number": "CL-500287",
    "vehicle_plate": "777ABC02",
    "culprit_vehicle_plate": "777ABC02",
    "vehicle_type": "car",
    "region": "almaty",
    "drivers_iin": '["850314300121"]',
    "new_driver_iin": "920607400233",
    "car_value": "12000000",
    "car_year": "2022",
    "franchise": "50000",
    "product_type": "ogpo",
    "trip_country": "Turkey",
    "trip_start": "2026-10-10",
    "trip_end": "2026-10-16",
    "travelers_count": "2",
    "traveler_max_age": "42",
    "property_type": "apartment",
    "property_address": "Almaty, Demo 1",
    "sum_insured": "5000000",
    "incident_date": "2026-09-28",
    "incident_description": "Synthetic incident",
    "injured": "false",
    "location": "Almaty",
    "city": "Almaty",
    "email": "demo@example.test",
    "contact_field": "email",
    "new_value": "new@example.test",
    "payment_date": "2026-09-30",
    "payment_amount": "38000",
    "callback_time": "18:00",
    "doctor_specialty": "therapist",
    "service_name": "lab tests",
    "preferred_date": "2026-10-02",
    "company_name": "Demo LLP",
    "employees_count": "25",
    "cancel_reason": "Car sold",
    "complaint_text": "Synthetic complaint",
    "fraud_details": "Synthetic suspicious call",
    "document_type": "contract_copy",
    "topic": "coverage",
}


def parameters(scenario_id, scenario):
    return [
        ExtractedParameter(scenario_id=scenario_id, name=name, value=VALUES[name])
        for name in scenario.details["slots"]["required"]
    ]


@pytest.mark.parametrize("scenario_id", [f"SC{index:02}" for index in range(1, 41)])
def test_every_starter_scenario_reaches_a_terminal_or_confirmation_state(scenario_id):
    catalog = load_demo_catalog()
    scenario = catalog.scenarios[scenario_id]
    progress = ScenarioProgress()
    outcome = advance_workflow(
        catalog=catalog,
        scenario=scenario,
        progress=progress,
        parameters=parameters(scenario_id, scenario),
        text="Complete the configured scenario",
        language="ru",
        session_id=f"test-{scenario_id}",
    )
    assert [trace.name for trace in outcome.traces] == scenario.details["actions"]
    if scenario.details["requires_confirmation"]:
        assert outcome.status == "awaiting_confirmation"
        outcome = advance_workflow(
            catalog=catalog,
            scenario=scenario,
            progress=progress,
            parameters=[],
            text="Да, подтверждаю",
            language="ru",
            session_id=f"test-{scenario_id}",
        )
    assert outcome.completed
    assert not outcome.missing


def test_all_action_adapters_are_referenced_and_side_effect_free():
    catalog = load_demo_catalog()
    before = copy.deepcopy(catalog.backend)
    referenced = {
        action
        for scenario in catalog.scenarios.values()
        for action in scenario.details["actions"]
    }
    assert referenced == set(SUPPORTED_ACTIONS)
    for scenario in catalog.scenarios.values():
        progress = ScenarioProgress()
        advance_workflow(
            catalog=catalog,
            scenario=scenario,
            progress=progress,
            parameters=parameters(scenario.id, scenario),
            text="оператор қажет емес",
            language="kk",
            session_id="immutable",
        )
    assert catalog.backend == before


@pytest.mark.parametrize(
    "name,value,expected",
    [
        ("phone", "+77010000001", "+77010000001"),
        ("employees_count", "25", 25),
        ("injured", "жоқ", False),
        ("drivers_iin", '["850314300121"]', ["850314300121"]),
        ("preferred_date", "2026-10-02", "2026-10-02"),
        ("city", "almaty", "Almaty"),
    ],
)
def test_slot_types_are_normalized(name, value, expected):
    catalog = load_demo_catalog()
    assert normalize_slot(value, catalog.slots[name]) == expected


def test_missing_slot_is_collected_without_handoff():
    catalog = load_demo_catalog()
    scenario = catalog.scenarios["SC02"]
    outcome = advance_workflow(
        catalog=catalog,
        scenario=scenario,
        progress=ScenarioProgress(),
        parameters=[],
        text="Хочу оформить полис",
        language="ru",
        session_id="collect",
    )
    assert outcome.status == "collecting"
    assert outcome.missing
    assert "оператор" not in outcome.deterministic_reply.casefold()


def test_catalog_rejects_unknown_configured_slot_and_action():
    base = load_demo_catalog()
    scenario = base.scenarios["SC01"].model_copy(deep=True)
    scenario.details["actions"] = ["run_arbitrary_code"]
    with pytest.raises(ValueError, match="unknown actions"):
        Catalog(
            [scenario],
            slots={"slots": list(base.slots.values())},
            actions={"actions": list(base.actions.values())},
        )


@pytest.mark.asyncio
async def test_orchestrator_collects_confirms_and_simulates_without_rerouting_confirmation():
    catalog = load_demo_catalog()
    scenario = catalog.scenarios["SC02"]

    class Gateway:
        route_calls = 0
        resolution_calls = []

        async def route(self, context, config):
            self.route_calls += 1
            return RoutingDecision(
                action="route",
                scenario_id="SC02",
                confidence=0.99,
                reason="The customer wants to issue OGPO",
                customer_message="Continue",
                language="ru",
                extracted_parameters=parameters("SC02", scenario),
            )

        async def resolve(self, context, config):
            self.resolution_calls.append(context)
            return (
                "Подтвердите демо-операцию."
                if context.confirmation_required
                else "Демо-операция завершена."
            )

    gateway = Gateway()
    core = VoiceRouterOrchestrator(gateway)
    config = RuntimeConfig(model="test", routing_prompt="route", answer_prompt="answer")
    before = copy.deepcopy(catalog.backend)
    preview = await core.turn(
        session_id="flow", text="Оформите ОГПО", catalog=catalog, config=config
    )
    completed = await core.turn(
        session_id="flow", text="Да, подтверждаю", catalog=catalog, config=config
    )
    assert preview.workflow_status == "awaiting_confirmation"
    assert preview.confirmation_required and not preview.completed
    assert completed.workflow_status == "completed" and completed.completed
    assert any(trace.mode == "simulate" for trace in completed.action_trace)
    assert gateway.route_calls == 1
    assert len(gateway.resolution_calls) == 2
    assert catalog.backend == before
