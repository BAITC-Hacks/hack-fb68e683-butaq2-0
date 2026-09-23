"""Import boundaries using an in-memory repository, without overwriting a real DB."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_service
from app.main import app
from multi_agent.contracts import Catalog
from tests.test_starter_kit import DATA


class MemoryCatalog:
    def __init__(self):
        self.current = Catalog.from_payload([{"id": "existing", "title": "Keep me"}])
        self.writes = 0

    def catalog(self):
        return self.current

    def count_scenarios(self):
        return len(self.current.scenarios)

    def replace_catalog(self, catalog):
        self.current = catalog
        self.writes += 1


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("ROUTER_ADMIN_TOKEN", "test-only-token")
    database = MemoryCatalog()
    app.dependency_overrides[get_service] = lambda: SimpleNamespace(database=database)
    try:
        with TestClient(app) as client:
            yield client, database
    finally:
        app.dependency_overrides.pop(get_service, None)


def files():
    return {name: (f"{name}.json", (DATA / f"{name}.json").read_bytes(), "application/json") for name in ("scenarios", "knowledge_base", "mock_backend", "slots", "actions")}


def test_import_requires_admin_and_complete_matching_dataset(api):
    client, database = api
    assert client.post("/router/admin/catalog/import-files", files=files()).status_code == 403
    response = client.post("/router/admin/catalog/import-files", files={"scenarios": files()["scenarios"]}, headers={"X-Admin-Token": "test-only-token"})
    assert response.status_code == 422
    assert database.writes == 0
    assert set(database.current.scenarios) == {"existing"}


def test_empty_catalog_status_does_not_become_internal_server_error(api, monkeypatch):
    client, database = api
    monkeypatch.setattr(database, "count_scenarios", lambda: 0)
    response = client.get("/router/catalog/status")
    assert response.status_code == 200
    assert response.json()["scenario_count"] == 0
    assert not response.json()["official_ids_complete"]


@pytest.mark.parametrize("format", ["files", "json"])
def test_complete_import_and_public_status_contain_no_customer_records(api, format):
    client, database = api
    headers = {"X-Admin-Token": "test-only-token"}
    if format == "files":
        response = client.post("/router/admin/catalog/import-files", files=files(), headers=headers)
    else:
        response = client.post("/router/admin/catalog/import", json={name: json.loads(value[1]) for name, value in files().items()}, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"count": 40}
    assert database.writes == 1
    status = client.get("/router/catalog/status")
    assert status.status_code == 200
    assert status.json()["official_ids_complete"]
    assert status.json()["speech_ready"] is None
    assert "C001" not in status.text and "+77010000001" not in status.text
    assert database.current.scenarios["SC17"].details["action_definitions"]
