"""Cosine matching over L2-normalised embeddings."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class Candidate:
    subject_id: str
    similarity: float


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two already-normalised vectors, clipped to [-1, 1]."""

    return float(np.clip(np.dot(a, b), -1.0, 1.0))


def best_against_template(probe: np.ndarray, embeddings: list[list[float]]) -> float:
    """Highest similarity between the probe and any enrolled sample."""

    if not embeddings:
        return -1.0
    mat = np.asarray(embeddings, dtype=np.float32)
    return float(np.clip(mat @ probe, -1.0, 1.0).max())


def top_k(
    probe: np.ndarray,
    matrix: np.ndarray,
    owners: list[str],
    k: int,
) -> list[Candidate]:
    """Rank subjects by their best-matching enrolled sample."""

    if matrix.size == 0:
        return []
    scores = np.clip(matrix @ probe, -1.0, 1.0)
    best: dict[str, float] = {}
    for owner, score in zip(owners, scores.tolist(), strict=False):
        if score > best.get(owner, -2.0):
            best[owner] = score
    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)[:k]
    return [Candidate(subject_id=sid, similarity=float(score)) for sid, score in ranked]


def confidence(similarity: float, threshold: float) -> float:
    """Map a cosine score to a 0..1 confidence around the decision threshold."""

    if similarity >= threshold:
        span = max(1e-6, 1.0 - threshold)
        return float(min(1.0, 0.5 + 0.5 * (similarity - threshold) / span))
    span = max(1e-6, threshold + 1.0)
    return float(max(0.0, 0.5 * (similarity + 1.0) / span))
