"""SQL-backed template storage.

One ``FACEID_DB_URL`` and enrollments live in Postgres/MySQL/SQLite instead of a
JSON file, so several workers share the same gallery::

    FACEID_DB_URL=postgresql+asyncpg://user:pass@host/faceid

The schema is deliberately plain SQL -- two tables, embeddings as ``float32``
blobs -- so it works on every dialect SQLAlchemy speaks and needs no extension.
1:N search still happens in-process on a numpy matrix; for a gallery large
enough that shipping every vector to the app is the bottleneck, push the search
into the database (pgvector) by subclassing and overriding :meth:`matrix` or
``FaceIDService.identify``.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import numpy as np

from .store import FaceTemplate, _now

__all__ = ["SQLFaceStore", "StoreUnavailable", "normalise_db_url"]


class StoreUnavailable(RuntimeError):
    """Raised when the driver for a configured database URL is not installed."""


#: Driver module -> the extra that installs it, for the error message.
_EXTRAS = {"asyncpg": "postgres", "aiosqlite": "sqlite", "aiomysql": "mysql"}

#: URL schemes people actually type, mapped to the async driver they need.
_ASYNC_DRIVERS = {
    "postgres": "postgresql+asyncpg",
    "postgresql": "postgresql+asyncpg",
    "sqlite": "sqlite+aiosqlite",
    "mysql": "mysql+aiomysql",
    "mariadb": "mariadb+aiomysql",
}


def normalise_db_url(url: str) -> str:
    """Upgrade a sync URL to its async driver; leave an explicit one alone.

    ``postgresql://...`` -> ``postgresql+asyncpg://...``, because a sync driver
    on an async engine fails at connect time with a much less obvious error.
    """

    scheme, sep, rest = url.partition("://")
    if not sep or "+" in scheme:
        return url
    return f"{_ASYNC_DRIVERS.get(scheme.lower(), scheme)}{sep}{rest}"


def _pack(vector: list[float] | np.ndarray) -> bytes:
    return np.ascontiguousarray(vector, dtype=np.float32).tobytes()


def _unpack(blob: bytes) -> list[float]:
    return np.frombuffer(blob, dtype=np.float32).tolist()


class SQLFaceStore:
    """:class:`~faceid.store.FaceStore` over any SQLAlchemy async URL.

        store = SQLFaceStore("postgresql+asyncpg://localhost/faceid")
        app.include_router(create_faceid_router(store=store))

    Tables (``faceid_subjects``, ``faceid_embeddings``) are created on first use
    unless ``create_schema=False`` -- set that when migrations own the schema.
    """

    def __init__(
        self,
        url: str,
        *,
        table_prefix: str = "faceid_",
        engine: Any | None = None,
        echo: bool = False,
        create_schema: bool = True,
        engine_kwargs: dict[str, Any] | None = None,
    ) -> None:
        try:
            import sqlalchemy as sa
            from sqlalchemy.ext.asyncio import create_async_engine
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on install
            raise StoreUnavailable(
                "FACEID_DB_URL is set but SQLAlchemy is missing. "
                'Install the extra: pip install "faceid[postgres]" (or [sqlite]).'
            ) from exc

        self._sa = sa
        self.url = normalise_db_url(url)
        try:
            self._engine = engine or create_async_engine(
                self.url, echo=echo, **(engine_kwargs or {})
            )
        except sa.exc.NoSuchModuleError as exc:
            raise StoreUnavailable(
                f"{url!r} names a dialect SQLAlchemy does not know ({exc}). "
                "Supported out of the box: postgresql, mysql/mariadb, sqlite."
            ) from exc
        except ModuleNotFoundError as exc:
            missing = exc.name or "its async driver"
            extra = _EXTRAS.get(missing)
            hint = f'pip install "faceid[{extra}]"' if extra else f"pip install {missing}"
            raise StoreUnavailable(
                f"No async driver for {self.url!r} ({missing} is not installed). {hint}"
            ) from exc
        self._schema_ready = not create_schema
        self._lock = asyncio.Lock()
        self._cache: tuple[tuple[int, int], np.ndarray, list[str]] | None = None

        meta = sa.MetaData()
        self.subjects = sa.Table(
            f"{table_prefix}subjects",
            meta,
            sa.Column("subject_id", sa.String(255), primary_key=True),
            sa.Column("backend", sa.String(64), nullable=False, default=""),
            sa.Column("meta", sa.Text, nullable=False, default="{}"),
            sa.Column("reference_images", sa.Text, nullable=False, default="[]"),
            sa.Column("created_at", sa.String(40), nullable=False),
            sa.Column("updated_at", sa.String(40), nullable=False),
        )
        self.embeddings = sa.Table(
            f"{table_prefix}embeddings",
            meta,
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "subject_id",
                sa.String(255),
                sa.ForeignKey(f"{table_prefix}subjects.subject_id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("dim", sa.Integer, nullable=False),
            sa.Column("vector", sa.LargeBinary, nullable=False),
            sa.Column("created_at", sa.String(40), nullable=False),
        )
        self._meta = meta

    # -- lifecycle ---------------------------------------------------------

    @asynccontextmanager
    async def _tx(self):
        """A transaction, with the schema created once on the first call."""

        if not self._schema_ready:
            async with self._lock:
                if not self._schema_ready:
                    async with self._engine.begin() as conn:
                        await conn.run_sync(self._meta.create_all)
                    self._schema_ready = True
        async with self._engine.begin() as conn:
            yield conn

    async def aclose(self) -> None:
        """Dispose the connection pool (call from your app's shutdown hook)."""

        await self._engine.dispose()

    # -- reads -------------------------------------------------------------

    async def get(self, subject_id: str) -> FaceTemplate | None:
        sa = self._sa
        async with self._tx() as conn:
            row = (
                (
                    await conn.execute(
                        sa.select(self.subjects).where(self.subjects.c.subject_id == subject_id)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            vectors = (
                (
                    await conn.execute(
                        sa.select(self.embeddings.c.vector)
                        .where(self.embeddings.c.subject_id == subject_id)
                        .order_by(self.embeddings.c.id)
                    )
                )
                .scalars()
                .all()
            )
        return self._template(row, [_unpack(v) for v in vectors])

    async def list_subjects(self, limit: int = 100, offset: int = 0) -> list[FaceTemplate]:
        sa = self._sa
        async with self._tx() as conn:
            rows = (
                (
                    await conn.execute(
                        sa.select(self.subjects)
                        .order_by(self.subjects.c.created_at, self.subjects.c.subject_id)
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .mappings()
                .all()
            )
            if not rows:
                return []
            ids = [row["subject_id"] for row in rows]
            vectors: dict[str, list[list[float]]] = {sid: [] for sid in ids}
            for sid, blob in await conn.execute(
                sa.select(self.embeddings.c.subject_id, self.embeddings.c.vector)
                .where(self.embeddings.c.subject_id.in_(ids))
                .order_by(self.embeddings.c.id)
            ):
                vectors[sid].append(_unpack(blob))
        return [self._template(row, vectors[row["subject_id"]]) for row in rows]

    async def count(self) -> int:
        sa = self._sa
        async with self._tx() as conn:
            return int(
                (await conn.execute(sa.select(sa.func.count()).select_from(self.subjects))).scalar()
                or 0
            )

    async def matrix(self) -> tuple[np.ndarray, list[str]]:
        """All embeddings as one ``(N, D)`` float32 matrix, plus their owners.

        Cached against a ``(rows, max id)`` stamp, so a busy ``/identify`` costs
        one cheap aggregate instead of a full table scan -- and the cache still
        invalidates when *another* worker enrolls or deletes.
        """

        sa = self._sa
        async with self._tx() as conn:
            stamp_row = (
                await conn.execute(
                    sa.select(sa.func.count(), sa.func.max(self.embeddings.c.id)).select_from(
                        self.embeddings
                    )
                )
            ).first()
            stamp = (int(stamp_row[0] or 0), int(stamp_row[1] or 0))
            if self._cache and self._cache[0] == stamp:
                return self._cache[1], self._cache[2]
            rows = await conn.execute(
                sa.select(self.embeddings.c.subject_id, self.embeddings.c.vector).order_by(
                    self.embeddings.c.id
                )
            )
            owners: list[str] = []
            vectors: list[list[float]] = []
            for subject_id, blob in rows:
                owners.append(subject_id)
                vectors.append(_unpack(blob))

        matrix = (
            np.asarray(vectors, dtype=np.float32) if vectors else np.zeros((0, 0), dtype=np.float32)
        )
        self._cache = (stamp, matrix, owners)
        return matrix, owners

    # -- writes ------------------------------------------------------------

    async def upsert(self, template: FaceTemplate) -> FaceTemplate:
        """Insert the subject, or append samples to the existing one."""

        sa = self._sa
        now = _now()
        async with self._tx() as conn:
            query = sa.select(self.subjects).where(
                self.subjects.c.subject_id == template.subject_id
            )
            if self._engine.dialect.name != "sqlite":  # SQLite has no SELECT ... FOR UPDATE
                query = query.with_for_update()
            existing = (await conn.execute(query)).mappings().first()

            if existing is None:
                merged_meta = dict(template.metadata)
                merged_refs = list(template.reference_images)
                created_at = template.created_at
                await conn.execute(
                    sa.insert(self.subjects).values(
                        subject_id=template.subject_id,
                        backend=template.backend,
                        meta=json.dumps(merged_meta, ensure_ascii=False),
                        reference_images=json.dumps(merged_refs),
                        created_at=created_at,
                        updated_at=now,
                    )
                )
            else:
                merged_meta = {**json.loads(existing["meta"] or "{}"), **template.metadata}
                merged_refs = [
                    *json.loads(existing["reference_images"] or "[]"),
                    *template.reference_images,
                ]
                created_at = existing["created_at"]
                await conn.execute(
                    sa.update(self.subjects)
                    .where(self.subjects.c.subject_id == template.subject_id)
                    .values(
                        backend=template.backend or existing["backend"],
                        meta=json.dumps(merged_meta, ensure_ascii=False),
                        reference_images=json.dumps(merged_refs),
                        updated_at=now,
                    )
                )

            if template.embeddings:
                await conn.execute(
                    sa.insert(self.embeddings),
                    [
                        {
                            "subject_id": template.subject_id,
                            "dim": len(vector),
                            "vector": _pack(vector),
                            "created_at": now,
                        }
                        for vector in template.embeddings
                    ],
                )
            stored = (
                (
                    await conn.execute(
                        sa.select(self.embeddings.c.vector)
                        .where(self.embeddings.c.subject_id == template.subject_id)
                        .order_by(self.embeddings.c.id)
                    )
                )
                .scalars()
                .all()
            )

        return FaceTemplate(
            subject_id=template.subject_id,
            embeddings=[_unpack(v) for v in stored],
            metadata=merged_meta,
            backend=template.backend or (existing["backend"] if existing else ""),
            created_at=created_at,
            updated_at=now,
            reference_images=merged_refs,
        )

    async def delete(self, subject_id: str) -> bool:
        # Deleted explicitly rather than via ON DELETE CASCADE: SQLite ships
        # with foreign keys switched off.
        sa = self._sa
        async with self._tx() as conn:
            await conn.execute(
                sa.delete(self.embeddings).where(self.embeddings.c.subject_id == subject_id)
            )
            result = await conn.execute(
                sa.delete(self.subjects).where(self.subjects.c.subject_id == subject_id)
            )
        return bool(result.rowcount)

    # -- helpers -----------------------------------------------------------

    def _template(self, row, embeddings: list[list[float]]) -> FaceTemplate:
        return FaceTemplate(
            subject_id=row["subject_id"],
            embeddings=embeddings,
            metadata=json.loads(row["meta"] or "{}"),
            backend=row["backend"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            reference_images=json.loads(row["reference_images"] or "[]"),
        )
