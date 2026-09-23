"""PostgreSQL repository for live-editable scenarios, grounding facts and prompts."""

from __future__ import annotations

import json
import os

from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.orm import sessionmaker

from app.demo_catalog import load_demo_catalog
from multi_agent.contracts import Catalog, Scenario
from multi_agent.prompts import ANSWER_PROMPT, ROUTING_PROMPT

from .models import ScenarioRecord, SettingRecord

CONFIG_KEYS = frozenset(
    {"routing_prompt", "answer_prompt", "model", "confidence_threshold"}
)


class RouterDatabase:
    def __init__(self, dsn: str | None = None):
        url = dsn or os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://butaq:butaq-local@localhost:5433/butaq",
        )
        self.engine = create_engine(url, pool_pre_ping=True)
        self.session = sessionmaker(self.engine, expire_on_commit=False)
        self.seed_defaults()

    def seed_defaults(self) -> None:
        """Insert default prompts once; existing DB edits always take precedence."""
        defaults = {
            "routing_prompt": ROUTING_PROMPT,
            "answer_prompt": ANSWER_PROMPT,
            "model": os.getenv("ROUTER_MODEL", "gpt-4o-mini"),
            "confidence_threshold": os.getenv("ROUTER_CONFIDENCE_THRESHOLD", "0.65"),
        }
        with self.session.begin() as db:
            for key, value in defaults.items():
                if db.get(SettingRecord, key) is None:
                    db.add(SettingRecord(key=key, value=value))

    def replace_catalog(self, catalog: Catalog) -> None:
        """Replace catalog and grounding facts in one transaction."""
        with self.session.begin() as db:
            db.execute(delete(ScenarioRecord))
            db.add_all(
                ScenarioRecord(id=s.id, title=s.title, details=s.details)
                for s in catalog.scenarios.values()
            )
            for key, value in (
                ("knowledge_base", catalog.knowledge),
                ("mock_backend", catalog.backend),
            ):
                db.merge(
                    SettingRecord(key=key, value=json.dumps(value, ensure_ascii=False))
                )

    def seed_demo_catalog(self) -> bool:
        """Seed an empty database once; preserve all existing catalogues and edits."""
        with self.session.begin() as db:
            # Serialize bootstrapping across API workers. Lock releases on rollback too.
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 2840172040})
            if db.scalar(select(func.count()).select_from(ScenarioRecord)):
                return False
            catalog = load_demo_catalog()
            db.add_all(ScenarioRecord(id=s.id, title=s.title, details=s.details) for s in catalog.scenarios.values())
            for key, value in (("knowledge_base", catalog.knowledge), ("mock_backend", catalog.backend)):
                db.merge(SettingRecord(key=key, value=json.dumps(value, ensure_ascii=False)))
        return True

    def catalog(self) -> Catalog:
        with self.session() as db:
            rows = db.scalars(select(ScenarioRecord).order_by(ScenarioRecord.id)).all()
            facts = db.scalars(
                select(SettingRecord).where(
                    SettingRecord.key.in_(["knowledge_base", "mock_backend"])
                )
            ).all()
            if not rows:
                raise ValueError(
                    "No scenarios configured: import scenarios.json via /router/admin/catalog/import-files"
                )
            settings = {row.key: row.value for row in facts}
            return Catalog(
                [
                    Scenario(id=row.id, title=row.title, details=row.details)
                    for row in rows
                ],
                knowledge=json.loads(settings.get("knowledge_base", "null")),
                backend=json.loads(settings.get("mock_backend", "null")),
            )

    def count_scenarios(self) -> int:
        with self.session() as db:
            return db.scalar(select(func.count()).select_from(ScenarioRecord)) or 0

    def settings(self) -> dict[str, str]:
        with self.session() as db:
            rows = db.scalars(
                select(SettingRecord).where(SettingRecord.key.in_(CONFIG_KEYS))
            ).all()
            return {row.key: row.value for row in rows}

    def update_settings(self, values: dict[str, str]) -> dict[str, str]:
        if (
            not values
            or values.keys() - CONFIG_KEYS
            or any(not isinstance(v, str) or not v.strip() for v in values.values())
        ):
            raise ValueError(
                "Settings require non-empty routing_prompt, answer_prompt, model or confidence_threshold"
            )
        if "confidence_threshold" in values:
            try:
                valid = 0 <= float(values["confidence_threshold"]) <= 1
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("confidence_threshold must be between 0 and 1")
        with self.session.begin() as db:
            for key, value in values.items():
                db.merge(SettingRecord(key=key, value=value))
        return self.settings()

    def upsert_scenario(self, scenario: Scenario) -> None:
        with self.session.begin() as db:
            db.merge(
                ScenarioRecord(
                    id=scenario.id, title=scenario.title, details=scenario.details
                )
            )

    def delete_scenario(self, scenario_id: str) -> bool:
        with self.session.begin() as db:
            if (db.scalar(select(func.count()).select_from(ScenarioRecord)) or 0) <= 1:
                raise ValueError("Cannot delete the last scenario")
            row = db.get(ScenarioRecord, scenario_id)
            if row is None:
                return False
            db.delete(row)
            return True
