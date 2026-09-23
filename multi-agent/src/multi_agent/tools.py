"""Read-only, scenario-scoped access to synthetic demonstration records."""

from __future__ import annotations

import re
from typing import Any

from .contracts import ResolutionContext

# These are resource types, not scenario or utterance routing rules.
_COLLECTIONS = {
    "customer": "customers",
    "policy": "policies",
    "application": "applications",
    "payment": "payments",
    "refund": "refunds",
    "claim": "claims",
    "payout": "payouts",
    "delivery": "deliveries",
    "document": "documents",
}


def scenario_knowledge(context: ResolutionContext) -> dict[str, Any]:
    """Select only knowledge references declared by the accepted scenario."""
    scenario = context.catalog.scenarios[context.decision.scenario_id]
    knowledge = context.catalog.knowledge
    if not isinstance(knowledge, dict):
        return {}
    references = scenario.details.get("knowledge_refs", [])
    if not isinstance(references, list):
        return {}
    selected = {
        key: knowledge[key]
        for key in references
        if isinstance(key, str) and key in knowledge
    }
    # Global data interpretation constraints accompany every selected excerpt.
    for key in ("snapshot_at", "demo_rules"):
        if key in knowledge:
            selected[key] = knowledge[key]
    return selected


class DemoRecordLookup:
    """Permit exact, explicitly supplied IDs in supported scenario resources.

    Catalog configuration cannot grant mutations or access to arbitrary resources.
    No guessing the first customer, implicit joins, or cross-customer searches.
    """

    def __init__(self, context: ResolutionContext):
        self._backend = context.catalog.backend
        details = context.catalog.scenarios[context.decision.scenario_id].details
        actions = details.get("actions", {})
        declared_tools = details.get("allowed_tools")
        self.enabled = (
            isinstance(self._backend, dict)
            and self._backend.get("synthetic") is True
            and self._backend.get("read_only") is True
            and isinstance(actions, dict)
            and actions.get("type") == "explain_or_lookup"
            and (
                declared_tools is None
                or (
                    isinstance(declared_tools, list)
                    and "lookup_demo_record" in declared_tools
                )
            )
        )
        parameters = details.get("parameters", [])
        parameter_words: set[str] = set()
        if isinstance(parameters, list):
            for parameter in parameters:
                if isinstance(parameter, dict) and isinstance(
                    parameter.get("name"), str
                ):
                    parameter_words.update(parameter["name"].split("_"))
        self._collections = {
            collection
            for resource, collection in _COLLECTIONS.items()
            if resource in parameter_words
        }
        self.enabled = self.enabled and bool(self._collections)
        user_text = "\n".join(
            [
                entry.get("content", "")
                for entry in context.history
                if entry.get("role") == "user"
            ]
            + [context.text]
        )
        self._explicit_ids = {
            identifier.upper()
            for identifier in re.findall(
                r"\bDEMO-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b", user_text, re.IGNORECASE
            )
        }

    def lookup(self, record_id: str) -> dict[str, Any]:
        identifier = record_id.strip().upper()
        if not self.enabled or identifier not in self._explicit_ids:
            return {
                "status": "denied",
                "reason": "Supply an explicit demo identifier allowed by this scenario.",
            }
        matches = []
        for collection in sorted(self._collections):
            records = self._backend.get(collection, [])
            if not isinstance(records, list):
                continue
            matches.extend(
                {"resource": collection, "record": record}
                for record in records
                if isinstance(record, dict)
                and str(record.get("id", "")).upper() == identifier
            )
        if len(matches) != 1:
            return {"status": "not_found" if not matches else "ambiguous"}
        return {
            "status": "found",
            "snapshot_at": self._backend.get("snapshot_at"),
            **matches[0],
        }
