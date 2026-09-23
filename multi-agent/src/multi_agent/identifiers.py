"""Conservative normalization of explicitly spoken synthetic record identifiers.

Only separators, case and a fixed set of spoken Cyrillic code spellings change.
Digits are never corrected, and bare numbers never authorize a record lookup.
"""

from __future__ import annotations

import re

from .contracts import RoutingContext

# These are record-format aliases, never scenario-routing rules. Cyrillic letters
# use phonetic spelling: Р means R, not the visually similar Latin P.
_CODE_ALIASES = {
    "U": "U",
    "У": "U",
    "P": "P",
    "П": "P",
    "A": "A",
    "А": "A",
    "T": "T",
    "Т": "T",
    "R": "R",
    "Р": "R",
    "C": "C",
    "С": "C",
    "L": "L",
    "Л": "L",
    "PO": "PO",
    "ПО": "PO",
    "DOC": "DOC",
    "ДОК": "DOC",
    "RC": "RC",
    "РС": "RC",
}

# The code identifies a resource, never the customer's intent or its status.
DEMO_RESOURCES = {
    "U": "customers",
    "P": "policies",
    "A": "applications",
    "T": "payments",
    "R": "refunds",
    "C": "claims",
    "PO": "payouts",
    "L": "deliveries",
    "DOC": "documents",
    "RC": "documents",
}


def demo_record_resource(identifier: str) -> str:
    """Return the resource type for an already canonical identifier."""
    return DEMO_RESOURCES[identifier.split("-")[1]]


# Require separators between every component. Reject partial matches inside
# extended identifiers, decimal suffixes and letter/number substitutions.
_SEPARATOR = r"(?:[^\S\r\n]*[-‐‑–—][^\S\r\n]*|[^\S\r\n]+)"
_CODE = "|".join(sorted(_CODE_ALIASES, key=len, reverse=True))
_IDENTIFIER = re.compile(
    rf"(?<![\w/‐‑–—-])(?:DEMO|ДЕМО){_SEPARATOR}(?P<code>{_CODE})"
    rf"{_SEPARATOR}(?P<number>[0-9]+)(?![\w/‐‑–—-]|[.,][0-9]|[^\S\r\n]+[0-9])",
    re.IGNORECASE,
)


def _canonical(match: re.Match[str]) -> str:
    return f"DEMO-{_CODE_ALIASES[match['code'].upper()]}-{match['number']}"


def normalize_demo_id(value: str) -> str | None:
    """Canonicalize a complete identifier without guessing missing components."""
    match = _IDENTIFIER.fullmatch(value.strip())
    return _canonical(match) if match else None


def parse_demo_ids(text: str) -> set[str]:
    """Extract explicit, complete synthetic IDs from one utterance."""
    return {_canonical(match) for match in _IDENTIFIER.finditer(text)}


def explicit_demo_ids(context: RoutingContext) -> set[str]:
    """Collect identifiers from user speech only, never model-provided state."""
    texts = [
        entry.get("content", "")
        for entry in context.history
        if entry.get("role") == "user"
    ]
    texts.append(context.text)
    return set().union(*(parse_demo_ids(text) for text in texts))
