"""Evaluate generated insurance fixtures against a running router; opt-in live calls."""

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--live", action="store_true", help="Send billable LLM requests; TTS is disabled")
    parser.add_argument("--dialogs", action="store_true", help="Evaluate multi-turn context instead of isolated utterances")
    parser.add_argument("--limit", type=int, default=10, help="Maximum utterances, or complete dialogs when --dialogs is set")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    folder = Path(__file__).parent
    if args.dialogs:
        groups = json.loads((folder / "dialogs_sample.json").read_text())["dialogs"]
    else:
        groups = [{"id": item["id"], "turns": [item]} for item in json.loads((folder / "dev_utterances.json").read_text())["utterances"]]
    groups = groups[:args.limit]
    if not args.live:
        print(f"Loaded {len(groups)} cases. No API requests sent. Add --live to measure LLM routing; these are synthetic dev labels, not jury tests.")
        return
    results = []
    for group in groups:
        session_id = f"eval-{uuid4().hex}"
        for index, turn in enumerate(group["turns"]):
            body = json.dumps({"session_id": session_id, "text": turn["text"], "synthesize": False}).encode()
            request = urllib.request.Request(args.base_url.rstrip("/") + "/router/text", data=body, headers={"Content-Type": "application/json"})
            row = {"case": group["id"], "turn": index + 1, "expected_action": turn["expected_action"], "expected_scenario_id": turn["expected_scenario_id"]}
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    actual = json.load(response)
                pending_ok = set(turn.get("expected_pending", [])) <= set(actual.get("pending_scenario_ids", []))
                row.update(action=actual["action"], scenario_id=actual["scenario_id"], correct=actual["action"] == turn["expected_action"] and actual["scenario_id"] == turn["expected_scenario_id"], pending_correct=pending_ok, timings=actual["timings"])
            except (urllib.error.URLError, ValueError, KeyError, TimeoutError) as exc:
                row.update(correct=False, error=str(exc))
            results.append(row)
    correct = sum(row["correct"] for row in results)
    print(json.dumps({"synthetic_dev_set": True, "total": len(results), "correct": correct, "accuracy": correct / len(results) if results else 0, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
