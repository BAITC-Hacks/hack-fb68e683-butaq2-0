"""Local face-embedding backends.

A backend turns image bytes into one or more :class:`DetectedFace` records that
carry an L2-normalised embedding. Matching is then plain cosine similarity, so
any backend producing normalised vectors can be dropped in -- implement
:class:`FaceEmbedder` and pass it to ``FaceIDService(embedder=...)``.

Both bundled backends go through :class:`BaseEmbedder`, which fixes two failure
modes that bite in production:

* **small images** -- a 120 px selfie thumbnail is upscaled before detection;
* **tightly cropped avatars** -- RetinaFace and dlib both need context around
  the head, so a 112x112 aligned crop detects as *no face*. On an empty result
  the image is re-tried on a padded canvas and the boxes are mapped back to the
  original coordinate system.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np

from .config import FaceIDSettings


class BackendUnavailable(RuntimeError):
    """No usable face-recognition backend is installed."""


class NoFaceDetected(ValueError):
    pass


class MultipleFacesDetected(ValueError):
    pass


@dataclass(slots=True)
class DetectedFace:
    embedding: np.ndarray  # L2-normalised, float32
    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2 in original image pixels
    det_score: float
    area: int = field(init=False)

    def __post_init__(self) -> None:
        x1, y1, x2, y2 = self.bbox
        self.area = max(0, x2 - x1) * max(0, y2 - y1)


@runtime_checkable
class FaceEmbedder(Protocol):
    name: str
    dim: int

    def detect(self, image: bytes) -> list[DetectedFace]:
        """Detect faces and return them with normalised embeddings.

        Synchronous and CPU-bound; the service calls it in a worker thread.
        """


def decode_rgb(image: bytes) -> np.ndarray:
    """Decode to an RGB array, honouring EXIF orientation."""

    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(image)) as img:
        img = ImageOps.exif_transpose(img)
        return np.asarray(img.convert("RGB"))


def normalise(vec: np.ndarray) -> np.ndarray:
    vec = np.asarray(vec, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(vec))
    if norm == 0.0:
        raise ValueError("Zero-length face embedding")
    return vec / norm


def _resize(rgb: np.ndarray, scale: float) -> np.ndarray:
    from PIL import Image

    h, w = rgb.shape[:2]
    resized = Image.fromarray(rgb).resize(
        (max(1, int(round(w * scale))), max(1, int(round(h * scale)))), Image.LANCZOS
    )
    return np.asarray(resized)


def _pad(rgb: np.ndarray, ratio: float) -> tuple[np.ndarray, int, int]:
    """Centre the image on a larger canvas of edge-replicated pixels."""

    h, w = rgb.shape[:2]
    dx, dy = int(round(w * ratio)), int(round(h * ratio))
    canvas = np.pad(rgb, ((dy, dy), (dx, dx), (0, 0)), mode="edge")
    return canvas, dx, dy


class BaseEmbedder:
    """Shared detection retry/rescale logic. Subclasses implement ``_detect_array``."""

    name = "base"
    dim = 0

    def __init__(self, settings: FaceIDSettings) -> None:
        self.settings = settings

    def _detect_array(self, rgb: np.ndarray) -> list[DetectedFace]:  # pragma: no cover
        raise NotImplementedError

    def detect(self, image: bytes) -> list[DetectedFace]:
        rgb = decode_rgb(image)
        h, w = rgb.shape[:2]

        scale = 1.0
        min_side = min(h, w)
        if self.settings.min_input_side and min_side < self.settings.min_input_side:
            scale = min(
                self.settings.max_upscale, self.settings.min_input_side / float(min_side)
            )
        working = _resize(rgb, scale) if scale > 1.0 else rgb

        faces = self._detect_array(working)
        if faces:
            return [self._rescale(f, scale, 0, 0, w, h) for f in faces]

        if not self.settings.pad_retry:
            return []

        padded, dx, dy = _pad(working, self.settings.pad_ratio)
        faces = self._detect_array(padded)
        return [self._rescale(f, scale, dx, dy, w, h) for f in faces]

    @staticmethod
    def _rescale(
        face: DetectedFace, scale: float, dx: int, dy: int, width: int, height: int
    ) -> DetectedFace:
        """Map a box found on the working canvas back onto the original image."""

        x1, y1, x2, y2 = face.bbox
        x1, y1, x2, y2 = (x1 - dx), (y1 - dy), (x2 - dx), (y2 - dy)
        if scale != 1.0:
            x1, y1, x2, y2 = (int(round(v / scale)) for v in (x1, y1, x2, y2))
        x1 = max(0, min(width, int(x1)))
        x2 = max(0, min(width, int(x2)))
        y1 = max(0, min(height, int(y1)))
        y2 = max(0, min(height, int(y2)))
        return DetectedFace(
            embedding=face.embedding, bbox=(x1, y1, x2, y2), det_score=face.det_score
        )


class InsightFaceEmbedder(BaseEmbedder):
    """ArcFace embeddings (512-d) via ``insightface`` + ``onnxruntime``.

    pip install insightface onnxruntime opencv-python-headless

    The first call downloads the model pack (~300 MB for ``buffalo_l``) into
    ``~/.insightface/models``; pre-bake it into your image for fast cold starts.
    """

    name = "insightface"
    dim = 512

    def __init__(self, settings: FaceIDSettings) -> None:
        super().__init__(settings)
        try:
            from insightface.app import FaceAnalysis
        except ImportError as exc:  # pragma: no cover - import guard
            raise BackendUnavailable(
                "insightface is not installed: pip install insightface onnxruntime "
                "opencv-python-headless"
            ) from exc
        self._app = FaceAnalysis(
            name=settings.insightface_model,
            providers=list(settings.insightface_providers),
        )
        self._app.prepare(
            ctx_id=0, det_size=(settings.detection_size, settings.detection_size)
        )

    def _detect_array(self, rgb: np.ndarray) -> list[DetectedFace]:
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])  # insightface expects BGR
        out: list[DetectedFace] = []
        for face in self._app.get(bgr):
            embedding = getattr(face, "normed_embedding", None)
            if embedding is None:
                embedding = face.embedding
            x1, y1, x2, y2 = (int(v) for v in face.bbox)
            out.append(
                DetectedFace(
                    embedding=normalise(embedding),
                    bbox=(x1, y1, x2, y2),
                    det_score=float(getattr(face, "det_score", 1.0)),
                )
            )
        return out


class FaceRecognitionEmbedder(BaseEmbedder):
    """dlib ResNet embeddings (128-d) via ``face_recognition``.

    pip install face-recognition

    dlib's classic euclidean threshold of 0.6 corresponds to roughly 0.82 cosine
    similarity on these vectors -- set ``FACEID_MATCH_THRESHOLD`` accordingly
    when you pick this backend.
    """

    name = "face_recognition"
    dim = 128
    suggested_threshold = 0.82

    def __init__(self, settings: FaceIDSettings) -> None:
        super().__init__(settings)
        try:
            import face_recognition  # noqa: F401
        except ImportError as exc:  # pragma: no cover - import guard
            raise BackendUnavailable(
                "face_recognition is not installed: pip install face-recognition"
            ) from exc

    def _detect_array(self, rgb: np.ndarray) -> list[DetectedFace]:
        import face_recognition

        rgb = np.ascontiguousarray(rgb)
        boxes = face_recognition.face_locations(rgb, model=self.settings.dlib_detector)
        if not boxes:
            return []
        encodings = face_recognition.face_encodings(rgb, known_face_locations=boxes)
        out: list[DetectedFace] = []
        for (top, right, bottom, left), enc in zip(boxes, encodings, strict=False):
            out.append(
                DetectedFace(
                    embedding=normalise(enc),
                    bbox=(int(left), int(top), int(right), int(bottom)),
                    det_score=1.0,
                )
            )
        return out


def build_embedder(settings: FaceIDSettings) -> FaceEmbedder:
    """Instantiate the configured backend, or the first one that imports."""

    order: tuple[str, ...]
    if settings.backend == "auto":
        order = ("insightface", "face_recognition")
    else:
        order = (settings.backend,)

    errors: list[str] = []
    for name in order:
        try:
            if name == "insightface":
                return InsightFaceEmbedder(settings)
            if name == "face_recognition":
                return FaceRecognitionEmbedder(settings)
        except BackendUnavailable as exc:
            errors.append(str(exc))
    raise BackendUnavailable(
        "No face-recognition backend available. Tried: "
        + ", ".join(order)
        + ". Details: "
        + " | ".join(errors)
    )
