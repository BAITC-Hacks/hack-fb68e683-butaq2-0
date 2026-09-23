"""FastAPI router for faceid.

    from faceid import create_faceid_router
    app.include_router(create_faceid_router())

Every endpoint is plain multipart/form-data so it works from curl, a browser
form or any HTTP client without a custom SDK.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from .backends import BackendUnavailable, MultipleFacesDetected, NoFaceDetected
from .config import FaceIDSettings, get_settings
from .images import ImageError
from .quality import QualityReport
from .schemas import (
    CompareResponse,
    DeleteResponse,
    DetectResponse,
    EnrollResponse,
    FaceBox,
    HealthResponse,
    IdentifyCandidate,
    IdentifyResponse,
    LivenessResponse,
    QualityInfo,
    SubjectInfo,
    SubjectListResponse,
    VerifyResponse,
)
from .service import FaceIDService, SubjectNotFound
from .store import FaceStore, FaceTemplate

_default_service: FaceIDService | None = None


def get_service() -> FaceIDService:
    """Default service dependency, built lazily from environment settings.

    Override it in your app when you want your own store/backend::

        app.dependency_overrides[get_service] = lambda: my_service
    """

    global _default_service
    if _default_service is None:
        _default_service = FaceIDService()
    return _default_service


def set_service(service: FaceIDService | None) -> None:
    """Replace (or reset with ``None``) the process-wide default service."""

    global _default_service
    _default_service = service


ServiceDep = Annotated[FaceIDService, Depends(get_service)]


def _box(face) -> FaceBox:
    x1, y1, x2, y2 = face.bbox
    return FaceBox(x1=x1, y1=y1, x2=x2, y2=y2, det_score=face.det_score)


def _quality(report: QualityReport | None) -> QualityInfo | None:
    if report is None:
        return None
    return QualityInfo(**report.model_dump())


def _subject(template: FaceTemplate) -> SubjectInfo:
    return SubjectInfo(
        subject_id=template.subject_id,
        samples=len(template.embeddings),
        backend=template.backend,
        metadata=template.metadata,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def _parse_metadata(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, f"metadata is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(422, "metadata must be a JSON object")
    return {str(k): str(v) for k, v in data.items()}


# A per-request threshold is a feature, but it gates access: cosine similarity
# over L2-normalised embeddings lives in [-1, 1], so anything <= -1 accepts every
# face. Bounding it to [0, 1] keeps the override and drops the open door.
_THRESHOLD = {
    "ge": 0.0,
    "le": 1.0,
    "description": "Override the configured cosine cut-off, 0.0-1.0",
}


async def _read(upload: UploadFile, settings: FaceIDSettings) -> bytes:
    data = await upload.read()
    if len(data) > settings.max_image_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Image exceeds {settings.max_image_bytes} bytes",
        )
    return data


def create_faceid_router(
    *,
    service: FaceIDService | None = None,
    settings: FaceIDSettings | None = None,
    store: FaceStore | None = None,
    embedder=None,
    prefix: str | None = None,
    tags: list[str] | None = None,
    dependencies: list | None = None,
) -> APIRouter:
    """Build the faceid router.

    Called bare it reads settings from the environment and uses the shared
    :func:`get_service` dependency (overridable via ``app.dependency_overrides``).
    Pass ``service=`` / ``settings=`` / ``store=`` / ``embedder=`` to bind a
    dedicated, fully isolated instance instead -- which is what you want when
    the router is mounted more than once, or inside a host app that already has
    its own settings management.
    """

    settings = settings or (service.settings if service else get_settings())
    bound: FaceIDService | None = service
    if bound is None and (
        store is not None or embedder is not None or settings is not get_settings()
    ):
        bound = FaceIDService(settings, store=store, embedder=embedder)

    # Evaluated at def-time inside this closure, so each router keeps its own
    # service without touching process-wide state.
    service_dep = Annotated[FaceIDService, Depends(lambda: bound)] if bound else ServiceDep

    router = APIRouter(
        prefix=prefix if prefix is not None else settings.api_prefix,
        tags=tags if tags is not None else [settings.api_tag],
        dependencies=dependencies or [],
    )

    @router.get("/health", response_model=HealthResponse, summary="Backend + store status")
    async def health(service: service_dep) -> HealthResponse:
        try:
            embedder = await service.embedder()
            name, dim, ok = embedder.name, embedder.dim, True
        except BackendUnavailable as exc:
            name, dim, ok = f"unavailable: {exc}", 0, False
        return HealthResponse(
            status="ok" if ok else "degraded",
            backend=name,
            embedding_dim=dim,
            subjects=await service.store.count(),
            store=type(service.store).__name__,
            openai_assist=service.settings.openai_assist,
        )

    @router.post("/detect", response_model=DetectResponse, summary="Detect faces in an image")
    async def detect(
        service: service_dep,
        image: Annotated[UploadFile, File(description="JPEG/PNG/WEBP frame")],
        check_quality: Annotated[bool, Query(description="Run the OpenAI capture check")] = False,
    ) -> DetectResponse:
        data = await _read(image, service.settings)
        faces = await service.detect(data)
        report = await service.quality(data) if check_quality else None
        embedder = await service.embedder()
        return DetectResponse(
            backend=embedder.name,
            faces=[_box(f) for f in faces],
            quality=_quality(report),
        )

    @router.post(
        "/enroll",
        response_model=EnrollResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Enroll (or add another sample to) a subject",
    )
    async def enroll(
        service: service_dep,
        subject_id: Annotated[str, Form(description="Your stable user id")],
        image: Annotated[UploadFile, File()],
        metadata: Annotated[
            str | None, Form(description='JSON object, e.g. {"name":"Ann"}')
        ] = None,
        check_quality: Annotated[bool, Form()] = True,
    ) -> EnrollResponse:
        data = await _read(image, service.settings)
        template, face, report = await service.enroll(
            subject_id,
            data,
            metadata=_parse_metadata(metadata),
            check_quality=check_quality,
        )
        return EnrollResponse(
            subject_id=template.subject_id,
            backend=template.backend,
            samples=len(template.embeddings),
            face=_box(face),
            quality=_quality(report),
            created_at=template.created_at,
            updated_at=template.updated_at,
        )

    @router.post(
        "/verify", response_model=VerifyResponse, summary="1:1 against an enrolled subject"
    )
    async def verify(
        service: service_dep,
        subject_id: Annotated[str, Form()],
        image: Annotated[UploadFile, File()],
        threshold: Annotated[float | None, Form(**_THRESHOLD)] = None,
        check_quality: Annotated[bool, Form()] = False,
    ) -> VerifyResponse:
        data = await _read(image, service.settings)
        similarity, face, report = await service.verify(
            subject_id, data, check_quality=check_quality
        )
        cut = threshold if threshold is not None else service.threshold
        return VerifyResponse(
            subject_id=subject_id,
            match=similarity >= cut,
            similarity=similarity,
            confidence=service.confidence(similarity, cut),
            threshold=cut,
            backend=(await service.embedder()).name,
            face=_box(face),
            quality=_quality(report),
        )

    @router.post(
        "/compare", response_model=CompareResponse, summary="1:1 between two images"
    )
    async def compare(
        service: service_dep,
        image_a: Annotated[UploadFile, File()],
        image_b: Annotated[UploadFile, File()],
        threshold: Annotated[float | None, Form(**_THRESHOLD)] = None,
    ) -> CompareResponse:
        a = await _read(image_a, service.settings)
        b = await _read(image_b, service.settings)
        similarity, face_a, face_b = await service.compare(a, b)
        cut = threshold if threshold is not None else service.threshold
        return CompareResponse(
            match=similarity >= cut,
            similarity=similarity,
            confidence=service.confidence(similarity, cut),
            threshold=cut,
            backend=(await service.embedder()).name,
            faces=[_box(face_a), _box(face_b)],
        )

    @router.post(
        "/identify", response_model=IdentifyResponse, summary="1:N search over enrollments"
    )
    async def identify(
        service: service_dep,
        image: Annotated[UploadFile, File()],
        limit: Annotated[int, Form(ge=1, description="Clamped to FACEID_MAX_IDENTIFY_RESULTS")] = 5,
        threshold: Annotated[float | None, Form(**_THRESHOLD)] = None,
        check_quality: Annotated[bool, Form()] = False,
    ) -> IdentifyResponse:
        data = await _read(image, service.settings)
        candidates, face, report, searched = await service.identify(
            data, limit=limit, check_quality=check_quality
        )
        cut = threshold if threshold is not None else service.threshold
        enriched: list[IdentifyCandidate] = []
        for cand in candidates:
            template = await service.store.get(cand.subject_id)
            enriched.append(
                IdentifyCandidate(
                    subject_id=cand.subject_id,
                    similarity=cand.similarity,
                    confidence=service.confidence(cand.similarity, cut),
                    match=cand.similarity >= cut,
                    metadata=template.metadata if template else {},
                )
            )
        best = enriched[0] if enriched and enriched[0].match else None
        return IdentifyResponse(
            best_match=best,
            candidates=enriched,
            threshold=cut,
            backend=(await service.embedder()).name,
            searched_subjects=searched,
            face=_box(face),
            quality=_quality(report),
        )

    @router.post(
        "/liveness",
        response_model=LivenessResponse,
        summary="Presentation-attack screening (OpenAI vision assist)",
    )
    async def liveness(
        service: service_dep,
        image: Annotated[UploadFile, File()],
    ) -> LivenessResponse:
        if not service.settings.openai_assist:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Liveness screening needs the OpenAI assist: set FACEID_OPENAI_ASSIST=true",
            )
        data = await _read(image, service.settings)
        report = await service.liveness(data)
        return LivenessResponse(
            live=report.liveness_score >= service.settings.liveness_threshold,
            liveness_score=report.liveness_score,
            quality_score=report.quality_score,
            threshold=service.settings.liveness_threshold,
            spoof_signals=report.spoof_signals,
            issues=report.issues,
            summary=report.summary,
        )

    @router.get("/subjects", response_model=SubjectListResponse, summary="List enrolled subjects")
    async def list_subjects(
        service: service_dep,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> SubjectListResponse:
        templates = await service.store.list_subjects(limit=limit, offset=offset)
        return SubjectListResponse(
            total=await service.store.count(),
            subjects=[_subject(t) for t in templates],
        )

    @router.get("/subjects/{subject_id}", response_model=SubjectInfo, summary="Get one subject")
    async def get_subject(service: service_dep, subject_id: str) -> SubjectInfo:
        template = await service.store.get(subject_id)
        if template is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown subject: {subject_id}")
        return _subject(template)

    @router.delete(
        "/subjects/{subject_id}",
        response_model=DeleteResponse,
        summary="Delete a subject and its templates",
    )
    async def delete_subject(service: service_dep, subject_id: str) -> DeleteResponse:
        deleted = await service.store.delete(subject_id)
        return DeleteResponse(subject_id=subject_id, deleted=deleted)

    return router


def register_exception_handlers(app) -> None:
    """Map the library's domain errors onto clean HTTP responses.

    Call once on your app: ``register_exception_handlers(app)``.
    """

    from fastapi.responses import JSONResponse

    def _json(code: int, detail: str) -> JSONResponse:
        return JSONResponse(status_code=code, content={"detail": detail})

    @app.exception_handler(NoFaceDetected)
    async def _no_face(_, exc: NoFaceDetected):
        return _json(422, str(exc))

    @app.exception_handler(MultipleFacesDetected)
    async def _many_faces(_, exc: MultipleFacesDetected):
        return _json(422, str(exc))

    @app.exception_handler(ImageError)
    async def _bad_image(_, exc: ImageError):
        return _json(status.HTTP_400_BAD_REQUEST, str(exc))

    @app.exception_handler(SubjectNotFound)
    async def _no_subject(_, exc: SubjectNotFound):
        return _json(status.HTTP_404_NOT_FOUND, f"Unknown subject: {exc.args[0]}")

    @app.exception_handler(BackendUnavailable)
    async def _no_backend(_, exc: BackendUnavailable):
        return _json(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
