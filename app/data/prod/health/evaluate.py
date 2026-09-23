"""Evaluate this synthetic catalogue; offline by default, live model calls opt in."""

from __future__ import annotations

import argparse
import json
import statistics
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def request_json(url: str, body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def verify_catalogue(expected: dict[str, Any], actual: Any) -> None:
    """Do not measure a different catalogue, even when some IDs overlap."""
    if not isinstance(actual, list) or not all(isinstance(row, dict) for row in actual):
        raise ValueError("Invalid /router/scenarios response")
    expected_rows = {row["id"]: row for row in expected["scenarios"]}
    if len(actual) != len(expected_rows) or {row.get("id") for row in actual} != set(expected_rows):
        raise ValueError("Active catalogue differs. Import this dataset into a test instance first.")
    for row in actual:
        if row.get("details") != expected_rows[row["id"]]:
            raise ValueError("Active scenario content differs from this dataset. Evaluation aborted.")


def score_turn(expected: dict[str, Any], actual: Any) -> dict[str, Any]:
    if not isinstance(actual, dict) or actual.get("action") not in ("route", "clarify", "handoff"):
        raise ValueError("Invalid turn response")
    action = actual["action"]
    scenario = actual["scenario_id"]
    if (action == "route" and not isinstance(scenario, str)) or (action != "route" and scenario is not None):
        raise ValueError("Invalid action/scenario combination")
    pending = actual["pending_scenario_ids"]
    timings = actual["timings"]
    if not isinstance(pending, list) or not all(isinstance(item, str) for item in pending):
        raise ValueError("Invalid pending topics")
    if not isinstance(timings, dict) or not isinstance(timings.get("routing_ms"), (int, float)) or timings["routing_ms"] < 0:
        raise ValueError("Missing or invalid routing latency")
    pending_expected = expected.get("expected_pending")
    return {
        "action": action,
        "scenario_id": scenario,
        "correct": action == expected["expected_action"] and scenario == expected["expected_scenario_id"],
        "pending_correct": set(pending_expected) <= set(pending) if pending_expected else None,
        "pending_scenario_ids": pending,
        "timings": timings,
        "reply": actual.get("reply"),
        "manual_response_checks": expected.get("response_checks", []),
    }


def evaluate(groups: list[dict[str, Any]], base_url: str) -> list[dict[str, Any]]:
    results = []
    for group in groups:
        session_id = "eval-" + uuid4().hex
        for index, turn in enumerate(group["turns"], 1):
            row = {"case": group["id"], "turn": index, "expected_action": turn["expected_action"], "expected_scenario_id": turn["expected_scenario_id"]}
            try:
                # Labels and response checks deliberately never leave this process.
                actual = request_json(base_url + "/router/text", {"session_id": session_id, "text": turn["text"], "synthesize": False})
                row.update(score_turn(turn, actual))
            except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
                row.update(correct=False, error=str(exc))
            results.append(row)
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    correct = sum(bool(row["correct"]) for row in results)
    pending = [row["pending_correct"] for row in results if row.get("pending_correct") is not None]
    latency = [row["timings"]["routing_ms"] for row in results if "timings" in row]
    return {
        "total": len(results), "correct": correct,
        "accuracy": correct / len(results) if results else None,
        "errors": sum("error" in row for row in results),
        "pending_checks": len(pending), "pending_correct": sum(pending),
        "routing_ms_mean": round(statistics.mean(latency), 1) if latency else None,
        "routing_ms_max": max(latency) if latency else None,
        "response_safety": "Manual review required; not included in routing accuracy.",
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--live", action="store_true", help="Send billable model calls; no TTS")
    parser.add_argument("--dialogs", action="store_true", help="Measure full multi-turn dialogues")
    parser.add_argument("--limit", type=int, default=10, help="Maximum utterances or complete dialogues")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    url = urllib.parse.urlsplit(args.base_url)
    if url.scheme not in ("http", "https") or not url.netloc or url.username or url.password or url.query or url.fragment:
        parser.error("--base-url must be an HTTP(S) URL without credentials, query or fragment")
    folder = Path(__file__).resolve().parent
    catalogue = load_json(folder / "scenarios.json")
    if args.dialogs:
        groups = load_json(folder / "dialogs_sample.json")["dialogs"]
    else:
        groups = [{"id": row["id"], "turns": [row]} for row in load_json(folder / "dev_utterances.json")["utterances"]]
    groups = groups[:args.limit]
    if not args.live:
        print(json.dumps({"dataset": catalogue["version"], "groups": len(groups), "turns": sum(len(group["turns"]) for group in groups), "live": False, "note": "No network requests. Synthetic labels are not measured accuracy."}, ensure_ascii=False, indent=2))
        return
    base_url = args.base_url.rstrip("/")
    try:
        verify_catalogue(catalogue, request_json(base_url + "/router/scenarios"))
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Catalogue preflight failed: {exc}\n")
    results = evaluate(groups, base_url)
    print(json.dumps({"dataset": catalogue["version"], "synthetic_dev_set": True, **summarize(results)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
