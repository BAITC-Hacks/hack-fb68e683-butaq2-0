"""Optional OpenAI vision assist: capture quality and presentation-attack screening.

This never decides *who* someone is -- identity comes from the local biometric
backend. Here the model only judges the *picture*: is it sharp, well lit, a
single frontal face, and does it look like a live capture rather than a photo of
a screen or a printout.

Uses the Responses API with structured outputs:
https://developers.openai.com/api/docs/guides/images-vision
"""

from __future__ import annotations

from openai import AsyncOpenAI
from pydantic import BaseModel

from .config import FaceIDSettings
from .images import to_data_url

_QUALITY_PROMPT = (
    "You are a capture-quality inspector for a face-verification pipeline. "
    "Judge only the image itself: sharpness, exposure, occlusion (mask, sunglasses, "
    "hand), head pose, face size in frame, and whether the capture looks like a live "
    "camera frame or a re-capture of a photo, printout or screen (moire, bezel, "
    "screen glare, paper edges, flat lighting). "
    "Never guess who the person is, never describe protected attributes. "
    "Scores are 0.0-1.0."
)


class QualityReport(BaseModel):
    """Structured verdict returned by the vision model."""

    usable: bool
    quality_score: float
    liveness_score: float
    face_count: int
    issues: list[str]
    spoof_signals: list[str]
    summary: str


async def assess(
    client: AsyncOpenAI,
    settings: FaceIDSettings,
    image: bytes,
    *,
    model: str | None = None,
) -> QualityReport:
    data_url = to_data_url(image, max_bytes=settings.max_image_bytes)
    response = await client.responses.parse(
        model=model or settings.screening_model,
        instructions=_QUALITY_PROMPT,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Assess this capture."},
                    {
                        "type": "input_image",
                        "image_url": data_url,
                        "detail": settings.image_detail,
                    },
                ],
            }
        ],
        text_format=QualityReport,
    )
    report = response.output_parsed
    if report is None:  # refusal or incomplete generation
        raise RuntimeError("Vision model returned no structured quality report")
    report.quality_score = min(1.0, max(0.0, report.quality_score))
    report.liveness_score = min(1.0, max(0.0, report.liveness_score))
    return report
