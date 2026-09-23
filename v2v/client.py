"""AsyncOpenAI client management.

An ``AsyncOpenAI`` instance owns an httpx connection pool bound to the event
loop it was first used on. Under uvicorn there is one loop and one client, but
tests, ``TestClient``, notebooks and multi-loop workers can hand you a different
loop -- reusing the old pool then fails with ``Event loop is closed``.

:class:`ClientProvider` keeps one client per loop (weakly, so loops are not
leaked) and hands back an explicitly injected client untouched.
"""

from __future__ import annotations

import asyncio
import weakref

from openai import AsyncOpenAI

from .config import V2VSettings, get_settings


def build_client(settings: V2VSettings | None = None) -> AsyncOpenAI:
    settings = settings or get_settings()
    kwargs: dict[str, object] = {
        "timeout": settings.request_timeout,
        "max_retries": settings.max_retries,
    }
    if settings.api_key:
        kwargs["api_key"] = settings.api_key
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    return AsyncOpenAI(**kwargs)  # type: ignore[arg-type]


class ClientProvider:
    def __init__(self, settings: V2VSettings, client: AsyncOpenAI | None = None) -> None:
        self._settings = settings
        self._explicit = client
        self._per_loop: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        self._detached: AsyncOpenAI | None = None

    def get(self) -> AsyncOpenAI:
        if self._explicit is not None:
            return self._explicit
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # called outside a running loop
            if self._detached is None:
                self._detached = build_client(self._settings)
            return self._detached
        client = self._per_loop.get(loop)
        if client is None:
            client = build_client(self._settings)
            self._per_loop[loop] = client
        return client


_default_provider: ClientProvider | None = None


def get_client() -> AsyncOpenAI:
    """Process-wide client for the environment-configured settings."""

    global _default_provider
    if _default_provider is None:
        _default_provider = ClientProvider(get_settings())
    return _default_provider.get()
