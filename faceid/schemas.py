"""Public request/response models for the faceid HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FaceBox(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int
    det_score: float


class QualityInfo(BaseModel):
    usable: bool
    quality_score: float
    liveness_score: float
    face_count: int
    issues: list[str] = Field(default_factory=list)
    spoof_signals: list[str] = Field(default_factory=list)
    summary: str = ""


class DetectResponse(BaseModel):
    backend: str
    faces: list[FaceBox]
    quality: QualityInfo | None = None


class EnrollResponse(BaseModel):
    subject_id: str
    backend: str
    samples: int = Field(description="Total embeddings stored for this subject")
    face: FaceBox
    quality: QualityInfo | None = None
    created_at: str
    updated_at: str


class VerifyResponse(BaseModel):
    """1:1 -- does this probe belong to the claimed subject?"""

    subject_id: str
    match: bool
    similarity: float
    confidence: float
    threshold: float
    backend: str
    face: FaceBox
    quality: QualityInfo | None = None


class CompareResponse(BaseModel):
    """1:1 -- do these two images show the same person? (no enrollment needed)"""

    match: bool
    similarity: float
    confidence: float
    threshold: float
    backend: str
    faces: list[FaceBox]


class IdentifyCandidate(BaseModel):
    subject_id: str
    similarity: float
    confidence: float
    match: bool
    metadata: dict[str, str] = Field(default_factory=dict)


class IdentifyResponse(BaseModel):
    """1:N -- who is this, among the enrolled subjects?"""

    best_match: IdentifyCandidate | None
    candidates: list[IdentifyCandidate]
    threshold: float
    backend: str
    searched_subjects: int
    face: FaceBox
    quality: QualityInfo | None = None


class SubjectInfo(BaseModel):
    subject_id: str
    samples: int
    backend: str
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class SubjectListResponse(BaseModel):
    total: int
    subjects: list[SubjectInfo]


class DeleteResponse(BaseModel):
    subject_id: str
    deleted: bool


class LivenessResponse(BaseModel):
    live: bool
    liveness_score: float
    quality_score: float
    threshold: float
    spoof_signals: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    summary: str = ""


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    backend: str
    embedding_dim: int
    subjects: int
    store: str
    openai_assist: bool
