"""faceid -- drop-in face enrollment / verification / identification for FastAPI.

Identity matching runs locally on an ArcFace-class model (InsightFace, with a
dlib fallback). OpenAI is used only as an optional capture-quality and
presentation-attack assist.

Quick start::

    from fastapi import FastAPI
    from faceid import create_faceid_router, register_exception_handlers

    app = FastAPI()
    app.include_router(create_faceid_router())
    register_exception_handlers(app)
"""

from .backends import (
    BackendUnavailable,
    DetectedFace,
    FaceEmbedder,
    FaceRecognitionEmbedder,
    InsightFaceEmbedder,
    MultipleFacesDetected,
    NoFaceDetected,
    build_embedder,
)
from .config import FaceIDSettings, get_settings
from .quality import QualityReport
from .router import (
    create_faceid_router,
    get_service,
    register_exception_handlers,
    set_service,
)
from .service import FaceIDService, SubjectNotFound
from .sql import SQLFaceStore, StoreUnavailable
from .store import FaceStore, FaceTemplate, InMemoryFaceStore, JSONFileFaceStore

__all__ = [
    "BackendUnavailable",
    "DetectedFace",
    "FaceEmbedder",
    "FaceIDService",
    "FaceIDSettings",
    "FaceRecognitionEmbedder",
    "FaceStore",
    "FaceTemplate",
    "InMemoryFaceStore",
    "InsightFaceEmbedder",
    "JSONFileFaceStore",
    "MultipleFacesDetected",
    "NoFaceDetected",
    "QualityReport",
    "SQLFaceStore",
    "StoreUnavailable",
    "SubjectNotFound",
    "build_embedder",
    "create_faceid_router",
    "get_service",
    "get_settings",
    "register_exception_handlers",
    "set_service",
]

__version__ = "1.0.0"
