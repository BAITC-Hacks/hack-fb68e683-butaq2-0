"""Separate routing definitions from customer facts and demonstration answers."""

from typing import Any

from .contracts import Catalog, Scenario


def scenario_definition(scenario: Scenario) -> dict[str, Any]:
    """Examples are training illustrations, never evidence about this caller."""
    return {
        "id": scenario.id,
        "title": scenario.title,
        "details": {
            key: value
            for key, value in scenario.details.items()
            if key
            not in {"examples", "id", "scenario_id", "code", "title", "name", "dataset"}
        },
    }


def routing_catalog(catalog: Catalog) -> list[dict[str, Any]]:
    """Keep every scenario and its boundaries; omit execution-only repetition."""
    entries = []
    for scenario in catalog.scenarios.values():
        entry = scenario_definition(scenario)
        details = entry["details"]
        details.pop("actions", None)
        details.pop("action_definitions", None)
        details.pop("knowledge_refs", None)
        parameters = details.get("parameters")
        if isinstance(parameters, list):
            details["parameters"] = [
                {key: value for key, value in item.items() if key != "source"}
                if isinstance(item, dict)
                else item
                for item in parameters
            ]
        entries.append(entry)
    return entries
