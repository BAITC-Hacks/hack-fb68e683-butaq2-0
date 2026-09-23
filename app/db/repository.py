"""PostgreSQL repository for live-editable scenarios, grounding facts and prompts."""

from __future__ import annotations

import json
import hashlib
import os

from multi_agent.contracts import DEFAULT_MODEL, Catalog, Scenario
from multi_agent.prompts import ANSWER_PROMPT, ROUTING_PROMPT
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.orm import sessionmaker

from app.demo_catalog import load_demo_catalog

from .models import ScenarioRecord, SettingRecord

CONFIG_KEYS = frozenset(
    {
        "routing_prompt",
        "answer_prompt",
        "model",
        "confidence_threshold",
        "workflow_enabled",
        "max_uncertain_turns",
        "simulation_mode",
    }
)
LEGACY_PROMPT_HASHES = {
    "routing_prompt": "cf3bfb1d1d963246002f3b6f48bb13170e6dd0a2bac9c853fafdf92d649ee2f7",
    "answer_prompt": "b1b6b80a4e375a222d072129273adb2758ee5dcd284e36b02df228f6c839b87f",
}


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
            "model": os.getenv("ROUTER_MODEL", DEFAULT_MODEL),
            "confidence_threshold": os.getenv("ROUTER_CONFIDENCE_THRESHOLD", "0.65"),
            "workflow_enabled": os.getenv("ROUTER_WORKFLOW_ENABLED", "true"),
            "max_uncertain_turns": os.getenv("ROUTER_MAX_UNCERTAIN_TURNS", "2"),
            "simulation_mode": "simulate",
        }
        with self.session.begin() as db:
            for key, value in defaults.items():
                existing = db.get(SettingRecord, key)
                if existing is None:
                    db.add(SettingRecord(key=key, value=value))
                elif (
                    key in LEGACY_PROMPT_HASHES
                    and hashlib.sha256(existing.value.encode()).hexdigest()
                    == LEGACY_PROMPT_HASHES[key]
                ):
                    existing.value = value

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
                ("slot_definitions", {"slots": list(catalog.slots.values())}),
                ("action_definitions", {"actions": list(catalog.actions.values())}),
                ("dev_utterances", catalog.dev_utterances),
                ("dialogs_sample", catalog.dialogs_sample),
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
            db.add_all(
                ScenarioRecord(id=s.id, title=s.title, details=s.details)
                for s in catalog.scenarios.values()
            )
            for key, value in (
                ("knowledge_base", catalog.knowledge),
                ("mock_backend", catalog.backend),
                ("slot_definitions", {"slots": list(catalog.slots.values())}),
                ("action_definitions", {"actions": list(catalog.actions.values())}),
                ("dev_utterances", catalog.dev_utterances),
                ("dialogs_sample", catalog.dialogs_sample),
            ):
                db.merge(
                    SettingRecord(key=key, value=json.dumps(value, ensure_ascii=False))
                )
        return True

    def catalog(self) -> Catalog:
        with self.session() as db:
            rows = db.scalars(select(ScenarioRecord).order_by(ScenarioRecord.id)).all()
            facts = db.scalars(
                select(SettingRecord).where(
                    SettingRecord.key.in_(
                        [
                            "knowledge_base",
                            "mock_backend",
                            "slot_definitions",
                            "action_definitions",
                            "dev_utterances",
                            "dialogs_sample",
                        ]
                    )
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
                slots=json.loads(settings.get("slot_definitions", "null")),
                actions=json.loads(settings.get("action_definitions", "null")),
                dev_utterances=json.loads(settings.get("dev_utterances", "null")),
                dialogs_sample=json.loads(settings.get("dialogs_sample", "null")),
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
        if "workflow_enabled" in values and values[
            "workflow_enabled"
        ].casefold() not in {
            "true",
            "false",
        }:
            raise ValueError("workflow_enabled must be true or false")
        if "max_uncertain_turns" in values:
            try:
                max_uncertain = int(values["max_uncertain_turns"])
            except ValueError:
                max_uncertain = 0
            if not 1 <= max_uncertain <= 10:
                raise ValueError("max_uncertain_turns must be between 1 and 10")
        if "simulation_mode" in values and values["simulation_mode"] != "simulate":
            raise ValueError("Only the safe simulate mode is supported")
        with self.session.begin() as db:
            for key, value in values.items():
                db.merge(SettingRecord(key=key, value=value))
        return self.settings()

    def upsert_scenario(self, scenario: Scenario) -> None:
        current = self.catalog()
        scenarios = [
            scenario if item.id == scenario.id else item
            for item in current.scenarios.values()
        ]
        if scenario.id not in current.scenarios:
            scenarios.append(scenario)
        Catalog(
            scenarios,
            knowledge=current.knowledge,
            backend=current.backend,
            slots={"slots": list(current.slots.values())},
            actions={"actions": list(current.actions.values())},
            dev_utterances=current.dev_utterances,
            dialogs_sample=current.dialogs_sample,
        )
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
