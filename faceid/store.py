"""Template storage.

A template is one subject's set of face embeddings. Swap the implementation for
pgvector/Qdrant/Milvus by implementing :class:`FaceStore` -- the service only
needs ``upsert``, ``get``, ``delete``, ``list_subjects`` and ``matrix``.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class FaceTemplate:
    subject_id: str
    embeddings: list[list[float]] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    backend: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    reference_images: list[str] = field(default_factory=list)  # base64, optional

    def to_json(self) -> dict:
        return {
            "subject_id": self.subject_id,
            "embeddings": self.embeddings,
            "metadata": self.metadata,
            "backend": self.backend,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "reference_images": self.reference_images,
        }

    @classmethod
    def from_json(cls, raw: dict) -> FaceTemplate:
        return cls(
            subject_id=raw["subject_id"],
            embeddings=[list(map(float, e)) for e in raw.get("embeddings", [])],
            metadata=dict(raw.get("metadata") or {}),
            backend=raw.get("backend", ""),
            created_at=raw.get("created_at", _now()),
            updated_at=raw.get("updated_at", _now()),
            reference_images=list(raw.get("reference_images") or []),
        )


@runtime_checkable
class FaceStore(Protocol):
    async def upsert(self, template: FaceTemplate) -> FaceTemplate: ...
    async def get(self, subject_id: str) -> FaceTemplate | None: ...
    async def delete(self, subject_id: str) -> bool: ...
    async def list_subjects(self, limit: int = 100, offset: int = 0) -> list[FaceTemplate]: ...
    async def count(self) -> int: ...
    async def matrix(self) -> tuple[np.ndarray, list[str]]:
        """Return (N x D matrix of all embeddings, subject_id per row)."""


class InMemoryFaceStore:
    """Process-local store. Fine for a single worker, tests and demos."""

    def __init__(self) -> None:
        self._data: dict[str, FaceTemplate] = {}
        self._lock = asyncio.Lock()

    async def upsert(self, template: FaceTemplate) -> FaceTemplate:
        async with self._lock:
            existing = self._data.get(template.subject_id)
            if existing:
                existing.embeddings.extend(template.embeddings)
                existing.reference_images.extend(template.reference_images)
                existing.metadata.update(template.metadata)
                existing.backend = template.backend or existing.backend
                existing.updated_at = _now()
                return existing
            self._data[template.subject_id] = template
            return template

    async def get(self, subject_id: str) -> FaceTemplate | None:
        return self._data.get(subject_id)

    async def delete(self, subject_id: str) -> bool:
        async with self._lock:
            return self._data.pop(subject_id, None) is not None

    async def list_subjects(self, limit: int = 100, offset: int = 0) -> list[FaceTemplate]:
        return list(self._data.values())[offset : offset + limit]

    async def count(self) -> int:
        return len(self._data)

    async def matrix(self) -> tuple[np.ndarray, list[str]]:
        rows: list[list[float]] = []
        owners: list[str] = []
        for tpl in self._data.values():
            for emb in tpl.embeddings:
                rows.append(emb)
                owners.append(tpl.subject_id)
        if not rows:
            return np.zeros((0, 0), dtype=np.float32), []
        return np.asarray(rows, dtype=np.float32), owners


class JSONFileFaceStore(InMemoryFaceStore):
    """In-memory store persisted to a JSON file after every write.

    Good enough up to a few thousand subjects. Beyond that use a vector DB.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        super().__init__()
        self._path = Path(path)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text("utf-8") or "{}")
        self._data = {
            sid: FaceTemplate.from_json(tpl) for sid, tpl in raw.get("subjects", {}).items()
        }

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"subjects": {sid: tpl.to_json() for sid, tpl in self._data.items()}}
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, self._path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    async def upsert(self, template: FaceTemplate) -> FaceTemplate:
        result = await super().upsert(template)
        await asyncio.to_thread(self._flush)
        return result

    async def delete(self, subject_id: str) -> bool:
        removed = await super().delete(subject_id)
        if removed:
            await asyncio.to_thread(self._flush)
        return removed
