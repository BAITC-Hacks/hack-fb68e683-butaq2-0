from fastapi.testclient import TestClient
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.main import app
from app.api.routes import get_service


def test_voice_router_routes_are_registered() -> None:
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}

        paths = client.get("/openapi.json").json()["paths"]
        assert "/router/text" in paths
        assert "/router/voice" in paths
        assert "/router/admin/catalog/import-files" in paths
        assert "/v2v/turn" not in paths

        preflight = client.options(
            "/router/text",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert preflight.status_code == 200
        assert (
            preflight.headers["access-control-allow-origin"] == "http://localhost:3000"
        )


def test_empty_catalog_rejects_voice_before_transcription() -> None:
    transcribe = AsyncMock()
    service = SimpleNamespace(database=SimpleNamespace(count_scenarios=Mock(return_value=0)), pipeline=SimpleNamespace(transcribe=transcribe))
    app.dependency_overrides[get_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.post("/router/voice", data={"session_id": "empty-catalog"}, files={"audio": ("voice.webm", b"unused", "audio/webm")})
        assert response.status_code == 503
        assert "No scenarios configured" in response.json()["detail"]
        transcribe.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_service, None)
