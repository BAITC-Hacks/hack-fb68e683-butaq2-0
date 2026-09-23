"""Voice Router endpoints and authenticated, persistent catalog editing."""

from __future__ import annotations

import hmac
import json
import os
from functools import lru_cache
from time import perf_counter
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.db.repository import RouterDatabase
from app.domain import Catalog, Scenario, TurnResult
from app.services.voice_router import RouterService
from v2v import VoicePipeline

router = APIRouter(prefix="/router", tags=["Voice Router"])


@lru_cache
def get_service() -> RouterService:
    database = RouterDatabase()
    database.seed_demo_catalog()
    return RouterService(VoicePipeline(), database=database)


Service = Annotated[RouterService, Depends(get_service)]


def require_admin(x_admin_token: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("ROUTER_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(503, "Set ROUTER_ADMIN_TOKEN to enable catalog editing")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(403, "Invalid admin token")


Admin = Annotated[None, Depends(require_admin)]


class TextRequest(BaseModel):
    session_id: str = Field(
        default_factory=lambda: str(uuid4()), min_length=1, max_length=128
    )
    text: str = Field(min_length=1, max_length=8000)
    synthesize: bool = False


@router.post("/text", response_model=TurnResult)
async def text_turn(body: TextRequest, service: Service) -> TurnResult:
    return await service.turn(
        session_id=body.session_id, text=body.text, synthesize=body.synthesize
    )


@router.post("/voice", response_model=TurnResult)
async def voice_turn(
    service: Service,
    audio: Annotated[UploadFile, File()],
    session_id: Annotated[str, Form(min_length=1, max_length=128)],
) -> TurnResult:
    if service.database is not None and service.database.count_scenarios() == 0:
        raise HTTPException(503, "No scenarios configured: import the starter-kit catalogue before starting a conversation")
    started = perf_counter()
    transcript = await service.pipeline.transcribe(
        await audio.read(), filename=audio.filename or "audio.wav"
    )
    stt_ms = (perf_counter() - started) * 1000
    if not transcript.text:
        raise HTTPException(422, "No speech detected")
    return await service.turn(
        session_id=session_id, text=transcript.text, stt_ms=stt_ms, synthesize=True
    )


@router.get("/scenarios", response_model=list[Scenario])
async def scenarios(service: Service) -> list[Scenario]:
    if service.database.count_scenarios() == 0:
        return []
    return list(service.database.catalog().scenarios.values())


@router.get("/sessions/{session_id}")
async def session(session_id: str, service: Service) -> dict[str, Any]:
    state = service.sessions.get(session_id)
    if state is None:
        raise HTTPException(404, "Unknown session")
    return {
        "history": state.history,
        "active_scenario": state.active_scenario,
        "pending_scenarios": state.pending_scenarios,
        "uncertain_turns": state.uncertain_turns,
    }


@router.get("/admin/settings")
async def settings(service: Service, _: Admin) -> dict[str, str]:
    return service.database.settings()


@router.patch("/admin/settings")
async def update_settings(
    values: dict[str, str], service: Service, _: Admin
) -> dict[str, str]:
    try:
        return service.database.update_settings(values)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/admin/scenarios/{scenario_id}")
async def save_scenario(
    scenario_id: str, scenario: Scenario, service: Service, _: Admin
) -> Scenario:
    if scenario_id != scenario.id:
        raise HTTPException(422, "Path ID and scenario ID must match")
    service.database.upsert_scenario(scenario)
    return scenario


@router.delete("/admin/scenarios/{scenario_id}")
async def delete_scenario(
    scenario_id: str, service: Service, _: Admin
) -> dict[str, bool]:
    try:
        return {"deleted": service.database.delete_scenario(scenario_id)}
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


class CatalogImport(BaseModel):
    scenarios: list[dict[str, Any]] = Field(min_length=1)
    knowledge_base: Any = None
    mock_backend: Any = None


@router.post("/admin/catalog/import")
async def import_catalog(
    body: CatalogImport, service: Service, _: Admin
) -> dict[str, int]:
    try:
        catalog = Catalog.from_payload(
            body.scenarios, knowledge=body.knowledge_base, backend=body.mock_backend
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    service.database.replace_catalog(catalog)
    return {"count": len(catalog.scenarios)}


@router.post("/admin/catalog/import-files")
async def import_files(
    service: Service,
    _: Admin,
    scenarios: Annotated[UploadFile, File()],
    knowledge_base: Annotated[UploadFile | None, File()] = None,
    mock_backend: Annotated[UploadFile | None, File()] = None,
) -> dict[str, int]:
    async def decode(file: UploadFile | None) -> Any:
        if file is None:
            return None
        content = await file.read(2_000_001)
        if len(content) > 2_000_000:
            raise HTTPException(413, f"{file.filename} exceeds 2 MB")
        try:
            return json.loads(content)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, f"Invalid JSON in {file.filename}") from exc

    try:
        catalog = Catalog.from_payload(
            await decode(scenarios),
            knowledge=await decode(knowledge_base),
            backend=await decode(mock_backend),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    service.database.replace_catalog(catalog)
    return {"count": len(catalog.scenarios)}
