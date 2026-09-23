"""Integrity checks for the synthetic dataset, not claims of LLM accuracy."""

import json
import re

from app.demo_catalog import DEMO_DATA_DIR, load_demo_catalog


def test_scenarios_are_complete_and_bilingual() -> None:
    catalog = load_demo_catalog()
    assert len(catalog.scenarios) == 40
    for scenario in catalog.scenarios.values():
        details = scenario.details
        assert details["dataset"] == "butaq-demo-1"
        assert details["purpose"] and details["boundaries"]
        assert details["actions"]["executes_mutations"] is False
        assert {e["language"] for e in details["examples"]} == {"ru", "kk"}
        assert all(e["request"] and e["response"] for e in details["examples"])
        assert all(ref in catalog.knowledge for ref in details["knowledge_refs"])
        assert set(re.findall(r"S\d{2}", details["boundaries"])) <= catalog.scenarios.keys()


def test_mock_records_have_consistent_links_and_states() -> None:
    data = load_demo_catalog().backend
    groups = [value for value in data.values() if isinstance(value, list)]
    records = {row["id"]: row for group in groups for row in group}
    assert len(records) == sum(len(group) for group in groups)
    assert data["synthetic"] and data["read_only"]
    for row in records.values():
        for key in ("customer_id", "policy_id", "application_id", "payment_id", "refund_id", "claim_id", "payout_id", "document_id", "delivery_id", "receipt_id", "duplicate_of"):
            if row.get(key):
                assert row[key] in records, (row["id"], key)
        for key in ("policy_ids", "application_ids", "payment_ids"):
            assert all(identifier in records for identifier in row.get(key, []))
    assert records["DEMO-A-2001"]["policy_id"] is None
    assert records["DEMO-T-3001"]["status"] == "settled"
    assert records["DEMO-R-4001"]["status"] == "sent"
    assert records["DEMO-R-4001"]["credited_at"] is None
    assert records["DEMO-C-5003"]["approved_payout_kzt"] == records["DEMO-PO-7001"]["amount_kzt"]


def test_evaluation_labels_and_dialogue_lengths() -> None:
    catalog = load_demo_catalog()
    cases = json.loads((DEMO_DATA_DIR / "dev_utterances.json").read_text())["utterances"]
    dialogs = json.loads((DEMO_DATA_DIR / "dialogs_sample.json").read_text())["dialogs"]
    assert len(cases) == 48
    assert len(dialogs) == 10
    assert {case["tests_scenario"] for case in cases if "tests_scenario" in case} == catalog.scenarios.keys()
    assert all(1 <= len(dialog["turns"]) <= 10 for dialog in dialogs)
    for turn in cases + [turn for dialog in dialogs for turn in dialog["turns"]]:
        if turn["expected_action"] == "route":
            assert turn["expected_scenario_id"] in catalog.scenarios
        else:
            assert turn["expected_scenario_id"] is None
        assert set(turn.get("expected_pending", [])) <= catalog.scenarios.keys()
