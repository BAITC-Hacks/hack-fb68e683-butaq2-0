"""Bundled synthetic insurance dataset, independent of the official starter kit."""

import json
from pathlib import Path
from typing import Any

from app.domain import Catalog

DEMO_DATA_DIR = Path(__file__).parent / "data" / "demo"


def load_demo_catalog() -> Catalog:
    def read(name: str) -> Any:
        return json.loads((DEMO_DATA_DIR / name).read_text(encoding="utf-8"))

    return Catalog.from_payload(read("scenarios.json"), knowledge=read("knowledge_base.json"), backend=read("mock_backend.json"))
