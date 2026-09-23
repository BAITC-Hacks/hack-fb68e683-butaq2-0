from fastapi.testclient import TestClient

from app.main import app


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
