"""Configuration for the faceid library.

Face recognition itself is done **locally** by a biometric model (ArcFace via
InsightFace, or dlib/face_recognition as a fallback) -- OpenAI has no face
identification API and its policies restrict biometric identification of
individuals. OpenAI is used here only as an optional *assist*: judging capture
quality and screening obvious presentation attacks (photo-of-a-photo, screen
replay, printed mask) from the image.

Override anything with ``FACEID_``-prefixed environment variables, e.g.
``FACEID_MATCH_THRESHOLD=0.45``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ImageDetail = Literal["low", "high", "auto"]
BackendName = Literal["auto", "insightface", "face_recognition"]


class FaceIDSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FACEID_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Biometric backend (local, no network) ---------------------------
    backend: BackendName = "auto"
    # InsightFace model pack: buffalo_l (ArcFace r100, 512-d) or buffalo_s.
    insightface_model: str = "buffalo_l"
    insightface_providers: list[str] = ["CPUExecutionProvider"]
    detection_size: int = 640
    min_detection_score: float = 0.50
    # Detectors need context around the head: upscale small images and, when a
    # frame comes back empty, retry it on an edge-padded canvas. This is what
    # makes tightly cropped avatars (e.g. 112x112 aligned faces) work.
    min_input_side: int = 320
    max_upscale: float = 4.0
    pad_retry: bool = True
    pad_ratio: float = 1.0
    # face_recognition backend only: "hog" (CPU, fast) or "cnn" (accurate, slow)
    dlib_detector: str = "hog"
    # Cosine similarity over L2-normalised embeddings. 0.40-0.50 is the usual
    # ArcFace operating range; raise it for lower FAR, lower it for lower FRR.
    match_threshold: float = 0.42
    # When several faces are in frame: "largest" | "best_score" | "reject"
    multi_face_policy: Literal["largest", "best_score", "reject"] = "largest"

    # --- Optional OpenAI assist (quality / anti-spoof screening) ---------
    openai_assist: bool = False
    api_key: str | None = None
    base_url: str | None = None
    request_timeout: float = 60.0
    max_retries: int = 2
    # Vision-capable models: https://developers.openai.com/api/docs/models
    vision_model: str = "gpt-5.6-terra"
    screening_model: str = "gpt-5.6-luna"
    image_detail: ImageDetail = "high"
    liveness_threshold: float = 0.70
    quality_threshold: float = 0.55

    # --- Limits ----------------------------------------------------------
    max_image_bytes: int = 8 * 1024 * 1024
    max_identify_results: int = 10

    # --- Storage ----------------------------------------------------------
    # Precedence: db_url -> store_path -> in-memory (process local).
    # db_url is any SQLAlchemy URL; a sync scheme is upgraded to its async
    # driver, so "postgresql://u:p@host/faceid" becomes postgresql+asyncpg.
    db_url: str | None = None
    db_table_prefix: str = "faceid_"
    db_echo: bool = False
    db_create_schema: bool = True
    store_path: str | None = None
    store_reference_images: bool = False

    # --- HTTP -------------------------------------------------------------
    api_prefix: str = "/faceid"
    api_tag: str = "faceid"


@lru_cache(maxsize=1)
def get_settings() -> FaceIDSettings:
    return FaceIDSettings()
