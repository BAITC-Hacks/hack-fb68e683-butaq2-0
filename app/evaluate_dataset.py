"""Replay official development utterances only with explicit --run-models opt-in.

Each utterance has a new session. Expected labels never enter model requests.
No ASR/TTS is used: timings describe text routing, not voice end-to-end latency.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


def prediction(result: dict) -> list[str]:
    if result.get("scenario_id"):
        return list(dict.fromkeys([result["scenario_id"], *result.get("pending_scenario_ids", [])]))
    reason = result.get("reason", "").lower()
    if reason.startswith("out_of_scope:"):
        return ["SYS_OUT_OF_SCOPE"]
    if reason.startswith("goodbye:"):
        return ["SYS_GOODBYE"]
    return ["SYS_UNCLEAR"]


def score(utterances: list[dict], predictions: dict[str, list[str]]) -> dict:
    groups = defaultdict(lambda: {"n": 0, "primary_correct": 0, "full_correct": 0})
    errors, hit, total = [], 0, 0
    for row in utterances:
        expected, got = row["expected"], predictions.get(row["id"], [])
        primary, full = bool(got) and got[0] == expected[0], set(got) == set(expected)
        for name in ("all", f"lang={row['lang']}", f"type={row['type']}"):
            groups[name]["n"] += 1
            groups[name]["primary_correct"] += int(primary)
            groups[name]["full_correct"] += int(full)
        if row["type"] == "multi_intent":
            hit += len(set(got) & set(expected))
            total += len(expected)
        if not full:
            errors.append({"id": row["id"], "expected": expected, "actual": got})
    return {"groups": {name: {**value, "primary_accuracy": value["primary_correct"] / value["n"], "full_match": value["full_correct"] / value["n"]} for name, value in groups.items()}, "multi_intent_recall": hit / total if total else None, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int, default=104)
    parser.add_argument("--run-models", action="store_true")
    args = parser.parse_args()
    utterances = json.loads((args.dataset / "dev_utterances.json").read_text(encoding="utf-8"))["utterances"]
    if not 1 <= args.limit <= len(utterances):
        parser.error(f"--limit must be between 1 and {len(utterances)}")
    utterances = utterances[:args.limit]
    if not args.run_models:
        print(json.dumps({"planned_utterances": len(utterances), "model_calls_made": 0, "notice": "Use --run-models and a new --output directory to run paid inference."}))
        return
    if args.output is None or args.output.exists():
        parser.error("--output must be a new directory; reports are never overwritten")
    with urlopen(args.base_url.rstrip("/") + "/router/catalog/status", timeout=10) as response:
        catalog = json.load(response)
    if not catalog.get("official_ids_complete") or not catalog.get("knowledge_loaded") or not catalog.get("records_loaded"):
        parser.error("Import the complete official starter kit before evaluation")
    args.output.mkdir(parents=True)
    predictions, results = {}, []
    with (args.output / "turns.jsonl").open("x", encoding="utf-8") as log:
        for row in utterances:
            request = Request(args.base_url.rstrip("/") + "/router/text", data=json.dumps({"session_id": f"eval-{uuid4()}", "text": row["text"], "synthesize": False}).encode(), headers={"Content-Type": "application/json"})
            started = perf_counter()
            try:
                with urlopen(request, timeout=90) as response:
                    result = json.load(response)
                predictions[row["id"]] = prediction(result)
                entry = {"id": row["id"], "result": result}
            except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                predictions[row["id"]] = []
                entry = {"id": row["id"], "error": type(exc).__name__} # No provider body or credentials.
            entry["text_request_ms"] = round((perf_counter() - started) * 1000, 1)
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
            log.flush()
            results.append(entry)
    report = {**score(utterances, predictions), "catalog": catalog, "evaluated": len(results), "voice_latency_measured": False}
    for name, value in (("predictions", predictions), ("report", report)):
        with (args.output / f"{name}.json").open("x", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
