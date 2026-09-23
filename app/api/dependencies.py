"""Shared API dependencies; one router service and database per worker."""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.db.repository import RouterDatabase
from app.services.voice_router import RouterService
from v2v import VoicePipeline


@lru_cache
def get_service() -> RouterService:
    database = RouterDatabase()
    database.seed_demo_catalog()
    return RouterService(VoicePipeline(), database=database)


def get_database(service: Annotated[RouterService, Depends(get_service)]) -> RouterDatabase:
    return service.database
