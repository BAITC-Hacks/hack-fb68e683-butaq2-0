"""Admin authentication, enrollment boundaries and session behavior."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api import admin_auth
from app.api.routes import get_service
from app.main import app
from app.services.voice_router import RouterService
from tests.test_database import database  # noqa: F401 - isolated migrated schema fixture
from tests.test_router import StubPipeline


class FakeFaceService:
    settings = SimpleNamespace(max_image_bytes=1024, openai_assist=False)
    threshold = 0.5

    def __init__(self):
        self.subject = None

    async def enroll(self, subject_id, image):
        self.subject = subject_id
        assert image == b"example-image"

    async def verify(self, subject_id, image, *, check_quality):
        assert check_quality is False
        assert image == b"example-image"
        return (0.7 if subject_id == self.subject else 0.2), None, None


def test_face_admin_requires_token_and_issues_revocable_session(database, monkeypatch):
    monkeypatch.setenv("ROUTER_ADMIN_TOKEN", "secret-for-tests")
    monkeypatch.setenv("ROUTER_WEBAUTHN_ORIGIN", "http://localhost:3000")
    service = RouterService(StubPipeline(), database=database)
    face = FakeFaceService()
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[admin_auth.face_service] = lambda: face
    try:
        with TestClient(app) as client:
            image = {"image": ("face.jpg", b"example-image", "image/jpeg")}
            assert client.post("/router/admin/auth/face/enroll", data={"subject_id": "owner"}, files=image).status_code == 403
            assert client.post("/router/admin/auth/face/login", data={"subject_id": "owner"}, files=image).status_code != 200
            enrolled = client.post("/router/admin/auth/face/enroll", headers={"X-Admin-Token": "secret-for-tests"}, data={"subject_id": "owner"}, files=image)
            assert enrolled.status_code == 200, enrolled.text
            assert face.subject == "admin:owner"
            assert client.post("/router/admin/auth/face/login", data={"subject_id": "stranger"}, files=image).status_code == 403
            signed_in = client.post("/router/admin/auth/face/login", data={"subject_id": "owner"}, files=image)
            assert signed_in.status_code == 200, signed_in.text
            assert "httponly" in signed_in.headers["set-cookie"].lower()
            assert client.get("/router/admin/settings").status_code == 200
            assert client.patch("/router/admin/settings", json={"model": "changed"}).status_code == 403
            assert client.patch("/router/admin/settings", headers={"Origin": "http://localhost:3000"}, json={"model": "changed"}).status_code == 200
            assert client.post("/router/admin/auth/logout", headers={"Origin": "http://localhost:3000"}).status_code == 200
            assert client.get("/router/admin/settings").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_passkey_registration_options_require_router_token(database, monkeypatch):
    monkeypatch.setenv("ROUTER_ADMIN_TOKEN", "secret-for-tests")
    app.dependency_overrides[get_service] = lambda: RouterService(StubPipeline(), database=database)
    try:
        with TestClient(app) as client:
            assert client.post("/router/admin/auth/register/options").status_code == 403
            issued = client.post("/router/admin/auth/register/options", headers={"X-Admin-Token": "secret-for-tests"})
            assert issued.status_code == 200, issued.text
            flow_id = issued.json()["flow_id"]
            assert issued.json()["options"]["challenge"]
            assert admin_auth.consume_challenge(database, flow_id, "register")
            try:
                admin_auth.consume_challenge(database, flow_id, "register")
                assert False, "challenge should be single-use"
            except Exception as exc:
                assert getattr(exc, "status_code", None) == 400
            another = client.post("/router/admin/auth/register/options", headers={"X-Admin-Token": "secret-for-tests"}).json()["flow_id"]
            invalid = client.post(
                "/router/admin/auth/register/finish",
                headers={"X-Admin-Token": "secret-for-tests"},
                json={"flow_id": another, "credential": {}},
            )
            assert invalid.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_token_login_keeps_admin_access_across_requests(database, monkeypatch):
    monkeypatch.setenv("ROUTER_ADMIN_TOKEN", "secret-for-tests")
    monkeypatch.setenv("ROUTER_WEBAUTHN_ORIGIN", "http://localhost:3000")
    app.dependency_overrides[get_service] = lambda: RouterService(StubPipeline(), database=database)
    try:
        with TestClient(app) as client:
            assert client.post("/router/admin/auth/token/login", headers={"X-Admin-Token": "wrong"}).status_code == 403
            assert client.post("/router/admin/auth/token/login", headers={"X-Admin-Token": "secret-for-tests"}).status_code == 200
            assert client.get("/router/admin/auth/me").status_code == 200
            assert client.get("/router/admin/settings").status_code == 200
            assert client.post("/router/admin/auth/logout", headers={"Origin": "http://localhost:3000"}).status_code == 200
            assert client.get("/router/admin/auth/me").status_code == 403
    finally:
        app.dependency_overrides.clear()
