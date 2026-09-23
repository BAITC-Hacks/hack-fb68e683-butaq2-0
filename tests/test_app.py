from fastapi.testclient import TestClient

from app.main import app


def test_service_and_v2v_routes_are_registered() -> None:
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}

        response = client.get("/v2v/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        paths = client.get("/openapi.json").json()["paths"]
        assert "/v2v/turn" in paths
        assert "/v2v/realtime/client-secret" in paths
