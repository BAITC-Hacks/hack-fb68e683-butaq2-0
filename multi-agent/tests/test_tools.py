"""Voice identifier regression tests against the actual synthetic demo catalog."""

import json
from pathlib import Path

import pytest
from multi_agent.contracts import Catalog, ResolutionContext, RoutingDecision
from multi_agent.identifiers import (
    explicit_demo_ids,
    normalize_demo_id,
    parse_demo_ids,
)
from multi_agent.tools import DemoRecordLookup


@pytest.fixture
def demo_catalog():
    demo = Path(__file__).resolve().parents[2] / "app" / "data" / "demo"
    return Catalog.from_payload(
        json.loads((demo / "scenarios.json").read_text()),
        knowledge=json.loads((demo / "knowledge_base.json").read_text()),
        backend=json.loads((demo / "mock_backend.json").read_text()),
    )


def context(catalog, text, scenario="S05", history=None):
    return ResolutionContext(
        catalog=catalog,
        text=text,
        history=history or [],
        active_scenario=None,
        pending_scenarios=[],
        parameters=[],
        trace_id="trace_identifiers",
        session_id="spoken-identifiers",
        decision=RoutingDecision(
            action="route",
            scenario_id=scenario,
            confidence=0.9,
            reason="Explicit policy query",
            customer_message="Проверю полис.",
        ),
    )


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("демо П-1001", "DEMO-P-1001"),
        ("Demo R 4001", "DEMO-R-4001"),
        ("ДЕМО Р 4001", "DEMO-R-4001"),
        ("demo-p-1001", "DEMO-P-1001"),
        ("Демо П — 1001", "DEMO-P-1001"),
        ("DEMO PO 7001", "DEMO-PO-7001"),
        ("демо ПО 7001", "DEMO-PO-7001"),
        ("DEMO-DOC-1001", "DEMO-DOC-1001"),
        ("DEMO-RC-3001", "DEMO-RC-3001"),
        ("демо У 001", "DEMO-U-001"),
    ],
)
def test_canonicalize_only_explicit_known_spellings(spoken, expected):
    assert normalize_demo_id(spoken) == expected
    assert parse_demo_ids(f"Проверь {spoken}, пожалуйста.") == {expected}
    assert parse_demo_ids(f"Статус {spoken}.") == {expected}


@pytest.mark.parametrize(
    "text",
    [
        "1001",
        "П-1001",
        "DEMO-P",
        "DEMO-1001",
        "DEMO-P-I001",
        "DEMO-P-1O01",
        "DEMO-P-1001.5",
        "DEMO-P-1001/1002",
        "DEMO-P-1001-2",
        "DEMO-P-1001a",
        "XDEMO-P-1001",
        "DEMO-X-1001",
        "DEMO-P1001",
        "Демо пи тысяча один",
        "DEMO-P-100 1",
        "DEMO-P-1001,5",
    ],
)
def test_does_not_guess_ambiguous_or_partial_identifiers(text):
    assert normalize_demo_id(text) is None
    assert parse_demo_ids(text) == set()


def test_voice_policy_lookup_preserves_actual_fixture(demo_catalog):
    current = context(demo_catalog, "Давай проверим полис демо П-1001.")
    lookup = DemoRecordLookup(current)
    result = lookup.lookup("ДЕМО П 1001")
    assert result["status"] == "found"
    assert result["resource"] == "policies"
    assert result["record"]["id"] == "DEMO-P-1001"
    assert result["snapshot_at"] == demo_catalog.backend["snapshot_at"]
    assert lookup.available_records() == [result]


def test_voice_refund_lookup(demo_catalog):
    current = context(demo_catalog, "Я про возврат на Demo R 4001.", "S16")
    result = DemoRecordLookup(current).lookup("DEMO-R-4001")
    assert result["status"] == "found"
    assert result["resource"] == "refunds"


def test_explicit_id_remains_available_in_user_history(demo_catalog):
    current = context(
        demo_catalog,
        "Когда он заканчивается?",
        history=[
            {"role": "user", "content": "Проверь демо П-1001."},
            {"role": "assistant", "content": "DEMO-P-1002"},
        ],
    )
    assert explicit_demo_ids(current) == {"DEMO-P-1001"}
    lookup = DemoRecordLookup(current)
    assert lookup.lookup("DEMO-P-1001")["status"] == "found"
    assert lookup.lookup("DEMO-P-1002")["status"] == "denied"
    assert [item["record"]["id"] for item in lookup.available_records()] == [
        "DEMO-P-1001"
    ]


def test_no_record_selection_from_assistant_or_catalog_examples(demo_catalog):
    current = context(
        demo_catalog,
        "Когда мне придут деньги?",
        "S26",
        history=[{"role": "assistant", "content": "Ваш DEMO-C-5003"}],
    )
    lookup = DemoRecordLookup(current)
    assert lookup.lookup("DEMO-C-5003")["status"] == "denied"
    assert lookup.available_records() == []


def test_wrong_resource_is_denied_without_record_or_join(demo_catalog):
    current = context(demo_catalog, "Возврат Demo R 4001", "S11")
    lookup = DemoRecordLookup(current)
    result = lookup.lookup("DEMO-R-4001")
    assert result["status"] == "denied"
    assert result["reason"] == "resource_not_allowed"
    assert "record" not in result
    assert lookup.available_records() == []


def test_nonexistent_explicit_allowed_id_is_not_found(demo_catalog):
    current = context(demo_catalog, "Полис демо П-9999")
    result = DemoRecordLookup(current).lookup("DEMO-P-9999")
    assert result["status"] == "not_found"
    assert result["record_id"] == "DEMO-P-9999"
    assert "record" not in result


def test_cyrillic_r_never_guesses_visually_similar_policy_code(demo_catalog):
    current = context(demo_catalog, "Демо Р-1001")
    lookup = DemoRecordLookup(current)
    assert explicit_demo_ids(current) == {"DEMO-R-1001"}
    assert lookup.lookup("DEMO-P-1001")["status"] == "denied"
    assert lookup.available_records() == []
