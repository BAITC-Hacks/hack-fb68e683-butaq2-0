#!/usr/bin/env python3
"""Evaluate the live Router API without sending expected labels to the service."""

from __future__ import annotations

import argparse
import json
import statistics
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).parent


def post(base_url: str, session_id: str, text: str) -> dict:
    body = json.dumps(
        {"session_id": session_id, "text": text, "synthesize": False}
    ).encode()
    request = urllib.request.Request(
        base_url.rstrip("/") + "/router/text",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def prediction(result: dict) -> list[str]:
    scenario = result.get("scenario_id")
    if scenario:
        return [scenario, *result.get("pending_scenario_ids", [])]
    reason = str(result.get("reason", ""))
    if reason.startswith("out_of_scope:"):
        return ["SYS_OUT_OF_SCOPE"]
    if reason.startswith("goodbye:"):
        return ["SYS_GOODBYE"]
    return ["SYS_UNCLEAR"]


def evaluate_utterances(base_url: str, limit: int | None) -> list[dict]:
    cases = json.loads((ROOT / "dev_utterances.json").read_text())["utterances"]
    rows = []
    for case in cases[:limit]:
        try:
            result = post(base_url, "eval-" + uuid4().hex, case["text"])
            predicted = prediction(result)
            rows.append(
                {
                    "id": case["id"],
                    "expected": case["expected"],
                    "predicted": predicted,
                    "case_type": case.get("type", "unknown"),
                    "primary_correct": predicted[:1] == case["expected"][:1],
                    "exact": predicted == case["expected"],
                    "routing_ms": result.get("timings", {}).get("routing_ms", 0),
                    "error": None,
                }
            )
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            rows.append(
                {"id": case["id"], "expected": case["expected"], "error": str(exc)}
            )
    return rows


def evaluate_dialogs(base_url: str) -> list[dict]:
    dialogs = json.loads((ROOT / "dialogs_sample.json").read_text())["dialogs"]
    rows = []
    for dialog in dialogs:
        session_id = "dialog-eval-" + uuid4().hex
        for index, turn in enumerate(dialog["turns"]):
            if turn["role"] != "client":
                continue
            try:
                result = post(base_url, session_id, turn["text"])
                predicted = prediction(result)
                expected = turn["scenarios"]
                rows.append(
                    {
                        "id": f"{dialog['dialog_id']}:{index}",
                        "expected": expected,
                        "predicted": predicted,
                        "primary_correct": predicted[:1] == expected[:1],
                        "exact": predicted == expected,
                        "workflow_status": result.get("workflow_status"),
                        "error": None,
                    }
                )
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                rows.append(
                    {
                        "id": f"{dialog['dialog_id']}:{index}",
                        "expected": turn["scenarios"],
                        "error": str(exc),
                    }
                )
    return rows


def summarize(rows: list[dict]) -> dict:
    successful = [row for row in rows if row.get("error") is None]
    latencies = [row["routing_ms"] for row in successful if "routing_ms" in row]
    return {
        "total": len(rows),
        "successful": len(successful),
        "errors": len(rows) - len(successful),
        "primary_accuracy": sum(row["primary_correct"] for row in successful)
        / len(successful)
        if successful
        else 0,
        "exact_accuracy": sum(row["exact"] for row in successful) / len(successful)
        if successful
        else 0,
        "routing_ms_mean": statistics.fmean(latencies) if latencies else None,
        "routing_ms_p95": sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]
        if latencies
        else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dialogs", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    utterances = evaluate_utterances(args.base_url, args.limit)
    dialogs = evaluate_dialogs(args.base_url) if args.dialogs else []
    report = {
        "utterances": summarize(utterances),
        "multi_intent": summarize(
            [row for row in utterances if row.get("case_type") == "multi_intent"]
        ),
        "dialogs": summarize(dialogs) if dialogs else None,
        "failures": [
            row
            for row in utterances + dialogs
            if row.get("error") or not row.get("exact")
        ],
    }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
