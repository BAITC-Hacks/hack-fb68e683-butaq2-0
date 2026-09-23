"""Router admin passkeys, backed by WebAuthn and PostgreSQL sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.api.dependencies import get_database
from app.db.models import AdminChallenge, AdminCredential, AdminSession
from app.db.repository import RouterDatabase
from faceid import FaceIDService, FaceIDSettings, SubjectNotFound
from faceid.backends import BackendUnavailable, MultipleFacesDetected, NoFaceDetected
from faceid.images import ImageError

auth_router = APIRouter(prefix="/router/admin/auth", tags=["Admin authentication"])
COOKIE_NAME = "router_admin_session"
Database = Annotated[RouterDatabase, Depends(get_database)]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def origin() -> str:
    return os.getenv("ROUTER_WEBAUTHN_ORIGIN", "http://localhost:3000").rstrip("/")


def rp_id() -> str:
    host = urlparse(origin()).hostname
    if not host:
        raise HTTPException(503, "Configure ROUTER_WEBAUTHN_ORIGIN")
    return host


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def face_service(database: Database) -> FaceIDService:
    # Always persist templates in the same PostgreSQL database as the router.
    url = database.engine.url.render_as_string(hide_password=False).replace("+psycopg", "+asyncpg")
    return _face_service_for_url(url)


@lru_cache(maxsize=4)
def _face_service_for_url(url: str) -> FaceIDService:
    settings = FaceIDSettings(db_url=url, multi_face_policy="reject", store_reference_images=False)
    return FaceIDService(settings)


def require_router_token(x_admin_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("ROUTER_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(503, "Set ROUTER_ADMIN_TOKEN to enable admin access")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(403, "Invalid admin token")


def require_admin(request: Request, database: Database, x_admin_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("ROUTER_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(503, "Set ROUTER_ADMIN_TOKEN to enable admin access")
    if x_admin_token and hmac.compare_digest(x_admin_token, expected):
        return
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(403, "Admin sign-in required")
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("origin") != origin():
        raise HTTPException(403, "Invalid request origin")
    with database.session() as db:
        session = db.get(AdminSession, hashlib.sha256(token.encode()).hexdigest())
        if session is None or session.expires_at <= utcnow():
            raise HTTPException(403, "Admin session expired")


def save_challenge(database: RouterDatabase, challenge: bytes, purpose: str) -> str:
    flow_id = uuid4().hex
    with database.session.begin() as db:
        db.add(AdminChallenge(id=flow_id, challenge=challenge, purpose=purpose, expires_at=utcnow() + timedelta(minutes=5)))
    return flow_id


def consume_challenge(database: RouterDatabase, flow_id: str, purpose: str) -> bytes:
    # DELETE RETURNING makes a challenge single-use even across multiple API workers.
    with database.session.begin() as db:
        row = db.execute(
            delete(AdminChallenge)
            .where(AdminChallenge.id == flow_id, AdminChallenge.purpose == purpose)
            .returning(AdminChallenge.challenge, AdminChallenge.expires_at)
        ).first()
    if row is None or row.expires_at <= utcnow():
        raise HTTPException(400, "Challenge expired or already used")
    return row.challenge


class FinishRequest(BaseModel):
    flow_id: str = Field(min_length=1, max_length=64)
    credential: dict
    label: str = Field(default="Admin device", max_length=80)


def set_admin_cookie(database: RouterDatabase, response: Response, identity: str) -> None:
    token = secrets.token_urlsafe(32)
    with database.session.begin() as db:
        db.add(AdminSession(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            credential_id=identity, expires_at=utcnow() + timedelta(hours=8),
        ))
    response.set_cookie(
        COOKIE_NAME, token, httponly=True, secure=origin().startswith("https://"),
        samesite="lax", max_age=8 * 3600, path="/router/admin",
    )


@auth_router.post("/token/login", dependencies=[Depends(require_router_token)])
def login_with_token(response: Response, database: Database) -> dict[str, str]:
    set_admin_cookie(database, response, "token:admin")
    return {"status": "signed_in"}


@auth_router.post("/register/options", dependencies=[Depends(require_router_token)])
def register_options(database: Database) -> dict:
    with database.session() as db:
        credentials = db.scalars(select(AdminCredential)).all()
    options = generate_registration_options(
        rp_id=rp_id(),
        rp_name="Butaq Admin",
        user_id=b"butaq-router-admin",
        user_name="admin",
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=unb64(item.id)) for item in credentials],
    )
    return {"flow_id": save_challenge(database, options.challenge, "register"), "options": json.loads(options_to_json(options))}


@auth_router.post("/register/finish", dependencies=[Depends(require_router_token)])
def register_finish(body: FinishRequest, database: Database) -> dict[str, str]:
    challenge = consume_challenge(database, body.flow_id, "register")
    try:
        result = verify_registration_response(
            credential=body.credential, expected_challenge=challenge,
            expected_rp_id=rp_id(), expected_origin=origin(), require_user_verification=True,
        )
    except (ValueError, TypeError, WebAuthnException) as exc:
        raise HTTPException(400, "Passkey registration failed") from exc
    with database.session.begin() as db:
        if db.get(AdminCredential, b64(result.credential_id)) is not None:
            raise HTTPException(409, "Passkey already registered")
        db.add(AdminCredential(
            id=b64(result.credential_id), public_key=result.credential_public_key,
            sign_count=result.sign_count, label=body.label.strip() or "Admin device", created_at=utcnow(),
        ))
    return {"status": "registered"}


@auth_router.post("/login/options")
def login_options(database: Database) -> dict:
    with database.session() as db:
        credentials = db.scalars(select(AdminCredential)).all()
    if not credentials:
        raise HTTPException(404, "No admin passkey enrolled. Register with the router token first.")
    options = generate_authentication_options(
        rp_id=rp_id(),
        allow_credentials=[PublicKeyCredentialDescriptor(id=unb64(item.id)) for item in credentials],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return {"flow_id": save_challenge(database, options.challenge, "login"), "options": json.loads(options_to_json(options))}


@auth_router.post("/login/finish")
def login_finish(body: FinishRequest, response: Response, database: Database) -> dict[str, str]:
    challenge = consume_challenge(database, body.flow_id, "login")
    credential_id = body.credential.get("id")
    if not isinstance(credential_id, str):
        raise HTTPException(400, "Invalid passkey")
    with database.session.begin() as db:
        credential = db.get(AdminCredential, credential_id, with_for_update=True)
        if credential is None:
            raise HTTPException(403, "Unknown passkey")
        try:
            verified = verify_authentication_response(
                credential=body.credential, expected_challenge=challenge,
                expected_rp_id=rp_id(), expected_origin=origin(),
                credential_public_key=credential.public_key,
                credential_current_sign_count=credential.sign_count,
                require_user_verification=True,
            )
        except (ValueError, TypeError, WebAuthnException) as exc:
            raise HTTPException(403, "Passkey verification failed") from exc
        credential.sign_count = verified.new_sign_count
    set_admin_cookie(database, response, credential_id)
    return {"status": "signed_in"}


async def face_image(image: UploadFile, maximum: int) -> bytes:
    data = await image.read(maximum + 1)
    if len(data) > maximum:
        raise HTTPException(413, "Image exceeds upload limit")
    return data


def face_error(exc: Exception) -> HTTPException:
    if isinstance(exc, BackendUnavailable):
        return HTTPException(503, str(exc))
    if isinstance(exc, (NoFaceDetected, MultipleFacesDetected, ImageError, ValueError)):
        return HTTPException(422, str(exc))
    return HTTPException(403, "Face not enrolled")


@auth_router.post("/face/enroll", dependencies=[Depends(require_router_token)])
async def enroll_face(
    subject_id: Annotated[str, Form(min_length=2, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")],
    image: Annotated[UploadFile, File()],
    service: Annotated[FaceIDService, Depends(face_service)],
) -> dict[str, str]:
    try:
        await service.enroll("admin:" + subject_id, await face_image(image, service.settings.max_image_bytes))
    except (BackendUnavailable, NoFaceDetected, MultipleFacesDetected, ImageError, ValueError) as exc:
        raise face_error(exc) from exc
    return {"status": "enrolled", "subject_id": subject_id}


@auth_router.post("/face/login")
async def login_face(
    response: Response,
    database: Database,
    subject_id: Annotated[str, Form(min_length=2, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")],
    image: Annotated[UploadFile, File()],
    service: Annotated[FaceIDService, Depends(face_service)],
) -> dict[str, str]:
    try:
        similarity, _, report = await service.verify(
            "admin:" + subject_id, await face_image(image, service.settings.max_image_bytes),
            check_quality=service.settings.openai_assist,
        )
    except (BackendUnavailable, NoFaceDetected, MultipleFacesDetected, ImageError, ValueError, SubjectNotFound) as exc:
        raise face_error(exc) from exc
    if similarity < service.threshold or (report is not None and not report.usable):
        raise HTTPException(403, "Face verification failed")
    set_admin_cookie(database, response, "face:" + subject_id)
    return {"status": "signed_in"}


@auth_router.get("/me", dependencies=[Depends(require_admin)])
def me() -> dict[str, str]:
    return {"status": "authenticated"}


@auth_router.post("/logout")
def logout(request: Request, response: Response, database: Database) -> dict[str, str]:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        if request.headers.get("origin") != origin():
            raise HTTPException(403, "Invalid request origin")
        with database.session.begin() as db:
            db.execute(delete(AdminSession).where(AdminSession.token_hash == hashlib.sha256(token.encode()).hexdigest()))
    response.delete_cookie(COOKIE_NAME, path="/router/admin")
    return {"status": "signed_out"}
