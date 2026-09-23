"""PostgreSQL + Alembic integration; run with TEST_DATABASE_URL set."""

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.api.routes import get_service
from app.db.repository import RouterDatabase
from app.domain import Catalog, Scenario
from app.main import app
from app.services.voice_router import RouterService
from tests.test_router import StubPipeline, decision


@pytest.fixture
def database(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run the PostgreSQL integration test")
    schema = "test_" + uuid4().hex[:16]
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    test_url = url + ("&" if "?" in url else "?") + f"options=-csearch_path%3D{schema}"
    try:
        monkeypatch.setenv("DATABASE_URL", test_url)
        command.upgrade(Config("alembic.ini"), "head")
        repo = RouterDatabase(test_url)
        yield repo
        repo.engine.dispose()
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()


def test_migration_catalog_replacement_and_live_settings(
    database: RouterDatabase,
) -> None:
    assert database.count_scenarios() == 0
    assert "code-switching" in database.settings()["routing_prompt"]
    scenarios = [
        Scenario(
            id=str(n),
            title=f"Scenario {n}",
            details={"id": str(n), "examples": ["Сәлем"]},
        )
        for n in range(40)
    ]
    database.replace_catalog(Catalog(scenarios, knowledge={"company": "synthetic"}))
    assert database.count_scenarios() == 40
    assert database.catalog().knowledge == {"company": "synthetic"}
    assert database.catalog().scenarios["39"].details["examples"] == ["Сәлем"]
    database.update_settings(
        {"routing_prompt": "Updated live routing prompt", "confidence_threshold": "0.8"}
    )
    assert database.settings()["routing_prompt"] == "Updated live routing prompt"
    with pytest.raises(ValueError):
        database.update_settings({"confidence_threshold": "1.1"})
    assert database.settings()["confidence_threshold"] == "0.8"
    database.upsert_scenario(
        Scenario(id="39", title="Updated", details={"purpose": "new"})
    )
    assert database.catalog().scenarios["39"].title == "Updated"
    assert database.delete_scenario("39") is True
    assert database.count_scenarios() == 39


def test_admin_import_and_live_prompt_are_used_by_next_turn(
    database: RouterDatabase, monkeypatch
) -> None:
    monkeypatch.setenv("ROUTER_ADMIN_TOKEN", "test-secret")
    pipeline = StubPipeline([decision("a"), "Номер заявки, пожалуйста."])
    service = RouterService(pipeline, database=database)
    app.dependency_overrides[get_service] = lambda: service
    try:
        with TestClient(app) as client:
            assert (
                client.patch(
                    "/router/admin/settings", json={"model": "new-model"}
                ).status_code
                == 403
            )
            assert (
                client.post("/router/text", json={"text": "Төлем қайда?"}).status_code
                == 503
            )
            headers = {"X-Admin-Token": "test-secret"}
            uploaded = client.post(
                "/router/admin/catalog/import-files",
                headers=headers,
                files={
                    "scenarios": (
                        "scenarios.json",
                        b'{"scenarios":[{"scenario_id":"a","name":"Payment"}]}',
                        "application/json",
                    )
                },
            )
            assert uploaded.status_code == 200
            assert uploaded.json() == {"count": 1}
            changed = client.patch(
                "/router/admin/settings",
                headers=headers,
                json={
                    "routing_prompt": "Updated prompt from PostgreSQL",
                    "model": "new-model",
                },
            )
            assert changed.status_code == 200
            routed = client.post(
                "/router/text", json={"session_id": "one", "text": "Төлем қайда?"}
            )
            assert routed.status_code == 200
            assert routed.json()["scenario_id"] == "a"
            assert (
                pipeline.responses.calls[0]["instructions"]
                == "Updated prompt from PostgreSQL"
            )
            assert pipeline.responses.calls[0]["model"] == "new-model"
    finally:
        app.dependency_overrides.clear()
