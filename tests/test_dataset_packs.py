"""Validate the three authored catalogues; model accuracy needs an opt-in live run."""

import importlib.util
import json
import re
from copy import deepcopy
from pathlib import Path
from urllib.error import URLError

import pytest

from app.demo_catalog import DEMO_DATA_DIR
from multi_agent.contracts import Catalog

DATA_ROOT = DEMO_DATA_DIR.parent / "prod"
PACKS = [("auto", "AUT", "AUTO"), ("health", "MED", "HEALTH"), ("travel", "TRV", "TRAVEL")]


def read(folder: Path, name: str):
    return json.loads((folder / name).read_text(encoding="utf-8"))


def evaluator(folder: Path):
    spec = importlib.util.spec_from_file_location("evaluate_" + folder.name, folder / "evaluate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name,prefix,record_prefix", PACKS)
def test_pack_is_complete_bilingual_and_importable(name, prefix, record_prefix):
    folder = DATA_ROOT / name
    raw = read(folder, "scenarios.json")
    knowledge = read(folder, "knowledge_base.json")
    backend = read(folder, "mock_backend.json")
    catalog = Catalog.from_payload(raw, knowledge=knowledge, backend=backend)
    assert len(catalog.scenarios) == 40
    assert set(catalog.scenarios) == {f"{prefix}{number:02}" for number in range(1, 41)}
    assert len({scenario.title for scenario in catalog.scenarios.values()}) == 40
    assert raw["synthetic"] and not raw["official_starter_kit"]
    assert raw["version"] == knowledge["version"] == backend["version"]
    assert knowledge["snapshot_at"] == backend["snapshot_at"]
    assert backend["read_only"] and not knowledge["company"]["real_service"]
    for scenario in catalog.scenarios.values():
        data = scenario.details
        assert data["dataset"] == raw["version"]
        assert data["purpose"] and data["boundaries"] and data["actions"]["steps"]
        assert data["actions"]["executes_mutations"] is False
        assert data["actions"]["requires_confirmation"] == (data["actions"]["type"] == "preview_change")
        assert set(data["knowledge_refs"]) <= knowledge.keys()
        assert scenario.id in knowledge["procedures"]
        assert set(re.findall(r"(?:AUT|MED|TRV)\d{2}", data["boundaries"])) <= catalog.scenarios.keys()
        assert {item["language"] for item in data["examples"]} == {"ru", "kk"}
        assert all(item["request"] and item["response"] for item in data["examples"])
        assert all(item["name"] and item["source"] for item in data["parameters"])
    assert (folder / "README.md").is_file() and (folder / "evaluate.py").is_file()


@pytest.mark.parametrize("name,prefix,record_prefix", PACKS)
def test_pack_records_and_example_references_are_consistent(name, prefix, record_prefix):
    folder = DATA_ROOT / name
    data = read(folder, "mock_backend.json")
    knowledge = read(folder, "knowledge_base.json")
    groups = [rows for rows in data.values() if isinstance(rows, list)]
    records = {row["id"]: row for rows in groups for row in rows}
    assert len(records) == sum(len(rows) for rows in groups)
    assert len(data["customers"]) >= 3
    for row in records.values():
        assert row["id"].startswith(record_prefix + "-")
        for key, value in row.items():
            if key.endswith("_id") and value is not None:
                assert value in records, (row["id"], key, value)
            if key.endswith("_ids"):
                assert set(value) <= records.keys()
        if "product" in row:
            assert row["product"] in knowledge["products"]
        if "policy_ids" in row:
            assert all(records[identifier]["customer_id"] == row["id"] for identifier in row["policy_ids"])
        if "application_ids" in row:
            assert all(records[identifier]["customer_id"] == row["id"] for identifier in row["application_ids"])
        if row.get("payout_id"):
            payout = records[row["payout_id"]]
            assert payout["claim_id"] == row["id"]
            assert payout["amount_kzt"] == row["approved_payout_kzt"]
        for benefit in row.get("benefits", {}).values():
            assert benefit["limit_kzt"] - benefit["used_kzt"] == benefit["remaining_kzt"]
    for scenario in read(folder, "scenarios.json")["scenarios"]:
        examples = json.dumps(scenario["examples"], ensure_ascii=False)
        assert set(re.findall(r"(?:AUTO|HEALTH|TRAVEL)-[A-Z]+\d+", examples)) <= records.keys()


