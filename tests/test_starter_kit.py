"""Official data contracts and isolation; no paid models or database writes."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.import_catalog import load_catalog
from multi_agent.contracts import Catalog
from multi_agent.starter_kit import StarterRecords, catalog_status, references
from multi_agent.tools import scenario_knowledge

DATA = Path(__file__).parents[1] / "case_2" / "voice_router_dataset"


@pytest.fixture
def catalog():
    return load_catalog(DATA)


def context(catalog, text, scenario="SC17", history=None):
    return SimpleNamespace(catalog=catalog, text=text, history=history or [], decision=SimpleNamespace(scenario_id=scenario))


def test_complete_official_catalog_includes_all_slot_and_action_definitions(catalog):
    assert catalog_status(catalog)["official_ids_complete"]
    assert set(catalog.scenarios) == {f"SC{i:02}" for i in range(1, 41)}
    for scenario in catalog.scenarios.values():
        details = scenario.details
        assert {item["name"] for item in details["action_definitions"]} == set(details["actions"])
        assert set(details["slots"]["required"]) <= {item["name"] for item in details["parameters"]}


def test_official_knowledge_is_available_without_exposing_client_rows(catalog):
    knowledge = scenario_knowledge(context(catalog, "Офисы?", "SC33"))
    assert knowledge["company"] == catalog.knowledge["company"]
    assert "clients" not in knowledge
    assert "+77010000001" not in json.dumps(knowledge)


@pytest.mark.parametrize("phone", ["+77010000001", "8 (701) 000-00-01", "+7 701 000 00 01"])
def test_exact_phone_identifies_only_its_client_and_related_claims(catalog, phone):
    records = StarterRecords(context(catalog, f"Статус выплаты, мой телефон {phone}")).available_records()
    assert records
    assert {r["record"]["client_id"] for r in records} == {"C001"}
    assert any(r["resource"] == "claims" for r in records)
    client = next(r["record"] for r in records if r["resource"] == "clients")
    assert "iin" not in client and "email" not in client and "phone" not in client


def test_no_identity_is_guessed_from_examples_or_assistant_text(catalog):
    value = context(catalog, "Где выплата?", history=[{"role": "assistant", "content": "+77010000001"}])
    assert StarterRecords(value).available_records() == []


def test_latest_user_identity_takes_precedence_and_unknown_does_not_reuse_previous(catalog):
    history = [{"role": "user", "content": "+77010000001"}]
    known = StarterRecords(context(catalog, "Статус?", history=history)).available_records()
    assert known and all(r["record"]["client_id"] == "C001" for r in known)
    unknown = StarterRecords(context(catalog, "+77010000099", history=history)).available_records()
    assert unknown[0]["status"] == "not_found" and "record" not in unknown[0]


def test_conflicting_clients_require_clarification(catalog):
    records = StarterRecords(context(catalog, "+77010000001 и +77010000002")).available_records()
    assert records[0]["status"] == "ambiguous"


def test_explicit_policy_lookup_cannot_select_another_policy(catalog):
    records = StarterRecords(context(catalog, "SQ-OGPO-104501", "SC25")).available_records()
    policies = [r["record"]["policy_number"] for r in records if r["resource"] == "policies"]
    assert policies == ["SQ-OGPO-104501"]


def test_missing_official_support_files_rejected_instead_of_silent_partial_import():
    raw = json.loads((DATA / "scenarios.json").read_text())
    with pytest.raises(ValueError, match="requires matching"):
        Catalog.from_payload(raw)


def test_mismatched_versions_and_unknown_references_rejected():
    files = {name: json.loads((DATA / f"{name}.json").read_text()) for name in ("scenarios", "knowledge_base", "mock_backend", "slots", "actions")}
    def load():
        return Catalog.from_payload(files["scenarios"], knowledge=files["knowledge_base"], backend=files["mock_backend"], slots=files["slots"], actions=files["actions"])
    original = copy.deepcopy(files["slots"])
    files["slots"]["meta"]["version"] = "invalid"
    with pytest.raises(ValueError, match="matching slots"):
        load()
    files["slots"] = original
    files["scenarios"]["scenarios"][0]["actions"] = ["invented_action"]
    with pytest.raises(ValueError, match="Unknown slot or action"):
        load()


def test_identifiers_never_match_partial_codes():
    assert references("SQ-OGPO-1045019 C001x CL-500198-extra") == set()
    assert references("Мой полис sq-ogpo-104501") == {"SQ-OGPO-104501"}
