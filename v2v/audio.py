"""Audio helpers for realtime PCM and for file uploads.

The Realtime API carries audio as **base64 of raw samples** inside JSON events:
``input_audio_buffer.append`` going up, ``response.output_audio.delta`` coming
down. PCM16 is 16-bit little-endian mono.
"""

from __future__ import annotations

import base64
import io
import struct

CONTENT_TYPE_BY_FORMAT = {
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "pcm": "audio/L16",
}


class AudioError(ValueError):
    """Raised when the uploaded bytes are not usable audio."""


EXT_BY_MIME = {
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/webm": "webm",
    "audio/mp4": "m4a",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
    "audio/aac": "aac",
}


def sniff_mime(data: bytes) -> str:
    """Identify the container from its magic bytes.

    Transcription rejects anything outside its accepted list, so bytes that are
    not audio at all are worth catching here: the round trip costs a request and
    comes back as an upstream failure rather than a bad request.
    """

    if data[:3] == b"ID3":
        return "audio/mpeg"
    if data[:4] == b"fLaC":
        return "audio/flac"
    if data[:4] == b"OggS":
        return "audio/ogg"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "audio/webm"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[4:8] == b"ftyp":
        return "audio/mp4"
    # MPEG frame sync: 11 set bits, then a layer field that tells MP3 (layers
    # I-III) apart from ADTS AAC (layer 00).
    if len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0:
        return "audio/mpeg" if data[1] & 0x06 else "audio/aac"
    raise AudioError("Unsupported audio format: expected MP3, WAV, WEBM, MP4/M4A, OGG, FLAC or AAC")


def validate(data: bytes, *, max_bytes: int) -> str:
    if not data:
        raise AudioError("Empty audio payload")
    if len(data) > max_bytes:
        # Plain ValueError: a size overrun stays 422, as it was before sniffing.
        raise ValueError(f"Audio exceeds {max_bytes} bytes")
    return sniff_mime(data)


def upload_name(filename: str | None, mime: str) -> str:
    """Name the upload after what the bytes actually are.

    Browsers hand over ``MediaRecorder`` blobs called ``blob`` or ``recording``,
    and transcription reads the container from the extension -- so a mislabelled
    file is refused even when its bytes are perfectly fine.
    """

    ext = EXT_BY_MIME.get(mime, "wav")
    stem = (filename or "audio").rsplit("/", 1)[-1]
    if "." in stem:
        stem, current = stem.rsplit(".", 1)
        if current.lower() == ext:
            return f"{stem}.{current}"
    return f"{stem or 'audio'}.{ext}"


def b64encode(pcm: bytes) -> str:
    return base64.b64encode(pcm).decode("ascii")


def b64decode(data: str) -> bytes:
    return base64.b64decode(data)


def pcm16_to_wav(pcm: bytes, *, sample_rate: int = 24000, channels: int = 1) -> bytes:
    """Wrap raw PCM16 in a WAV container so it can be uploaded or played back."""

    bits = 16
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    header += b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits
    )
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def wav_stream(pcm: bytes, *, sample_rate: int = 24000) -> io.BytesIO:
    return io.BytesIO(pcm16_to_wav(pcm, sample_rate=sample_rate))


def chunk(data: bytes, size: int) -> list[bytes]:
    """Split a buffer into fixed-size frames (e.g. 20 ms of PCM16 @24k = 960 B)."""

    return [data[i : i + size] for i in range(0, len(data), size)]
