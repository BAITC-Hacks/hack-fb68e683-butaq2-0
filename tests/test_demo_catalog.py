"""Integrity and executable-workflow checks for the bundled starter kit."""

from app.demo_catalog import STARTER_DATA_DIR, load_demo_catalog
from multi_agent.simulation import SUPPORTED_ACTIONS


def test_starter_catalog_is_complete_bilingual_and_executable() -> None:
    catalog = load_demo_catalog()
    assert set(catalog.scenarios) == {f"SC{index:02}" for index in range(1, 41)}
    assert catalog.slots and catalog.actions
    referenced_actions = set()
    for scenario in catalog.scenarios.values():
        details = scenario.details
        assert details["description"] and isinstance(details["not_this_if"], list)
        assert (
            set(details["slots"]["required"] + details["slots"]["optional"])
            <= catalog.slots.keys()
        )
        assert set(details["examples"]) == {"ru", "kk"}
        referenced_actions.update(details["actions"])
    assert referenced_actions == set(catalog.actions) == set(SUPPORTED_ACTIONS)


def test_irreversible_actions_and_scenarios_require_confirmation() -> None:
    catalog = load_demo_catalog()
    irreversible = {
        name for name, action in catalog.actions.items() if action["irreversible"]
    }
    assert len(irreversible) == 9
    confirming = [
        scenario
        for scenario in catalog.scenarios.values()
        if scenario.details["requires_confirmation"]
    ]
    assert len(confirming) == 14
    for scenario in confirming:
        assert irreversible.intersection(scenario.details["actions"])


def test_starter_annotations_cover_all_scenarios_and_languages() -> None:
    catalog = load_demo_catalog()
    cases = catalog.dev_utterances["utterances"]
    dialogs = catalog.dialogs_sample["dialogs"]
    assert len(cases) == 104 and len(dialogs) == 10
    expected = {
        sid for case in cases for sid in case["expected"] if sid.startswith("SC")
    }
    assert expected == catalog.scenarios.keys()
    assert {case["lang"] for case in cases} == {"ru", "kk", "mixed"}
    assert all(2 <= len(dialog["turns"]) <= 12 for dialog in dialogs)


def test_mock_backend_remains_read_only_during_catalog_load() -> None:
    before = (STARTER_DATA_DIR / "mock_backend.json").read_bytes()
    first = load_demo_catalog().backend
    second = load_demo_catalog().backend
    assert first == second
    assert (STARTER_DATA_DIR / "mock_backend.json").read_bytes() == before
