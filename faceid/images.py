"""Image helpers: sniffing, validation and data-URL encoding.

The Responses API takes images as ``input_image`` parts whose ``image_url`` is
either a public URL or a base64 data URL:
``data:image/jpeg;base64,<encoded>``.
"""

from __future__ import annotations

import base64

SUPPORTED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}

_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class ImageError(ValueError):
    """Raised when the uploaded bytes are not a usable image."""


def sniff_mime(data: bytes) -> str:
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ImageError("Unsupported image format: expected JPEG, PNG, WEBP or GIF")


def validate(data: bytes, *, max_bytes: int) -> str:
    if not data:
        raise ImageError("Empty image payload")
    if len(data) > max_bytes:
        raise ImageError(f"Image too large: {len(data)} bytes > {max_bytes} limit")
    mime = sniff_mime(data)
    if mime not in SUPPORTED_MIME:
        raise ImageError(f"Unsupported image mime type: {mime}")
    return mime


def to_data_url(data: bytes, *, max_bytes: int) -> str:
    mime = validate(data, max_bytes=max_bytes)
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def data_url_from_b64(b64: str, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{b64}"


def raw_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
