"""Bundled official synthetic starter kit for the Voice Router case."""

import json
from pathlib import Path
from typing import Any

from multi_agent.contracts import Catalog

DEMO_DATA_DIR = Path(__file__).parent / "data" / "demo"
STARTER_DATA_DIR = Path(__file__).parents[1] / "case_2" / "voice_router_dataset"


def load_demo_catalog() -> Catalog:
    def read(name: str) -> Any:
        return json.loads((STARTER_DATA_DIR / name).read_text(encoding="utf-8"))

    return Catalog.from_payload(
        read("scenarios.json"),
        knowledge=read("knowledge_base.json"),
        backend=read("mock_backend.json"),
        slots=read("slots.json"),
        actions=read("actions.json"),
        dev_utterances=read("dev_utterances.json"),
        dialogs_sample=read("dialogs_sample.json"),
    )
