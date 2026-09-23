"""Persistent scenario catalog and live-editable router configuration."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .router_service import ANSWER_PROMPT, ROUTING_PROMPT, Catalog, ROOT, Scenario


class RouterDatabase:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("ROUTER_DB_PATH", "/var/lib/butaq/router.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS scenarios (id TEXT PRIMARY KEY, title TEXT NOT NULL, details TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            for key, value in {
                "routing_prompt": ROUTING_PROMPT,
                "answer_prompt": ANSWER_PROMPT,
                "model": os.getenv("ROUTER_MODEL", "gpt-4o-mini"),
                "confidence_threshold": os.getenv("ROUTER_CONFIDENCE_THRESHOLD", "0.65"),
            }.items():
                db.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (key, value))
            if db.execute("SELECT COUNT(*) FROM scenarios").fetchone()[0] == 0:
                try:
                    catalog = Catalog.from_files()
                except FileNotFoundError:
                    catalog = None
                if catalog:
                    self._replace(db, catalog)

    def _replace(self, db: sqlite3.Connection, catalog: Catalog) -> None:
        db.execute("DELETE FROM scenarios")
        db.executemany(
            "INSERT INTO scenarios VALUES (?, ?, ?)",
            [(s.id, s.title, json.dumps(s.details, ensure_ascii=False)) for s in catalog.scenarios.values()],
        )
        for key, value in (("knowledge_base", catalog.knowledge), ("mock_backend", catalog.backend)):
            db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))

    def replace_catalog(self, catalog: Catalog) -> None:
        with self.connect() as db:
            self._replace(db, catalog)

    def catalog(self) -> Catalog:
        with self.connect() as db:
            rows = db.execute("SELECT id, title, details FROM scenarios ORDER BY id").fetchall()
            settings = dict(db.execute("SELECT key, value FROM settings WHERE key IN ('knowledge_base', 'mock_backend')"))
        if not rows:
            raise ValueError("No scenarios configured: import the starter kit in /docs")
        return Catalog(
            [Scenario(id=r["id"], title=r["title"], details=json.loads(r["details"])) for r in rows],
            knowledge=json.loads(settings.get("knowledge_base", "null")),
            backend=json.loads(settings.get("mock_backend", "null")),
        )

    def settings(self) -> dict[str, str]:
        with self.connect() as db:
            return dict(db.execute("SELECT key, value FROM settings WHERE key IN ('routing_prompt', 'answer_prompt', 'model', 'confidence_threshold')"))

    def update_settings(self, values: dict[str, str]) -> dict[str, str]:
        allowed = {"routing_prompt", "answer_prompt", "model", "confidence_threshold"}
        if not values or values.keys() - allowed or any(not value.strip() for value in values.values()):
            raise ValueError("Settings must contain non-empty routing_prompt, answer_prompt, model or confidence_threshold")
        if "confidence_threshold" in values:
            try:
                valid = 0 <= float(values["confidence_threshold"]) <= 1
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("confidence_threshold must be between 0 and 1")
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO settings VALUES (?, ?)", values.items())
        return self.settings()

    def upsert_scenario(self, scenario: Scenario) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO scenarios VALUES (?, ?, ?)",
                (scenario.id, scenario.title, json.dumps(scenario.details, ensure_ascii=False)),
            )

    def delete_scenario(self, scenario_id: str) -> bool:
        with self.connect() as db:
            if db.execute("SELECT COUNT(*) FROM scenarios").fetchone()[0] <= 1:
                raise ValueError("Cannot delete the last scenario")
            return db.execute("DELETE FROM scenarios WHERE id = ?", (scenario_id,)).rowcount > 0
