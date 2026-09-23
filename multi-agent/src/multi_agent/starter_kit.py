"""Saqta starter-kit normalization and exact, read-only synthetic record access.

This module never chooses a scenario. It runs after the LLM's route decision.
Importing action definitions does not grant permission to execute mutations.
"""
from __future__ import annotations

import re
from typing import Any

DATASET = "Voice Router - Saqta Insurance"
PHONE = re.compile(r"(?<!\w)(?:\+7|8)(?:[ ()-]*\d){10}(?!\d)")
IDENTIFIER = re.compile(r"(?<![\w-])(?:SQ-(?:OGPO|CASCO|TRVL|PROP|NS|DMS)-\d{6}|CL-\d{6}|C\d{3}|P-\d+|\d{12}|\d{3}[A-Z]{2,3}\d{2})(?![\w-])", re.I)
IDENTITY_FIELDS = ("phone", "iin", "client_id", "policy_number", "claim_number", "payment_id", "vehicle_plate")


def references(text: str) -> set[str]:
    values = {m.group().upper() for m in IDENTIFIER.finditer(text)}
    values.update("+7" + re.sub(r"\D", "", m.group())[1:] for m in PHONE.finditer(text))
    return values


def normalize_scenarios(raw: Any, knowledge: Any, backend: Any, slots: Any, actions: Any) -> Any:
    if not isinstance(raw, dict) or not isinstance(raw.get("meta"), dict) or raw["meta"].get("dataset") != DATASET:
        return raw
    for label, value in (("knowledge_base", knowledge), ("mock_backend", backend), ("slots", slots), ("actions", actions)):
        if not isinstance(value, dict) or value.get("meta") != raw["meta"]:
            raise ValueError(f"Official catalogue requires matching {label}.json (dataset, version and date)")
    if not isinstance(knowledge.get("company"), dict):
        raise ValueError("Official knowledge_base must contain company facts")
    for collection in ("clients", "policies", "claims", "payments"):
        rows = backend.get(collection)
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("client_id"), str) or not isinstance(row.get("details", {}), dict) for row in rows):
            raise ValueError(f"Invalid {collection} records in mock_backend.json")
    for label, payload in (("slots", slots), ("actions", actions)):
        entries = payload.get(label)
        if not isinstance(entries, list) or any(not isinstance(item, dict) or not isinstance(item.get("name"), str) for item in entries):
            raise ValueError(f"{label}.json must contain named definitions")
        if len({item["name"] for item in entries}) != len(entries):
            raise ValueError(f"Duplicate {label} definitions")
    slot_map = {item["name"]: item for item in slots["slots"]}
    action_map = {item["name"]: item for item in actions["actions"]}
    entries = []
    if not isinstance(raw.get("scenarios"), list):
        raise ValueError("Official scenarios must be a list")
    for item in raw["scenarios"]:
        if not isinstance(item, dict) or not isinstance(item.get("slots"), dict):
            raise ValueError("Each official scenario must declare its slots")
        for names in (item["slots"].get("required"), item["slots"].get("optional"), item.get("actions")):
            if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
                raise ValueError("Slot and action references must be lists of names")
        names = list(dict.fromkeys(item["slots"]["required"] + item["slots"]["optional"]))
        if item.get("requires_identification"):
            names = list(dict.fromkeys(names + ["phone", "iin", "policy_number", "claim_number"]))
        if any(name not in slot_map for name in names) or any(name not in action_map for name in item["actions"]):
            raise ValueError("Unknown slot or action reference in official catalogue")
        entries.append({**item, "starter_kit": raw["meta"], "parameters": [slot_map[name] for name in names],
                        "action_definitions": [action_map[name] for name in item["actions"]]})
    return {**raw, "scenarios": entries}


def is_starter_scenario(details: dict[str, Any]) -> bool:
    marker = details.get("starter_kit")
    return isinstance(marker, dict) and marker.get("dataset") == DATASET


class StarterRecords:
    """Resolve only explicit user identifiers; never use assistant examples as identity."""

    def __init__(self, context: Any):
        self.context = context
        self.backend = context.catalog.backend or {}
        self.details = context.catalog.scenarios[context.decision.scenario_id].details
        self.enabled = is_starter_scenario(self.details) and self.backend.get("meta", {}).get("dataset") == DATASET

    def available_records(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        # Most recent explicit identity wins. A typo must not fall back to an old client.
        texts = [e["content"] for e in self.context.history if e.get("role") == "user"] + [self.context.text]
        identifiers = next((refs for text in reversed(texts) if (refs := references(text))), set())
        if not identifiers:
            return []
        owners: set[str] = set()
        matched: list[tuple[str, dict[str, Any]]] = []
        for identifier in identifiers:
            hits = []
            for collection in ("clients", "policies", "claims", "payments"):
                for row in self.backend.get(collection, []):
                    fields = {str(row.get(key, "")).upper() for key in IDENTITY_FIELDS if key != "client_id" or collection == "clients"}
                    fields.add(str(row.get("details", {}).get("vehicle_plate", "")).upper())
                    if identifier in fields:
                        hits.append((collection, row))
                        owners.add(row["client_id"])
            if not hits:
                return [{"status": "not_found", "message": "No record for the supplied identifier. Ask the caller to verify it; do not guess."}]
            matched.extend(hits)
        if len(owners) != 1:
            return [{"status": "ambiguous", "message": "Identifiers refer to different clients. Clarify whose request this is."}]
        owner = next(iter(owners))
        allowed = {"clients"}
        action_names = set(self.details.get("actions", []))
        if action_names & {"get_policy", "get_policies", "check_coverage", "get_claim", "check_payment"}:
            allowed.add("policies")
        if "get_claim" in action_names:
            allowed.add("claims")
        if "check_payment" in action_names:
            allowed.add("payments")
        records = []
        for collection in sorted(allowed):
            specific = [row for group, row in matched if group == collection]
            rows = specific or [row for row in self.backend.get(collection, []) if row.get("client_id") == owner]
            for row in rows:
                if collection == "clients":
                    # The voice agent needs identity and preferences, not a full PII dump.
                    row = {key: row[key] for key in ("client_id", "full_name", "city", "bm_class", "preferred_language") if key in row}
                entry = {"status": "found", "resource": collection, "record": row, "snapshot_at": self.backend["meta"]["as_of_date"]}
                if entry not in records:
                    records.append(entry)
        return records


def catalog_status(catalog: Any) -> dict[str, Any]:
    scenarios = list(catalog.scenarios.values()) if catalog is not None else []
    official = bool(scenarios) and all(is_starter_scenario(s.details) for s in scenarios)
    meta = scenarios[0].details.get("starter_kit", {}) if official else {}
    return {"name": meta.get("dataset", "Custom / demo catalogue"), "version": meta.get("version"),
            "as_of_date": meta.get("as_of_date"), "scenario_count": len(scenarios), "official": official,
            "official_ids_complete": official and set(catalog.scenarios) == {f"SC{i:02}" for i in range(1, 41)},
            "knowledge_loaded": bool(catalog and catalog.knowledge), "records_loaded": bool(catalog and catalog.backend),
            "execution_mode": "read_only", "speech_ready": None}