@pytest.mark.parametrize("name,prefix,record_prefix", PACKS)
def test_annotations_cover_all_intents_without_copying_examples(name, prefix, record_prefix):
    folder = DATA_ROOT / name
    raw = read(folder, "scenarios.json")
    scenarios = {row["id"]: row for row in raw["scenarios"]}
    cases = read(folder, "dev_utterances.json")["utterances"]
    dialogs = read(folder, "dialogs_sample.json")["dialogs"]
    assert len(cases) == 48 and len(dialogs) == 10
    assert {case["tests_scenario"] for case in cases if "tests_scenario" in case} == set(scenarios)
    assert len({case["id"] for case in cases}) == len(cases)
    assert len({dialog["id"] for dialog in dialogs}) == len(dialogs)
    assert all(2 <= len(dialog["turns"]) <= 10 for dialog in dialogs)
    assert max(len(dialog["turns"]) for dialog in dialogs) == 10
    examples = {example["request"].strip().casefold() for row in scenarios.values() for example in row["examples"]}
    assert not examples.intersection(case["text"].strip().casefold() for case in cases)
    for turn in cases + [turn for dialog in dialogs for turn in dialog["turns"]]:
        assert turn["text"]
        assert turn["expected_action"] in ("route", "clarify", "handoff")
        if turn["expected_action"] == "route":
            assert turn["expected_scenario_id"] in scenarios
            assert scenarios[turn["expected_scenario_id"]]["actions"]["type"] != "handoff"
        else:
            assert turn["expected_scenario_id"] is None
        assert set(turn.get("expected_pending", [])) <= scenarios.keys()
        assert turn["expected_scenario_id"] not in turn.get("expected_pending", [])


@pytest.mark.parametrize("name,prefix,record_prefix", PACKS)
def test_evaluator_preflight_rejects_wrong_or_changed_catalogue(name, prefix, record_prefix):
    folder = DATA_ROOT / name
    module = evaluator(folder)
    expected = read(folder, "scenarios.json")
    actual = [{"id": row["id"], "title": row["title"], "details": row} for row in expected["scenarios"]]
    module.verify_catalogue(expected, actual)
    with pytest.raises(ValueError):
        module.verify_catalogue(expected, actual[:-1])
    changed = deepcopy(actual)
    changed[0]["details"]["purpose"] = "Changed on server"
    with pytest.raises(ValueError):
        module.verify_catalogue(expected, changed)


@pytest.mark.parametrize("name,prefix,record_prefix", PACKS)
def test_evaluator_preserves_session_and_never_sends_labels(name, prefix, record_prefix, monkeypatch):
    module = evaluator(DATA_ROOT / name)
    calls = []
    sid = prefix + "01"

    def fake_request(url, body):
        calls.append((url, body))
        if len(calls) == 2:
            raise URLError("simulated upstream outage")
        return {"action": "route", "scenario_id": sid, "pending_scenario_ids": [prefix + "02"], "timings": {"routing_ms": 20}, "reply": "demo"}

    monkeypatch.setattr(module, "request_json", fake_request)
    turn = {"text": "test", "expected_action": "route", "expected_scenario_id": sid, "expected_pending": [prefix + "02"], "response_checks": ["manual check"]}
    rows = module.evaluate([{"id": "dialog", "turns": [turn, turn]}, {"id": "next", "turns": [turn]}], "http://example.invalid")
    assert calls[0][1]["session_id"] == calls[1][1]["session_id"]
    assert calls[0][1]["session_id"] != calls[2][1]["session_id"]
    assert all(set(body) == {"text", "session_id", "synthesize"} and body["synthesize"] is False for _, body in calls)
    report = module.summarize(rows)
    assert report["total"] == 3 and report["correct"] == 2 and report["errors"] == 1
    assert report["accuracy"] == pytest.approx(2 / 3)
    assert report["pending_checks"] == report["pending_correct"] == 2
    assert report["routing_ms_mean"] == 20
    assert rows[0]["manual_response_checks"] == ["manual check"]


@pytest.mark.parametrize("name,prefix,record_prefix", PACKS)
def test_evaluator_offline_never_touches_network(name, prefix, record_prefix, monkeypatch, capsys):
    module = evaluator(DATA_ROOT / name)

    def forbidden(*args, **kwargs):
        pytest.fail("Offline evaluation must not contact the network")

    monkeypatch.setattr(module, "request_json", forbidden)
    monkeypatch.setattr("sys.argv", ["evaluate.py", "--limit", "48"])
    module.main()
    report = json.loads(capsys.readouterr().out)
    assert report["groups"] == report["turns"] == 48
    assert report["live"] is False


def test_three_catalogues_are_independent():
    ids = []
    for name, _, _ in PACKS:
        ids.extend(row["id"] for row in read(DATA_ROOT / name, "scenarios.json")["scenarios"])
    assert len(ids) == len(set(ids)) == 120
