"""Framework-agnostic face identification service.

Nothing here imports FastAPI: you can use ``FaceIDService`` from a worker, a CLI
or a gRPC server just as well as from the bundled router.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import numpy as np

from .backends import (
    DetectedFace,
    FaceEmbedder,
    MultipleFacesDetected,
    NoFaceDetected,
    build_embedder,
)
from .client import ClientProvider
from .config import FaceIDSettings, get_settings
from .images import raw_b64, validate
from .matching import Candidate, best_against_template, confidence, top_k
from .quality import QualityReport, assess
from .store import FaceStore, FaceTemplate, InMemoryFaceStore, JSONFileFaceStore


class SubjectNotFound(KeyError):
    pass


@dataclass(slots=True)
class ProbeResult:
    face: DetectedFace
    quality: QualityReport | None


class FaceIDService:
    def __init__(
        self,
        settings: FaceIDSettings | None = None,
        *,
        store: FaceStore | None = None,
        embedder: FaceEmbedder | None = None,
        client=None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store: FaceStore = store or self._default_store(self.settings)
        self._embedder = embedder
        self._embedder_lock = asyncio.Lock()
        self._clients = ClientProvider(self.settings, client)

    # -- wiring ----------------------------------------------------------
    @staticmethod
    def _default_store(settings: FaceIDSettings) -> FaceStore:
        if settings.db_url:
            from .sql import SQLFaceStore

            return SQLFaceStore(
                settings.db_url,
                table_prefix=settings.db_table_prefix,
                echo=settings.db_echo,
                create_schema=settings.db_create_schema,
            )
        if settings.store_path:
            return JSONFileFaceStore(settings.store_path)
        return InMemoryFaceStore()

    async def embedder(self) -> FaceEmbedder:
        """Load the biometric model once, off the event loop."""

        if self._embedder is None:
            async with self._embedder_lock:
                if self._embedder is None:
                    self._embedder = await asyncio.to_thread(build_embedder, self.settings)
        return self._embedder

    def _openai(self):
        return self._clients.get()

    @property
    def threshold(self) -> float:
        return self.settings.match_threshold

    # -- primitives ------------------------------------------------------
    async def detect(self, image: bytes) -> list[DetectedFace]:
        validate(image, max_bytes=self.settings.max_image_bytes)
        embedder = await self.embedder()
        faces = await asyncio.to_thread(embedder.detect, image)
        return [f for f in faces if f.det_score >= self.settings.min_detection_score]

    def _pick(self, faces: list[DetectedFace]) -> DetectedFace:
        if not faces:
            raise NoFaceDetected("No face detected in the image")
        if len(faces) == 1:
            return faces[0]
        policy = self.settings.multi_face_policy
        if policy == "reject":
            raise MultipleFacesDetected(f"{len(faces)} faces detected; expected exactly one")
        if policy == "best_score":
            return max(faces, key=lambda f: f.det_score)
        return max(faces, key=lambda f: f.area)

    async def quality(self, image: bytes) -> QualityReport | None:
        """Run the OpenAI capture-quality/anti-spoof assist when enabled."""

        if not self.settings.openai_assist:
            return None
        return await assess(self._openai(), self.settings, image)

    async def _probe(self, image: bytes, *, with_quality: bool) -> ProbeResult:
        faces_task = asyncio.create_task(self.detect(image))
        quality_task = (
            asyncio.create_task(self.quality(image))
            if with_quality and self.settings.openai_assist
            else None
        )
        try:
            faces = await faces_task
        except BaseException:
            if quality_task:
                quality_task.cancel()
            raise
        report = await quality_task if quality_task else None
        return ProbeResult(face=self._pick(faces), quality=report)

    # -- public API ------------------------------------------------------
    async def enroll(
        self,
        subject_id: str,
        image: bytes,
        *,
        metadata: dict[str, str] | None = None,
        check_quality: bool = True,
    ) -> tuple[FaceTemplate, DetectedFace, QualityReport | None]:
        probe = await self._probe(image, with_quality=check_quality)
        if probe.quality and not probe.quality.usable:
            raise ValueError(
                "Capture rejected by quality check: " + "; ".join(probe.quality.issues)
            )
        embedder = await self.embedder()
        template = FaceTemplate(
            subject_id=subject_id,
            embeddings=[probe.face.embedding.tolist()],
            metadata=metadata or {},
            backend=embedder.name,
            reference_images=[raw_b64(image)] if self.settings.store_reference_images else [],
        )
        stored = await self.store.upsert(template)
        return stored, probe.face, probe.quality

    async def verify(
        self,
        subject_id: str,
        image: bytes,
        *,
        threshold: float | None = None,
        check_quality: bool = False,
    ) -> tuple[float, DetectedFace, QualityReport | None]:
        template = await self.store.get(subject_id)
        if template is None:
            raise SubjectNotFound(subject_id)
        probe = await self._probe(image, with_quality=check_quality)
        similarity = best_against_template(probe.face.embedding, template.embeddings)
        return similarity, probe.face, probe.quality

    async def compare(
        self, image_a: bytes, image_b: bytes
    ) -> tuple[float, DetectedFace, DetectedFace]:
        faces_a, faces_b = await asyncio.gather(self.detect(image_a), self.detect(image_b))
        face_a, face_b = self._pick(faces_a), self._pick(faces_b)
        similarity = float(np.clip(np.dot(face_a.embedding, face_b.embedding), -1.0, 1.0))
        return similarity, face_a, face_b

    async def identify(
        self,
        image: bytes,
        *,
        limit: int = 5,
        check_quality: bool = False,
    ) -> tuple[list[Candidate], DetectedFace, QualityReport | None, int]:
        probe = await self._probe(image, with_quality=check_quality)
        matrix, owners = await self.store.matrix()
        limit = max(1, min(limit, self.settings.max_identify_results))
        candidates = top_k(probe.face.embedding, matrix, owners, limit)
        return candidates, probe.face, probe.quality, len(set(owners))

    async def liveness(self, image: bytes) -> QualityReport:
        if not self.settings.openai_assist:
            raise RuntimeError(
                "Liveness screening needs the OpenAI assist: set FACEID_OPENAI_ASSIST=true"
            )
        validate(image, max_bytes=self.settings.max_image_bytes)
        return await assess(self._openai(), self.settings, image, model=self.settings.vision_model)

    def confidence(self, similarity: float, threshold: float | None = None) -> float:
        return confidence(similarity, threshold if threshold is not None else self.threshold)
